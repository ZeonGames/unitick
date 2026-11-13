"""Project world coordinates onto a 2D map image or a unit square.

Two transform kinds:

* **radar** (Source / awpy convention): ``px = (x - pos_x) / scale``,
  ``py = (pos_y - y) / scale``. CS2 transforms are bundled (from awpy-data, MIT).
* **affine** (any engine): ``u = a*x + b*y + c``, ``v = d*x + e*y + f`` in unit
  coordinates, ``px = u * size``. Valorant's minimap (whose axes cross) is one of
  these; transforms for its maps are bundled from valorant-api.com.

Games without either can register a transform or fall back to a bounding box
fitted to the match itself.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import numpy as np
import polars as pl

RADAR_PX = 1024


@dataclass(frozen=True)
class MapTransform:
    name: str
    pos_x: float = 0.0
    pos_y: float = 0.0
    scale: float = 1.0
    size: int = RADAR_PX
    lower_below_z: float | None = None
    affine: tuple[float, float, float, float, float, float] | None = None
    radar_url: str | None = None

    def __post_init__(self):
        if self.affine is not None:
            a, b, _, d, e, _ = self.affine
            det = abs(a * e - b * d)
            # World units per pixel, so radii and stroke widths stay physical.
            object.__setattr__(self, "affine", tuple(float(v) for v in self.affine))
            object.__setattr__(self, "scale", 1.0 / (math.sqrt(det) * self.size) if det else 1.0)

    # ------------------------------------------------------------ projections
    def to_unit(self, x, y):
        """Normalized coordinates in [0, 1] (origin top-left, like an image)."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if self.affine is not None:
            a, b, c, d, e, f = self.affine
            return a * x + b * y + c, d * x + e * y + f
        s = self.scale * self.size
        return (x - self.pos_x) / s, (self.pos_y - y) / s

    def to_pixels(self, x, y):
        u, v = self.to_unit(x, y)
        return u * self.size, v * self.size

    def heading(self, yaw):
        """Screen-space heading in degrees (canvas convention, y down) for a world yaw."""
        r = np.radians(np.asarray(yaw, dtype=np.float64))
        c, s = np.cos(r), np.sin(r)
        if self.affine is not None:
            a, b, _, d, e, _ = self.affine
            du, dv = a * c + b * s, d * c + e * s
        else:
            du, dv = c, -s
        return np.degrees(np.arctan2(dv, du))

    def exprs(self, x: str = "x", y: str = "y", suffix: str = "_norm") -> list[pl.Expr]:
        """Polars expressions adding ``x_norm`` / ``y_norm`` columns (unit coords)."""
        if self.affine is not None:
            a, b, c, d, e, f = self.affine
            u = pl.col(x) * a + pl.col(y) * b + c
            v = pl.col(x) * d + pl.col(y) * e + f
        else:
            s = self.scale * self.size
            u = (pl.col(x) - self.pos_x) / s
            v = (self.pos_y - pl.col(y)) / s
        return [u.cast(pl.Float32).alias(f"{x}{suffix}"), v.cast(pl.Float32).alias(f"{y}{suffix}")]

    def is_lower(self, z) -> np.ndarray:
        z = np.asarray(z, dtype=np.float64)
        if self.lower_below_z is None:
            return np.zeros_like(z, dtype=bool)
        return z < self.lower_below_z

    # ------------------------------------------------------------------- io
    def to_dict(self) -> dict:
        return {"name": self.name, "pos_x": self.pos_x, "pos_y": self.pos_y, "scale": self.scale,
                "size": self.size, "lower_below_z": self.lower_below_z,
                "affine": list(self.affine) if self.affine else None, "radar_url": self.radar_url}

    @classmethod
    def from_dict(cls, d: dict) -> MapTransform:
        aff = d.get("affine")
        return cls(d.get("name", "map"), d.get("pos_x", 0.0), d.get("pos_y", 0.0), d.get("scale", 1.0),
                   size=d.get("size", RADAR_PX), lower_below_z=d.get("lower_below_z"),
                   affine=tuple(aff) if aff else None, radar_url=d.get("radar_url"))

    @classmethod
    def fit(cls, xs, ys, name: str = "fitted", pad: float = 0.05) -> MapTransform:
        """Square bounding box around observed positions (for games without radar data)."""
        xs = np.asarray(xs, dtype=np.float64)
        ys = np.asarray(ys, dtype=np.float64)
        xs, ys = xs[np.isfinite(xs)], ys[np.isfinite(ys)]
        if xs.size == 0:
            return cls(name, 0.0, 1.0, 1.0 / RADAR_PX)
        cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
        half = max(xs.max() - xs.min(), ys.max() - ys.min(), 1e-6) * (0.5 + pad)
        return cls(name, cx - half, cy + half, 2 * half / RADAR_PX)


_REGISTRY: dict[str, MapTransform] = {}


@lru_cache(maxsize=1)
def _bundled() -> dict[str, MapTransform]:
    out: dict[str, MapTransform] = {}
    data = resources.files("unitick.data")
    for name, d in json.loads(data.joinpath("cs2_maps.json").read_text(encoding="utf-8")).items():
        out[name] = MapTransform(name, d["pos_x"], d["pos_y"], d["scale"],
                                 lower_below_z=d.get("lower_below_z"))
    for key, d in json.loads(data.joinpath("valorant_maps.json").read_text(encoding="utf-8")).items():
        t = MapTransform(d["name"], affine=tuple(d["affine"]), radar_url=d.get("icon"))
        # Findable by internal name (Bonsai), display name (Split) and full map URL.
        for k in (key, d["name"], d["map_url"]):
            out[k] = t
    return out


def register(transform: MapTransform) -> None:
    """Register a transform so ``get(name)`` finds it."""
    _REGISTRY[transform.name] = transform


def get(name: str | None) -> MapTransform | None:
    if not name:
        return None
    return _REGISTRY.get(name) or _bundled().get(name)


def known_maps() -> list[str]:
    return sorted(set(_bundled()) | set(_REGISTRY))
