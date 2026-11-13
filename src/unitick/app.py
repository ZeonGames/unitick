"""Local web app backend: a small JSON API over your matches, plus the built web UI.

    unitick app                 # http://localhost:8765

Endpoints (localhost only):

    GET    /api/matches              every match: samples, watch mode, uploads
    GET    /api/matches/<id>         the full report payload (built on first open, then cached)
    DELETE /api/matches/<id>         remove an uploaded match
    POST   /api/jobs                 start an upload, returns {"id": ...}
    PUT    /api/jobs/<id>/files/<n>  add a file to the upload (raw body)
    POST   /api/jobs/<id>/run        analyze the uploaded files
    GET    /api/jobs/<id>            progress: queued, parsing, analyzing, done, failed
    GET    /_media/<key>             local recordings registered by a report (Range supported)

Anything else is served from ``web/dist`` when it has been built (``npm run build``);
during development run ``npm run dev`` in ``web/`` instead, which proxies /api here.
Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from urllib.parse import unquote, urlparse

from .render.images import cache_dir

REPLAY_EXT = {".dem", ".vrf", ".rec", ".json"}
MAX_UPLOAD = 2 * 1024**3  # 2 GB per file


# Bump when summaries or payloads change shape, so caches from older versions rebuild.
SUMMARY_VERSION = 2
PAYLOAD_VERSION = 3


def _web_dist() -> Path | None:
    d = Path(__file__).resolve().parents[2] / "web" / "dist"
    return d if (d / "index.html").exists() else None


def _sid(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]


class Store:
    """Where matches come from and where their payloads are cached."""

    def __init__(self, root: Path | None = None, watch_dir: Path | None = None, samples: Path | None = None):
        self.root = Path(root or cache_dir() / "app")
        self.payloads = self.root / "payloads"
        self.uploads = self.root / "uploads"
        for d in (self.payloads, self.uploads):
            d.mkdir(parents=True, exist_ok=True)
        self.watch_dir = Path(watch_dir or Path.home() / "unitick" / "matches")
        self.samples = samples
        self.index_file = self.root / "index.json"
        self.lock = threading.RLock()
        self._index = self._read_index()

    # ---- index
    def _read_index(self) -> dict:
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_index(self) -> None:
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=1), encoding="utf-8")
        tmp.replace(self.index_file)

    def _sources(self) -> list[tuple[str, Path]]:
        """(kind, path) for every match on disk."""
        out: list[tuple[str, Path]] = []
        from .samples import discover, samples_dir

        try:
            root = samples_dir(self.samples)
            out += [("sample", p) for _, p in discover(root, raw=False)]
        except (OSError, SystemExit):
            pass
        if self.watch_dir.is_dir():
            out += [("watch", p.parent) for p in sorted(self.watch_dir.glob("*/meta.json"))]
        for job in sorted(self.uploads.glob("*/source")):
            out.append(("upload", job))
        return out

    def refresh(self) -> list[dict]:
        """Summarize any new or changed source; keep the rest from the cache."""
        with self.lock:
            seen = set()
            for kind, path in self._sources():
                sid = _sid(path)
                seen.add(sid)
                mtime = _mtime(path)
                cur = self._index.get(sid)
                if cur and cur.get("mtime") == mtime and cur.get("v") == SUMMARY_VERSION:
                    continue
                try:
                    self._index[sid] = {
                        **summarize(path),
                        "id": sid,
                        "kind": kind,
                        "path": str(path),
                        "mtime": mtime,
                        "v": SUMMARY_VERSION,
                    }
                except Exception as e:  # noqa: BLE001 - one unreadable file must not hide the rest
                    self._index[sid] = {
                        "id": sid,
                        "kind": kind,
                        "path": str(path),
                        "mtime": mtime,
                        "error": f"{type(e).__name__}: {e}",
                        "v": SUMMARY_VERSION,
                    }
            for sid in [s for s in self._index if s not in seen]:
                del self._index[sid]
            self._write_index()
            return self.listing()

    def listing(self) -> list[dict]:
        rows = []
        for r in self._index.values():
            if r.get("error"):
                continue
            # Samples that are not real matches (solo test clips, single recorded rounds) stay out of the list.
            if r["kind"] == "sample" and (r.get("kills", 0) == 0 or r.get("rounds", 0) < 3):
                continue
            rows.append({k: v for k, v in r.items() if k not in ("path", "mtime", "v")})
        order = {"valorant": 0, "cs2": 1, "r6": 2}
        return sorted(
            rows, key=lambda r: (r["kind"] != "upload", order.get(r.get("game"), 9), -r.get("added", 0))
        )

    def entry(self, sid: str) -> dict | None:
        with self.lock:
            return self._index.get(sid)

    # ---- payloads
    def payload(self, sid: str) -> Path:
        e = self.entry(sid)
        if not e or e.get("error"):
            raise KeyError(sid)
        out = self.payloads / f"{sid}.v{PAYLOAD_VERSION}.json"
        if out.exists() and out.stat().st_mtime >= e.get("mtime", 0):
            return out
        with self.lock:  # one build at a time; the parsers are memory hungry
            if out.exists() and out.stat().st_mtime >= e.get("mtime", 0):
                return out
            build_payload_file(Path(e["path"]), out, media_dir=self.root)
        return out

    def delete(self, sid: str) -> bool:
        e = self.entry(sid)
        if not e or e["kind"] != "upload":
            return False
        with self.lock:
            shutil.rmtree(Path(e["path"]).parent, ignore_errors=True)
            (self.payloads / f"{sid}.v{PAYLOAD_VERSION}.json").unlink(missing_ok=True)
            self._index.pop(sid, None)
            self._write_index()
        return True


def _mtime(path: Path) -> float:
    if path.is_dir():
        return max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()), default=path.stat().st_mtime)
    return path.stat().st_mtime


def _source_input(path: Path) -> Path:
    """An upload folder holds one replay file, or several R6 .rec files for one match."""
    if path.name != "source" or not path.is_dir():
        return path
    files = [p for p in path.iterdir() if p.suffix.lower() in REPLAY_EXT]
    if len(files) == 1:
        return files[0]
    return path


def _load(path: Path):
    from .cli import load_any

    return load_any(str(_source_input(path)))


def summarize(path: Path) -> dict:
    """What the match list shows, without building the whole report."""
    from .render.report import _map_display, _teams

    m = _load(path)
    s = m.summary()
    teams = _teams(m, m.players())
    raw_map = s.get("map") or ""
    return {
        "game": m.game,
        "map": raw_map,
        "map_display": _map_display(m) or raw_map.removeprefix("de_").replace("_", " ").title(),
        "rounds": s.get("rounds", 0),
        "kills": s.get("n_kills", 0),
        "event": _event(m),
        "teams": [{"name": t["name"], "score": t["score"], "start_side": t.get("start_side"),
                   "players": _roster(m, t["name"])} for t in teams],
        "mode": m.meta.get("mode"),
        "patch": m.meta.get("patch"),
        "added": time.time(),
    }


def _roster(m, team: str) -> list[str]:
    """Known player names for a team (pro rosters); empty for anonymous or agent-named players."""
    if m.game == "valorant":
        return []
    names = [str(n) for n in (m.meta.get("teams") or {}).get(team, [])]
    return [] if any(n.startswith("Player ") for n in names) else names


def _event(m) -> str:
    """A short label for where the match was played."""
    if m.meta.get("event"):
        return str(m.meta["event"])
    server = str(m.meta.get("server") or "")
    if "faceit" in server.lower():
        return "FACEIT"
    if server.lower().startswith("valve"):
        return "Valve matchmaking"
    return {"r6": "Siege X match", "valorant": "VALORANT match"}.get(m.game, "")


def build_payload_file(path: Path, out: Path, *, media_dir: Path) -> None:
    from .cli import _visibility
    from .render import report
    from .video import sidecar

    m = _load(path)
    vis = _visibility(m, None, True)
    src = _source_input(path)
    videos = sidecar(src) if src.is_dir() else []
    payload = report.build_payload(m, visibility=vis, videos=videos or None)
    payload["back"] = None
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(report.payload_json(payload), encoding="utf-8")
    tmp.replace(out)
    if videos:
        report._register_media(media_dir, videos)


# --------------------------------------------------------------------- uploads
class Jobs:
    def __init__(self, store: Store):
        self.store = store
        self.jobs: dict[str, dict] = {}
        self.q: Queue = Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def create(self) -> dict:
        jid = uuid.uuid4().hex[:12]
        d = self.store.uploads / jid / "source"
        d.mkdir(parents=True, exist_ok=True)
        self.jobs[jid] = {"id": jid, "state": "uploading", "files": [], "error": None, "match_id": None}
        return self.jobs[jid]

    def add_file(self, jid: str, name: str, stream, length: int) -> dict:
        job = self.jobs[jid]
        name = Path(name).name
        if Path(name).suffix.lower() not in REPLAY_EXT:
            raise ValueError("Unsupported file. Use a CS2 .dem, a VALORANT .vrf or Siege .rec files.")
        if length > MAX_UPLOAD:
            raise ValueError("File is larger than 2 GB.")
        dest = self.store.uploads / jid / "source" / name
        with open(dest, "wb") as f:
            left = length
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        job["files"].append(name)
        return job

    def run(self, jid: str) -> dict:
        job = self.jobs[jid]
        if not job["files"]:
            raise ValueError("No files uploaded.")
        job["state"] = "queued"
        self.q.put(jid)
        return job

    def _worker(self) -> None:
        while True:
            jid = self.q.get()
            job = self.jobs[jid]
            src = self.store.uploads / jid / "source"
            try:
                job["state"] = "parsing"
                self.store.refresh()
                sid = _sid(src)
                e = self.store.entry(sid)
                if not e or e.get("error"):
                    raise RuntimeError((e or {}).get("error") or "This file could not be read as a match.")
                job["state"] = "analyzing"
                self.store.payload(sid)
                job.update(state="done", match_id=sid)
            except Exception as ex:  # noqa: BLE001 - report every failure to the UI
                job.update(
                    state="failed", error=str(ex) or type(ex).__name__, trace=traceback.format_exc(limit=3)
                )


# --------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    store: Store
    jobs: Jobs
    dist: Path | None

    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str | None = None) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self) -> tuple[str, list[str]]:
        p = unquote(urlparse(self.path).path)
        return p, [s for s in p.split("/") if s]

    def do_GET(self):
        p, parts = self._route()
        try:
            if p == "/api/matches":
                return self._json(self.store.refresh())
            if len(parts) == 3 and parts[:2] == ["api", "matches"]:
                try:
                    return self._file(self.store.payload(parts[2]), "application/json")
                except KeyError:
                    return self._json({"error": "No such match."}, 404)
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                job = self.jobs.jobs.get(parts[2])
                return self._json(
                    {k: v for k, v in job.items() if k != "trace"} if job else {"error": "No such upload."},
                    200 if job else 404,
                )
            if re.fullmatch(r"/_media/[0-9a-f]{16}", p):
                return self._media(parts[1])
            return self._static(p)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        p, parts = self._route()
        try:
            if p == "/api/jobs":
                return self._json(self.jobs.create(), 201)
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "run":
                return self._json(self.jobs.run(parts[2]))
        except KeyError:
            return self._json({"error": "No such upload."}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"error": "Not found."}, 404)

    def do_PUT(self):
        _, parts = self._route()
        if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3] == "files":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return self._json(self.jobs.add_file(parts[2], parts[4], self.rfile, n))
            except KeyError:
                return self._json({"error": "No such upload."}, 404)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        return self._json({"error": "Not found."}, 404)

    def do_DELETE(self):
        _, parts = self._route()
        if len(parts) == 3 and parts[:2] == ["api", "matches"]:
            ok = self.store.delete(parts[2])
            return self._json({"deleted": ok}, 200 if ok else 404)
        return self._json({"error": "Not found."}, 404)

    def _media(self, key: str) -> None:
        reg_file = self.store.root / ".unitick_media.json"
        reg = json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else {}
        path = reg.get(key)
        if not path or not Path(path).is_file():
            return self.send_error(404)
        size = Path(path).stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and (r := re.match(r"bytes=(\d*)-(\d*)", rng)):
            start = int(r.group(1)) if r.group(1) else 0
            end = min(int(r.group(2)), size - 1) if r.group(2) else end
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _static(self, p: str) -> None:
        if not self.dist:
            body = (
                b"<!DOCTYPE html><meta charset=utf-8><title>unitick</title>"
                b"<body style='font:16px system-ui;background:#0b0712;color:#f5f1ff;padding:40px'>"
                b"<h1>unitick API is running</h1><p>Start the web app with <code>npm run dev</code> in "
                b"<code>web/</code>, or build it once with <code>npm run build</code> and reload this page.</p>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        f = (self.dist / p.lstrip("/")).resolve()
        if not str(f).startswith(str(self.dist.resolve())) or not f.is_file():
            f = self.dist / "index.html"  # client-side routes
        return self._file(f)


def make_server(port: int = 8765, store: Store | None = None) -> ThreadingHTTPServer:
    """The app server, not yet running. ``port=0`` picks a free port."""
    store = store or Store()
    handler = type("AppHandler", (Handler,), {"store": store, "jobs": Jobs(store), "dist": _web_dist()})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(port: int = 8765, open_browser: bool = True, store: Store | None = None) -> int:
    httpd = make_server(port, store)
    handler = httpd.RequestHandlerClass
    # Advertise localhost (not 127.0.0.1): YouTube blocks embeds on bare IP hosts.
    url = f"http://localhost:{httpd.server_address[1]}/"
    print(f"unitick app running at {url}  (ctrl+c to stop)")
    if not handler.dist:
        print("web UI not built yet: run `npm install && npm run dev` in web/ and open http://localhost:5173")
    elif open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0
