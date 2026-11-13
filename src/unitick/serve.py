"""Serve reports from http://localhost so embedded YouTube players and local videos work.

Browsers and YouTube restrict pages opened as ``file://``: YouTube refuses to embed
without an HTTP referrer, and an http page cannot read local files. This tiny server
fixes both: it serves the reports folder, and streams only the local recordings that
a report registered in ``.unitick_media.json`` (with Range support for seeking).
It listens on 127.0.0.1 only, but the URL uses the ``localhost`` hostname - YouTube
blocks embeds when the parent page is opened via a bare IP like 127.0.0.1.
"""

from __future__ import annotations

import json
import mimetypes
import re
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, root: Path, **kw):
        self.root = root
        super().__init__(*a, directory=str(root), **kw)

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        m = re.fullmatch(r"/_media/([0-9a-f]{16})", self.path.split("?")[0])
        if not m:
            return super().do_GET()
        reg_file = self.root / ".unitick_media.json"
        reg = json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else {}
        path = reg.get(m.group(1))
        if not path or not Path(path).is_file():
            return self.send_error(404)
        size = Path(path).stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and (r := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if r.group(1):
                start = int(r.group(1))
            if r.group(2):
                end = min(int(r.group(2)), size - 1)
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


def serve(path: Path, port: int = 8765, open_browser: bool = True) -> int:
    path = path.resolve()
    root = path if path.is_dir() else path.parent
    httpd = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, root=root))
    # localhost hostname required for YouTube embeds (127.0.0.1 is rejected by many VODs).
    url = f"http://localhost:{port}/" + ("" if path.is_dir() else path.name)
    print(f"serving {root} at {url}  (ctrl+c to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0
