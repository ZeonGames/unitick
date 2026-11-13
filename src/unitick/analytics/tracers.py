"""Bullet tracers in 3D: where every shot started and where it ended.

* Shots (CS2): from the shooter's eye along the exact view angles. If the shot
  damaged someone within two ticks, it ends on that player's chest and counts as
  a hit (a kill if the victim died on it). Otherwise it ends where it meets the
  map: a binary search along the ray against the collision mesh, or a fixed range
  when there is no mesh.
* Kills only (VALORANT records no individual shots): one tracer per kill, from
  the killer's eye to the victim's chest.

Every tracer is one row: round, tick, shooter, start and end in world units,
victim (if any), and kind (``miss``, ``hit``, ``kill``).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import polars as pl

from ..match import Match
from .engagements import is_aim_weapon
from .geometry import view_vector

Visibility = Callable[[np.ndarray, np.ndarray], np.ndarray]
COLUMNS = ["round_num", "tick", "shooter_id", "x0", "y0", "z0", "x1", "y1", "z1", "victim_id", "kind"]


def _eye(match: Match) -> tuple[float, float]:
    e = match.meta.get("eye_height") or {}
    return float(e.get("stand", 64.0)), float(e.get("crouch", 46.0))


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema={"round_num": pl.Int32, "tick": pl.Int64, "shooter_id": pl.String,
                                "x0": pl.Float64, "y0": pl.Float64, "z0": pl.Float64, "x1": pl.Float64,
                                "y1": pl.Float64, "z1": pl.Float64, "victim_id": pl.String, "kind": pl.String})


def _victim_at(match: Match, q: pl.DataFrame, chest: float) -> pl.DataFrame:
    """Join each (victim_id, tick) to the victim's nearest frame position."""
    f = (match.frames.select(pl.col("tick").cast(pl.Int64).alias("_ft"), pl.col("player_id").alias("victim_id"),
                             pl.col("x").alias("vx"), pl.col("y").alias("vy"), pl.col("z").alias("vz"))
         .sort("_ft"))
    return (q.with_columns(pl.col("tick").cast(pl.Int64)).sort("tick")
            .join_asof(f, left_on="tick", right_on="_ft", by="victim_id", strategy="nearest",
                       tolerance=match.ticks(0.5), check_sortedness=False)
            .with_columns((pl.col("vz") + chest).alias("vz")))


def _ray_ends(a: np.ndarray, d: np.ndarray, visibility: Visibility | None, max_range: float,
              iters: int = 12) -> np.ndarray:
    """Where each ray from a along unit vector d first meets the map."""
    if visibility is None or not len(a):
        return a + d * max_range
    lo = np.zeros(len(a))
    hi = np.full(len(a), max_range)
    clear = visibility(a, a + d * max_range)
    hi[clear] = max_range
    lo[clear] = max_range
    todo = ~clear
    for _ in range(iters):
        if not todo.any():
            break
        mid = (lo + hi) / 2
        vis = np.zeros(len(a), dtype=bool)
        idx = np.nonzero(todo)[0]
        vis[idx] = visibility(a[idx], a[idx] + d[idx] * mid[idx, None])
        lo = np.where(todo & vis, mid, lo)
        hi = np.where(todo & ~vis, mid, hi)
    return a + d * lo[:, None]


def tracers(match: Match, *, visibility: Visibility | None = None, max_range: float | None = None,
            hit_window_ticks: int = 2) -> pl.DataFrame:
    stand, _ = _eye(match)
    chest = stand * 0.72
    units = match.meta.get("units", "")
    max_range = max_range or (6000.0 if units == "cm" else 3000.0)
    shots = match.shots
    if not shots.is_empty() and {"x", "y", "z", "pitch", "yaw"} <= set(shots.columns):
        s = (shots.filter(is_aim_weapon() & pl.col("x").is_not_null() & pl.col("pitch").is_not_null())
             .with_columns(pl.col("tick").cast(pl.Int64)).with_row_index("_sid"))
        if s.is_empty():
            return _empty()
        # Hits: the shooter damaged someone within a couple of ticks of the shot.
        hits = pl.DataFrame()
        if not match.damages.is_empty():
            dm = (match.damages.filter(is_aim_weapon())
                  .select(pl.col("attacker_id").alias("player_id"), pl.col("tick").cast(pl.Int64).alias("_dt"),
                          "victim_id"))
            hits = (s.select("_sid", "player_id", "tick")
                    .join(dm, on="player_id")
                    .filter((pl.col("_dt") - pl.col("tick")).abs() <= hit_window_ticks)
                    .sort("_sid", (pl.col("_dt") - pl.col("tick")).abs())
                    .unique("_sid", keep="first").select("_sid", "victim_id", "_dt"))
        kills = (match.kills.select(pl.col("victim_id"), pl.col("tick").cast(pl.Int64).alias("_kt"))
                 if not match.kills.is_empty() else pl.DataFrame(schema={"victim_id": pl.String, "_kt": pl.Int64}))
        a = np.column_stack([s["x"].to_numpy(), s["y"].to_numpy(), s["z"].to_numpy() + stand]).astype(np.float64)
        d = view_vector(s["pitch"].to_numpy().astype(np.float64), s["yaw"].to_numpy().astype(np.float64))
        out = s.select("_sid", "round_num", "tick", pl.col("player_id").alias("shooter_id")).with_columns(
            pl.Series("x0", a[:, 0]), pl.Series("y0", a[:, 1]), pl.Series("z0", a[:, 2]))
        if not hits.is_empty():
            out = out.join(hits, on="_sid", how="left")
            hv = _victim_at(match, out.filter(pl.col("victim_id").is_not_null()).select("_sid", "tick", "victim_id"), chest)
            out = out.join(hv.select("_sid", "vx", "vy", "vz"), on="_sid", how="left")
            dead = out.join(kills, left_on=["victim_id", "_dt"], right_on=["victim_id", "_kt"], how="semi")["_sid"]
        else:
            out = out.with_columns(pl.lit(None, pl.String).alias("victim_id"), pl.lit(None, pl.Float64).alias("vx"),
                                   pl.lit(None, pl.Float64).alias("vy"), pl.lit(None, pl.Float64).alias("vz"))
            dead = pl.Series([], dtype=pl.UInt32)
        out = out.sort("_sid")
        miss = out["vx"].is_null().to_numpy()
        end = np.column_stack([out["vx"].fill_null(0).to_numpy(), out["vy"].fill_null(0).to_numpy(),
                               out["vz"].fill_null(0).to_numpy()]).astype(np.float64)
        if miss.any():
            end[miss] = _ray_ends(a[miss], d[miss], visibility, max_range)
        kind = np.where(miss, "miss", "hit").astype(object)
        kind[np.isin(out["_sid"].to_numpy(), dead.to_numpy())] = "kill"
        return out.select("round_num", "tick", "shooter_id").with_columns(
            pl.Series("x0", a[:, 0]), pl.Series("y0", a[:, 1]), pl.Series("z0", a[:, 2]),
            pl.Series("x1", end[:, 0]), pl.Series("y1", end[:, 1]), pl.Series("z1", end[:, 2]),
            out["victim_id"], pl.Series("kind", kind.tolist(), dtype=pl.String)).select(COLUMNS)

    # Kills only: killer's eye to victim's chest at the moment of the kill.
    k = match.kills.filter(pl.col("attacker_id").is_not_null() & pl.col("victim_id").is_not_null()
                           & (pl.col("attacker_id") != pl.col("victim_id")))
    need = {"attacker_x", "attacker_y", "victim_x", "victim_y"}
    if k.is_empty() or not need <= set(k.columns):
        return _empty()
    k = k.filter(pl.col("attacker_x").is_not_null() & pl.col("victim_x").is_not_null())
    az = pl.col("attacker_z") if "attacker_z" in k.columns else pl.lit(0.0)
    vz = pl.col("victim_z") if "victim_z" in k.columns else pl.lit(0.0)
    return k.select(
        "round_num", pl.col("tick").cast(pl.Int64), pl.col("attacker_id").alias("shooter_id"),
        pl.col("attacker_x").cast(pl.Float64).alias("x0"), pl.col("attacker_y").cast(pl.Float64).alias("y0"),
        (az.cast(pl.Float64).fill_null(0) + stand).alias("z0"),
        pl.col("victim_x").cast(pl.Float64).alias("x1"), pl.col("victim_y").cast(pl.Float64).alias("y1"),
        (vz.cast(pl.Float64).fill_null(0) + chest).alias("z1"),
        "victim_id", pl.lit("kill").alias("kind")).select(COLUMNS)
