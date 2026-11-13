"""What-if: your odds of surviving with every other play.

For one moment of one player (normally 1.5 s before a death), take the most similar
moments in the library, the same way situation search does: same side, same spot,
a similar enemy and teammate setup, similar time, same bomb state. Then sort those
players by what they did next and count how many were still alive five seconds later:

    held       stayed within 120 units of the spot
    pushed     moved toward the enemies
    fell back  moved away from them

The result is not a simulation: every number comes from real players who were in that
situation and made that choice. Closer matches count more, a play few players chose is
pulled toward the spot's overall odds, and the odds are anchored to how often players
on that map survive a fight at all. Each play shows how many players it rests on and
a likely range that is wide when that number is small.
"""

from __future__ import annotations

import math

import numpy as np

from . import situations as S

OPTIONS = ("held", "pushed", "fell back")
K = 60             # most similar moments used, at most one per round
PREFILTER = 1500   # candidates scored before picking the K best
TAU = 0.5          # similarity weight: a moment 0.5 less similar than the best one counts e^-1 as much
PRIOR = 10.0       # a rarely chosen play is pulled toward the spot's overall odds by this much weight
ANCHOR = 0.7       # final odds = 70% this spot + 30% the map's typical survival in a fight
MIN_N = 8          # a play needs this many players before it gets a percentage
CLEAR_GAIN = 0.10  # another play is clearly better at 10+ points more, with its low estimate above yours
Z = 1.2816         # 80% interval
FIGHT_RANGE = 1200.0
# The constants above were picked on held-out pro matches (see docs/whatif.md and
# scripts/validate_whatif.py): predicting whether a player survives the next 5 s, this
# estimator scores Brier 0.083 against 0.090 for the map's base rate and 0.098 for raw counts.


def wilson(k: float, n: float, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for k successes out of n (n may be an effective, non-integer count)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(max(p * (1 - p), 0.0) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


_BASE: dict[tuple[int, str], float] = {}


def fight_base(lib: S.Library, map_name: str, *, sample: int = 20000) -> float:
    """Share of pro players on this map still alive 5 s after a moment with an enemy within range."""
    ck = (id(lib), map_name)
    if ck in _BASE:
        return _BASE[ck]
    d = lib._prep(map_name)
    rows = d["rows"]
    keep = np.flatnonzero(d["pro"] & (d["cols"]["n_enemy"] >= 1) & (d["cols"]["t_live"] > 5))
    if keep.size == 0:
        keep = np.flatnonzero(d["cols"]["n_enemy"] >= 1)
    if keep.size > sample:
        keep = np.sort(np.random.default_rng(0).choice(keep, sample, replace=False))
    cols, xy, died = d["cols"], d["xy"], rows["died5"].to_numpy()
    other = {"attack": "defense", "defense": "attack"}
    hit, total = 0, 0
    for i in keep:
        foes = d["groups"].get((cols["match_key"][i], cols["round_num"][i], cols["tick"][i],
                                other.get(cols["side"][i], "")), None)
        if foes is None or len(foes) == 0:
            continue
        if np.hypot(foes[:, 0] - xy[i, 0], foes[:, 1] - xy[i, 1]).min() > FIGHT_RANGE:
            continue
        total += 1
        hit += int(not died[i])
    _BASE[ck] = hit / total if total >= 200 else 0.9
    return _BASE[ck]


def estimate(died: np.ndarray, moves: np.ndarray, scores: np.ndarray, kills: np.ndarray,
             base: float) -> list[dict]:
    """Survival odds per play from ranked neighbours (best match first). Pure function."""
    w = np.exp(-(scores - scores[0]) / TAU)
    pool_d = float(np.sum(w * died) / np.sum(w))
    out = []
    for mv in OPTIONS:
        sel = moves == mv
        n = int(sel.sum())
        ws = float(w[sel].sum())
        raw_d = (float(np.sum(w[sel] * died[sel])) + PRIOR * pool_d) / (ws + PRIOR)
        rate = ANCHOR * (1 - raw_d) + (1 - ANCHOR) * base
        n_eff = (ws * ws / float(np.sum(w[sel] ** 2))) if n else 0.0
        lo, hi = wilson((1 - raw_d) * (n_eff + PRIOR), n_eff + PRIOR)
        lo, hi = ANCHOR * lo + (1 - ANCHOR) * base, ANCHOR * hi + (1 - ANCHOR) * base
        kr = float(np.sum(w[sel] * kills[sel]) / ws) if n else None
        out.append({"move": mv, "n": n, "rate": _r(rate), "lo": _r(lo), "hi": _r(hi),
                    "kill_rate": _r(kr), "enough": n >= MIN_N and n_eff >= 3})
    return out


def judge(options: list[dict], you: str | None) -> tuple[str | None, str | None, float | None]:
    """Best play, verdict on the player's own play, and the gap between them."""
    rated = [o for o in options if o["enough"]]
    best = max(rated, key=lambda o: (o["rate"], o["n"])) if rated else None
    mine = next((o for o in options if o["move"] == you), None)
    if not best or not mine:
        return (best["move"] if best else None), None, None
    if not mine["enough"]:
        return best["move"], "rare", None  # what you did was too uncommon here to rate
    gain = _r(best["rate"] - mine["rate"])
    if best["move"] == you:
        return best["move"], "best", gain
    if gain >= CLEAR_GAIN and best["lo"] > mine["rate"]:
        return best["move"], "better", gain
    return best["move"], "close", gain


def odds(lib: S.Library, *, map_name: str, side: str, pos, enemies: np.ndarray, mates: np.ndarray,
         t_live: float, planted: bool, exclude_match: str | None = None, you: str | None = None,
         k: int = K) -> dict | None:
    """Survival odds per play for one situation, or None when the library has too little."""
    if lib is None or lib.snaps.is_empty() or len(enemies) == 0:
        return None
    q = {"map_name": map_name, "side": side, "pos": pos, "enemies": enemies, "mates": mates,
         "t_live": t_live, "planted": planted, "exclude_match": exclude_match, "k": k, "prefilter": PREFILTER}
    hits, pool = lib.search(**q, pro_only=True), "pro"
    if hits.height < 2 * MIN_N:
        wide = lib.search(**q)
        if wide.height > hits.height:
            hits, pool = wide, "all"
    if hits.height < MIN_N:
        return None

    other = {"attack": "defense", "defense": "attack"}[side]
    moves = np.array([S.move_kind(h["x"], h["y"], h["fx"], h["fy"],
                                  lib.bodies(map_name, h["match_key"], h["round_num"], h["tick"], other))
                      for h in hits.iter_rows(named=True)])
    died = hits["died5"].to_numpy().astype(float)
    kills = (hits["kills5"].fill_null(0).to_numpy() > 0).astype(float)
    options = estimate(died, moves, hits["score"].to_numpy().astype(float), kills, fight_base(lib, map_name))
    best, verdict, gain = judge(options, you)
    return {"n": int(hits.height), "pool": pool, "horizon_s": S.OUTCOME_S, "you": you,
            "options": options, "best": best, "gain": gain, "verdict": verdict}


def summary(results: list[dict | None]) -> dict:
    """How often another play had clearly better odds, over a set of what-ifs."""
    rated = [r for r in results if r and r.get("verdict") in ("best", "better", "close")]
    better = [r for r in rated if r["verdict"] == "better"]
    return {"rated": len(rated), "better": len(better), "best": sum(r["verdict"] == "best" for r in rated),
            "mean_gain": _r(float(np.mean([r["gain"] for r in better]))) if better else None,
            "min_n": MIN_N, "clear_gain": CLEAR_GAIN, "horizon_s": S.OUTCOME_S}


def _r(v, n: int = 3):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), n)
