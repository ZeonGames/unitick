"""Fill in bombsites that a replay did not record.

Some plants arrive without a site: VALORANT replays never name it, and the odd
CS2 bomb event has none. A plant takes the site of the nearest reference point:
first the other plants in the same match whose site is known, then any
map-level reference points supplied by the adapter (VALORANT's named A/B/C
callouts from valorant-api.com).
"""

from __future__ import annotations

import numpy as np
import polars as pl

MAX_DIST = 4000.0  # world units; a plant further than this from every reference stays unknown


def fill_missing(obj: pl.DataFrame, refs: list[tuple[float, float, str]] | None = None) -> pl.DataFrame:
    if obj.is_empty() or "site" not in obj.columns or not {"x", "y"} <= set(obj.columns):
        return obj
    known = obj.filter(pl.col("site").is_not_null() & pl.col("x").is_not_null() & (pl.col("event") == "plant"))
    pts = [(float(r["x"]), float(r["y"]), r["site"]) for r in known.iter_rows(named=True)] + list(refs or [])
    missing = obj["site"].is_null() & obj["x"].is_not_null() & (obj["event"] == "plant")
    if not pts or not missing.any():
        return obj
    ref = np.array([[p[0], p[1]] for p in pts], dtype=float)
    lab = [p[2] for p in pts]
    xs, ys = obj["x"].to_numpy(), obj["y"].to_numpy()
    sites = obj["site"].to_list()
    for i in np.nonzero(missing.to_numpy())[0]:
        d = np.hypot(ref[:, 0] - xs[i], ref[:, 1] - ys[i])
        j = int(np.argmin(d))
        if d[j] <= MAX_DIST:
            sites[i] = lab[j]
    out = obj.with_columns(pl.Series("site", sites, dtype=pl.String))
    # plant_start, defuse and explode in the same round share the plant's site
    if "round_num" in out.columns:
        site = pl.col("site").forward_fill().backward_fill().over("round_num", order_by="tick")
        out = out.with_columns(pl.coalesce(pl.col("site"), site).alias("site"))
    return out
