"""Download real replay files for the samples (stdlib only, checksums verified).

    python samples/download_samples.py            # ~140 MB: 1 CS2 demo + 4 R6 rounds
    python samples/download_samples.py --all      # every sample (~2 GB, mostly CS2 pro demos)
    python samples/download_samples.py --game r6  # one game only

Sources: CS2 demos from awpy-fixtures release assets; R6 .rec files from
r6-dissect's test data (MIT). Public VALORANT .vrf files do not exist: see README.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "unitick-samples"})
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as out:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r   {done / 1e6:7.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="download every sample, not just the default set")
    ap.add_argument("--game", choices=["cs2", "r6"], help="only this game")
    a = ap.parse_args(argv)
    items = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))["samples"]
    items = [i for i in items if (a.all or i["default"]) and (not a.game or i["game"] == a.game)]
    failed = 0
    for i in items:
        dest = HERE / i["dest"]
        if dest.exists() and sha256(dest) == i["sha256"]:
            print(f"[ok] {i['dest']} (already downloaded)")
            continue
        print(f"[..] {i['dest']}")
        try:
            fetch(i["url"], dest)
            if sha256(dest) != i["sha256"]:
                dest.unlink()
                raise ValueError("checksum mismatch")
            print(f"[ok] {i['dest']}")
        except Exception as e:  # noqa: BLE001 - report and continue with the rest
            failed += 1
            print(f"[!!] {i['dest']}: {e}", file=sys.stderr)
    print("done" if not failed else f"done, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
