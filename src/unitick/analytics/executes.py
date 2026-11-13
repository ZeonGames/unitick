"""StratSketch: isolate site executes, fingerprint them, and group them into plays.

An *execute* is the run-up to an objective plant (CS2 bomb, Valorant spike): the
attacking side's movement, utility and fights in the ``window_s`` seconds before
the plant, never crossing the round's live start.
"""

from __future__ import annotations

from itertools import permutations

import numpy as np
import polars as pl

from ..match import Match
from ..schema import ATTACK


def plants(match: Match) -> pl.DataFrame:
    """First completed plant of every round."""
    o = match.objectives.filter(pl.col("event") == "plant")
    return (o.sort("tick").group_by("round_num", maintain_order=True).first()
            .select("round_num", pl.col("tick").alias("plant_tick"), "site",
                    pl.col("player_name").alias("planter"), "x", "y"))


def _window(match: Match, window_s: float) -> pl.DataFrame:
    p = plants(match).join(match.rounds.select("round_num", "live_tick", "start_tick", "winner_side"),
                           on="round_num", how="left")
    return p.with_columns(
        pl.max_horizontal(pl.col("plant_tick") - match.ticks(window_s),
                          pl.coalesce("live_tick", "start_tick")).alias("window_start"))


def execute_paths(match: Match, round_num: int | None = None, *, window_s: float = 20.0,
                  side: str = ATTACK) -> pl.DataFrame:
    """Frames of ``side`` during each execute window (optionally one round).

    Unlike a global tick filter, the window is computed per round and clipped at the
    round's live start, so paths never bleed in from the previous round.
    """
    w = _window(match, window_s)
    if round_num is not None:
        w = w.filter(pl.col("round_num") == round_num)
    f = match.frames.filter((pl.col("side") == side) & pl.col("is_alive").fill_null(True))
    return (f.join(w.select("round_num", "window_start", "plant_tick", "site"), on="round_num")
            .filter(pl.col("tick").is_between(pl.col("window_start"), pl.col("plant_tick")))
            .with_columns(((pl.col("tick") - pl.col("plant_tick")) / match.tick_rate)
                          .cast(pl.Float32).alias("t_rel_s"))
            .select("round_num", "site", "tick", "t_rel_s", "player_id", "player_name", "side",
                    "x", "y", "z", "yaw"))


def summary(match: Match, *, window_s: float = 20.0, side: str = ATTACK) -> pl.DataFrame:
    """One row per executed round: timing, utility, fights, and outcome."""
    w = _window(match, window_s)
    if w.is_empty():
        return pl.DataFrame()
    u = (match.utility.join(w.select("round_num", "window_start", "plant_tick"), on="round_num")
         .filter((pl.col("side") == side)
                 & pl.col("start_tick").is_between(pl.col("window_start"), pl.col("plant_tick")))
         .group_by("round_num").agg(
             *[(pl.col("kind") == k).sum().alias(f"{k}s") for k in ("smoke", "flash", "fire", "he")]))
    k = (match.kills.join(w.select("round_num", "window_start", "plant_tick"), on="round_num")
         .filter(pl.col("tick").is_between(pl.col("window_start"), pl.col("plant_tick")))
         .group_by("round_num").agg(
             (pl.col("attacker_side") == side).sum().alias("entry_kills"),
             (pl.col("victim_side") == side).sum().alias("entry_deaths")))
    alive = (match.frames.join(w.select("round_num", "plant_tick"), on="round_num")
             .filter(pl.col("side") == side)
             .with_columns((pl.col("tick") - pl.col("plant_tick")).abs().alias("_d"))
             .sort("_d").group_by(["round_num", "player_id"]).first()
             .group_by("round_num").agg(pl.col("is_alive").sum().alias("alive_at_plant")))
    out = (w.join(u, on="round_num", how="left").join(k, on="round_num", how="left")
           .join(alive, on="round_num", how="left").sort("round_num"))
    counts = ["smokes", "flashs", "fires", "hes", "entry_kills", "entry_deaths"]
    return out.with_columns(
        pl.col(counts).fill_null(0),
        ((pl.col("plant_tick") - pl.coalesce("live_tick", "start_tick")) / match.tick_rate)
        .round(1).alias("plant_time_s"),
        (pl.col("winner_side") == side).alias("won"),
    ).rename({"flashs": "flashes"}).select(
        "round_num", "site", "planter", "plant_time_s", "alive_at_plant", "smokes", "flashes",
        "fires", "hes", "entry_kills", "entry_deaths", "won")


# ----------------------------------------------------------------- fingerprints
def signatures(match: Match, *, offsets_s: tuple[float, ...] = (15.0, 10.0, 5.0, 0.0),
               side: str = ATTACK) -> dict[int, dict]:
    """Per round: {site, points: [array (n_alive, 2) of unit coords per offset]}."""
    t = match.transform
    w = _window(match, max(offsets_s) + 1)
    f = match.frames.filter((pl.col("side") == side) & pl.col("is_alive").fill_null(True))
    out: dict[int, dict] = {}
    tol = match.ticks(0.5)
    for row in w.iter_rows(named=True):
        rf = f.filter(pl.col("round_num") == row["round_num"])
        pts = []
        for off in offsets_s:
            target = row["plant_tick"] - match.ticks(off)
            snap = (rf.with_columns((pl.col("tick") - target).abs().alias("_d"))
                    .filter(pl.col("_d") <= tol).sort("_d").group_by("player_id").first())
            ux, uy = t.to_unit(snap["x"].to_numpy(), snap["y"].to_numpy())
            pts.append(np.column_stack([ux, uy]) if len(ux) else np.zeros((0, 2)))
        out[row["round_num"]] = {"site": row["site"], "points": pts}
    return out


def _set_distance(a: np.ndarray, b: np.ndarray, penalty: float) -> float:
    """Mean distance under the best one-to-one matching; unmatched points cost ``penalty``."""
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return penalty if len(a) else 0.0
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=-1)  # (na, nb)
    n_extra = len(a) - len(b)
    if len(a) <= 7:
        best = min(sum(d[p[i], i] for i in range(len(b))) for p in permutations(range(len(a)), len(b)))
    else:  # greedy for unusually large teams
        best, used = 0.0, set()
        for i in np.argsort(d.min(axis=0)):
            j = min((r for r in range(len(a)) if r not in used), key=lambda r: d[r, i])
            used.add(j)
            best += d[j, i]
    return float((best + n_extra * penalty) / len(a))


def distance(sig_a: dict, sig_b: dict, *, penalty: float = 0.15) -> float:
    """Permutation-invariant distance between two executes in unit-map coordinates."""
    ds = [_set_distance(a, b, penalty) for a, b in zip(sig_a["points"], sig_b["points"], strict=True)]
    return float(np.mean(ds)) if ds else float("inf")


def playbook(match: Match, *, threshold: float = 0.06, **kw) -> pl.DataFrame:
    """Cluster executes into plays (average-linkage, per site).

    ``threshold`` is in unit-map distance (0.06 ~ 60 px on a 1024 radar). Returns one
    row per executed round with ``play_id`` and whether it is the play's medoid.
    """
    sigs = signatures(match, **kw)
    rows = []
    next_id = 0
    by_site: dict[str, list[int]] = {}
    for rn, s in sigs.items():
        by_site.setdefault(s["site"] or "?", []).append(rn)
    for site, rns in sorted(by_site.items()):
        n = len(rns)
        dm = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                dm[i, j] = dm[j, i] = distance(sigs[rns[i]], sigs[rns[j]])
        clusters = [[i] for i in range(n)]
        while len(clusters) > 1:
            best, pair = np.inf, None
            for a in range(len(clusters)):
                for b in range(a + 1, len(clusters)):
                    dd = dm[np.ix_(clusters[a], clusters[b])].mean()
                    if dd < best:
                        best, pair = dd, (a, b)
            if best > threshold:
                break
            a, b = pair
            clusters[a] += clusters.pop(b)
        for c in sorted(clusters, key=len, reverse=True):
            medoid = c[int(np.argmin(dm[np.ix_(c, c)].sum(axis=1)))]
            spread = float(dm[np.ix_(c, c)].mean()) if len(c) > 1 else 0.0
            for i in c:
                rows.append({"round_num": rns[i], "site": site, "play_id": next_id,
                             "play_size": len(c), "is_medoid": i == medoid,
                             "spread": round(spread, 4)})
            next_id += 1
    if not rows:
        return pl.DataFrame(schema={"round_num": pl.Int32, "site": pl.String, "play_id": pl.Int64,
                                    "play_size": pl.Int64, "is_medoid": pl.Boolean,
                                    "spread": pl.Float64})
    return pl.DataFrame(rows).with_columns(pl.col("round_num").cast(pl.Int32)).sort("round_num")
