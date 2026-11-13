"""Build the single-file interactive match report (overview, aim, executes, replay)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import numpy as np
import polars as pl

from .. import __version__
from ..analytics import crosshair, executes, fights
from ..match import Match
from . import images, svg

MISSING = -32768


def _teams(match: Match, players: pl.DataFrame) -> list[dict]:
    """Two teams, identified by the side each player started on."""
    named = match.meta.get("teams") or {}
    teams = []
    for start_side in ("attack", "defense"):
        roster = players.filter(pl.col("first_side") == start_side)
        names = set(roster["player_name"].to_list())
        label = next((t for t, members in named.items() if names & set(members)), None)
        teams.append({"name": label or ("Team A" if start_side == "attack" else "Team B"),
                      "start_side": start_side, "player_ids": roster["player_id"].to_list()})
    # Which side each team played in each round, and rounds won.
    if not match.frames.is_empty():
        f = match.frames.select("round_num", "player_id", "side").unique()
    elif match.meta.get("roster_sides"):  # event-only sources with a roster (R6)
        f = pl.DataFrame(match.meta["roster_sides"]).select("round_num", "player_id", "side")
    else:  # event-only sources: sides come from the kill feed
        k = match.kills
        f = pl.concat([k.select("round_num", pl.col(f"{w}_id").alias("player_id"), pl.col(f"{w}_side").alias("side"))
                       for w in ("attacker", "victim")]).drop_nulls().unique()
    for t in teams:
        sides = (f.filter(pl.col("player_id").is_in(t["player_ids"]))
                 .group_by("round_num").agg(pl.col("side").mode().first()).sort("round_num"))
        side_by_round = dict(sides.iter_rows())
        t["side_by_round"] = {int(k): v for k, v in side_by_round.items()}
        t["score"] = int(sum(1 for r in match.rounds.iter_rows(named=True)
                             if side_by_round.get(r["round_num"]) == r["winner_side"]))
    return teams


def _pixels_per_unit(match: Match) -> float:
    """Radar pixels per world unit, so heights share the map's scale in the 3D view."""
    t = match.transform
    xs = np.array([0.0, 1000.0, 0.0]); ys = np.array([0.0, 0.0, 1000.0])
    px, py = t.to_pixels(xs, ys)
    d = max(np.hypot(px[1] - px[0], py[1] - py[0]), np.hypot(px[2] - px[0], py[2] - py[0]))
    return float(d / 1000.0) if np.isfinite(d) and d > 0 else 1.0


def _map_display(match: Match) -> str | None:
    """VALORANT maps have internal names (Juliett is Sunset); CS2 names are fine as is."""
    from ..maps import get
    t = get(match.map_name) if match.map_name else None
    name = getattr(t, "name", None)
    return name if name and name != match.map_name else None


def _encode_round(match: Match, rnd: dict, pidx: dict[str, int], step: int, tr: pl.DataFrame | None = None,
                  ppu: float = 1.0) -> dict:
    t = match.transform
    start, end = rnd["start_tick"], rnd["end_tick"] + match.ticks(2.0)
    f = match.frames.filter(pl.col("round_num") == rnd["round_num"])
    nt = max(1, (end - start) // step + 1)
    P = len(pidx)
    arr = np.full((nt, P, 4), MISSING, dtype=np.int16)
    zarr = np.full((nt, P), MISSING, dtype=np.int16)
    side = [-1] * P
    if not f.is_empty():
        f = (f.with_columns(((pl.col("tick") - start) // step).alias("_slot"))
             .filter(pl.col("_slot").is_between(0, nt - 1))
             .sort("tick").group_by(["_slot", "player_id"]).first())
        px, py = t.to_pixels(f["x"].to_numpy(), f["y"].to_numpy())
        slot = f["_slot"].to_numpy()
        pi = np.array([pidx.get(p, -1) for p in f["player_id"].to_list()])
        ok = (pi >= 0) & np.isfinite(px) & np.isfinite(py)
        # Screen-space heading, so the canvas never needs to know the map's axes.
        yaw = ((t.heading(f["yaw"].fill_null(0).to_numpy()) + 180) % 360 - 180) * 10
        hp = np.where(f["is_alive"].fill_null(True).to_numpy(), f["health"].fill_null(100).to_numpy(), 0)
        arr[slot[ok], pi[ok], 0] = np.clip(px[ok] * 8, -32000, 32000)
        arr[slot[ok], pi[ok], 1] = np.clip(py[ok] * 8, -32000, 32000)
        arr[slot[ok], pi[ok], 2] = yaw[ok]
        arr[slot[ok], pi[ok], 3] = hp[ok]
        if "z" in f.columns:
            zz = f["z"].fill_null(np.nan).to_numpy().astype(float) * ppu * 4
            okz = ok & np.isfinite(zz)
            zarr[slot[okz], pi[okz]] = np.clip(zz[okz], -32000, 32000)
        for p, s in f.select("player_id", "side").unique("player_id").iter_rows():
            if p in pidx:
                side[pidx[p]] = 0 if s == "attack" else 1

    def rel(tick):
        return None if tick is None else round((tick - start) / step, 2)

    def pxy(x, y):
        if x is None or y is None:
            return [None, None]
        a, b = t.to_pixels(x, y)
        return [round(float(a), 1), round(float(b), 1)]

    rn = rnd["round_num"]
    kills = [[rel(k["tick"]), pidx.get(k["attacker_id"], -1), pidx.get(k["victim_id"], -1),
              *pxy(k["attacker_x"], k["attacker_y"]), *pxy(k["victim_x"], k["victim_y"]),
              k["weapon"], bool(k["headshot"])]
             for k in match.kills.filter(pl.col("round_num") == rn).iter_rows(named=True)]
    util = [[rel(u["start_tick"]), rel(u["end_tick"]), u["kind"], *pxy(u["x"], u["y"]),
             *pxy(u["origin_x"], u["origin_y"]), pidx.get(u["player_id"], -1),
             round(120.0 / t.scale if u["kind"] == "fire" else 144.0 / t.scale, 1)]
            for u in match.utility.filter(pl.col("round_num") == rn).iter_rows(named=True)]
    obj = [[rel(o["tick"]), o["event"], *pxy(o["x"], o["y"]), o["site"]]
           for o in match.objectives.filter(pl.col("round_num") == rn).iter_rows(named=True)]
    shots = [[rel(s["tick"]), pidx.get(s["player_id"], -1)]
             for s in match.shots.filter(pl.col("round_num") == rn).select("tick", "player_id").iter_rows(named=True)]
    tracers = []
    if tr is not None and not tr.is_empty():
        kinds = {"miss": 0, "hit": 1, "kill": 2}
        for r in tr.filter(pl.col("round_num") == rn).iter_rows(named=True):
            a, b = t.to_pixels(np.array([r["x0"], r["x1"]]), np.array([r["y0"], r["y1"]]))
            tracers.append([rel(r["tick"]), pidx.get(r["shooter_id"], -1),
                            round(float(a[0]), 1), round(float(b[0]), 1), round(r["z0"] * ppu, 1),
                            round(float(a[1]), 1), round(float(b[1]), 1), round(r["z1"] * ppu, 1),
                            pidx.get(r["victim_id"], -1) if r["victim_id"] else -1, kinds.get(r["kind"], 0)])
    plant = next((o for o in obj if o[1] == "plant"), None)
    return {
        "n": rn, "t0": start, "live_tick": rnd["live_tick"], "winner": rnd["winner_side"],
        "reason": rnd["reason"], "nt": nt,
        "live": rel(rnd["live_tick"]) or 0, "end": rel(rnd["end_tick"]),
        "site": plant[4] if plant else None, "plant": plant[0] if plant else None,
        "side": side, "frames": base64.b64encode(arr.tobytes()).decode(),
        "z": base64.b64encode(zarr.tobytes()).decode() if (zarr != MISSING).any() else None,
        "tracers": tracers,
        "kills": kills, "util": util, "obj": obj, "shots": shots,
    }


def _video_payload(match: Match, videos) -> list[dict]:
    from ..video import media_key
    out = []
    for v in videos or []:
        d = v.to_dict()
        if v.kind == "file" and v.src:
            d["file_url"] = Path(v.src).resolve().as_uri()
            d["media"] = media_key(str(Path(v.src).resolve()))
            d.pop("src", None)
        if v.kind == "youtube" and v.id and not d.get("url"):
            d["url"] = f"https://www.youtube.com/watch?v={v.id}"
        out.append(d)
    return out


def build_payload(match: Match, *, visibility=None, radar: str | Path | None = "auto",
                  replay_every: int = 8, include_replay: bool = True, window_s: float = 20.0,
                  min_engagements: int = 3, videos=None, library="auto") -> dict:
    players = match.players()
    pidx = {p: i for i, p in enumerate(players["player_id"].to_list())}
    t = match.transform

    if radar == "auto":
        rp = images.find_radar(match.map_name, t)
        radar_url = images.file_data_url(rp) if rp else None
    elif radar:
        radar_url = str(radar) if str(radar).startswith("data:") else images.file_data_url(radar)
    else:
        radar_url = None
    sil = None
    if not match.frames.is_empty():
        sample = match.frames.gather_every(max(1, match.frames.height // 400_000))
        px, py = t.to_pixels(sample["x"].to_numpy(), sample["y"].to_numpy())
        sil = images.silhouette(px, py, size=t.size)

    sb = fights.scoreboard(match)
    try:
        cp = crosshair.placement(match, visibility=visibility)
    except ValueError:
        cp = pl.DataFrame()
    lb = crosshair.leaderboard(cp, min_engagements=min_engagements) if not cp.is_empty() else pl.DataFrame()
    strips = {}
    if not lb.is_empty():
        strips = {r["player_id"]: svg.error_strip(cp, r["player_id"]) for r in lb.iter_rows(named=True)}
    points = ([[pidx.get(r["attacker_id"], -1), round(r["error_deg"], 2), round(r["yaw_err"], 2),
                round(r["pitch_err"], 2), r["round_num"], r["ref_source"], r["victim_name"],
                r["weapon"], bool(r["killed"])] for r in cp.iter_rows(named=True)]
              if not cp.is_empty() else [])

    ex_sum = executes.summary(match, window_s=window_s)
    pb = executes.playbook(match)
    plays = []
    if not pb.is_empty():
        for (pid,), grp in pb.group_by("play_id", maintain_order=True):
            grp = grp.sort("is_medoid", descending=True)
            rounds = grp["round_num"].to_list()
            sketches = [{"round": r, "svg": svg.execute_svg(match, r, window_s=window_s,
                                                           background=radar_url, width=420)}
                        for r in rounds]
            plays.append({"play_id": int(pid), "site": grp["site"][0], "rounds": rounds,
                          "spread": grp["spread"][0], "sketches": sketches})
        plays.sort(key=lambda p: (-len(p["rounds"]), p["site"] or ""))

    op = fights.openings(match)
    open_by_side = (op.group_by("attacker_side").agg(pl.len(), pl.col("opener_side_won").mean())
                    .to_dicts() if not op.is_empty() else [])

    from . import deaths as _deaths
    if library == "auto":
        from ..analytics.situations import default_library
        library = default_library()
    try:
        death_payload = _deaths.build(match, visibility=visibility, library=library, pidx=pidx)
    except (ValueError, pl.exceptions.PolarsError):
        death_payload = None

    from ..analytics import control as _control
    try:
        control_payload = _control.payload(match, library) if include_replay else None
    except (ValueError, pl.exceptions.PolarsError):
        control_payload = None

    rounds = []
    if include_replay:
        from ..analytics import tracers as _tracers
        try:
            tr = _tracers.tracers(match, visibility=visibility)
        except (ValueError, pl.exceptions.PolarsError):
            tr = None
        ppu = _pixels_per_unit(match)
        rounds = [_encode_round(match, r, pidx, replay_every, tr, ppu) for r in match.rounds.iter_rows(named=True)]

    return {
        "version": __version__,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "meta": {**{k: match.meta.get(k) for k in ("game", "map_name", "tick_rate", "source", "server")},
                 "map_display": _map_display(match)},
        "summary": match.summary(),
        "transform": t.to_dict(),
        "radar": radar_url,
        "silhouette": sil,
        "step": replay_every,
        "players": players.to_dicts(),
        "teams": _teams(match, players),
        "round_results": match.rounds.select("round_num", "winner_side", "reason").to_dicts(),
        "scoreboard": sb.to_dicts(),
        "crosshair": lb.to_dicts() if not lb.is_empty() else [],
        "crosshair_strips": strips,
        "crosshair_points": points,
        "crosshair_mode": ("line-of-sight" if visibility is not None else "reaction-lead"),
        "executes": ex_sum.to_dicts() if not ex_sum.is_empty() else [],
        "plays": plays,
        "openings": open_by_side,
        "rounds": rounds,
        "deaths": death_payload,
        "control": control_payload,
        "view3d": {"ppu": _pixels_per_unit(match), "eye": float((match.meta.get("eye_height") or {}).get("stand", 64.0))},
        "videos": _video_payload(match, videos),
        "wall": {"end": match.meta.get("wall_end"), "end_tick": match.meta.get("wall_end_tick")},
        "match_key": str(match.meta.get("hltv_match") or match.meta.get("match_id")
                         or f"{match.game}:{match.map_name}:{match.rounds.height}:{match.kills.height}"),
        "info": {k: match.meta.get(k) for k in ("event", "stage", "date", "result", "hltv_match")
                 if match.meta.get(k)},
    }


def payload_json(payload: dict) -> str:
    """The payload as JSON, safe to inline in a <script> or serve to the web app."""
    return json.dumps(payload, separators=(",", ":"), default=str).replace("</", "<\\/")


def page(payload: dict, *, title: str = "unitick") -> str:
    """A self-contained HTML page: fonts, the shared report CSS and JS, and the payload."""
    res = resources.files("unitick.render")
    read = lambda name: res.joinpath(name).read_text(encoding="utf-8")
    return (read("app.html")
            .replace("/*__FONTS__*/", read("fonts.css"))
            .replace("/*__REPORT_CSS__*/", "html,body{margin:0;background:#0b0712}\n" + read("report.css"))
            .replace("/*__REPORT_JS__*/", read("report.js"))
            .replace("/*__PAYLOAD__*/null", payload_json(payload))
            .replace("__TITLE__", title))


def render(match: Match, out: str | Path, **kw) -> Path:
    """Write the report to ``out`` (a single self-contained .html file)."""
    back = kw.pop("back", None)
    payload = build_payload(match, **kw)
    payload["back"] = back
    html = page(payload, title=f"{match.map_name or 'match'}: unitick")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    _register_media(out.parent, kw.get("videos"))
    return out


def _register_media(folder: Path, videos) -> None:
    """Map media keys to local files so ``unitick serve`` can stream them (localhost only)."""
    from ..video import media_key
    files = [str(Path(v.src).resolve()) for v in videos or [] if v.kind == "file" and v.src]
    if not files:
        return
    reg_file = folder / ".unitick_media.json"
    reg = json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else {}
    reg.update({media_key(f): f for f in files})
    reg_file.write_text(json.dumps(reg, indent=1), encoding="utf-8")
