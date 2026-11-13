"""The match list page that sits next to the reports (samples and watch mode)."""

from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path

GAME = {"cs2": "CS2", "valorant": "VALORANT", "r6": "Siege X"}


def write(folder: Path, rows: list[dict], *, title: str = "Your matches", note: str = "") -> Path:
    """rows: {href, game, map, rounds, kills, label, when, error}. Newest first is up to the caller."""
    fonts = resources.files("unitick.render").joinpath("fonts.css").read_text(encoding="utf-8")
    items = []
    for r in rows:
        if r.get("error"):
            items.append(f'<li class="err"><b>{html.escape(r.get("label", "?"))}</b>'
                         f'<span>Could not be read: {html.escape(r["error"])}</span></li>')
            continue
        mp = (r.get("map") or "").replace("de_", "").replace("_", " ").title()
        bits = [GAME.get(r.get("game", ""), r.get("game", "")), f'{r.get("rounds", 0)} rounds',
                f'{r.get("kills", 0)} kills']
        if r.get("video"):
            bits.append("with video")
        items.append(
            f'<li><a href="{html.escape(r["href"])}"><span class="mp">{html.escape(mp or "Match")}</span>'
            f'<span class="lb">{html.escape(r.get("label", ""))}</span>'
            f'<span class="mt">{html.escape(", ".join(bits))}{(", " + html.escape(r["when"])) if r.get("when") else ""}</span>'
            f'</a></li>')
    empty = '<p class="note">No reports yet. Play a match with unitick watching, or run <b>unitick report</b> on a replay.</p>'
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}: unitick</title><style>{fonts}
:root{{--bg:#0b0712;--panel:#14101c;--panel2:#1c1628;--line:#241d33;--text:#f5f1ff;--dim:#b7adc8;--atk:#FF6B3D;--def:#33C3DB;--bad:#FF5C7A;
--display:'Big Shoulders Display',Impact,sans-serif;--cond:'Barlow Condensed',sans-serif;--body:'Barlow',system-ui,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:17px/1.5 var(--body)}}
main{{max-width:980px;margin:0 auto;padding:36px 24px 60px}}
.wordmark{{font:900 26px var(--display);letter-spacing:.02em}}.wordmark i{{font-style:normal;color:#a78bfa}}
h1{{font:900 clamp(40px,6vw,72px)/.95 var(--display);text-transform:uppercase;margin:28px 0 10px}}
.note{{color:var(--dim);max-width:62ch;margin:0 0 28px}}
ul{{list-style:none;padding:0;margin:0;display:grid;gap:8px}}
a{{display:grid;grid-template-columns:minmax(120px,190px) 1fr auto;gap:18px;align-items:baseline;padding:14px 18px;background:var(--panel);color:var(--text);text-decoration:none;border-left:4px solid var(--def)}}
a:hover{{background:var(--panel2)}}a:focus-visible{{outline:2px solid var(--text);outline-offset:2px}}
.mp{{font:800 26px/1 var(--display);text-transform:uppercase}}.lb{{font:600 18px var(--cond)}}.mt{{font:500 15px var(--cond);color:var(--dim);text-align:right}}
li.err{{padding:14px 18px;background:var(--panel);border-left:4px solid var(--bad)}}li.err span{{display:block;color:var(--dim);font-size:15px}}
@media (max-width:640px){{a{{grid-template-columns:1fr}}.mt{{text-align:left}}}}
</style></head><body><main><div class="wordmark">uni<i>tick</i></div><h1>{html.escape(title)}</h1>
{f'<p class="note">{html.escape(note)}</p>' if note else ""}{"<ul>" + "".join(items) + "</ul>" if items else empty}</main></body></html>"""
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


def rescan(folder: Path, *, title: str = "Your matches", note: str = "") -> Path:
    """Rebuild the list from the .meta sidecars written next to each report."""
    rows = []
    for m in sorted(folder.glob("*.html.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(m.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    rows = [r for r in rows if (folder / r.get("href", "")).exists()]
    return write(folder, rows, title=title, note=note)


def record(report_path: Path, match, **extra) -> None:
    """Write the small sidecar rescan() reads."""
    s = match.summary()
    row = {"href": report_path.name, "game": match.game, "map": s.get("map"), "rounds": s.get("rounds"),
           "kills": s.get("n_kills"), **extra}
    Path(str(report_path) + ".json").write_text(json.dumps(row), encoding="utf-8")
