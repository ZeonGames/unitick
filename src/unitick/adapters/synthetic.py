"""Deterministic synthetic matches with known ground truth.

Used by the test-suite and for trying unitick without any demo files. The script:

* Attack spawns west and alternates between two scripted executes: "A split"
  (rounds where ``r % 3 != 0``) and "B rush" (the rest).
* Defender ``d{j}`` holds the target site with a crosshair offset of exactly
  ``aim_error_deg[j]`` degrees of yaw from the first attacker's head.
* Each round: d0-site hits a0 and kills him, a1 trades d0 within 2 s, a2 plants,
  attack wins. So every round has exactly one trade.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from ..match import Match
from ..schema import ATTACK, DEFENSE

SITES = {"A": (1000.0, 1000.0), "B": (1000.0, -1000.0)}
SPAWN = {ATTACK: (-1500.0, 0.0), DEFENSE: (1500.0, 0.0)}
WAYPOINT = {"A": (0.0, 900.0), "B": (-200.0, -900.0)}


def _lerp(p0, p1, t):
    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)


def generate(n_rounds: int = 12, *, tick_rate: float = 64.0, frame_every: int = 4,
             aim_error_deg: tuple[float, ...] = (1.0, 3.0, 6.0, 10.0, 15.0),
             seed: int = 7) -> Match:
    rng = np.random.default_rng(seed)
    tr = tick_rate
    att = [f"a{i}" for i in range(5)]
    dfn = [f"d{i}" for i in range(5)]
    rounds, frames, kills, damages, shots, utility, objectives = [], [], [], [], [], [], []
    tick0 = 1000
    round_len_s = 50.0
    for r in range(1, n_rounds + 1):
        site = "A" if r % 3 else "B"
        start = tick0 + (r - 1) * int((round_len_s + 15) * tr)
        live = start + int(15 * tr)
        end = live + int(round_len_s * tr)
        plant_t = live + int(42 * tr)
        hold_t = live + int(30 * tr)       # a0 reaches his peek spot and stops
        fight_t = live + int(35 * tr)      # d0 opens fire on a0
        trade_t = fight_t + int(1.5 * tr)  # a1 kills d0
        sx, sy = SITES[site]
        jitter = rng.normal(0, 15, size=(5, 2))
        # Defender j stands at a fixed spot near the target site (index = role).
        def_pos = {f"d{j}": (sx + 200 + 60 * j, sy + (60 * j - 120)) for j in range(5)}
        peek = (sx - 600.0, sy)  # a0's peek spot
        dead: dict[str, int] = {}
        # a0 killed by the defender with the scripted aim error: d{(r-1) % 5}.
        shooter = f"d{(r - 1) % 5}"
        sp = def_pos[shooter]
        true_yaw = math.degrees(math.atan2(peek[1] - sp[1], peek[0] - sp[0]))
        aim_err = aim_error_deg[(r - 1) % 5]
        dead["a0"] = fight_t + 8
        dead[shooter] = trade_t
        for t in range(start, end + 1, frame_every):
            for i, pid in enumerate(att):
                if t < live:
                    pos = SPAWN[ATTACK]
                else:
                    u = min(1.0, (t - live) / (hold_t - live))
                    tgt = peek if pid == "a0" else (sx - 150 + 60 * i, sy + 40 * i - 80)
                    if u < 0.5:
                        pos = _lerp(SPAWN[ATTACK], WAYPOINT[site], u * 2)
                    else:
                        pos = _lerp(WAYPOINT[site], tgt, (u - 0.5) * 2)
                    if pid != "a0" and t > hold_t:
                        pos = _lerp(tgt, (sx, sy), min(1.0, (t - hold_t) / (plant_t - hold_t)))
                pos = (pos[0] + jitter[i, 0] * (pid != "a0"), pos[1] + jitter[i, 1] * (pid != "a0"))
                alive = t < dead.get(pid, 10**12)
                frames.append((t, r, pid, pid, ATTACK, pos[0], pos[1], 0.0, 0.0, 0.0, 0.0,
                               100 if alive else 0, alive))
            for j, pid in enumerate(dfn):
                pos = def_pos[pid]
                yaw = true_yaw + aim_err if pid == shooter else 180.0
                alive = t < dead.get(pid, 10**12)
                frames.append((t, r, pid, pid, DEFENSE, pos[0], pos[1], 0.0, 0.0, 0.0, yaw,
                               100 if alive else 0, alive))
        rounds.append((r, start, live, end, ATTACK, "objective"))
        # Fight 1: shooter -> a0 (two hits, kill). Shots start 4 ticks before the hit.
        for k, dt in enumerate((0, 8)):
            shots.append((fight_t - 4 + dt, r, shooter, shooter, DEFENSE, *sp, 0.0, 0.0,
                          true_yaw + aim_err, "ak47"))
            damages.append((fight_t + dt, r, shooter, shooter, DEFENSE, *sp, 0.0, "a0", "a0",
                            ATTACK, *peek, 0.0, "ak47", 50, "head" if k else "chest"))
        kills.append((fight_t + 8, r, shooter, shooter, DEFENSE, *sp, 0.0, "a0", "a0", ATTACK,
                      *peek, 0.0, None, None, None, "ak47", True))
        # Fight 2: a1 trades the shooter.
        a1 = (sx - 90.0, sy - 40.0)
        damages.append((trade_t, r, "a1", "a1", ATTACK, *a1, 0.0, shooter, shooter, DEFENSE,
                        *sp, 0.0, "m4a1", 100, "head"))
        kills.append((trade_t, r, "a1", "a1", ATTACK, *a1, 0.0, shooter, shooter, DEFENSE,
                      *sp, 0.0, "a2", "a2", ATTACK, "m4a1", True))
        # Utility: two smokes onto the site and a flash, thrown from the waypoint.
        wx, wy = WAYPOINT[site]
        for kind, dx, dy, dt in (("smoke", 250, 150, -9), ("smoke", 300, -150, -8), ("flash", 0, 0, -3)):
            st = plant_t + int(dt * tr)
            utility.append((st, st + (int(18 * tr) if kind == "smoke" else 0), r, "a3", "a3",
                            ATTACK, kind, sx + dx, sy + dy, 0.0, wx, wy, 0.0))
        objectives.append((plant_t - int(3 * tr), r, "plant_start", "a2", "a2", site, sx, sy, 0.0))
        objectives.append((plant_t, r, "plant", "a2", "a2", site, sx, sy, 0.0))
        objectives.append((end, r, "explode", None, None, site, sx, sy, 0.0))

    def df(rows, cols):
        return pl.DataFrame(rows, schema=cols, orient="row")

    actor = ["_id", "_name", "_side", "_x", "_y", "_z"]
    a = [f"attacker{c}" for c in actor]
    v = [f"victim{c}" for c in actor]
    return Match.build(
        {"game": "synthetic", "map_name": "synthetic_box", "tick_rate": tick_rate,
         "frame_every": frame_every, "source": "unitick.adapters.synthetic",
         "map_transform": {"name": "synthetic_box", "pos_x": -2048.0, "pos_y": 2048.0,
                           "scale": 4.0}},
        rounds=df(rounds, ["round_num", "start_tick", "live_tick", "end_tick", "winner_side", "reason"]),
        frames=df(frames, ["tick", "round_num", "player_id", "player_name", "side", "x", "y", "z",
                           "pitch", "velocity", "yaw", "health", "is_alive"]),
        kills=df(kills, ["tick", "round_num", *a, *v, "assister_id", "assister_name",
                         "assister_side", "weapon", "headshot"]),
        damages=df(damages, ["tick", "round_num", *a, *v, "weapon", "damage", "hitgroup"]),
        shots=df(shots, ["tick", "round_num", "player_id", "player_name", "side", "x", "y", "z",
                         "pitch", "yaw", "weapon"]),
        utility=df(utility, ["start_tick", "end_tick", "round_num", "player_id", "player_name",
                             "side", "kind", "x", "y", "z", "origin_x", "origin_y", "origin_z"]),
        objectives=df(objectives, ["tick", "round_num", "event", "player_id", "player_name",
                                   "site", "x", "y", "z"]),
    )
