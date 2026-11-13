"""Write a small export in vrfkit's documented layout (manifest + Parquet tables).

Scenario on Ascent, two rounds, 128 Hz:
  * team 0 (agents Jett..) spawns at (-6000, 0), team 1 at (+6000, 0) in round 1;
    sides swap for round 2 (switchTeams event between rounds).
  * attacker a0 flicks 30 deg onto defender d0 and kills him; d1 kills a1 while
    already on target (no flick).
  * round 1: attackers plant (TimedBomb actor at the site) and it detonates.
  * round 2: defenders eliminate all attackers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import polars as pl

JETT = "add6443a-41bd-e414-f6ad-e58d267f4e95"
SAGE = "569fdd95-4d10-43ab-ca70-79becc718b46"
AGENTS = [JETT, SAGE, "a3bfb853-43b2-7238-a4f1-ad90e9e46bcc", "7f94d92c-4234-0a36-9646-3a87eb8b5c89",
          "320b2a48-4d9b-a075-30f1-1f93a9b638fa"]  # Jett Sage Reyna Yoru Sova


def write(d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    subj = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(10)]
    char = [1000 + i for i in range(10)]
    players = [{"actor_net_guid": 500 + i, "subject": subj[i], "character_net_guid": char[i]} for i in range(10)]
    loadouts = {"playerLoadouts": [{"subject": subj[i], "characterId": AGENTS[i % 5]} for i in range(10)]}
    manifest = {"level_names_and_times": [{"name": "/Game/Maps/Ascent/Ascent", "time_ms": 0}],
                "game_specific_data": [json.dumps({"matchId": "x"}), json.dumps(loadouts)],
                "players": players}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    ms_per_tick = 1000 / 128
    r_starts = [0, 120_000]
    r_len = 100_000
    rows = []
    truth = {}
    for r, t0 in enumerate(r_starts):
        atk = [0, 1, 2, 3, 4] if r == 0 else [5, 6, 7, 8, 9]
        dfn = [5, 6, 7, 8, 9] if r == 0 else [0, 1, 2, 3, 4]
        a_spawn, d_spawn = (-6000.0, 0.0), (6000.0, 0.0)
        site = (4000.0, 2500.0)
        a0, d0 = atk[0], dfn[0]
        a0_pos, d0_pos = (2000.0, 2500.0), (4000.0, 2500.0)
        kill1 = t0 + 60_000          # a0 kills d0 after a flick
        flick_start = kill1 - 200    # flick lasts 200 ms
        for k in range(int(r_len / ms_per_tick)):
            tm = int(t0 + k * ms_per_tick)
            for i in range(10):
                is_atk = i in atk
                sp = a_spawn if is_atk else d_spawn
                u = min(1.0, max(0.0, (tm - t0 - 30_000) / 25_000))
                if i == a0:
                    pos = (sp[0] + (a0_pos[0] - sp[0]) * u, sp[1] + (a0_pos[1] - sp[1]) * u)
                elif i == d0:
                    pos = d0_pos
                elif r == 0 and i == atk[2]:  # the planter walks onto the site
                    v = min(1.0, max(0.0, (tm - t0 - 40_000) / 35_000))
                    pos = (sp[0] + (site[0] + 60 - sp[0]) * v, sp[1] + (site[1] - sp[1]) * v)
                else:
                    slot = (atk if is_atk else dfn).index(i)
                    pos = (sp[0] + 40 * slot, sp[1] + 60 * slot)
                yaw, pitch = 0.0, 0.0
                if i == a0:
                    true = math.degrees(math.atan2(d0_pos[1] - a0_pos[1], d0_pos[0] - a0_pos[0]))
                    if tm < flick_start:
                        yaw = true + 30.0
                    elif tm < kill1:
                        yaw = true + 30.0 * (1 - (tm - flick_start) / 200)
                    else:
                        yaw = true
                rows.append((tm, k, char[i], pos[0], pos[1], 0.0, yaw, pitch, 0.0, 0.0, 0.0, k % 65536, 0, 1))
        truth[r] = {"kill1": kill1, "flick_start": flick_start, "a0": subj[a0], "d0": subj[d0]}
    # Park slot rows (hidden actors) must be ignored.
    rows.append((5, 0, char[0], -50000.0, 0.0, -49900.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 1))
    mv = pl.DataFrame(rows, schema={"time_ms": pl.UInt32, "packet_id": pl.UInt32,
                                    "character_net_guid": pl.UInt32, "pos_x": pl.Float32, "pos_y": pl.Float32,
                                    "pos_z": pl.Float32, "yaw": pl.Float32, "pitch": pl.Float32,
                                    "vel_x": pl.Float32, "vel_y": pl.Float32, "vel_z": pl.Float32,
                                    "timestamp": pl.UInt32, "movement_state": pl.UInt8, "move_type": pl.UInt8},
                      orient="row")
    mv.write_parquet(d / "movement.parquet")

    ev = []

    def e(group, t, w0=0, w1=0):
        ev.append({"id": f"e{len(ev)}", "group": group, "metadata": "", "time1": t, "time2": t,
                   "payload_size": 0, "raw_payload": b"", "word0": w0, "word1": w1})
    # Round 1: a0 kills d0 (flick), d1 kills a1 (pre-aimed), plant + detonate.
    e("roundStarted", 0, 0)
    e("characterDeath", truth[0]["kill1"], char[0], char[5])
    e("characterDeath", truth[0]["kill1"] + 3000, char[6], char[1])
    e("spikePlanted", 80_000)
    e("spikeExploded", 99_000)
    e("switchTeams", 110_000, 1)
    # Round 2: defenders (team 0) kill all five attackers (team 1).
    e("roundStarted", 120_000, 1)
    for j, v in enumerate([5, 6, 7, 8, 9]):
        e("characterDeath", 150_000 + 2000 * j, char[j], char[v])
    pl.DataFrame(ev, schema={"id": pl.String, "group": pl.String, "metadata": pl.String, "time1": pl.UInt32,
                             "time2": pl.UInt32, "payload_size": pl.UInt32, "raw_payload": pl.Binary,
                             "word0": pl.UInt32, "word1": pl.UInt32}).write_parquet(d / "events.parquet")

    actors = [
        {"time_ms": 79_900, "actor_net_guid": 9001, "event": "open",
         "class_path": "/Game/Equippables/Bomb/TimedBomb.TimedBomb_C", "archetype_path": "",
         "spawn_x": 4000.0, "spawn_y": 2500.0, "spawn_z": 0.0, "spawn_pitch": 0.0, "spawn_yaw": 0.0, "spawn_roll": 0.0},
        {"time_ms": 70_000, "actor_net_guid": 9002, "event": "open",
         "class_path": "/Game/Characters/Sage/Abilities/Wall/SlowOrb_Smoke_Production.Smoke_C", "archetype_path": "",
         "spawn_x": 3500.0, "spawn_y": 2000.0, "spawn_z": 0.0, "spawn_pitch": 0.0, "spawn_yaw": 0.0, "spawn_roll": 0.0},
        {"time_ms": 88_000, "actor_net_guid": 9002, "event": "close", "class_path": "", "archetype_path": "",
         "spawn_x": 0.0, "spawn_y": 0.0, "spawn_z": 0.0, "spawn_pitch": 0.0, "spawn_yaw": 0.0, "spawn_roll": 0.0},
    ]
    # Same column types as real vrfkit output: repeated text is categorical.
    pl.DataFrame(actors).with_columns(pl.col("class_path", "archetype_path").cast(pl.Categorical)
                                      ).write_parquet(d / "actors.parquet")
    pl.DataFrame({"time_ms": [80_000], "actor_net_guid": [1], "field_name": ["PlantedAtSite"], "value_i64": [1]},
                 schema={"time_ms": pl.UInt32, "actor_net_guid": pl.UInt32, "field_name": pl.Categorical,
                         "value_i64": pl.Int64}
                 ).write_parquet(d / "fields.parquet")
    return {"truth": truth, "subjects": subj}


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(write(Path(sys.argv[1])))
    _ = np
