"""Build reports for everything in the repo's ``samples/`` folder, plus an index page."""

from __future__ import annotations

import time
import webbrowser
from pathlib import Path


def samples_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    for cand in (Path(__file__).resolve().parents[2] / "samples", Path.cwd() / "samples"):
        if cand.is_dir():
            return cand
    raise SystemExit("unitick: no samples/ folder found (run from the repo, or pass --dir)")


def discover(root: Path, raw: bool) -> list[tuple[str, Path]]:
    """(label, path) for every sample unitick can load."""
    out: list[tuple[str, Path]] = []
    for d in sorted((root / "cs2").glob("*/meta.json")):
        out.append((f"CS2: {d.parent.name} (pre-parsed)", d.parent))
    for d in sorted((root / "valorant").glob("*/meta.json")):
        out.append((f"VALORANT: {d.parent.name} (pre-parsed)", d.parent))
    for d in sorted((root / "valorant").glob("*/manifest.json")):
        out.append((f"VALORANT: {d.parent.name}", d.parent))
    # Raw .vrf files need a native vrfkit. The repo only ships Linux and Windows builds.
    from .adapters.valorant import find_vrfkit
    if find_vrfkit():
        for f in sorted((root / "valorant").rglob("*.vrf")):
            out.append((f"VALORANT: {f.stem}", f))
    for f in sorted((root / "r6").glob("*.json")):
        out.append((f"R6: {f.stem}", f))
    if raw:
        for f in sorted((root / "cs2" / "raw").glob("*.dem")):
            out.append((f"CS2: {f.stem} (.dem)", f))
        r6raw = root / "r6" / "raw"
        for d in sorted(p for p in r6raw.glob("*") if p.is_dir() and any(p.glob("*.rec"))):
            out.append((f"R6: {d.name} (.rec match)", d))
        for f in sorted(r6raw.glob("*.rec")):
            out.append((f"R6: {f.stem} (.rec)", f))
    return out


def build(out: Path, root: Path | None = None, *, raw: bool = False, open_index: bool = False,
          log=print) -> Path:
    from .cli import load_any
    from .render import report

    root = samples_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    from .render import index as _index
    rows = []
    for label, path in discover(root, raw):
        t0 = time.perf_counter()
        name = path.stem if path.is_file() else path.name
        try:
            from . import video
            m = load_any(str(path))
            vids = video.sidecar(path) if path.is_dir() else []
            vis = None
            if m.game == "cs2":
                try:
                    from .adapters import cs2
                    vis = cs2.visibility(m.map_name)
                except Exception:  # noqa: BLE001 - no mesh: facing-only autopsy
                    vis = None
            dest = report.render(m, out / f"{m.game}_{name}.html", videos=vids, visibility=vis,
                                 back="index.html")
            s = m.summary()
            rows.append({"href": dest.name, "game": m.game, "map": s["map"], "rounds": s["rounds"],
                         "kills": s["n_kills"], "label": label.split(": ", 1)[-1].replace("_", " "),
                         "video": bool(vids)})
            log(f"[ok] {label}  ({time.perf_counter() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001 - one broken sample must not stop the rest
            rows.append({"label": label, "error": f"{type(e).__name__}: {e}"})
            log(f"[!!] {label}: {type(e).__name__}: {e}")
    index = _index.write(out, rows, title="Sample matches",
                         note="Real matches from the samples folder. Drop your own .vrf into samples/valorant to add a VALORANT game.")
    log(f"index -> {index}")
    if open_index:
        webbrowser.open(index.resolve().as_uri())
    return index
