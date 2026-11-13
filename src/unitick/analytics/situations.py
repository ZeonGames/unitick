"""Situation search: find the moments in other matches that look most like this one.

A *situation* is the state of a round at one instant, seen from one player:
where they stand, which side they are on, where their living teammates and
enemies are, whether the bomb is down, and how far into the round it is.

``snapshots(match)`` turns a match into one row per (instant, living player),
with what happened to that player next (survived the next 5 s? got a kill? where
did they move?). Stack snapshots from many matches and you have a library.
``search(library, query)`` ranks library rows by similarity:

    score = spot distance (you vs them)
          + order-invariant distance between the two enemy sets
          + order-invariant distance between the two teammate sets
          + round-clock difference

Order-invariant means player identity does not matter, only where bodies are:
the same situation with the roles swapped is still the same situation.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import permutations
from pathlib import Path

import numpy as np
import polars as pl

from ..match import Match

SNAP_S = 0.5
OUTCOME_S = 5.0
MOVE_S = 3.0


def snapshots(match: Match, *, every_s: float = SNAP_S) -> pl.DataFrame:
    """One row per (snapshot instant, living player) during the live phase."""
    f = match.frames.filter(pl.col("is_alive").fill_null(True) & pl.col("side").is_not_null())
    if f.is_empty():
        return pl.DataFrame()
    r = match.rounds.select("round_num", pl.coalesce("live_tick", "start_tick").alias("_live"), "end_tick")
    step = max(1, match.ticks(every_s))
    f = (f.join(r, on="round_num").filter(pl.col("tick").is_between(pl.col("_live"), pl.col("end_tick")))
         .with_columns(((pl.col("tick") - pl.col("_live")) // step).alias("_slot")))
    keep = f.group_by("round_num", "_slot").agg(pl.col("tick").min().alias("tick"))
    s = f.join(keep, on=["round_num", "_slot", "tick"]).with_columns(
        ((pl.col("tick") - pl.col("_live")) / match.tick_rate).cast(pl.Float32).alias("t_live"))

    plants = (match.objectives.filter(pl.col("event") == "plant").group_by("round_num")
              .agg(pl.col("tick").min().alias("_plant")))
    s = s.join(plants, on="round_num", how="left").with_columns(
        (pl.col("_plant").is_not_null() & (pl.col("tick") >= pl.col("_plant"))).alias("planted"))

    # Outcomes over the next few seconds, per player.
    k = match.kills
    w = match.ticks(OUTCOME_S)
    died = (s.select("round_num", "tick", "player_id")
            .join(k.select("round_num", pl.col("victim_id").alias("player_id"), pl.col("tick").alias("_kt")),
                  on=["round_num", "player_id"])
            .filter(pl.col("_kt").is_between(pl.col("tick"), pl.col("tick") + w))
            .group_by("round_num", "tick", "player_id").agg(pl.lit(True).alias("died5")))
    kil = (s.select("round_num", "tick", "player_id")
           .join(k.filter(pl.col("attacker_side") != pl.col("victim_side"))
                 .select("round_num", pl.col("attacker_id").alias("player_id"), pl.col("tick").alias("_kt")),
                 on=["round_num", "player_id"])
           .filter(pl.col("_kt").is_between(pl.col("tick"), pl.col("tick") + w))
           .group_by("round_num", "tick", "player_id").agg(pl.len().alias("kills5")))
    s = (s.join(died, on=["round_num", "tick", "player_id"], how="left")
         .join(kil, on=["round_num", "tick", "player_id"], how="left")
         .with_columns(pl.col("died5").fill_null(False), pl.col("kills5").fill_null(0)))

    # Where they were MOVE_S later (or where they died).
    fut = (match.frames.select(pl.col("tick").cast(pl.Int64).alias("_ft"), "player_id",
                               pl.col("x").alias("fx"), pl.col("y").alias("fy")).sort("_ft"))
    s = (s.with_columns((pl.col("tick") + match.ticks(MOVE_S)).cast(pl.Int64).alias("_q")).sort("_q")
         .join_asof(fut, left_on="_q", right_on="_ft", by="player_id", strategy="backward",
                    check_sortedness=False)
         .drop("_q", "_ft"))

    alive = s.group_by("round_num", "tick", "side").agg(pl.len().alias("_n"))
    s = s.join(alive.rename({"side": "_sd"}), left_on=["round_num", "tick", "side"],
               right_on=["round_num", "tick", "_sd"], how="left").rename({"_n": "n_own"})
    s = s.join(alive.with_columns(pl.when(pl.col("side") == "attack").then(pl.lit("defense"))
                                  .otherwise(pl.lit("attack")).alias("_sd")).drop("side"),
               left_on=["round_num", "tick", "side"], right_on=["round_num", "tick", "_sd"], how="left") \
        .rename({"_n": "n_enemy"}).with_columns(pl.col("n_enemy").fill_null(0))

    info = match.meta
    label = info.get("result") or " vs ".join(list((info.get("teams") or {}).keys())[:2]) or "match"
    return s.select(
        pl.lit(_match_key(match)).alias("match_key"), pl.lit(match.map_name).alias("map"),
        pl.lit(label).alias("match_label"), pl.lit(info.get("event") or "").alias("event"),
        pl.lit(bool(info.get("hltv_match") or info.get("event"))).alias("pro"),
        "round_num", "tick", "t_live", "planted", "player_id", "player_name", "side",
        pl.col("x").cast(pl.Float32), pl.col("y").cast(pl.Float32), pl.col("yaw").cast(pl.Float32),
        "n_own", "n_enemy", "died5", "kills5", pl.col("fx").cast(pl.Float32), pl.col("fy").cast(pl.Float32))


def _match_key(match: Match) -> str:
    m = match.meta
    # library_key: the id this match already has inside a library parquet, so a match is never
    # compared against its own copy.
    return str(m.get("library_key") or m.get("hltv_match") or m.get("match_id") or
               f"{m.get('game')}:{m.get('map_name')}:{match.rounds.height}:{match.kills.height}")


# --------------------------------------------------------------------- library
class Library:
    """Snapshots from many matches, searchable by situation."""

    def __init__(self, snaps: pl.DataFrame):
        self.snaps = snaps
        self._by_map: dict[str, dict] = {}

    @classmethod
    def from_matches(cls, matches) -> Library:
        parts = [snapshots(m) for m in matches]
        parts = [p for p in parts if not p.is_empty()]
        return cls(pl.concat(parts) if parts else pl.DataFrame())

    @classmethod
    def from_dirs(cls, *dirs: str | Path) -> Library:
        ms = []
        for d in dirs:
            d = Path(d)
            if not d.exists():
                continue
            for meta in sorted(d.glob("*/meta.json")):
                try:
                    ms.append(Match.load(meta.parent))
                except Exception:  # noqa: BLE001, S112 - skip unreadable entries
                    continue
        return cls.from_matches(ms)

    def save(self, path: str | Path) -> Path:
        self.snaps.write_parquet(path, compression="zstd")
        return Path(path)

    @classmethod
    def load(cls, path: str | Path) -> Library:
        return cls(pl.read_parquet(path))

    def maps(self) -> list[str]:
        return [] if self.snaps.is_empty() else sorted(self.snaps["map"].drop_nulls().unique().to_list())

    def _prep(self, map_name: str) -> dict:
        if map_name in self._by_map:
            return self._by_map[map_name]
        s = self.snaps.filter(pl.col("map") == map_name)
        # Group the living players of each (match, round, tick) by side for set matching.
        ctx = (s.group_by("match_key", "round_num", "tick", "side")
               .agg(pl.col("x"), pl.col("y")))
        groups: dict[tuple, np.ndarray] = {}
        for mk, rn, tk, sd, xs, ys in ctx.iter_rows():
            groups[(mk, rn, tk, sd)] = np.column_stack([xs, ys]).astype(np.float64)
        d = {"rows": s, "xy": s.select("x", "y").to_numpy().astype(np.float64), "groups": groups,
             # column arrays, converted once per map instead of on every search
             "cols": {c: s[c].to_numpy() for c in ("side", "planted", "n_enemy", "n_own", "match_key",
                                                   "round_num", "tick", "player_id", "t_live")},
             "pro": s["pro"].fill_null(False).to_numpy()}
        self._by_map[map_name] = d
        return d

    def search(self, *, map_name: str, side: str, pos, enemies: np.ndarray, mates: np.ndarray,
               t_live: float, planted: bool, exclude_match: str | None = None, k: int = 5,
               pro_only: bool = False, radius: float = 400.0, prefilter: int = 400) -> pl.DataFrame:
        """Top-k most similar situations (one per match-round), best first."""
        if self.snaps.is_empty():
            return pl.DataFrame()
        d = self._prep(map_name)
        rows = d["rows"]
        if rows.is_empty():
            return pl.DataFrame()
        cols = d["cols"]
        dist = np.hypot(d["xy"][:, 0] - pos[0], d["xy"][:, 1] - pos[1])
        mask = ((cols["side"] == side) & (cols["planted"] == planted)
                & (dist < radius)
                & (np.abs(cols["n_enemy"] - len(enemies)) <= 1)
                & (np.abs(cols["n_own"] - (len(mates) + 1)) <= 1))
        if exclude_match:
            mask &= cols["match_key"] != exclude_match
        if pro_only:
            mask &= d["pro"]
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return pl.DataFrame()
        idx = idx[np.argsort(dist[idx])[:prefilter]]
        other = {"attack": "defense", "defense": "attack"}[side]
        scored = []
        mk_col, rn_col, tk_col, pid_col = cols["match_key"], cols["round_num"], cols["tick"], cols["player_id"]
        tl_col, x_col, y_col = cols["t_live"], d["xy"][:, 0], d["xy"][:, 1]
        for i in idx:
            key = (mk_col[i], rn_col[i], tk_col[i])
            en = d["groups"].get((*key, other), np.zeros((0, 2)))
            own = d["groups"].get((*key, side), np.zeros((0, 2)))
            own = own[~((np.abs(own[:, 0] - x_col[i]) < 1e-3) & (np.abs(own[:, 1] - y_col[i]) < 1e-3))]
            s_en = _set_distance(enemies, en, penalty=900.0)
            s_own = _set_distance(mates, own, penalty=900.0)
            score = dist[i] / radius + 0.8 * s_en / 600.0 + 0.35 * s_own / 600.0 \
                + 0.25 * min(abs(tl_col[i] - t_live), 40.0) / 20.0
            scored.append((score, i))
        scored.sort()
        best, seen = [], set()
        for score, i in scored:
            key = (mk_col[i], rn_col[i])
            if key in seen:
                continue
            seen.add(key)
            best.append((i, score))
            if len(best) == k:
                break
        out = rows[[i for i, _ in best]].with_columns(pl.Series("score", [s for _, s in best]))
        return out.with_columns(pid=pl.Series(pid_col[[i for i, _ in best]]))

    def bodies(self, map_name: str, match_key: str, round_num: int, tick: int, side: str) -> np.ndarray:
        """x, y of every living player on ``side`` at that instant (fast; no DataFrame)."""
        return self._prep(map_name)["groups"].get((match_key, round_num, tick, side), np.zeros((0, 2)))

    def context(self, map_name: str, match_key: str, round_num: int, tick: int) -> pl.DataFrame:
        """Every living player at that instant (for drawing the matched situation)."""
        rows = self._prep(map_name)["rows"]
        return rows.filter((pl.col("match_key") == match_key) & (pl.col("round_num") == round_num)
                           & (pl.col("tick") == tick))


@lru_cache(maxsize=64)
def _perms(n: int, k: int) -> np.ndarray:
    """Every ordered pick of k rows out of n, as an index array (cached; n <= 6)."""
    return np.array(list(permutations(range(n), k)), dtype=np.intp).reshape(-1, k)


def _set_distance(a: np.ndarray, b: np.ndarray, penalty: float) -> float:
    """Mean distance under the best one-to-one matching; unmatched bodies cost ``penalty``."""
    if len(a) < len(b):
        a, b = b, a
    if len(a) == 0:
        return 0.0
    if len(b) == 0:
        return penalty
    d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    if len(a) <= 6:
        best = float(d[_perms(len(a), len(b)), np.arange(len(b))].sum(axis=1).min())
    else:
        best = float(d.min(axis=0).sum())
    return float((best + (len(a) - len(b)) * penalty) / len(a))


def move_kind(x, y, fx, fy, enemies: np.ndarray, *, still: float = 120.0) -> str:
    """Classify what a player did next: held, pushed toward the enemy, or fell back."""
    if fx is None or fy is None or np.isnan(fx):
        return "held"
    dx, dy = fx - x, fy - y
    if np.hypot(dx, dy) < still:
        return "held"
    if len(enemies) == 0:
        return "moved"
    c = enemies.mean(axis=0)
    toward = (dx * (c[0] - x) + dy * (c[1] - y)) / (np.hypot(c[0] - x, c[1] - y) + 1e-6)
    return "pushed" if toward > 0 else "fell back"


# --------------------------------------------------------------- default library
_DEFAULT: Library | None = None


def library_dirs() -> list[Path]:
    """Where libraries live: the repo's samples/library and ~/.unitick/library."""
    from ..render.images import cache_dir
    out = [cache_dir() / "library"]
    for cand in (Path(__file__).resolve().parents[3] / "samples", Path.cwd() / "samples"):
        if (cand / "library").is_dir():
            out.insert(0, cand / "library")
            break
    return out


def default_library(*, refresh: bool = False) -> Library:
    """Every library .parquet found in :func:`library_dirs`, plus the sample matches."""
    global _DEFAULT
    if _DEFAULT is not None and not refresh:
        return _DEFAULT
    parts = []
    for d in library_dirs():
        parts += [pl.read_parquet(f) for f in sorted(d.glob("*.parquet"))]
        samples = d.parent / "cs2"
        if d.name == "library" and samples.is_dir():
            s = Library.from_dirs(samples).snaps
            if not s.is_empty():
                parts.append(s)
    parts = [p for p in parts if not p.is_empty()]
    snaps = pl.concat(parts, how="vertical_relaxed").unique(["match_key", "round_num", "tick", "player_id"],
                                                            maintain_order=True) \
        if parts else pl.DataFrame()
    _DEFAULT = Library(snaps)
    return _DEFAULT


def add_to_library(match: Match, dest: str | Path | None = None) -> Path:
    """Save one match's snapshots into the user's library (~/.unitick/library)."""
    from ..render.images import cache_dir
    d = Path(dest) if dest else cache_dir() / "library"
    d.mkdir(parents=True, exist_ok=True)
    s = snapshots(match)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in _match_key(match))
    out = d / f"{safe}.parquet"
    s.write_parquet(out, compression="zstd")
    return out
