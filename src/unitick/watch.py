"""Watch the folders games write replays into; archive and analyze each new one.

Only finished files on disk are read. Nothing touches a running game process, so
this is safe alongside Vanguard / VAC / BattlEye.

Sources (Windows defaults, override with --dir GAME=PATH):
  valorant  %LOCALAPPDATA%\\VALORANT\\Saved\\Demos\\*.vrf          (game rotates these: archived)
  cs2       <Steam>\\steamapps\\common\\Counter-Strike Global Offensive\\game\\csgo\\replays\\*.dem
            and csgo\\*.dem (console ``record``)
  r6        %USERPROFILE%\\Documents\\My Games\\Rainbow Six - Siege\\*\\MatchReplay\\Match-*\\
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import traceback
import webbrowser
from dataclasses import dataclass
from pathlib import Path

GAMES = ("valorant", "cs2", "r6")


@dataclass
class Item:
    game: str
    path: Path          # file (.vrf/.dem) or folder (R6 match)
    key: str            # stable identity for state tracking
    size: int
    mtime: float


def _steam_libraries() -> list[Path]:
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
             Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
             Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]
    libs = []
    for r in roots:
        vdf = r / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="ignore"))]
        if r.exists():
            libs.append(r)
    seen, out = set(), []
    for lib in libs:
        if str(lib).lower() not in seen:
            seen.add(str(lib).lower())
            out.append(lib)
    return out


def default_dirs() -> dict[str, list[Path]]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    docs = Path.home() / "Documents"
    cs2 = []
    for lib in _steam_libraries():
        base = lib / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo"
        cs2 += [base / "replays", base]
    return {
        "valorant": [local / "VALORANT" / "Saved" / "Demos"],
        "cs2": cs2,
        "r6": [docs / "My Games" / "Rainbow Six - Siege"],
    }


def scan(dirs: dict[str, list[Path]]) -> list[Item]:
    items: list[Item] = []
    for game, roots in dirs.items():
        for root in roots:
            if not root.exists():
                continue
            if game == "valorant":
                files = root.glob("*.vrf")
            elif game == "cs2":
                files = root.glob("*.dem")
            else:  # r6: one folder per match, one .rec per round
                files = [p for p in root.glob("*/MatchReplay/Match-*") if p.is_dir()]
                files += [p for p in root.glob("MatchReplay/Match-*") if p.is_dir()]
                files += [p for p in root.glob("Match-*") if p.is_dir()]
            for f in files:
                try:
                    if f.is_dir():
                        recs = list(f.glob("*.rec"))
                        if not recs:
                            continue
                        size = sum(r.stat().st_size for r in recs)
                        mtime = max(r.stat().st_mtime for r in recs)
                    else:
                        st = f.stat()
                        size, mtime = st.st_size, st.st_mtime
                except OSError:
                    continue
                items.append(Item(game, f, f"{game}:{f.name}", size, mtime))
    return items


class Watcher:
    def __init__(self, out: Path, dirs: dict[str, list[Path]], *, open_reports: bool = False,
                 settle_s: float = 20.0, log=print):
        self.out = Path(out)
        self.dirs = dirs
        self.open_reports = open_reports
        self.settle_s = settle_s
        self.log = log
        self.state_file = self.out / "state.json"
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
        self._seen: dict[str, tuple[int, float]] = {}

    def _save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def _archive(self, it: Item) -> Path:
        dest = self.out / "archive" / it.game / it.path.name
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if it.path.is_dir():
            shutil.copytree(it.path, dest)
        else:
            shutil.copy2(it.path, dest)
        return dest

    def process(self, it: Item) -> Path:
        from .cli import load_any
        from .render import report

        src = self._archive(it)
        self.log(f"[{it.game}] analyzing {it.path.name}")
        m = load_any(str(src))
        vis = None
        if m.game == "cs2":
            try:
                from .adapters import cs2
                vis = cs2.visibility(m.map_name)
            except Exception:  # noqa: BLE001 - no mesh: reaction-lead timing
                vis = None
        # The archived copy has a new mtime; the original's is when the match ended.
        m.meta["wall_end"] = it.mtime
        from .cli import collect_videos
        videos = collect_videos(m, str(src), search=True)
        for v in videos:
            self.log(f"[{it.game}] paired recording: {v.title}")
        name = f"{time.strftime('%Y-%m-%d_%H%M', time.localtime(it.mtime))}_{it.game}_{m.map_name or 'match'}"
        m.save(self.out / "matches" / name)
        html = report.render(m, self.out / "reports" / f"{name}.html", visibility=vis, videos=videos,
                             back="index.html")
        from .render import index as _index
        _index.record(html, m, label=name.replace("_", " "), video=bool(videos),
                      when=time.strftime("%d %b %Y, %H:%M", time.localtime(it.mtime)))
        _index.rescan(html.parent)
        self.log(f"[{it.game}] report -> {html}")
        if self.open_reports:
            webbrowser.open(html.resolve().as_uri())
        return html

    def tick(self) -> list[Path]:
        """One scan. A file is processed once its size and mtime stop changing."""
        done = []
        now = time.time()
        for it in scan(self.dirs):
            prev = self.state.get(it.key)
            if prev and prev.get("size") == it.size and prev.get("status") in ("ok", "failed", "skipped"):
                continue
            sig = (it.size, it.mtime)
            if self._seen.get(it.key) != sig or now - it.mtime < self.settle_s:
                self._seen[it.key] = sig
                continue
            try:
                html = self.process(it)
                self.state[it.key] = {"size": it.size, "status": "ok", "report": str(html)}
                done.append(html)
            except Exception as e:  # noqa: BLE001 - one bad replay must not stop the watcher
                self.log(f"[{it.game}] failed on {it.path.name}: {e}")
                self.state[it.key] = {"size": it.size, "status": "failed", "error": str(e),
                                      "trace": traceback.format_exc()[-2000:]}
            self._save()
        return done

    def baseline(self) -> int:
        """Mark everything already on disk as seen (watch only new matches)."""
        n = 0
        for it in scan(self.dirs):
            if it.key not in self.state:
                self.state[it.key] = {"size": it.size, "status": "skipped"}
                n += 1
        self._save()
        return n

    def run(self, interval: float = 10.0, once: bool = False) -> None:
        found = {g: [str(p) for p in ps if p.exists()] for g, ps in self.dirs.items()}
        for g, ps in found.items():
            self.log(f"watching {g}: {', '.join(ps) if ps else '(folder not found)'}")
        self.log(f"reports go to {self.out / 'reports'}  (ctrl+c to stop)")
        if once:  # process whatever is already on disk, then exit
            self.settle_s = 0.0
            self.tick()  # first pass records file signatures
            self.tick()  # second pass processes the unchanged ones
            return
        while True:
            self.tick()
            try:
                time.sleep(interval)
            except KeyboardInterrupt:  # pragma: no cover
                self.log("stopped")
                return


def main_watch(args) -> int:
    dirs = default_dirs()
    if args.game:
        dirs = {g: dirs[g] for g in args.game}
    for spec in args.dir or []:
        g, _, p = spec.partition("=")
        if g not in GAMES or not p:
            print(f"--dir expects GAME=PATH with GAME in {GAMES}", file=sys.stderr)
            return 2
        dirs.setdefault(g, [])
        dirs[g] = [Path(p)] + dirs[g]
    w = Watcher(Path(args.out), dirs, open_reports=args.open)
    if args.new_only:
        n = w.baseline()
        print(f"ignoring {n} replay(s) already on disk; waiting for new matches")
    w.run(interval=args.interval, once=args.once)
    return 0
