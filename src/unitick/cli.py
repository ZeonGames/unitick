"""``unitick`` command line."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import polars as pl

from . import __version__
from .match import Match


def _stamp(m: Match, src: Path) -> Match:
    """Record when the replay file was finished: the wall-clock end of the match.
    Used to line up recordings by time. (Wrong for demos downloaded later; the
    report lets you nudge the offset.)"""
    try:
        if src.is_dir():
            files = [f for f in src.iterdir() if f.is_file()]
            mtime = max(f.stat().st_mtime for f in files) if files else src.stat().st_mtime
        else:
            mtime = src.stat().st_mtime
    except (OSError, ValueError):
        return m
    m.meta.setdefault("wall_end", mtime)
    if m.rounds.height:
        last = [int(m.rounds["end_tick"].max())]
        if not m.frames.is_empty():
            last.append(int(m.frames["tick"].max()))
        m.meta.setdefault("wall_end_tick", max(last))
    return m


def load_any(path: str, frame_every: int = 4) -> Match:
    m = _load_any(path, frame_every)
    p = Path(path)
    if p.exists() and not (p.is_dir() and (p / "meta.json").exists()):
        _stamp(m, p)
    return m


def _load_any(path: str, frame_every: int = 4) -> Match:
    """Load anything unitick understands, detected from the file or folder:

    CS2 ``.dem``, VALORANT ``.vrf`` or a vrfkit export dir, R6 match folder, ``.rec``
    or r6-dissect JSON, a saved unitick match dir, a table-adapter spec ``.json``,
    ``synthetic``.
    """
    if path == "synthetic":
        from .adapters import synthetic
        return synthetic.generate()
    p = Path(path)
    suf = p.suffix.lower()
    if p.is_dir():
        if (p / "meta.json").exists():
            return Match.load(p)
        if (p / "manifest.json").exists() and (p / "movement.parquet").exists():
            from .adapters import valorant
            return valorant.from_export(p, frame_every=frame_every)
        if any(p.glob("*.rec")):
            from .adapters import r6
            return r6.load(p)
        raise SystemExit(f"unitick: {path!r} is not a match, vrfkit export or R6 match folder")
    if suf == ".dem":
        from .adapters import cs2
        return cs2.load(p, frame_every=frame_every)
    if suf == ".vrf":
        from .adapters import valorant
        return valorant.load(p, frame_every=frame_every)
    if suf == ".rec":
        from .adapters import r6
        return r6.load(p)
    if suf == ".json":
        doc = json.loads(p.read_text(encoding="utf-8"))
        rounds = doc.get("rounds") if isinstance(doc.get("rounds"), list) else [doc]
        if rounds and isinstance(rounds[0], dict) and "matchFeedback" in rounds[0]:
            from .adapters import r6
            return r6.from_json(doc, source=str(p))
        from .adapters import table
        return table.from_spec(p)
    raise SystemExit(f"unitick: don't know how to load {path!r}")


def collect_videos(m: Match, input_path: str, *, files=None, youtube=None, search=False) -> list:
    from . import video
    out = []
    p = Path(input_path)
    if p.is_dir():
        out += video.sidecar(p)
    for f in files or []:
        out.append(video.file_video(f))
    if youtube:
        vid = youtube.rsplit("v=", 1)[-1].split("&")[0].rsplit("/", 1)[-1]
        out.append(video.Video(kind="youtube", id=vid, title="broadcast"))
    if search:
        win = video.match_wall_window(m)
        if win:
            out += video.find_recordings(win[0], win[1], video.recording_dirs(m.game))
    return out


def _visibility(match: Match, mesh: str | None, enabled: bool):
    if not enabled or match.game != "cs2":
        return None
    try:
        from .adapters import cs2
        return cs2.visibility(mesh or match.map_name)
    except Exception as e:  # noqa: BLE001
        print(f"unitick: line-of-sight unavailable ({e.__class__.__name__}: {e}); "
              f"using reaction-lead crosshair timing", file=sys.stderr)
        return None


def deaths_table(payload: dict | None, m: Match) -> pl.DataFrame:
    """One row per death: cause, the play made, and the survival odds of each play."""
    cols = ["round", "time", "player", "killer", "cause", "cause_text", "play", "hold %", "push %",
            "fall back %", "best play", "verdict"]
    if not payload:
        return pl.DataFrame(schema=dict.fromkeys(cols, pl.Utf8))
    live = {r["round_num"]: r.get("live_tick") or r.get("start_tick")
            for r in m.rounds.select("round_num", *[c for c in ("live_tick", "start_tick") if c in m.rounds.columns])
            .iter_rows(named=True)}
    def pct(opt: dict, mv: str):
        return round(100 * opt[mv]["rate"]) if mv in opt and opt[mv]["enough"] else None

    out = []
    for d in payload["deaths"]:
        w = d.get("whatif") or {}
        opt = {o["move"]: o for o in w.get("options", [])}
        lt = live.get(d["round_num"])
        sec = max(0, int((d["tick"] - lt) / m.tick_rate)) if lt is not None else None
        out.append({"round": d["round_num"], "time": f"{sec // 60}:{sec % 60:02d}" if sec is not None else "",
                    "player": d["victim_name"], "killer": d["killer_name"], "cause": d["cause"],
                    "cause_text": d["cause_text"], "play": w.get("you"), "hold %": pct(opt, "held"),
                    "push %": pct(opt, "pushed"), "fall back %": pct(opt, "fell back"), "best play": w.get("best"),
                    "verdict": w.get("verdict")})
    return pl.DataFrame(out, schema_overrides={"hold %": pl.Int64, "push %": pl.Int64, "fall back %": pl.Int64,
                                               "play": pl.Utf8, "best play": pl.Utf8, "verdict": pl.Utf8})


def _log(msg: str, t0: float) -> None:
    print(f"[{time.perf_counter() - t0:6.1f}s] {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="unitick", description="Unified tactical-shooter telemetry.")
    ap.add_argument("--version", action="version", version=f"unitick {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def inp(p):
        p.add_argument("input", help=".dem file, saved match dir, table spec .json, or 'synthetic'")
        p.add_argument("--frame-every", type=int, default=4, help="CS2 snapshot stride in ticks (default 4)")

    p = sub.add_parser("parse", help="convert to a canonical parquet match directory")
    inp(p)
    p.add_argument("-o", "--out", required=True)

    p = sub.add_parser("report", help="single-file interactive HTML report")
    inp(p)
    p.add_argument("-o", "--out", default=None)
    p.add_argument("--mesh", help="CS2 collision .mesh for line-of-sight (default: awpy cache)")
    p.add_argument("--no-los", action="store_true", help="skip line-of-sight; use reaction-lead timing")
    p.add_argument("--radar", help="radar image (default: awpy cache; else auto silhouette)")
    p.add_argument("--no-replay", action="store_true", help="omit the replay (smaller file)")
    p.add_argument("--replay-every", type=int, default=8, help="replay sample stride in ticks")
    p.add_argument("--video", action="append", help="a recording of this match (.mp4 ...); repeatable")
    p.add_argument("--youtube", help="YouTube video id or URL of the broadcast")
    p.add_argument("--find-video", action="store_true",
                   help="look for NVIDIA/OBS recordings of this match in your Videos folders")
    p.add_argument("--library", help="situation library .parquet for the Deaths tab (default: bundled pro library)")
    p.add_argument("--no-library", action="store_true", help="skip the 'what others did here' search")

    p = sub.add_parser("library", help="add matches to your situation library (used by the Deaths tab)")
    p.add_argument("inputs", nargs="+", help=".dem files or saved match dirs")
    p.add_argument("--frame-every", type=int, default=8)
    p.add_argument("-o", "--out", help="library folder (default ~/.unitick/library)")

    p = sub.add_parser("crosshair", help="Crosshair Placement Score leaderboard")
    inp(p)
    p.add_argument("--mesh")
    p.add_argument("--no-los", action="store_true")
    p.add_argument("--csv", help="write per-engagement rows to CSV")
    p.add_argument("--min", type=int, default=5, help="min engagements per player")

    p = sub.add_parser("deaths", help="why each player died, and their odds of surviving with every other play")
    inp(p)
    p.add_argument("--player", help="only this player (name, case-insensitive)")
    p.add_argument("--mesh")
    p.add_argument("--no-los", action="store_true")
    p.add_argument("--csv", help="write one row per death to CSV")

    p = sub.add_parser("stats", help="scoreboard: K/D/A, ADR, KAST, openings, trades")
    inp(p)

    p = sub.add_parser("executes", help="site executes and playbook sketches")
    inp(p)
    p.add_argument("--svg-dir", help="write one SVG per execute here")
    p.add_argument("--radar")
    p.add_argument("--window", type=float, default=20.0)

    p = sub.add_parser("watch", help="analyze every new replay automatically after you play")
    p.add_argument("-o", "--out", default=str(Path.home() / "unitick"), help="output folder (default ~/unitick)")
    p.add_argument("--game", nargs="*", choices=["valorant", "cs2", "r6"], help="limit to these games")
    p.add_argument("--dir", action="append", help="extra folder: GAME=PATH (repeatable)")
    p.add_argument("--open", action="store_true", help="open each report in the browser")
    p.add_argument("--new-only", action="store_true", help="ignore replays already on disk")
    p.add_argument("--once", action="store_true", help="process what is there now, then exit")
    p.add_argument("--interval", type=float, default=10.0)

    p = sub.add_parser("samples", help="build reports for everything in samples/ (no game needed)")
    p.add_argument("-o", "--out", default=str(Path.home() / "unitick" / "reports" / "samples"))
    p.add_argument("--dir", help="samples folder (default: the repo's samples/)")
    p.add_argument("--raw", action="store_true", help="also parse downloaded raw .dem / .rec files")
    p.add_argument("--open", action="store_true", help="open the index page in the browser")

    p = sub.add_parser("app", help="run the local web app: your matches, uploads and reports in the browser")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="do not open a browser tab")

    p = sub.add_parser("serve", help="open reports through a local web server (needed for YouTube sync)")
    p.add_argument("path", nargs="?", default=str(Path.home() / "unitick" / "reports"),
                   help="a report .html or a folder of reports")
    p.add_argument("--port", type=int, default=8765)

    a = ap.parse_args(argv)
    if a.cmd == "app":
        from .app import serve as serve_app
        return serve_app(port=a.port, open_browser=not a.no_browser)
    if a.cmd == "serve":
        from .serve import serve
        return serve(Path(a.path), port=a.port)
    if a.cmd == "samples":
        from .samples import build
        build(Path(a.out), a.dir, raw=a.raw, open_index=a.open)
        return 0
    if a.cmd == "watch":
        from .watch import main_watch
        return main_watch(a)
    if a.cmd == "library":
        from .analytics.situations import add_to_library
        for i in a.inputs:
            m = load_any(i, a.frame_every)
            print(f"added {m.map_name}: {add_to_library(m, a.out)}")
        return 0
    t0 = time.perf_counter()
    pl.Config.set_tbl_rows(50)
    pl.Config.set_tbl_cols(20)
    pl.Config.set_tbl_width_chars(200)
    m = load_any(a.input, a.frame_every)
    _log(f"loaded {m!r}", t0)

    if a.cmd == "parse":
        _log(f"wrote {m.save(a.out)}", t0)
    elif a.cmd == "report":
        from .render import report
        out = a.out or (Path(a.input).stem if a.input != "synthetic" else "synthetic") + ".html"
        vis = _visibility(m, a.mesh, not a.no_los)
        videos = collect_videos(m, a.input, files=a.video, youtube=a.youtube, search=a.find_video)
        lib = None if a.no_library else "auto"
        if a.library and not a.no_library:
            from .analytics.situations import Library
            lib = Library.load(a.library)
        path = report.render(m, out, visibility=vis, radar=a.radar or "auto", library=lib,
                             include_replay=not a.no_replay, replay_every=a.replay_every, videos=videos)
        for v in videos:
            print(f"  video: {v.title or v.id} ({v.kind})", file=sys.stderr)
        _log(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)", t0)
    elif a.cmd == "crosshair":
        from .analytics import crosshair
        per = crosshair.placement(m, visibility=_visibility(m, a.mesh, not a.no_los))
        print(crosshair.leaderboard(per, min_engagements=a.min))
        if a.csv:
            per.write_csv(a.csv)
            _log(f"wrote {a.csv}", t0)
    elif a.cmd == "deaths":
        from .render import deaths as _deaths
        lib = None
        if m.game == "cs2":
            from .analytics.situations import default_library
            lib = default_library()
        rows = deaths_table(_deaths.build(m, visibility=_visibility(m, a.mesh, not a.no_los), library=lib), m)
        if a.player:
            rows = rows.filter(pl.col("player").str.to_lowercase() == a.player.lower())
        if rows.is_empty():
            print("no deaths with position data" + (f" for {a.player}" if a.player else ""))
        else:
            print(rows.drop("cause_text"))
            rated = rows.filter(pl.col("verdict").is_in(["best", "better", "close"]))
            if rated.height:
                print(f"another play had clearly better odds in {(rated['verdict'] == 'better').sum()} "
                      f"of {rated.height} rated deaths (alive 5 s later, from the closest pro moments)")
            elif m.game != "cs2":
                print("what-if odds need pro matches on the same map; they cover CS2 today")
        if a.csv:
            rows.write_csv(a.csv)
            _log(f"wrote {a.csv}", t0)
    elif a.cmd == "stats":
        from .analytics import fights
        print(fights.scoreboard(m).drop("player_id"))
    elif a.cmd == "executes":
        from .analytics import executes
        print(executes.summary(m, window_s=a.window))
        print(executes.playbook(m))
        if a.svg_dir:
            from .render import images, svg
            d = Path(a.svg_dir)
            d.mkdir(parents=True, exist_ok=True)
            rp = a.radar or images.find_radar(m.map_name)
            bg = images.file_data_url(rp) if rp else None
            for rn in executes.plants(m)["round_num"].to_list():
                (d / f"execute_r{rn:02d}.svg").write_text(svg.execute_svg(m, rn, window_s=a.window, background=bg), encoding="utf-8")
            _log(f"wrote SVGs to {d}", t0)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
