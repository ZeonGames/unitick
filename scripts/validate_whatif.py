"""Check the what-if engine on held-out pro matches.

For random fight moments (an enemy within 1200 units) from each pro match, predict whether
that player was alive 5 s later from the play they actually made, using only the other
matches in the library. Lower Brier score and log loss are better.

    python scripts/validate_whatif.py                 # about 2 minutes
    python scripts/validate_whatif.py --per-map 600   # the numbers in docs/whatif.md
"""

from __future__ import annotations

import argparse

import numpy as np
import polars as pl

from unitick.analytics import situations as S
from unitick.analytics import whatif as W


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-map", type=int, default=250, help="held-out moments per map")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    lib = S.default_library()
    rng = np.random.default_rng(args.seed)
    shuffle = np.random.default_rng(args.seed + 1)  # separate stream: the control never changes the sample
    rows = lib.snaps.filter(pl.col("pro") & (pl.col("n_enemy") >= 1) & (pl.col("t_live") > 5))
    y, p_engine, p_raw, p_base, p_shuffled = [], [], [], [], []
    for mp in lib.maps():
        base = W.fight_base(lib, mp)
        cand = rows.filter(pl.col("map") == mp)
        if cand.is_empty():
            continue
        cand = cand.sample(min(4 * args.per_map, cand.height), seed=int(rng.integers(1 << 30)))
        used = 0
        for q in cand.iter_rows(named=True):
            other = {"attack": "defense", "defense": "attack"}[q["side"]]
            foes = lib.bodies(mp, q["match_key"], q["round_num"], q["tick"], other)
            if len(foes) == 0 or np.hypot(foes[:, 0] - q["x"], foes[:, 1] - q["y"]).min() > W.FIGHT_RANGE:
                continue
            own = lib.bodies(mp, q["match_key"], q["round_num"], q["tick"], q["side"])
            own = own[~((np.abs(own[:, 0] - q["x"]) < 1e-3) & (np.abs(own[:, 1] - q["y"]) < 1e-3))]
            you = S.move_kind(q["x"], q["y"], q["fx"], q["fy"], foes)
            h = lib.search(map_name=mp, side=q["side"], pos=(q["x"], q["y"]), enemies=foes, mates=own,
                           t_live=q["t_live"], planted=q["planted"], exclude_match=q["match_key"],
                           k=W.K, prefilter=W.PREFILTER, pro_only=True)
            if h.height < W.MIN_N:
                continue
            moves = np.array([S.move_kind(r["x"], r["y"], r["fx"], r["fy"],
                                          lib.bodies(mp, r["match_key"], r["round_num"], r["tick"], other))
                              for r in h.iter_rows(named=True)])
            died = h["died5"].to_numpy().astype(float)
            kills = (h["kills5"].fill_null(0).to_numpy() > 0).astype(float)
            opts = {o["move"]: o for o in W.estimate(died, moves, h["score"].to_numpy().astype(float), kills, base)}
            sel = moves == you
            y.append(0.0 if q["died5"] else 1.0)
            p_engine.append(opts[you]["rate"])
            p_shuffled.append(opts[W.OPTIONS[int(shuffle.integers(len(W.OPTIONS)))]]["rate"])  # control: wrong play
            p_raw.append(1 - died[sel].mean() if sel.any() else 1 - died.mean())
            p_base.append(base)
            used += 1
            if used >= args.per_map:
                break
        print(f"{mp}: {used} held-out moments, fight survival base {base:.3f}", flush=True)

    y = np.array(y)

    def score(p):
        p = np.clip(np.array(p, float), 1e-3, 1 - 1e-3)
        return np.mean((p - y) ** 2), -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

    print(f"\n{len(y)} moments, {100 * y.mean():.1f}% survived the next 5 s")
    for name, p in (("what-if engine", p_engine), ("same, with a random play", p_shuffled),
                    ("raw counts of the same play", p_raw), ("map base rate", p_base)):
        b, ll = score(p)
        print(f"  {name:28s} Brier {b:.4f}  log loss {ll:.4f}")
    p = np.array(p_engine)
    print("\ncalibration (predicted survival -> actual):")
    for lo, hi in ((0, .7), (.7, .8), (.8, .9), (.9, .95), (.95, 1.01)):
        s = (p >= lo) & (p < hi)
        if s.any():
            print(f"  {lo:.2f}-{min(hi, 1):.2f}: predicted {p[s].mean():.2f}, actual {y[s].mean():.2f}  ({s.sum()} moments)")


if __name__ == "__main__":
    main()
