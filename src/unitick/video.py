"""Pair a match with video of it, and say how game time maps to video time.

Two kinds of video:

* **Your recordings** (NVIDIA ShadowPlay / NVIDIA app, OBS, any .mp4): found by file
  time. A replay file is finished when the match ends, and a recording is finished
  when the recording stops, so both mtimes are wall-clock anchors. One recording
  or many 30 s clips, each one is placed on the match timeline automatically.
  The report lets you nudge the offset if a clock was off.

* **Broadcast VODs** (YouTube): broadcasts pause, replay and cut away, so a single
  offset cannot work. Sync is per round: ``anchors = [[round_num, video_seconds]]``
  is where each round's live phase starts in the VOD. Set them in the report with
  one click per round and export them as ``video.json`` next to the match.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
# Folder names the NVIDIA app / ShadowPlay uses under Videos\.
GAME_FOLDERS = {
    "cs2": ["Counter-strike 2", "Counter-Strike 2", "Counter-strike Global Offensive", "CS2"],
    "valorant": ["VALORANT", "Valorant"],
    "r6": ["Tom Clancy's Rainbow Six Siege", "Rainbow Six Siege", "Tom Clancy's Rainbow Six Siege X"],
}


@dataclass
class Video:
    kind: str                    # "file" | "youtube"
    title: str = ""
    src: str | None = None       # file path (kind=file)
    id: str | None = None        # YouTube id (kind=youtube)
    wall_start: float | None = None
    duration: float | None = None
    offset: float = 0.0          # user nudge, seconds
    anchors: list[list[float]] = field(default_factory=list)
    url: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


# ------------------------------------------------------------------ durations
def _mp4_duration(path: Path) -> float | None:
    """Duration from the ISO-BMFF ``moov/mvhd`` box (MP4, MOV, M4V)."""
    try:
        with path.open("rb") as f:
            size = path.stat().st_size

            def boxes(start: int, end: int):
                pos = start
                while pos + 8 <= end:
                    f.seek(pos)
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        return
                    n, typ = struct.unpack(">I4s", hdr)
                    hl = 8
                    if n == 1:
                        n = struct.unpack(">Q", f.read(8))[0]
                        hl = 16
                    elif n == 0:
                        n = end - pos
                    if n < hl:
                        return
                    yield typ, pos + hl, pos + n
                    pos += n

            for typ, s, e in boxes(0, size):
                if typ != b"moov":
                    continue
                for t2, s2, _ in boxes(s, e):
                    if t2 == b"mvhd":
                        f.seek(s2)
                        ver = f.read(1)[0]
                        f.read(3)
                        if ver == 1:
                            f.read(16)
                            scale, dur = struct.unpack(">IQ", f.read(12))
                        else:
                            f.read(8)
                            scale, dur = struct.unpack(">II", f.read(8))
                        return dur / scale if scale else None
    except (OSError, struct.error, IndexError):
        return None
    return None


def duration(path: str | Path) -> float | None:
    path = Path(path)
    if path.suffix.lower() in (".mp4", ".mov", ".m4v"):
        d = _mp4_duration(path)
        if d:
            return d
    exe = shutil.which("ffprobe")
    if exe:
        try:
            out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of",
                                  "default=nw=1:nk=1", str(path)], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20, check=False)
            return float(out.stdout.strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            return None
    return None


# ----------------------------------------------------------------- discovery
def recording_dirs(game: str | None) -> list[Path]:
    home = Path.home()
    roots = [home / "Videos", home / "Movies", Path(os.environ.get("USERPROFILE", home)) / "Videos"]
    out = []
    for r in roots:
        for name in GAME_FOLDERS.get(game or "", []):
            if (r / name).is_dir():
                out.append(r / name)
        if r.is_dir():
            out.append(r)  # OBS default: straight into Videos
    seen, uniq = set(), []
    for p in out:
        if str(p).lower() not in seen:
            seen.add(str(p).lower())
            uniq.append(p)
    return uniq


def file_video(path: str | Path) -> Video:
    p = Path(path)
    d = duration(p)
    mtime = p.stat().st_mtime
    return Video(kind="file", title=p.name, src=str(p.resolve()), duration=d,
                 wall_start=(mtime - d) if d else None)


def find_recordings(wall_start: float, wall_end: float, dirs: list[Path], *,
                    slack_s: float = 120.0) -> list[Video]:
    """Video files whose recording interval overlaps the match's wall-clock window."""
    hits = []
    for d in dirs:
        try:
            files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
        except OSError:
            continue
        for p in files:
            m = p.stat().st_mtime
            if m < wall_start - slack_s or m > wall_end + 6 * 3600:
                continue  # finished before the match, or long after
            v = file_video(p)
            if v.wall_start is None:
                continue
            if v.wall_start <= wall_end + slack_s and m >= wall_start - slack_s:
                hits.append(v)
    hits.sort(key=lambda v: v.wall_start or 0)
    seen, out = set(), []
    for v in hits:
        if v.src not in seen:
            seen.add(v.src)
            out.append(v)
    return out


def match_wall_window(match) -> tuple[float, float] | None:
    """(wall_start, wall_end) of the match from ``meta['wall_end']`` (replay file mtime)."""
    we = match.meta.get("wall_end")
    if not we:
        return None
    last = match.meta.get("wall_end_tick") or int(match.rounds["end_tick"].max() or 0)
    first = int(match.rounds["start_tick"].min() or 0)
    return we - (last - first) / match.tick_rate, we


def sidecar(match_dir: str | Path) -> list[Video]:
    """Videos declared in ``video.json`` next to a saved match (used by samples).

    File entries must live under the match directory (relative paths only after resolve).
    Absolute paths and ``..`` escapes are ignored so a crafted sidecar cannot register
    arbitrary local files for ``/_media`` streaming.
    """
    f = Path(match_dir) / "video.json"
    if not f.exists():
        return []
    doc = json.loads(f.read_text(encoding="utf-8"))
    items = doc if isinstance(doc, list) else doc.get("videos", [doc])
    root = Path(match_dir).resolve()
    out = []
    for d in items:
        d = dict(d)
        if d.get("kind") == "file" and d.get("src"):
            src = Path(d["src"])
            if src.is_absolute():
                continue
            resolved = (root / src).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            d["src"] = str(resolved)
        out.append(Video(**{k: v for k, v in d.items() if k in Video.__dataclass_fields__}))
    return out


def media_key(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
