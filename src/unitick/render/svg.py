"""Static SVG playbook sketches of executes (StratSketch output)."""

from __future__ import annotations

from html import escape

import numpy as np
import polars as pl

from ..analytics import executes as ex
from ..match import Match
from ..schema import ATTACK

PLAYER_COLORS = ["#FF6B3D", "#F5C451", "#B78CFF", "#5BE3A8", "#FF8FB8", "#7FD1FF", "#FFFFFF"]
SIDE_COLOR = {"attack": "#FF6B3D", "defense": "#33C3DB"}
UTIL_RADIUS = {"smoke": 144.0, "fire": 120.0, "flash": 40.0, "he": 60.0, "decoy": 30.0}
UTIL_STYLE = {
    "smoke": 'fill="#d8dee9" fill-opacity="0.38" stroke="#eceff4" stroke-opacity="0.7"',
    "fire": 'fill="#ff6a00" fill-opacity="0.35" stroke="#ff8c33"',
    "flash": 'fill="#ffffff" fill-opacity="0.85" stroke="#fff6c2"',
    "he": 'fill="#ff3b3b" fill-opacity="0.4" stroke="#ff6b6b"',
    "decoy": 'fill="#888" fill-opacity="0.4" stroke="#aaa"',
}


def _poly(px, py) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(px, py, strict=True))


def execute_svg(match: Match, round_num: int, *, window_s: float = 20.0,
                background: str | None = None, width: int = 520, title: str | None = None,
                side: str = ATTACK) -> str:
    """Render one execute as a self-contained SVG string."""
    t = match.transform
    paths = ex.execute_paths(match, round_num, window_s=window_s, side=side)
    plant = ex.plants(match).filter(pl.col("round_num") == round_num)
    if paths.is_empty() or plant.is_empty():
        return ""
    p0 = plant.row(0, named=True)
    w0 = int(paths["tick"].min())
    util = match.utility.filter((pl.col("round_num") == round_num) & (pl.col("side") == side)
                                & pl.col("start_tick").is_between(w0, p0["plant_tick"]))
    kills = match.kills.filter((pl.col("round_num") == round_num)
                               & pl.col("tick").is_between(w0, p0["plant_tick"]))

    xs, ys = [], []
    lines, ends = [], []
    names = sorted(paths["player_name"].unique().drop_nulls().to_list())
    for i, name in enumerate(names):
        pp = paths.filter(pl.col("player_name") == name).sort("tick")
        px, py = t.to_pixels(pp["x"].to_numpy(), pp["y"].to_numpy())
        if len(px) < 2:
            continue
        xs += [px.min(), px.max()]
        ys += [py.min(), py.max()]
        c = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        lines.append(f'<polyline points="{_poly(px, py)}" fill="none" stroke="{c}" stroke-width="__SW24__" '
                     f'stroke-linejoin="round" stroke-linecap="round" opacity="0.95" '
                     f'marker-end="url(#arr{i % len(PLAYER_COLORS)})"/>'
                     f'<circle cx="{px[0]:.1f}" cy="{py[0]:.1f}" r="__R3__" fill="{c}"/>')
        ends.append((px[-1], py[-1], name, c))
    ulines = []
    for u in util.iter_rows(named=True):
        ux, uy = t.to_pixels(u["x"], u["y"])
        ux, uy = float(ux), float(uy)
        xs.append(ux)
        ys.append(uy)
        r = UTIL_RADIUS.get(u["kind"], 40.0) / t.scale
        if u["origin_x"] is not None:
            ox, oy = (float(v) for v in t.to_pixels(u["origin_x"], u["origin_y"]))
            ulines.append(f'<line x1="{ox:.1f}" y1="{oy:.1f}" x2="{ux:.1f}" y2="{uy:.1f}" '
                          f'stroke="#e5e9f0" stroke-opacity="0.45" stroke-width="__SW1__" '
                          f'stroke-dasharray="__DASH__"/>')
        ulines.append(f'<circle cx="{ux:.1f}" cy="{uy:.1f}" r="{r:.1f}" '
                      f'{UTIL_STYLE.get(u["kind"], UTIL_STYLE["decoy"])} stroke-width="__SW1__"/>')
    kmarks = []
    for k in kills.iter_rows(named=True):
        if k["victim_x"] is None:
            continue
        kx, ky = (float(v) for v in t.to_pixels(k["victim_x"], k["victim_y"]))
        c = SIDE_COLOR.get(k["victim_side"], "#ccc")
        kmarks.append(f'<g transform="translate({kx:.1f},{ky:.1f}) scale(__K__)" stroke="{c}" '
                      f'stroke-width="2.2" stroke-linecap="round"><line x1="-5" y1="-5" x2="5" y2="5"/>'
                      f'<line x1="-5" y1="5" x2="5" y2="-5"/></g>')
    bx, by = (float(v) for v in t.to_pixels(p0["x"], p0["y"]))
    xs.append(bx)
    ys.append(by)

    pad = 40.0
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    span = max(x1 - x0, y1 - y0, 260.0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    vb = (cx - span / 2, cy - span / 2, span, span)
    k = span / width  # keep strokes visually constant

    markers = "".join(
        f'<marker id="arr{i}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
        for i, c in enumerate(PLAYER_COLORS))
    bg = (f'<image href="{background}" x="0" y="0" width="{t.size}" height="{t.size}" '
          f'opacity="0.9"/>' if background else "")
    label_fs = 11 * k
    labels = "".join(
        f'<text x="{x + 7 * k:.1f}" y="{y - 6 * k:.1f}" font-size="{label_fs:.1f}" fill="{c}" '
        f'paint-order="stroke" stroke="#09060f" stroke-width="{3 * k:.1f}">{escape(n)}</text>'
        for x, y, n, c in ends)
    site = f"{p0['site']} execute" if p0.get("site") else "Execute"
    ttl = title or f"Round {round_num}, {site}, last {int(window_s)}s"
    bomb = (f'<g transform="translate({bx:.1f},{by:.1f}) scale({k:.3f})">'
            f'<rect x="-7" y="-7" width="14" height="14" transform="rotate(45)" fill="#ff3b3b" '
            f'stroke="#fff" stroke-width="1.5"/><text y="24" text-anchor="middle" font-size="12" '
            f'fill="#fff" font-weight="700">{escape(str(p0.get("site") or ""))}</text></g>')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb[0]:.1f} {vb[1]:.1f} {vb[2]:.1f} '
        f'{vb[3]:.1f}" width="{width}" height="{width}" style="background:#09060f;'
        f'font-family:&quot;Barlow Condensed&quot;,sans-serif;font-weight:600">'
        f"<title>{escape(ttl)}</title><defs>{markers}</defs>{bg}"
        f'<g>{"".join(ulines)}</g><g>{"".join(lines)}</g>'
        f'{"".join(kmarks)}{bomb}{labels}</svg>'
    )
    tokens = {"__K__": k, "__SW24__": 2.4 * k, "__SW1__": 1.0 * k, "__R3__": 3.0 * k}
    for tok, val in tokens.items():
        svg = svg.replace(tok, f"{val:.2f}")
    return svg.replace("__DASH__", f"{3 * k:.1f} {3 * k:.1f}")


def error_strip(per_engagement: pl.DataFrame, player_id: str, width: int = 150,
                max_deg: float = 20.0) -> str:
    """Tiny dot strip of one player's crosshair errors (0 .. max_deg)."""
    e = per_engagement.filter(pl.col("attacker_id") == player_id)["error_deg"].to_numpy()
    e = np.clip(e, 0, max_deg)
    dots = "".join(
        f'<circle cx="{4 + v / max_deg * (width - 8):.1f}" cy="{9 + (i % 3 - 1) * 3}" r="2.6" '
        f'fill="{"#5BE3A8" if v < 5 else "#F5C451" if v < 10 else "#FF5C7A"}" fill-opacity="0.85"/>'
        for i, v in enumerate(e))
    ticks = "".join(f'<line x1="{4 + d / max_deg * (width - 8):.1f}" y1="1" '
                    f'x2="{4 + d / max_deg * (width - 8):.1f}" y2="17" stroke="#332a47"/>'
                    for d in (0, 5, 10, 15, 20))
    return f'<svg viewBox="0 0 {width} 18" width="{width}" height="18">{ticks}{dots}</svg>'
