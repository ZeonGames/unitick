"""Rainbow Six Siege X adapter: native ``.rec`` match replays via r6-dissect (MIT, Go).

Siege saves match replays to ``Documents\\My Games\\Rainbow Six - Siege\\<id>\\MatchReplay``
as one folder per match with one ``.rec`` per round. ``r6-dissect <folder> -o x.json``
decodes them. The format exposes the round feed (kills, headshots, plants,
defuses), rosters, operators and team roles, but not player positions, so R6
matches get the scoreboard, opening duels, trades and round outcomes, and no
replay or aim metrics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import polars as pl

from ..match import Match
from ..schema import ATTACK, DEFENSE

TICK_RATE = 10.0  # feed timestamps are whole seconds; 10 ticks/s keeps order stable
ACTION_S = 180
DEFUSER_S = 45
ROUND_GAP_S = 60


def find_r6dissect(explicit: str | Path | None = None) -> str | None:
    if explicit:
        return str(explicit)
    for cand in ("r6-dissect", "r6-dissect.exe"):
        p = shutil.which(cand)
        if p:
            return p
    here = Path(__file__).resolve().parents[3] / "tools"
    for cand in ("r6-dissect.exe", "r6-dissect"):
        if (here / cand).exists():
            return str(here / cand)
    return None


def dissect(path: str | Path, out_json: str | Path, exe: str | None = None) -> Path:
    exe = find_r6dissect(exe)
    if not exe:
        raise FileNotFoundError("r6-dissect not found. Put r6-dissect(.exe) on PATH or in unitick/tools/, "
                                "or: go install github.com/redraskal/r6-dissect@latest")
    subprocess.run([exe, str(path), "-o", str(out_json)], check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    return Path(out_json)


def load(path: str | Path, *, r6dissect: str | None = None) -> Match:
    """Load a match folder / .rec (runs r6-dissect) or an r6-dissect JSON."""
    p = Path(path)
    if p.suffix.lower() != ".json":
        out = p.with_suffix(".json") if p.is_file() else p.parent / f"{p.name}.json"
        if not out.exists():
            dissect(p, out, r6dissect)
        p = out
    return from_json(json.loads(p.read_text(encoding="utf-8")), source=str(p))


def _rounds_of(doc: dict) -> list[dict]:
    if isinstance(doc.get("rounds"), list):
        return doc["rounds"]
    return [doc]


PREP_S = 45
PREP_EVENTS = {"LocateObjective", "OperatorSwap"}
OBJECTIVE_EVENTS = {"DefuserPlantStart": "plant_start", "DefuserPlantComplete": "plant",
                    "DefuserDisableStart": "defuse_start", "DefuserDisableComplete": "defuse"}
ZERO_ID = "00000000-0000-0000-0000-000000000000"


def _type(f: dict) -> str | None:
    t = f.get("type")
    return t.get("name") if isinstance(t, dict) else t


def _player_id(p: dict) -> str:
    """Profile UUID when real; pro/custom matches zero it, so fall back to the name."""
    prof = p.get("profileID")
    if prof and prof != ZERO_ID:
        return str(prof)
    return str(p.get("username"))


def from_json(doc: dict, source: str | None = None) -> Match:
    rounds_in = _rounds_of(doc)
    rounds, kills, objectives = [], [], []
    names: dict[str, str] = {}
    roster_sides: dict[tuple[int, str], str] = {}
    t0 = 0
    for i, rd in enumerate(rounds_in, start=1):
        teams = rd.get("teams") or []
        role = {k: (ATTACK if str(t.get("role", "")).lower().startswith("att") else DEFENSE)
                for k, t in enumerate(teams)}
        side_of = {}
        for pl_ in rd.get("players") or []:
            uname = pl_.get("username")
            names[uname] = _player_id(pl_)
            side_of[uname] = role.get(pl_.get("teamIndex"))
        rn = int(rd["roundNumber"]) + 1 if isinstance(rd.get("roundNumber"), int) else i
        start = t0
        live = start + int(PREP_S * TICK_RATE)
        plant_t = None
        last = live
        # The feed is chronological. Each phase has its own countdown clock:
        # preparation (45 s), action (180 s), then the defuser timer after a plant.
        for f in rd.get("matchFeedback") or []:
            typ, rem = _type(f), f.get("timeInSeconds")
            if rem is None or typ in (None, "Other"):
                continue
            if typ in PREP_EVENTS and plant_t is None:
                t = start + int(max(0, PREP_S - rem) * TICK_RATE)
            elif plant_t is None:
                t = live + int(max(0, ACTION_S - rem) * TICK_RATE)
            else:
                t = plant_t + int(max(0, DEFUSER_S - rem) * TICK_RATE)
            t = max(t, last if typ not in PREP_EVENTS else start)
            if typ == "DefuserPlantComplete" and plant_t is None:
                plant_t = t
            if typ not in PREP_EVENTS:
                last = max(last, t)
            if typ == "Kill":
                a, v = f.get("username"), f.get("target")
                kills.append({"tick": t, "round_num": rn, "attacker_id": names.get(a, a), "attacker_name": a,
                              "attacker_side": side_of.get(a), "victim_id": names.get(v, v), "victim_name": v,
                              "victim_side": side_of.get(v), "headshot": bool(f.get("headshot"))})
            elif typ in OBJECTIVE_EVENTS:
                objectives.append({"tick": t, "round_num": rn, "event": OBJECTIVE_EVENTS[typ],
                                   "player_id": names.get(f.get("username")), "player_name": f.get("username"),
                                   "site": rd.get("site")})
        winner = next((role.get(k) for k, t in enumerate(teams) if t.get("won")), None)
        reason = next((t.get("winCondition") for t in teams if t.get("won")), None)
        end = last + int(5 * TICK_RATE)
        rounds.append({"round_num": rn, "start_tick": start, "live_tick": live, "end_tick": end,
                       "winner_side": winner, "reason": reason})
        roster_sides.update({(rn, names[u]): sd for u, sd in side_of.items() if sd})
        t0 = end + int(ROUND_GAP_S * TICK_RATE)

    first = rounds_in[0] if rounds_in else {}
    teams_meta: dict[str, list[str]] = {}
    for pl_ in first.get("players") or []:
        tname = (first.get("teams") or [{}, {}])[pl_.get("teamIndex", 0)].get("name", f"Team {pl_.get('teamIndex')}")
        teams_meta.setdefault(tname, []).append(pl_.get("username"))
    mt = first.get("matchType")
    meta = {"game": "r6", "map_name": (first.get("map") or {}).get("name"), "tick_rate": TICK_RATE,
            "source": source, "teams": teams_meta, "match_id": first.get("matchID"),
            "match_type": mt.get("name") if isinstance(mt, dict) else mt,
            "game_version": first.get("gameVersion"),
            # Every player's side in every round, from the roster (the feed only names
            # players who killed or died).
            "roster_sides": [{"round_num": r, "player_id": p, "side": sd} for (r, p), sd in roster_sides.items()]}
    return Match.build(meta, rounds=pl.DataFrame(rounds), kills=pl.DataFrame(kills) if kills else None,
                       objectives=pl.DataFrame(objectives) if objectives else None)
