"""View-angle geometry shared by aim analytics. Angles in degrees."""

from __future__ import annotations

import numpy as np


def wrap180(a):
    """Wrap angles to (-180, 180]."""
    a = np.asarray(a, dtype=np.float64)
    return (a + 180.0) % 360.0 - 180.0


def view_vector(pitch, yaw) -> np.ndarray:
    """Unit forward vector for (pitch positive-down, yaw ccw from +x)."""
    p = np.radians(np.asarray(pitch, dtype=np.float64))
    y = np.radians(np.asarray(yaw, dtype=np.float64))
    cp = np.cos(p)
    return np.stack([cp * np.cos(y), cp * np.sin(y), -np.sin(p)], axis=-1)


def required_angles(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(pitch, yaw) that points from ``src`` (N,3) to ``dst`` (N,3)."""
    d = np.asarray(dst, dtype=np.float64) - np.asarray(src, dtype=np.float64)
    yaw = np.degrees(np.arctan2(d[..., 1], d[..., 0]))
    horiz = np.hypot(d[..., 0], d[..., 1])
    pitch = -np.degrees(np.arctan2(d[..., 2], horiz))
    return pitch, yaw


def angular_error(pitch, yaw, src, dst) -> dict[str, np.ndarray]:
    """Crosshair error from the aim (pitch, yaw) at ``src`` to the target ``dst``.

    Returns total great-circle error plus signed components:
    ``yaw_err`` (+ = aiming left of target) and ``pitch_err`` (+ = aiming below).
    """
    req_p, req_y = required_angles(src, dst)
    v = view_vector(pitch, yaw)
    t = view_vector(req_p, req_y)
    cos = np.clip(np.sum(v * t, axis=-1), -1.0, 1.0)
    return {
        "error_deg": np.degrees(np.arccos(cos)),
        "yaw_err": wrap180(np.asarray(yaw, dtype=np.float64) - req_y),
        "pitch_err": np.asarray(pitch, dtype=np.float64) - req_p,
        "distance": np.linalg.norm(np.asarray(dst, float) - np.asarray(src, float), axis=-1),
    }
