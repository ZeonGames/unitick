"""CrosshairIQ: how far was your crosshair from the enemy's head when the fight began?

For every engagement the reference moment is, in order of preference:

``sight``  the first tick of the uninterrupted line of sight that ended in the
           attacker's first shot (needs a ``visibility`` function, e.g. awpy's BVH).
``held``   line of sight existed for the whole search window: a long hold. The
           reference is the window start, so this still rewards pre-aim.
``flick``  no visibility data and no hit data (kill-only sources such as VALORANT
           replays): the start of the attacker's final flick onto the target,
           found from view-angle speed in the 1.5 s before the kill.
``lead``   no visibility data: ``lead_s`` seconds before the first shot (a
           reaction-time proxy), or ``kill_lead_s`` before a kill when the
           player was already on target (no flick).

Error is the great-circle angle between the view vector and the eye->head vector,
split into ``yaw_err`` (horizontal) and ``pitch_err`` (vertical / head-height).

Per-engagement score: ``100 * 0.5 ** (error_deg / half_life_deg)``. With the
default 5 degree half-life: 0 deg = 100, 5 deg = 50, 10 deg = 25.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import polars as pl

from ..match import Match
from .engagements import engagements as build_engagements
from .geometry import angular_error

Visibility = Callable[[np.ndarray, np.ndarray], np.ndarray]

DEFAULT_EYE = {"stand": 64.0, "crouch": 46.0}


def _eye(match: Match) -> tuple[float, float]:
    e = {**DEFAULT_EYE, **(match.meta.get("eye_height") or {})}
    return float(e["stand"]), float(e["crouch"])


def _frames(match: Match) -> pl.DataFrame:
    return (match.frames.filter(pl.col("is_alive").fill_null(True))
            .select("tick", "player_id", "x", "y", "z", "pitch", "yaw",
                    pl.col("is_crouched").fill_null(False))
            .with_columns(pl.col("tick").cast(pl.Int64)).sort("tick"))


def _lookup(frames: pl.DataFrame, q: pl.DataFrame, id_col: str, tick_col: str, prefix: str,
            tolerance: int) -> pl.DataFrame:
    """Nearest frame of player ``q[id_col]`` at ``q[tick_col]``, columns prefixed."""
    right = frames.rename({c: f"{prefix}{c}" for c in frames.columns if c != "tick"})
    right = right.rename({"tick": f"{prefix}tick"})
    idx = "_q_row"
    left = q.with_row_index(idx).with_columns(pl.col(tick_col).cast(pl.Int64).alias("_qt")).sort("_qt")
    out = left.join_asof(right, left_on="_qt", right_on=f"{prefix}tick", by_left=id_col,
                         by_right=f"{prefix}player_id", strategy="nearest", tolerance=tolerance,
                         check_sortedness=False)
    return out.sort(idx).drop(idx, "_qt")


def _points(df: pl.DataFrame, prefix: str, stand: float, crouch: float) -> np.ndarray:
    z = df[f"{prefix}z"].to_numpy().astype(np.float64)
    h = np.where(df[f"{prefix}is_crouched"].fill_null(False).to_numpy(), crouch, stand)
    return np.column_stack([df[f"{prefix}x"].to_numpy(), df[f"{prefix}y"].to_numpy(), z + h]).astype(
        np.float64)


def _sight_onsets(match: Match, e: pl.DataFrame, frames: pl.DataFrame, visibility: Visibility,
                  search_s: float, tol: int, stand: float, crouch: float) -> pl.DataFrame:
    win = match.ticks(search_s)
    w = (e.select("engagement_id", "attacker_id", "victim_id", "first_shot_tick")
         .join(frames.select("tick", pl.col("player_id").alias("attacker_id")), on="attacker_id")
         .filter((pl.col("tick") <= pl.col("first_shot_tick"))
                 & (pl.col("tick") >= pl.col("first_shot_tick") - win)))
    if w.is_empty():
        return pl.DataFrame(schema={"engagement_id": pl.UInt32, "onset_tick": pl.Int64,
                                    "onset_kind": pl.String})
    w = _lookup(frames, w, "attacker_id", "tick", "a_", tol)
    w = _lookup(frames, w, "victim_id", "tick", "v_", tol).drop_nulls(["a_x", "v_x"])
    vis = visibility(_points(w, "a_", stand, crouch), _points(w, "v_", stand, crouch))
    w = w.with_columns(pl.Series("_vis", vis, dtype=pl.Boolean))
    # Walk backwards from the shot: the onset is the earliest tick of the visible run
    # that touches the shot.
    w = w.sort(["engagement_id", "tick"], descending=[False, True]).with_columns(
        pl.col("_vis").cast(pl.Int8).cum_min().over("engagement_id").alias("_run"))
    agg = w.group_by("engagement_id").agg(
        pl.col("_vis").first().alias("_vis_at_shot"),
        pl.col("tick").filter(pl.col("_run") == 1).min().alias("onset_tick"),
        pl.col("tick").min().alias("_wstart"),
        pl.col("_run").min().alias("_all"))
    return agg.with_columns(
        pl.when(~pl.col("_vis_at_shot")).then(None).otherwise(pl.col("onset_tick")).alias("onset_tick"),
        pl.when(~pl.col("_vis_at_shot")).then(None)
        .when(pl.col("_all") == 1).then(pl.lit("held"))
        .otherwise(pl.lit("sight")).alias("onset_kind"),
    ).select("engagement_id", "onset_tick", "onset_kind")


def _flick_onsets(match: Match, e: pl.DataFrame, frames: pl.DataFrame, *, window_s: float = 1.5,
                  speed_thr: float = 90.0, settle_s: float = 0.45) -> pl.DataFrame:
    """Start of the final flick before each kill, from angular speed of the view."""
    win = match.ticks(window_s)
    w = (e.select("engagement_id", "attacker_id", "first_shot_tick")
         .join(frames.select("tick", pl.col("player_id").alias("attacker_id"), "pitch", "yaw"),
               on="attacker_id")
         .filter(pl.col("tick").is_between(pl.col("first_shot_tick") - win, pl.col("first_shot_tick")))
         .sort("engagement_id", "tick"))
    rows = []
    settle = match.ticks(settle_s)
    for (eid,), g in w.group_by("engagement_id", maintain_order=True):
        t = g["tick"].to_numpy()
        if len(t) < 3:
            continue
        v = np.stack([np.cos(np.radians(g["pitch"].to_numpy())) * np.cos(np.radians(g["yaw"].to_numpy())),
                      np.cos(np.radians(g["pitch"].to_numpy())) * np.sin(np.radians(g["yaw"].to_numpy())),
                      -np.sin(np.radians(g["pitch"].to_numpy()))], axis=1)
        ang = np.degrees(np.arccos(np.clip(np.sum(v[1:] * v[:-1], axis=1), -1, 1)))
        dt = np.maximum(np.diff(t), 1) / match.tick_rate
        speed = ang / dt  # speed[i] = motion between t[i] and t[i+1]
        i = len(speed) - 1
        while i >= 0 and speed[i] < speed_thr and t[-1] - t[i] <= settle:
            i -= 1
        if i < 0 or speed[i] < speed_thr:
            continue  # no flick: player was already on target
        while i >= 0 and speed[i] >= speed_thr:
            i -= 1
        rows.append((eid, int(t[i + 1])))  # i = last still segment before the flick
    return pl.DataFrame(rows, schema={"engagement_id": pl.UInt32, "flick_tick": pl.Int64}, orient="row")


def placement(match: Match, *, visibility: Visibility | None = None, lead_s: float = 0.2,
              kill_lead_s: float = 0.3,
              search_s: float = 3.0, half_life_deg: float = 5.0, min_distance: float = 96.0,
              engagements: pl.DataFrame | None = None) -> pl.DataFrame:
    """Per-engagement crosshair placement. See module docstring for definitions."""
    if match.frames.is_empty():
        raise ValueError("crosshair placement needs frames (player positions and angles)")
    e = engagements if engagements is not None else build_engagements(match)
    if e.is_empty():
        return e
    stand, crouch = _eye(match)
    frames = _frames(match)
    tol = int(match.meta.get("frame_every", 4)) * 2 + 1

    if visibility is not None:
        on = _sight_onsets(match, e, frames, visibility, search_s, tol, stand, crouch)
        e = e.join(on, on="engagement_id", how="left", maintain_order="left")
    else:
        e = e.with_columns(pl.lit(None, pl.Int64).alias("onset_tick"),
                           pl.lit(None, pl.String).alias("onset_kind"))
    if "source" not in e.columns:
        e = e.with_columns(pl.lit("damage").alias("source"))
    kill_based = e.filter((pl.col("source") == "kill") & pl.col("onset_tick").is_null())
    if not kill_based.is_empty():
        fl = _flick_onsets(match, kill_based, frames)
        e = e.join(fl, on="engagement_id", how="left", maintain_order="left")
    else:
        e = e.with_columns(pl.lit(None, pl.Int64).alias("flick_tick"))
    is_kill = pl.col("source") == "kill"
    lead = pl.when(is_kill).then(match.ticks(kill_lead_s)).otherwise(match.ticks(lead_s))
    e = e.with_columns(
        pl.coalesce("onset_tick", "flick_tick", pl.col("first_shot_tick") - lead).cast(pl.Int64)
        .alias("ref_tick"),
        pl.coalesce("onset_kind", pl.when(pl.col("flick_tick").is_not_null()).then(pl.lit("flick")),
                    pl.lit("lead")).alias("ref_source"),
        pl.coalesce("onset_tick", "flick_tick").alias("onset_tick"),
    )
    e = _lookup(frames, e, "attacker_id", "ref_tick", "a_", tol)
    e = _lookup(frames, e, "victim_id", "ref_tick", "v_", tol).drop_nulls(["a_x", "v_x", "a_pitch"])
    if e.is_empty():
        return e
    err = angular_error(e["a_pitch"].to_numpy(), e["a_yaw"].to_numpy(),
                        _points(e, "a_", stand, crouch), _points(e, "v_", stand, crouch))
    out = e.with_columns(
        *[pl.Series(k, v, dtype=pl.Float32) for k, v in err.items()],
        ((pl.col("first_shot_tick") - pl.col("onset_tick")) / match.tick_rate)
        .cast(pl.Float32).alias("reaction_s"),
    ).filter(pl.col("distance") >= min_distance)
    out = out.with_columns(
        (100.0 * 0.5 ** (pl.col("error_deg") / half_life_deg)).cast(pl.Float32).alias("score"))
    keep = ["engagement_id", "round_num", "attacker_id", "attacker_name", "attacker_side",
            "victim_id", "victim_name", "weapon", "first_shot_tick", "ref_tick", "ref_source",
            "reaction_s", "error_deg", "yaw_err", "pitch_err", "distance", "score", "killed",
            "damage", "a_x", "a_y", "a_z", "v_x", "v_y", "v_z", "a_pitch", "a_yaw"]
    return out.select(keep)


def leaderboard(per_engagement: pl.DataFrame, *, min_engagements: int = 5) -> pl.DataFrame:
    """Per-player Crosshair Placement Score and its components."""
    if per_engagement.is_empty():
        return pl.DataFrame()
    return (
        per_engagement.group_by("attacker_id")
        .agg(
            pl.col("attacker_name").last().alias("player"),
            pl.len().alias("engagements"),
            pl.col("score").mean().round(1).alias("cps"),
            pl.col("error_deg").median().round(2).alias("median_err_deg"),
            pl.col("yaw_err").abs().median().round(2).alias("median_horizontal_deg"),
            pl.col("pitch_err").abs().median().round(2).alias("median_vertical_deg"),
            pl.col("pitch_err").median().round(2).alias("vertical_bias_deg"),
            (pl.col("error_deg") < 5).mean().mul(100).round(1).alias("pct_under_5deg"),
            pl.col("reaction_s").median().round(3).alias("median_reaction_s"),
            pl.col("killed").mean().mul(100).round(1).alias("fight_kill_pct"),
        )
        .filter(pl.col("engagements") >= min_engagements)
        .sort("cps", descending=True)
        .rename({"attacker_id": "player_id"})
    )
