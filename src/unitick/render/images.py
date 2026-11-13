"""Tiny dependency-free image helpers: PNG encoding and map silhouettes."""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import numpy as np


def png_bytes(rgba: np.ndarray) -> bytes:
    """Encode an (H, W, 4) uint8 array as PNG (no Pillow needed)."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def data_url(png: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(png).decode()}"


def file_data_url(path: str | Path) -> str:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return data_url(p.read_bytes(), mime)


def silhouette(px: np.ndarray, py: np.ndarray, size: int = 1024, res: int = 512,
               color=(142, 162, 188)) -> str:
    """Approximate walkable-area image from where players have been.

    Works for any game with no map assets: a blurred occupancy histogram of every
    recorded position, rendered as a soft translucent mask.
    """
    ok = np.isfinite(px) & np.isfinite(py)
    h, _, _ = np.histogram2d(py[ok], px[ok], bins=res, range=[[0, size], [0, size]])
    h = (h > 0).astype(np.float32)
    for _ in range(3):  # cheap separable box blur
        h = (np.roll(h, 1, 0) + h + np.roll(h, -1, 0)) / 3
        h = (np.roll(h, 1, 1) + h + np.roll(h, -1, 1)) / 3
    a = np.clip(h * 2.2, 0, 1)
    img = np.zeros((res, res, 4), dtype=np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = color
    img[..., 3] = (a * 70).astype(np.uint8)
    edge = (a > 0.15) & (a < 0.55)
    img[edge, 3] = 140
    return data_url(png_bytes(img))


def cache_dir() -> Path:
    import os

    d = Path(os.environ.get("UNITICK_CACHE", Path.home() / ".unitick"))
    d.mkdir(parents=True, exist_ok=True)
    return d


BUNDLED_RADARS = Path(__file__).resolve().parents[1] / "data" / "radars"


def find_radar(map_name: str | None, transform=None) -> Path | None:
    """Locate a radar image: awpy's cache for CS2, or a cached download of the
    transform's ``radar_url`` (Valorant minimaps from valorant-api.com)."""
    if not map_name:
        return None
    url = getattr(transform, "radar_url", None)
    bundled = BUNDLED_RADARS / f"{Path(url).parent.name if url else map_name}.png"
    if bundled.exists():  # shipped with unitick, works offline
        return bundled
    if url:
        dest = cache_dir() / "radars" / f"{Path(url).parent.name or map_name}.png"
        if dest.exists():
            return dest
        try:
            import urllib.request

            dest.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "unitick"})
            with urllib.request.urlopen(req, timeout=20) as r:
                dest.write_bytes(r.read())
            return dest
        except Exception:  # noqa: BLE001 - offline: fall back to the silhouette
            return None
    local = cache_dir() / "radars" / f"{map_name}.png"
    if local.exists():
        return local
    try:
        from awpy import data

        p = data.radar_path(map_name)
        return Path(p) if p and Path(p).exists() else None
    except Exception:  # noqa: BLE001 - offline / no awpy / unknown map
        return None
