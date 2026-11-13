"""Death autopsy: reconstruct the last seconds before every death and say why it happened.

For each kill (victim V, killer K) unitick measures, from the replay:

* ``exposed_s``      how long K had an uninterrupted line of sight to V before the kill
* ``facing_err``     how far V's crosshair was from K's head when K first had sight
                     (big = V was looking somewhere else)
* ``killer_err``     the same for K (small = K was already aiming there)
* ``shot_back``      whether V fired at all between K's first sight and the death
* ``crossfire``      how many enemies had sight of V in the last second (K included)
* ``tradeable``      whether any living teammate of V had sight of K within 2 s after
* ``no_sight``       K never had line of sight at the kill: wallbang, smoke, utility

and assigns a primary cause, checked in this order:

    no_sight     "Killed without line of sight"   (wallbang, through smoke, utility)
    off_angle    "Not looking at the killer"      (facing_err > 40 deg)
    crossfire    "Caught in a crossfire"          (2+ enemies could see you)
    late         "Saw them, did not shoot back"   (looking roughly right, never fired)
    duel         "Lost the aim duel"              (both on target, the killer was faster)

Line of sight needs map geometry (CS2 via awpy's BVH). Without it, the autopsy falls
back to facing only, and says so.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import polars as pl

from ..match import Match
from .crosshair import _eye
from .engagements import is_aim_weapon
from .geometry import angular_error

Visibility = Callable[[np.ndarray, np.ndarray], np.ndarray]

CAUSES = {
    "no_sight": "Killed without line of sight",
    "off_angle": "Not looking at the killer",
    "crossfire": "Caught in a crossfire",
    "late": "Saw them, did not shoot back",
    "duel": "Lost the aim duel",
    "unknown": "Not enough data",
}


def _frames(match: Match) -> pl.DataFrame:
    return (match.frames.select("tick", "round_num", "player_id", "side", "x", "y", "z", "pitch", "yaw",
                                pl.col("is_alive").fill_null(True), pl.col("is_crouched").fill_null(False))
            .with_columns(pl.col("tick").cast(pl.Int64)).sort("tick"))


def _eye_xyz(df: pl.DataFrame, stand: float, crouch: float, p: str = "") -> np.ndarray:
    z = df[f"{p}z"].to_numpy().astype(np.float64)
    h = np.where(df[f"{p}is_crouched"].to_numpy(), crouch, stand)
    return np.column_stack([df[f"{p}x"].to_numpy(), df[f"{p}y"].to_numpy(), z + h]).astype(np.float64)


def autopsy(match: Match, *, visibility: Visibility | None = None, window_s: float = 3.0,
            off_angle_deg: float = 40.0, trade_s: float = 2.0) -> pl.DataFrame:
    """One row per kill with the measurements above and a ``cause``."""
    k = match.kills.filter(pl.col("attacker_id").is_not_null() & pl.col("victim_id").is_not_null()
                           & (pl.col("attacker_side") != pl.col("victim_side")))
    if k.is_empty() or match.frames.is_empty():
        return pl.DataFrame()
    k = k.with_row_index("death_id").with_columns(pl.col("tick").cast(pl.Int64))
    f = _frames(match)
    stand, crouch = _eye(match)
    tr = match.tick_rate
    W, T = match.ticks(window_s), match.ticks(trade_s)
    shots = match.shots.select(pl.col("player_id"), pl.col("tick").cast(pl.Int64)) if not match.shots.is_empty() else None
    aimable = k.select("death_id", is_aim_weapon().alias("aim_weapon"))

    # --- the window before each death: killer and victim, frame by frame
    w = (k.select("death_id", "round_num", "tick", "attacker_id", "victim_id")
         .rename({"tick": "kt"})
         .join(f.select(pl.col("tick"), pl.col("player_id").alias("victim_id"),
                        *[pl.col(c).alias(f"v_{c}") for c in ("x", "y", "z", "pitch", "yaw", "is_crouched", "is_alive")]),
               on="victim_id")
         .filter(pl.col("tick").is_between(pl.col("kt") - W, pl.col("kt")) & pl.col("v_is_alive")))
    kf = f.select(pl.col("tick").alias("_kt2"), pl.col("player_id").alias("attacker_id"),
                  *[pl.col(c).alias(f"k_{c}") for c in ("x", "y", "z", "pitch", "yaw", "is_crouched")])
    w = (w.sort("tick").join_asof(kf.sort("_kt2"), left_on="tick", right_on="_kt2", by="attacker_id",
                                  strategy="nearest", tolerance=match.ticks(0.2), check_sortedness=False)
         .drop_nulls("k_x").sort("death_id", "tick"))
    if w.is_empty():
        return pl.DataFrame()
    v_eye = _eye_xyz(w, stand, crouch, "v_")
    k_eye = _eye_xyz(w, stand, crouch, "k_")
    have_los = visibility is not None
    los = visibility(k_eye, v_eye) if have_los else np.ones(len(w), dtype=bool)
    v_err = angular_error(w["v_pitch"].to_numpy(), w["v_yaw"].to_numpy(), v_eye, k_eye)["error_deg"]
    k_err = angular_error(w["k_pitch"].to_numpy(), w["k_yaw"].to_numpy(), k_eye, v_eye)["error_deg"]
    w = w.with_columns(pl.Series("los", los, dtype=pl.Boolean), pl.Series("v_err", v_err, dtype=pl.Float32),
                       pl.Series("k_err", k_err, dtype=pl.Float32))
    # Sight onset: earliest tick of the visible run that reaches the kill. Without
    # walls there is no run to find, so the reference moment is a fixed reaction
    # lead before the kill, and exposure time is unknown.
    if have_los:
        w = w.sort("death_id", "tick", descending=[False, True]).with_columns(
            pl.col("los").cast(pl.Int8).cum_min().over("death_id").alias("_run"))
    else:
        lead = match.ticks(0.3)
        w = w.sort("death_id", "tick", descending=[False, True]).with_columns(
            (pl.col("tick") >= pl.col("kt") - lead).cast(pl.Int8).alias("_run"))
    per = w.group_by("death_id").agg(
        pl.col("los").first().alias("los_at_kill"),
        pl.col("tick").filter(pl.col("_run") == 1).min().alias("onset"),
        pl.col("tick").max().alias("_last"),
        pl.col("v_err").filter(pl.col("_run") == 1).last().alias("facing_err"),
        pl.col("k_err").filter(pl.col("_run") == 1).last().alias("killer_err"),
        pl.col("v_err").first().alias("facing_err_at_kill"),
        pl.col("v_x").first().alias("vx"), pl.col("v_y").first().alias("vy"),
        pl.col("k_x").first().alias("kx"), pl.col("k_y").first().alias("ky"))
    per = per.with_columns(((pl.col("_last") - pl.col("onset")) / tr).cast(pl.Float32).alias("exposed_s")
                           if have_los else pl.lit(None, pl.Float32).alias("exposed_s"))

    # --- did the victim shoot back after the killer had sight?
    out = k.select("death_id", "round_num", "tick", "attacker_id", "attacker_name", "victim_id", "victim_name",
                   "victim_side", "weapon", "headshot").join(per, on="death_id", how="left").join(
        aimable, on="death_id")
    if shots is not None:
        sb = (out.select("death_id", "victim_id", "onset", "tick")
              .join(shots.rename({"player_id": "victim_id", "tick": "_st"}), on="victim_id")
              .filter(pl.col("_st").is_between(pl.col("onset").fill_null(pl.col("tick") - W), pl.col("tick")))
              .group_by("death_id").agg(pl.len().alias("shots_back")))
        out = out.join(sb, on="death_id", how="left").with_columns(pl.col("shots_back").fill_null(0))
    else:
        out = out.with_columns(pl.lit(None, pl.UInt32).alias("shots_back"))

    # --- crossfire and trade (need line of sight)
    cross, trade = {}, {}
    if have_los:
        alive = f.filter(pl.col("is_alive"))
        last1 = match.ticks(1.0)
        q = (out.select("death_id", "round_num", "tick", "victim_id", "victim_side")
             .join(alive.select("round_num", pl.col("tick").alias("ft"), "player_id", "side",
                                "x", "y", "z", "is_crouched"), on="round_num"))
        # enemies -> victim in the last second (sampled frames)
        en = q.filter((pl.col("side") != pl.col("victim_side")) & pl.col("ft").is_between(pl.col("tick") - last1, pl.col("tick")))
        vv = alive.select(pl.col("tick").alias("ft"), pl.col("player_id").alias("victim_id"),
                          pl.col("x").alias("vx_"), pl.col("y").alias("vy_"), pl.col("z").alias("vz_"),
                          pl.col("is_crouched").alias("vc_"))
        en = en.join(vv, on=["ft", "victim_id"])
        if not en.is_empty():
            a = _eye_xyz(en, stand, crouch)
            b = _eye_xyz(en.rename({"vx_": "bx", "vy_": "by", "vz_": "bz", "vc_": "bc"})
                         .select(pl.col("bx").alias("x"), pl.col("by").alias("y"), pl.col("bz").alias("z"),
                                 pl.col("bc").alias("is_crouched")), stand, crouch)
            en = en.with_columns(pl.Series("_v", visibility(a, b), dtype=pl.Boolean))
            cross = dict(en.filter("_v").group_by("death_id").agg(pl.col("player_id").n_unique()).iter_rows())
        # teammates -> killer within trade_s after the death
        tm = (out.select("death_id", "round_num", "tick", "attacker_id", "victim_id", "victim_side")
              .join(alive.select("round_num", pl.col("tick").alias("ft"), "player_id", "side",
                                 "x", "y", "z", "is_crouched"), on="round_num")
              .filter((pl.col("side") == pl.col("victim_side")) & (pl.col("player_id") != pl.col("victim_id"))
                      & pl.col("ft").is_between(pl.col("tick"), pl.col("tick") + T)))
        kk = alive.select(pl.col("tick").alias("ft"), pl.col("player_id").alias("attacker_id"),
                          pl.col("x").alias("bx"), pl.col("y").alias("by"), pl.col("z").alias("bz"),
                          pl.col("is_crouched").alias("bc"))
        tm = tm.join(kk, on=["ft", "attacker_id"])
        if not tm.is_empty():
            a = _eye_xyz(tm, stand, crouch)
            b = _eye_xyz(tm.select(pl.col("bx").alias("x"), pl.col("by").alias("y"), pl.col("bz").alias("z"),
                                   pl.col("bc").alias("is_crouched")), stand, crouch)
            tm = tm.with_columns(pl.Series("_v", visibility(a, b), dtype=pl.Boolean))
            trade = {d: True for d in tm.filter("_v")["death_id"].unique().to_list()}
    out = out.with_columns(
        pl.col("death_id").replace_strict(cross, default=0 if have_los else None, return_dtype=pl.Int32).alias("crossfire"),
        pl.col("death_id").replace_strict(trade, default=False if have_los else None, return_dtype=pl.Boolean).alias("tradeable"),
    )

    # --- primary cause
    def cause(r) -> str:
        if r["onset"] is None and r["facing_err_at_kill"] is None:
            return "unknown"
        if have_los and (not r["los_at_kill"] or not r["aim_weapon"]):
            return "no_sight"
        fe = r["facing_err"] if r["facing_err"] is not None else r["facing_err_at_kill"]
        if fe is not None and fe > off_angle_deg:
            return "off_angle"
        if have_los and (r["crossfire"] or 0) >= 2:
            return "crossfire"
        if r["shots_back"] == 0:
            return "late"
        return "duel"

    causes = [cause(r) for r in out.iter_rows(named=True)]
    out = out.with_columns(pl.Series("cause", causes), pl.lit(have_los).alias("used_los"))
    return out.with_columns(pl.col("cause").replace_strict(CAUSES).alias("cause_text")).sort("tick")


def summary(per_death: pl.DataFrame) -> pl.DataFrame:
    """Per player: deaths by cause, share of off-angle deaths, untradeable deaths."""
    if per_death.is_empty():
        return pl.DataFrame()
    return (per_death.group_by("victim_id").agg(
        pl.col("victim_name").last().alias("player"),
        pl.len().alias("deaths"),
        *[(pl.col("cause") == c).sum().alias(c) for c in CAUSES if c != "unknown"],
        (pl.col("tradeable") == False).sum().alias("untradeable"),
        pl.col("exposed_s").median().round(2).alias("median_exposed_s"))
        .sort("deaths", descending=True))
