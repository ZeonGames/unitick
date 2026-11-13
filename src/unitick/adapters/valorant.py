"""VALORANT adapter: native ``.vrf`` replays via vrfkit (MIT, Rust).

VALORANT writes a replay of every match to ``%LOCALAPPDATA%\\VALORANT\\Saved\\Demos``
(game data, not video). ``vrfkit export file.vrf --out dir`` turns one into Parquet
tables; this module maps those tables onto the canonical schema.

What the replay gives directly (documented and validated by vrfkit):
  * 128 Hz position, yaw and pitch of all ten players       (movement.parquet)
  * deaths with killer / victim, round starts, spike plant / defuse / detonation,
    side switches                                            (events.parquet)
  * ability / smoke / wall actors with spawn position        (actors.parquet)
  * account UUID per character, agent per account            (manifest.json)

What unitick derives, and how (documented so it can be checked):
  * teams       from the roster blob when present, else by splitting players into
                the two spawn groups at the start of round 1
  * sides       per round, the team that starts nearest the attackers' spawn, where
                that spawn is learned from rounds with a plant
  * winner      detonation -> attack, defuse -> defense, else elimination, else time
  * names       replays carry no display names; players are labeled by agent
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import numpy as np
import polars as pl

from ..match import Match
from ..schema import ATTACK, DEFENSE

TICK_RATE = 128.0
PARK_Z = -40000.0  # hidden actors are parked near (-50000, *, -49900)
EYE = {"stand": 154.0, "crouch": 112.0}  # cm above the capsule origin (approx.)

# Actor class fragments -> utility kind. Abilities are actors in VALORANT.
UTILITY_CLASSES = {
    "smoke": ("Smoke", "Cloud", "Dome", "Orb_Smoke", "Nebula", "Cove"),
    "fire": ("Molotov", "Incendiary", "Fire", "Snake", "Nanoswarm", "Mosh", "Paint"),
    "flash": ("Flash", "Blind", "Curveball", "Leer", "Guiding", "Seize"),
    "wall": ("Wall", "Barrier", "HighGear", "Blaze"),
    "trap": ("Trap", "Tripwire", "Alarm", "Turret", "Cam", "Recon"),
}
SPIKE_CLASS = "TimedBomb"


def _agents() -> dict[str, str]:
    return json.loads(resources.files("unitick.data").joinpath("valorant_agents.json").read_text(encoding="utf-8"))


def _codenames() -> dict[str, str]:
    """Riot's internal character names (developerName) -> agent, e.g. Rift -> Astra."""
    return json.loads(resources.files("unitick.data").joinpath("valorant_codenames.json").read_text(encoding="utf-8"))


def _pawn_agents(d: Path, players: pl.DataFrame) -> dict[str, str]:
    """player_id -> agent, read from the class of each character they controlled.

    Used when the header carries no loadouts (11.06 replays). The character class
    path names the agent by codename: /Game/Characters/Rift/Rift_PC... is Astra.
    """
    f = d / "actors.parquet"
    if not f.exists():
        return {}
    code = _codenames()
    a = _strings(pl.read_parquet(f, columns=["actor_net_guid", "class_path"]))
    m = pl.col("class_path").str.extract_groups(r"^/Game/Characters/([^/]+)/([^/.]+)_PC\.")
    a = (a.with_columns(m.struct.field("1").alias("_code"), m.struct.field("2").alias("_file"))
         .filter(pl.col("_code") == pl.col("_file"))  # the agent itself, not Smonk_PostDeath etc.
         .unique("actor_net_guid"))
    if a.is_empty():
        return {}
    pawns = _pawns(d, players)
    j = a.join(pawns, left_on=pl.col("actor_net_guid").cast(pl.Int64), right_on="character_net_guid")
    out = {}
    for pid, g in j.group_by("player_id"):
        top = g["_code"].mode().to_list()
        if top and top[0] in code:
            out[pid[0]] = code[top[0]]
    return out


def _strings(df: pl.DataFrame) -> pl.DataFrame:
    """vrfkit writes repeated text (class paths, field names) as categoricals."""
    return df.with_columns(pl.col(pl.Categorical).cast(pl.String))


def _executable_for_this_os(path: Path) -> bool:
    """True when ``path`` is a binary this OS can exec.

    ``tools/vrfkit`` is a Linux build. macOS would otherwise chmod it and die
    with ``Exec format error`` on every ``.vrf``.
    """
    try:
        head = path.read_bytes()[:4]
    except OSError:
        return False
    if sys.platform == "win32":
        return head.startswith(b"MZ")
    if sys.platform == "darwin":
        return head in (
            b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",  # Mach-O, big endian
            b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",  # Mach-O, little endian
            b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf",  # fat / fat64
        )
    return head == b"\x7fELF"


def find_vrfkit(explicit: str | Path | None = None) -> str | None:
    """Locate the vrfkit binary: explicit path, $PATH, or next to unitick's tools."""
    if explicit:
        return str(explicit)
    for cand in ("vrfkit", "vrfkit.exe"):
        p = shutil.which(cand)
        if p and _executable_for_this_os(Path(p)):
            return p
    here = Path(__file__).resolve().parents[3] / "tools"
    for cand in (("vrfkit.exe", "vrfkit") if sys.platform == "win32" else ("vrfkit",)):
        exe = here / cand
        if not exe.is_file() or not _executable_for_this_os(exe):
            continue
        if sys.platform != "win32" and not os.access(exe, os.X_OK):
            # zip extraction often drops the executable bit on the bundled binary
            try:
                exe.chmod(exe.stat().st_mode | 0o111)
            except OSError:
                pass
        return str(exe)
    return None


def export_vrf(vrf: str | Path, out_dir: str | Path, vrfkit: str | None = None) -> Path:
    """Run ``vrfkit export`` on a .vrf and return the output directory."""
    exe = find_vrfkit(vrfkit)
    if not exe:
        raise FileNotFoundError(
            "vrfkit not found. Put vrfkit(.exe) on PATH or in unitick/tools/, "
            "or build it: cargo install --git https://github.com/wbxs2077/vrfkit vrfkit")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, "export", str(vrf), "--out", str(out)], check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out


def load(path: str | Path, *, frame_every: int = 4, vrfkit: str | None = None,
         work_dir: str | Path | None = None) -> Match:
    """Load a ``.vrf`` (runs vrfkit) or an existing vrfkit export directory."""
    p = Path(path)
    if p.suffix.lower() == ".vrf":
        # Exports go to unitick's cache, never into the game's Demos folder.
        from ..render.images import cache_dir

        out = Path(work_dir) if work_dir else cache_dir() / "vrf_exports" / p.stem
        if not (out / "movement.parquet").exists():
            export_vrf(p, out, vrfkit)
        p = out
    return from_export(p, frame_every=frame_every)


# --------------------------------------------------------------------- roster
def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _roster(manifest: dict) -> dict[str, dict]:
    """subject -> {agent, team} from the header's game_specific_data blobs."""
    out: dict[str, dict] = {}
    agents = _agents()
    for blob in manifest.get("game_specific_data") or []:
        try:
            doc = json.loads(blob) if isinstance(blob, str) else blob
        except (TypeError, ValueError):
            continue
        for d in _walk(doc):
            subj = d.get("subject") or d.get("Subject") or d.get("puuid")
            if not isinstance(subj, str):
                continue
            rec = out.setdefault(subj.lower(), {})
            cid = d.get("characterId") or d.get("CharacterID") or d.get("characterID")
            if isinstance(cid, str) and cid.lower() in agents:
                rec["agent"] = agents[cid.lower()]
            team = d.get("teamId") or d.get("TeamID") or d.get("teamID")
            if isinstance(team, str) and team:
                rec["team"] = team
    return out


def _map_name(manifest: dict) -> str | None:
    lv = manifest.get("level_names_and_times") or []
    if lv:
        name = lv[0].get("name") if isinstance(lv[0], dict) else lv[0]
        return str(name).rsplit("/", 1)[-1] if name else None
    return None


# ------------------------------------------------------------------- tables
def _ms_to_tick(col: str | pl.Expr) -> pl.Expr:
    e = pl.col(col) if isinstance(col, str) else col
    return (e.cast(pl.Float64) * (TICK_RATE / 1000.0)).round().cast(pl.Int32)


def _pawns(d: Path, players: pl.DataFrame) -> pl.DataFrame:
    """Every character a player controlled: (character_net_guid, player_id).

    Players get a new character every round (and on agent swaps in custom games),
    while the manifest names only the last one. The player state's
    SpawnedCharacter/PossessedCharacter fields and each character's PlayerState
    field link all of them.
    """
    base = players.select("character_net_guid", "player_id").drop_nulls()
    f = d / "fields.parquet"
    if not f.exists() or "actor_net_guid" not in pl.read_parquet_schema(f):
        return base
    ps = players.select(pl.col("actor_net_guid").cast(pl.Int64).alias("_ps"), "player_id").drop_nulls()
    fl = (pl.scan_parquet(f)
          .select("actor_net_guid", pl.col("field_name").cast(pl.String), "value_i64")
          .filter(pl.col("field_name").is_in(["SpawnedCharacter", "PossessedCharacter", "PlayerState"])
                  & (pl.col("value_i64") > 0))
          .collect())
    a = (fl.filter(pl.col("field_name") != "PlayerState")
         .select(pl.col("actor_net_guid").cast(pl.Int64).alias("_ps"),
                 pl.col("value_i64").alias("character_net_guid")))
    b = (fl.filter(pl.col("field_name") == "PlayerState")
         .select(pl.col("value_i64").alias("_ps"), pl.col("actor_net_guid").cast(pl.Int64).alias("character_net_guid")))
    linked = pl.concat([a, b]).join(ps, on="_ps").select("character_net_guid", "player_id")
    return pl.concat([base, linked]).unique("character_net_guid", keep="first")


def _movement(d: Path, players: pl.DataFrame, every: int) -> pl.DataFrame:
    mv = pl.scan_parquet(d / "movement.parquet").select(
        "time_ms", "character_net_guid", "pos_x", "pos_y", "pos_z", "yaw", "pitch",
        "vel_x", "vel_y", "vel_z")
    mv = (mv.filter(~((pl.col("pos_x") < -40000) & (pl.col("pos_z") < PARK_Z)))
          .with_columns(_ms_to_tick("time_ms").alias("tick"))
          .with_columns((pl.col("tick") // every).alias("_slot"))
          .group_by("character_net_guid", "_slot").agg(pl.all().sort_by("time_ms").last())
          .collect())
    pawns = _pawns(d, players).join(players.select("player_id", "player_name"), on="player_id")
    return (mv.with_columns(pl.col("character_net_guid").cast(pl.Int64))
            .join(pawns, on="character_net_guid", how="inner")
            .select("tick", "player_id", "player_name",
                    pl.col("pos_x").alias("x"), pl.col("pos_y").alias("y"), pl.col("pos_z").alias("z"),
                    # UE pitch is positive looking up; unitick's is positive looking down.
                    (-pl.col("pitch").cast(pl.Float64)).alias("pitch"),
                    pl.col("yaw").cast(pl.Float64).alias("yaw"),
                    (pl.col("vel_x") ** 2 + pl.col("vel_y") ** 2 + pl.col("vel_z") ** 2).sqrt()
                    .alias("velocity"))
            .sort("tick"))


def _events(d: Path) -> pl.DataFrame:
    ev = _strings(pl.read_parquet(d / "events.parquet", columns=["group", "time1", "word0", "word1"]))
    return ev.with_columns(_ms_to_tick("time1").alias("tick")).sort("tick")


def _rounds(ev: pl.DataFrame, end_tick: int) -> pl.DataFrame:
    rs = ev.filter(pl.col("group") == "roundStarted").sort("tick").unique("tick", keep="first",
                                                                         maintain_order=True)
    if rs.is_empty():
        return pl.DataFrame({"round_num": [1], "start_tick": [0], "live_tick": [0],
                             "end_tick": [end_tick]})
    starts = rs["tick"].to_list()
    ends = starts[1:] + [end_tick]
    n = len(starts)
    # Buy phase: 45 s on the first round of each half, 30 s otherwise.
    switches = set(ev.filter(pl.col("group") == "switchTeams")["tick"].to_list())
    first_of_half = {0}
    for i in range(1, n):
        if any(starts[i - 1] < s <= starts[i] + int(TICK_RATE * 2) for s in switches):
            first_of_half.add(i)
    live = [s + int(TICK_RATE * (45 if i in first_of_half else 30)) for i, s in enumerate(starts)]
    return pl.DataFrame({"round_num": list(range(1, n + 1)), "start_tick": starts,
                         "live_tick": [min(lv, e) for lv, e in zip(live, ends, strict=True)],
                         "end_tick": ends}).cast(pl.Int32)


def _guid_map(players: pl.DataFrame) -> dict[int, str]:
    m: dict[int, str] = {}
    for row in players.iter_rows(named=True):
        for g in (row["character_net_guid"], row["actor_net_guid"]):
            if g:
                m[int(g)] = row["player_id"]
    return m


def _nearest(frames: pl.DataFrame, q: pl.DataFrame, id_col: str, tick_col: str, prefix: str):
    right = frames.select(pl.col("tick").cast(pl.Int64).alias("_ft"), pl.col("player_id").alias(id_col),
                          pl.col("x").alias(f"{prefix}_x"), pl.col("y").alias(f"{prefix}_y"),
                          pl.col("z").alias(f"{prefix}_z")).sort("_ft")
    left = q.with_row_index("_r").with_columns(pl.col(tick_col).cast(pl.Int64).alias("_qt")).sort("_qt")
    out = left.join_asof(right, left_on="_qt", right_on="_ft", by=id_col, strategy="backward",
                         tolerance=int(TICK_RATE), check_sortedness=False)
    return out.sort("_r").drop("_r", "_qt", "_ft")


def _teams_from_spawns(frames: pl.DataFrame, rounds: pl.DataFrame) -> dict[str, int]:
    """Split players into two teams by their positions shortly after round 1 starts."""
    r1 = rounds.row(0, named=True)
    t = r1["start_tick"] + int(TICK_RATE * 2)
    snap = (frames.filter(pl.col("tick").is_between(r1["start_tick"], r1["live_tick"]))
            .with_columns((pl.col("tick") - t).abs().alias("_d")).sort("_d")
            .group_by("player_id").first().sort("player_id"))
    if snap.height < 2:
        return {}
    ids = snap["player_id"].to_list()
    pts = snap.select("x", "y").to_numpy()
    dm = np.linalg.norm(pts[:, None] - pts[None], axis=-1)
    i, j = np.unravel_index(np.argmax(dm), dm.shape)
    team = (np.linalg.norm(pts - pts[j], axis=1) < np.linalg.norm(pts - pts[i], axis=1)).astype(int)
    return dict(zip(ids, team.tolist(), strict=True))


def _sides(frames: pl.DataFrame, rounds: pl.DataFrame, team: dict[str, int],
           plants: pl.DataFrame) -> dict[int, dict[int, str]]:
    """round_num -> {team_index: side}. Attack is learned from who stood at the spike."""
    starts = {}
    for r in rounds.iter_rows(named=True):
        t = r["start_tick"] + int(TICK_RATE * 2)
        snap = (frames.filter(pl.col("tick").is_between(r["start_tick"], r["live_tick"]))
                .with_columns((pl.col("tick") - t).abs().alias("_d")).sort("_d")
                .group_by("player_id").first())
        cents = {}
        for k in (0, 1):
            s = snap.filter(pl.col("player_id").is_in([p for p, v in team.items() if v == k]))
            if s.height:
                cents[k] = s.select(pl.col("x").mean(), pl.col("y").mean()).row(0)
        starts[r["round_num"]] = cents
    # Attack spawn reference: the start centroid of whichever team planted.
    refs = []
    for p in plants.iter_rows(named=True):
        if p["planter_team"] is None:
            continue
        c = starts.get(p["round_num"], {}).get(p["planter_team"])
        if c:
            refs.append(c)
    out: dict[int, dict[int, str]] = {}
    ref = np.mean(np.array(refs), axis=0) if refs else None
    for rn, cents in starts.items():
        if ref is None or len(cents) < 2:
            out[rn] = {0: ATTACK, 1: DEFENSE}
            continue
        d0 = np.hypot(*(np.array(cents[0]) - ref))
        d1 = np.hypot(*(np.array(cents[1]) - ref))
        out[rn] = {0: ATTACK, 1: DEFENSE} if d0 < d1 else {0: DEFENSE, 1: ATTACK}
    return out


def _utility(d: Path, rounds: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    f = d / "actors.parquet"
    if not f.exists():
        return pl.DataFrame(), pl.DataFrame()
    a = _strings(pl.read_parquet(f))
    if "time_ms" not in a.columns:
        return pl.DataFrame(), pl.DataFrame()
    key = "actor_net_guid" if "actor_net_guid" in a.columns else ("channel_index" if "channel_index" in a.columns else None)
    cls = pl.col("class_path").fill_null("")
    kind = pl.lit(None, pl.String)
    for k, frags in reversed(list(UTILITY_CLASSES.items())):
        cond = pl.lit(False)
        for fr in frags:
            cond = cond | cls.str.contains(fr, literal=True)
        kind = pl.when(cond).then(pl.lit(k)).otherwise(kind)
    a = a.with_columns(kind.alias("kind"), _ms_to_tick("time_ms").alias("tick"),
                       cls.str.contains(SPIKE_CLASS, literal=True).alias("_spike"))
    opens = a.filter(pl.col("event") == "open")
    closes = a.filter(pl.col("event") == "close")
    if key:
        opens = opens.join(closes.select(key, pl.col("tick").alias("end_tick")), on=key, how="left",
                           maintain_order="left").filter(
            pl.col("end_tick").is_null() | (pl.col("end_tick") >= pl.col("tick")))
        opens = opens.sort("end_tick").unique(subset=[key, "tick"], keep="first")
    else:
        opens = opens.with_columns(pl.lit(None, pl.Int32).alias("end_tick"))
    util = opens.filter(pl.col("kind").is_not_null() & ~pl.col("_spike")).select(
        pl.col("tick").alias("start_tick"), "end_tick", "kind",
        pl.col("spawn_x").alias("x"), pl.col("spawn_y").alias("y"), pl.col("spawn_z").alias("z"))
    bombs = opens.filter(pl.col("_spike")).select("tick", pl.col("spawn_x").alias("x"),
                                                  pl.col("spawn_y").alias("y"), pl.col("spawn_z").alias("z"))
    return util, bombs


def _plant_sites(d: Path) -> list[int]:
    """PlantedAtSite enum values in plant order (absent handle = default site A)."""
    f = d / "fields.parquet"
    if not f.exists():
        return []
    s = (pl.scan_parquet(f).with_columns(pl.col(pl.Categorical).cast(pl.String))
         .filter(pl.col("field_name") == "PlantedAtSite")
         .select("time_ms", "value_i64").sort("time_ms").collect())
    return s["value_i64"].fill_null(0).to_list()


def from_export(d: str | Path, *, frame_every: int = 4) -> Match:
    d = Path(d)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    roster = _roster(manifest)
    seen: dict[str, int] = {}
    prow = []
    base = pl.DataFrame([{"player_id": (p.get("subject") or "").lower(), "actor_net_guid": p.get("actor_net_guid"),
                          "character_net_guid": p.get("character_net_guid")}
                         for p in manifest.get("players") or [] if p.get("subject")],
                        schema={"player_id": pl.String, "actor_net_guid": pl.Int64, "character_net_guid": pl.Int64})
    from_pawns = {} if all(roster.get(s, {}).get("agent") for s in base["player_id"]) else _pawn_agents(d, base)
    for p in manifest.get("players") or []:
        subj = (p.get("subject") or "").lower()
        if not subj:
            continue
        agent = roster.get(subj, {}).get("agent") or from_pawns.get(subj) or "Player"
        seen[agent] = seen.get(agent, 0) + 1
        name = agent if seen[agent] == 1 else f"{agent} {seen[agent]}"
        prow.append({"player_id": subj, "player_name": name, "actor_net_guid": p.get("actor_net_guid"),
                     "character_net_guid": p.get("character_net_guid")})
    players = pl.DataFrame(prow, schema={"player_id": pl.String, "player_name": pl.String,
                                         "actor_net_guid": pl.Int64, "character_net_guid": pl.Int64})

    frames = _movement(d, players, frame_every)
    ev = _events(d)
    end_tick = int(max(frames["tick"].max() or 0, ev["tick"].max() or 0))
    rounds = _rounds(ev, end_tick)
    gmap = _guid_map(players)
    for g, pid in _pawns(d, players).iter_rows():
        gmap.setdefault(int(g), pid)

    # Teams: roster team ids if present, else spawn split.
    roster_teams = {s: r["team"] for s, r in roster.items() if "team" in r}
    if len({roster_teams.get(p) for p in players["player_id"]} - {None}) == 2:
        labels = sorted(set(roster_teams.values()))
        team = {p: labels.index(roster_teams[p]) for p in players["player_id"] if p in roster_teams}
    else:
        team = _teams_from_spawns(frames, rounds)
    if not team and players.height:
        # Solo custom games and range sessions: one team, shown as attack.
        team = dict.fromkeys(players["player_id"].to_list(), 0)

    deaths = ev.filter(pl.col("group") == "characterDeath").with_columns(
        pl.col("word0").map_elements(lambda g: gmap.get(int(g)) if g is not None else None,
                                     return_dtype=pl.String).alias("attacker_id"),
        pl.col("word1").map_elements(lambda g: gmap.get(int(g)) if g is not None else None,
                                     return_dtype=pl.String).alias("victim_id"))
    starts = rounds["start_tick"].to_list()

    def dead_at(t: int) -> set[str]:
        """Players killed earlier in the round that contains tick ``t``."""
        rs = max((s for s in starts if s <= t), default=0)
        return set(deaths.filter(pl.col("tick").is_between(rs, t))["victim_id"].drop_nulls().to_list())

    # Plants: spike actor spawn = plant location; planter = nearest living player.
    util, bombs = _utility(d, rounds)
    plant_ev = ev.filter(pl.col("group") == "spikePlanted").select("tick")
    sites = _plant_sites(d)
    plant_rows = []
    for i, t in enumerate(plant_ev["tick"].to_list()):
        loc = None
        if not bombs.is_empty():
            b = bombs.with_columns((pl.col("tick") - t).abs().alias("_d")).sort("_d")
            if b["_d"][0] <= TICK_RATE * 3:
                loc = b.row(0, named=True)
        planter = planter_team = None
        if loc is not None:
            near = (frames.filter(pl.col("tick").is_between(t - int(TICK_RATE), t)
                                  & ~pl.col("player_id").is_in(list(dead_at(t))))
                    .with_columns(((pl.col("x") - loc["x"]) ** 2 + (pl.col("y") - loc["y"]) ** 2).sqrt()
                                  .alias("_dist")).sort("_dist"))
            if near.height and near["_dist"][0] < 250:
                planter = near["player_id"][0]
                planter_team = team.get(planter)
        site = "ABC"[sites[i]] if i < len(sites) and 0 <= sites[i] < 3 else None
        plant_rows.append({"tick": t, "planter": planter, "planter_team": planter_team, "site": site,
                           "x": loc["x"] if loc else None, "y": loc["y"] if loc else None,
                           "z": loc["z"] if loc else None})
    plants = pl.DataFrame(plant_rows, schema={"tick": pl.Int64, "planter": pl.String,
                                              "planter_team": pl.Int64, "site": pl.String,
                                              "x": pl.Float64, "y": pl.Float64, "z": pl.Float64})
    plants = _assign(plants, rounds)
    sides = _sides(frames, rounds, team, plants)
    # Team A is always the team that starts on attack, so labels do not depend on
    # the order players happened to come out of the spawn clustering.
    first = rounds["round_num"].min() if rounds.height else None
    if first is not None and sides.get(first, {}).get(1) == ATTACK:
        team = {p: 1 - v for p, v in team.items()}
        sides = {rn: {1 - k: v for k, v in m.items()} for rn, m in sides.items()}
        plants = plants.with_columns((1 - pl.col("planter_team")).alias("planter_team"))

    def side_of(pid, rn):
        k = team.get(pid)
        return None if k is None or rn is None else sides.get(rn, {}).get(k)

    frames = _assign(frames, rounds).filter(pl.col("round_num").is_not_null())
    frames = frames.with_columns(
        pl.struct("player_id", "round_num").map_elements(lambda s: side_of(s["player_id"], s["round_num"]),
                                                        return_dtype=pl.String).alias("side"),
        pl.lit(100).alias("health"), pl.lit(True).alias("is_alive"))

    # Kills.
    names = dict(players.select("player_id", "player_name").iter_rows())
    kills = _assign(deaths.filter(pl.col("victim_id").is_not_null()).select("tick", "attacker_id", "victim_id"),
                    rounds)
    kills = _nearest(frames, kills, "attacker_id", "tick", "attacker")
    kills = _nearest(frames, kills, "victim_id", "tick", "victim")
    kills = kills.with_columns(
        pl.col("attacker_id").replace_strict(names, default=None).alias("attacker_name"),
        pl.col("victim_id").replace_strict(names, default=None).alias("victim_name"),
        pl.struct("attacker_id", "round_num").map_elements(
            lambda s: side_of(s["attacker_id"], s["round_num"]), return_dtype=pl.String).alias("attacker_side"),
        pl.struct("victim_id", "round_num").map_elements(
            lambda s: side_of(s["victim_id"], s["round_num"]), return_dtype=pl.String).alias("victim_side"))

    # Dead players: health 0 / not alive from their death to the end of the round.
    if not kills.is_empty():
        death = kills.select("round_num", pl.col("victim_id").alias("player_id"),
                             pl.col("tick").alias("_death")).group_by("round_num", "player_id").agg(
            pl.col("_death").min())
        frames = frames.join(death, on=["round_num", "player_id"], how="left", maintain_order="left") \
            .with_columns((pl.col("_death").is_null() | (pl.col("tick") < pl.col("_death"))).alias("is_alive")) \
            .with_columns(pl.when(pl.col("is_alive")).then(100).otherwise(0).alias("health")).drop("_death")

    # Objectives.
    obj = [plants.select("tick", "round_num", pl.lit("plant").alias("event"),
                         pl.col("planter").alias("player_id"), "site", "x", "y", "z")]
    for grp, name in (("spikeDefused", "defuse"), ("spikeExploded", "explode")):
        e = ev.filter(pl.col("group") == grp).select("tick")
        if e.height:
            obj.append(_assign(e, rounds).with_columns(pl.lit(name).alias("event")))
    objectives = pl.concat(obj, how="diagonal_relaxed").sort("tick")
    if objectives.height:
        site = (pl.col("site").forward_fill().backward_fill()).over("round_num", order_by="tick")
        xyz = [(pl.col(c).forward_fill()).over("round_num", order_by="tick") for c in "xyz"]
        objectives = objectives.with_columns(pl.coalesce("site", site).alias("site"),
                                             *[pl.coalesce(c, x).alias(c) for c, x in zip("xyz", xyz, strict=True)],
                                             pl.col("player_id").replace_strict(names, default=None)
                                             .alias("player_name"))

    # Winners.
    team_size = {k: sum(1 for v in team.values() if v == k) for k in (0, 1)}
    winners, reasons = [], []
    for r in rounds.iter_rows(named=True):
        rn = r["round_num"]
        o = objectives.filter(pl.col("round_num") == rn)["event"].to_list() if objectives.height else []
        rk = kills.filter(pl.col("round_num") == rn)
        dead_side = {}
        for v in rk["victim_side"].to_list():
            if v:
                dead_side[v] = dead_side.get(v, 0) + 1
        side_size = {sides[rn][k]: team_size[k] for k in (0, 1)} if rn in sides else {}
        if "explode" in o:
            winners.append(ATTACK); reasons.append("detonate")
        elif "defuse" in o:
            winners.append(DEFENSE); reasons.append("defuse")
        elif side_size and dead_side.get(DEFENSE, 0) >= side_size.get(DEFENSE, 5) > 0:
            winners.append(ATTACK); reasons.append("elimination")
        elif side_size and dead_side.get(ATTACK, 0) >= side_size.get(ATTACK, 5) > 0 and "plant" not in o:
            winners.append(DEFENSE); reasons.append("elimination")
        else:
            winners.append(DEFENSE if "plant" not in o else ATTACK)
            reasons.append("time" if "plant" not in o else "unknown")
    rounds = rounds.with_columns(pl.Series("winner_side", winners), pl.Series("reason", reasons))

    if not util.is_empty():
        util = _assign(util, rounds, tick="start_tick").with_columns(
            pl.lit(None, pl.String).alias("player_id"), pl.lit(None, pl.String).alias("side"))

    team_names = {}
    for k in (0, 1):
        team_names[f"Team {'AB'[k]}"] = [names[p] for p, v in team.items() if v == k and p in names]

    meta = {
        "game": "valorant",
        "map_name": _map_name(manifest),
        "tick_rate": TICK_RATE,
        "source": str(d),
        "frame_every": frame_every,
        "teams": team_names,
        "eye_height": EYE,
        "units": "cm",
    }
    from ..analytics.sites import fill_missing
    callouts = json.loads(resources.files("unitick.data").joinpath("valorant_site_callouts.json")
                          .read_text(encoding="utf-8")).get(meta.get("map_name") or "", [])
    objectives = fill_missing(objectives, [tuple(c) for c in callouts])
    return Match.build(meta, rounds=rounds, frames=frames, kills=kills, utility=util,
                       objectives=objectives)


def _assign(df: pl.DataFrame, rounds: pl.DataFrame, tick: str = "tick") -> pl.DataFrame:
    if df.is_empty():
        return df.with_columns(pl.lit(None, pl.Int32).alias("round_num"))
    b = rounds.select(pl.col("round_num").alias("round_num"),
                      pl.col("start_tick").cast(pl.Int64).alias("_s")).sort("_s")
    out = (df.with_row_index("_r").with_columns(pl.col(tick).cast(pl.Int64).alias("_t")).sort("_t")
           .join_asof(b, left_on="_t", right_on="_s", strategy="backward").sort("_r"))
    return out.drop("_r", "_t", "_s")
