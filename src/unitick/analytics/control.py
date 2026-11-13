"""Map control without map files: who can reach and who can see each part of the map.

awpy answers this for CS2 using Valve's nav mesh and collision geometry. unitick learns
the walkable map from the replays themselves: every position any player has ever stood
on (this match plus every match in the situation library on the same map) becomes a
walkable cell. That works for any game that records positions, VALORANT included, with
no game assets.

On that grid, for a set of living players:

* **reach**   multi-source BFS from each team. A cell belongs to the team that can get
              there first; within ``margin`` cells it is contested. Burning molotovs or
              incendiaries are impassable.
* **vision**  rays cast through each player's view cone. Rays stop at cells nobody has
              ever walked on (walls, in practice) and inside active smokes.

The report draws both live on the 2D replay and plots the reach share over every round.
Grids are 2D, so on maps with stacked floors (Nuke, Vertigo) the floors merge.
"""

from __future__ import annotations

import base64
from collections import deque

import numpy as np
import polars as pl

from ..maps import MapTransform

GRID = 160


def walkable(points_px: np.ndarray, *, n: int = GRID, size: float = 1024.0, close: int = 1) -> np.ndarray:
    """Boolean n×n grid (row = y) of cells that any observed position fell in.

    ``close`` applies a morphological closing (dilate then erode) to fill the small
    gaps left by sampling, without growing into walls.
    """
    g = np.zeros((n, n), dtype=bool)
    if len(points_px):
        p = np.asarray(points_px, dtype=np.float64)
        p = p[np.isfinite(p).all(axis=1)]
        ix = np.clip((p[:, 0] / size * n).astype(int), 0, n - 1)
        iy = np.clip((p[:, 1] / size * n).astype(int), 0, n - 1)
        g[iy, ix] = True
    for _ in range(close):
        g = _dilate(g)
    for _ in range(close):
        g = ~_dilate(~g)
    return g


def _dilate(g: np.ndarray) -> np.ndarray:
    out = g.copy()
    out[1:, :] |= g[:-1, :]
    out[:-1, :] |= g[1:, :]
    out[:, 1:] |= g[:, :-1]
    out[:, :-1] |= g[:, 1:]
    return out


def grid_for(match, library=None, *, n: int = GRID) -> tuple[np.ndarray, dict]:
    """Learn the walkable grid for a match's map from the match and the library."""
    t: MapTransform = match.transform
    xs, ys = [], []
    if not match.frames.is_empty():
        f = match.frames.filter(pl.col("is_alive").fill_null(True)).select("x", "y")
        xs.append(f["x"].to_numpy())
        ys.append(f["y"].to_numpy())
    n_lib = 0
    if library is not None and not library.snaps.is_empty():
        s = library.snaps.filter(pl.col("map") == match.map_name).select("x", "y", "match_key")
        if not s.is_empty():
            n_lib = s["match_key"].n_unique()
            xs.append(s["x"].to_numpy())
            ys.append(s["y"].to_numpy())
    if not xs:
        return np.zeros((n, n), dtype=bool), {"positions": 0, "matches": 0}
    x, y = np.concatenate(xs).astype(float), np.concatenate(ys).astype(float)
    px, py = t.to_pixels(x, y)
    g = walkable(np.column_stack([px, py]), n=n, size=t.size)
    return g, {"positions": len(x), "matches": n_lib + 1}


def pack(grid: np.ndarray) -> str:
    return base64.b64encode(np.packbits(grid.ravel())).decode("ascii")


def unpack(s: str, n: int = GRID) -> np.ndarray:
    return np.unpackbits(np.frombuffer(base64.b64decode(s), dtype=np.uint8))[: n * n].reshape(n, n).astype(bool)


def _cells(grid: np.ndarray, pts_px, size: float) -> list[tuple[int, int]]:
    n = grid.shape[0]
    out = []
    for x, y in pts_px:
        c = (min(n - 1, max(0, int(y / size * n))), min(n - 1, max(0, int(x / size * n))))
        if not grid[c]:  # snap to the nearest walkable cell
            ys, xs = np.nonzero(grid)
            if not len(ys):
                continue
            j = int(np.argmin((ys - c[0]) ** 2 + (xs - c[1]) ** 2))
            c = (int(ys[j]), int(xs[j]))
        out.append(c)
    return out


def distance(grid: np.ndarray, sources: list[tuple[int, int]], blocked: np.ndarray | None = None) -> np.ndarray:
    """8-neighbour BFS steps from the nearest source; -1 where unreachable."""
    n = grid.shape[0]
    ok = grid & ~blocked if blocked is not None else grid
    d = np.full(grid.shape, -1, dtype=np.int32)
    q = deque()
    for c in sources:
        if d[c] < 0:
            d[c] = 0
            q.append(c)
    nb = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    while q:
        r, c = q.popleft()
        nd = d[r, c] + 1
        for dr, dc in nb:
            rr, cc = r + dr, c + dc
            if 0 <= rr < n and 0 <= cc < n and ok[rr, cc] and d[rr, cc] < 0:
                d[rr, cc] = nd
                q.append((rr, cc))
    return d


def reach(grid: np.ndarray, attack_px, defense_px, *, size: float = 1024.0, margin: int = 2,
          blocked: np.ndarray | None = None) -> np.ndarray:
    """Per cell: 0 attack gets there first, 1 defense, 2 contested, -1 not walkable/unreachable."""
    da = distance(grid, _cells(grid, attack_px, size), blocked)
    dd = distance(grid, _cells(grid, defense_px, size), blocked)
    out = np.full(grid.shape, -1, dtype=np.int8)
    big = 10**6  # unreachable; small enough that adding the margin cannot overflow
    a = np.where(da < 0, big, da).astype(np.int64)
    b = np.where(dd < 0, big, dd).astype(np.int64)
    reachable = (a < big) | (b < big)
    out[reachable & (a + margin < b)] = 0
    out[reachable & (b + margin < a)] = 1
    out[reachable & (np.abs(a - b) <= margin)] = 2
    return out


def shares(owner: np.ndarray) -> dict:
    """Fraction of reachable cells owned by attack, defense and contested."""
    tot = max(1, int((owner >= 0).sum()))
    return {"attack": round(float((owner == 0).sum()) / tot, 3),
            "defense": round(float((owner == 1).sum()) / tot, 3),
            "contested": round(float((owner == 2).sum()) / tot, 3)}


def payload(match, library=None) -> dict | None:
    """What the report needs: the packed grid and where it came from."""
    if match.frames.is_empty():
        return None
    g, info = grid_for(match, library)
    if g.sum() < 50:
        return None
    return {"n": int(g.shape[0]), "cells": pack(g), **info}
