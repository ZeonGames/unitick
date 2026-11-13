"""Build the pro situation library from public HLTV demos.

Source: the Hugging Face dataset ``aldigobbler/cs2demos`` (177 CS2 demos from hltv.org).
Each demo is downloaded, parsed, turned into situation snapshots and deleted again, so
only the compact library (a few MB) stays on disk.

    python samples/build_pro_library.py                 # default Mirage + Inferno set
    python samples/build_pro_library.py --maps mirage --limit 5
    python samples/build_pro_library.py --files astralis-vs-betboom-m1-mirage.dem

Output: samples/library/cs2_pro.parquet (merged), plus one part per demo in
samples/library/parts/ so a rerun skips what it already has.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
OUT = HERE / "library"
REPO = "aldigobbler/cs2demos"
API = f"https://huggingface.co/api/datasets/{REPO}/tree/main"
RAW = f"https://huggingface.co/datasets/{REPO}/resolve/main/"


def listing() -> list[dict]:
    with urllib.request.urlopen(API, timeout=60) as r:
        return [f for f in json.load(r) if f["path"].endswith(".dem")]


def label_of(name: str) -> tuple[str, str]:
    """'astralis-vs-betboom-m1-mirage.dem' -> ('Astralis vs Betboom', 'de_mirage')."""
    m = re.match(r"(.+)-vs-(.+)-m\d-([a-z0-9]+)", name)
    if not m:
        return name, ""
    a, b, mp = m.groups()
    pretty = lambda s: " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in s.split("-"))
    return f"{pretty(a)} vs {pretty(b)}", f"de_{mp}"


def build_one(name: str, parts: Path) -> Path | None:
    from unitick.adapters import cs2
    from unitick.analytics.situations import snapshots

    out = parts / (Path(name).stem + ".parquet")
    if out.exists():
        return out
    label, _ = label_of(name)
    with tempfile.TemporaryDirectory() as tmp:
        dem = Path(tmp) / name
        t = time.time()
        urllib.request.urlretrieve(RAW + name, dem)
        dl = time.time() - t
        m = cs2.load(dem, frame_every=8)
        m.meta.update(event="HLTV pro match", match_id=f"hltv:{Path(name).stem}", result=label)
        s = snapshots(m)
    if s.is_empty():
        return None
    s = s.with_columns(pl.lit(True).alias("pro"), pl.lit(f"hltv.org via {REPO}").alias("event"))
    s.write_parquet(out, compression="zstd")
    print(f"  {name}: {m.rounds.height} rounds, {s.height} snapshots, "
          f"download {dl:.0f}s, total {time.time() - t:.0f}s", flush=True)
    return out


def merge(parts: Path) -> Path:
    files = sorted(parts.glob("*.parquet"))
    lib = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
    dest = OUT / "cs2_pro.parquet"
    lib.write_parquet(dest, compression="zstd", compression_level=19)
    print(f"library: {len(files)} matches, {lib.height} snapshots -> {dest} "
          f"({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--maps", nargs="*", default=["mirage", "inferno"])
    ap.add_argument("--limit", type=int, default=10, help="demos per map")
    ap.add_argument("--files", nargs="*", help="exact file names from the dataset")
    a = ap.parse_args(argv)
    parts = OUT / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    if a.files:
        names = a.files
    else:
        files = listing()
        names = []
        for mp in a.maps:
            # whole maps only (skip "-p1"/"-p2" split demos), smallest first to keep it quick
            pick = sorted((f for f in files if re.search(rf"-m\d-{mp}\.dem$", f["path"])), key=lambda f: f["size"])
            names += [f["path"] for f in pick[: a.limit]]
    for n in names:
        try:
            build_one(n, parts)
        except Exception as e:  # noqa: BLE001 - keep going, report
            print(f"  {n}: failed ({e})", file=sys.stderr, flush=True)
    merge(parts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
