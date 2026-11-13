"""Canonical, game-agnostic telemetry schema.

Every adapter (CS2, Valorant exports, Deadlock exports, synthetic) produces these
seven tables. Every analytic and renderer consumes only these tables, so one
implementation works for every game.

Conventions (adapters must convert into these):

* ``tick``      integer game tick. ``Match.meta["tick_rate"]`` converts to seconds.
* ``side``      ``"attack"`` or ``"defense"`` (CS2 T/CT, Valorant attackers/defenders).
* ``x, y, z``   world units, z up. Renderers use ``unitick.maps`` to project.
* ``yaw``       degrees, counter-clockwise from +x (atan2(dy, dx)), any range.
* ``pitch``     degrees, positive = looking down (Source / Unreal-style convention).
* ``*_id``      player identifier as a string (SteamID64, Riot PUUID, ...).
"""

from __future__ import annotations

import polars as pl

ATTACK = "attack"
DEFENSE = "defense"
SIDES = (ATTACK, DEFENSE)

_I32 = pl.Int32
_F32 = pl.Float32
_STR = pl.String
_BOOL = pl.Boolean


def _actor(prefix: str, with_pos: bool = True) -> dict[str, pl.DataType]:
    cols: dict[str, pl.DataType] = {
        f"{prefix}_id": _STR,
        f"{prefix}_name": _STR,
        f"{prefix}_side": _STR,
    }
    if with_pos:
        cols |= {f"{prefix}_x": _F32, f"{prefix}_y": _F32, f"{prefix}_z": _F32}
    return cols


SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "rounds": {
        "round_num": _I32,
        "start_tick": _I32,
        "live_tick": _I32,
        "end_tick": _I32,
        "winner_side": _STR,
        "reason": _STR,
    },
    "frames": {
        "tick": _I32,
        "round_num": _I32,
        "player_id": _STR,
        "player_name": _STR,
        "side": _STR,
        "x": _F32,
        "y": _F32,
        "z": _F32,
        "pitch": _F32,
        "yaw": _F32,
        "health": _I32,
        "is_alive": _BOOL,
        "is_crouched": _BOOL,
        "velocity": _F32,
        "active_weapon": _STR,
    },
    "kills": {
        "tick": _I32,
        "round_num": _I32,
        **_actor("attacker"),
        **_actor("victim"),
        **_actor("assister", with_pos=False),
        "weapon": _STR,
        "headshot": _BOOL,
    },
    "damages": {
        "tick": _I32,
        "round_num": _I32,
        **_actor("attacker"),
        **_actor("victim"),
        "weapon": _STR,
        "damage": _I32,
        "hitgroup": _STR,
    },
    "shots": {
        "tick": _I32,
        "round_num": _I32,
        "player_id": _STR,
        "player_name": _STR,
        "side": _STR,
        "x": _F32,
        "y": _F32,
        "z": _F32,
        "pitch": _F32,
        "yaw": _F32,
        "weapon": _STR,
    },
    "utility": {
        "start_tick": _I32,
        "end_tick": _I32,
        "round_num": _I32,
        "player_id": _STR,
        "player_name": _STR,
        "side": _STR,
        "kind": _STR,
        "x": _F32,
        "y": _F32,
        "z": _F32,
        "origin_x": _F32,
        "origin_y": _F32,
        "origin_z": _F32,
    },
    "objectives": {
        "tick": _I32,
        "round_num": _I32,
        "event": _STR,
        "player_id": _STR,
        "player_name": _STR,
        "site": _STR,
        "x": _F32,
        "y": _F32,
        "z": _F32,
    },
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "rounds": ("round_num", "start_tick", "end_tick"),
    "frames": ("tick", "player_id", "side", "x", "y"),
    "kills": ("tick", "attacker_id", "victim_id"),
    "damages": ("tick", "attacker_id", "victim_id"),
    "shots": ("tick", "player_id"),
    "utility": ("start_tick", "kind", "x", "y"),
    "objectives": ("tick", "event"),
}

# Tables whose events carry a single tick column used for round assignment.
TICK_COLUMN = {
    "frames": "tick",
    "kills": "tick",
    "damages": "tick",
    "shots": "tick",
    "utility": "start_tick",
    "objectives": "tick",
}

OBJECTIVE_EVENTS = ("plant_start", "plant", "defuse_start", "defuse", "explode")


class SchemaError(ValueError):
    """Raised when a table cannot be conformed to the canonical schema."""


def empty(table: str) -> pl.DataFrame:
    """Return an empty, correctly typed canonical table."""
    return pl.DataFrame(schema=SCHEMAS[table])


def conform(df: pl.DataFrame | None, table: str, *, strict: bool = False) -> pl.DataFrame:
    """Cast, order and complete ``df`` so it matches ``SCHEMAS[table]``.

    Missing optional columns are added as typed nulls. Missing required columns
    raise ``SchemaError``. Extra columns are dropped unless ``strict`` is False and
    they are prefixed with ``x_`` (adapter-specific extras are kept that way).
    """
    if table not in SCHEMAS:
        raise SchemaError(f"unknown table {table!r}")
    if df is None or df.width == 0:
        return empty(table)
    schema = SCHEMAS[table]
    missing = [c for c in REQUIRED[table] if c not in df.columns]
    if missing:
        raise SchemaError(f"{table}: missing required columns {missing}")
    exprs = []
    for col, dtype in schema.items():
        if col in df.columns:
            exprs.append(pl.col(col).cast(dtype, strict=strict))
        else:
            exprs.append(pl.lit(None, dtype=dtype).alias(col))
    extras = [] if strict else [c for c in df.columns if c.startswith("x_")]
    out = df.select(*exprs, *extras)
    if "side" in schema or "attacker_side" in schema:
        _check_sides(out, table)
    return out


def _check_sides(df: pl.DataFrame, table: str) -> None:
    for col in (c for c in df.columns if c == "side" or c.endswith("_side")):
        bad = df.filter(pl.col(col).is_not_null() & ~pl.col(col).is_in(SIDES))[col].unique()
        if bad.len():
            raise SchemaError(
                f"{table}.{col} has values {bad.to_list()[:5]}; expected 'attack'/'defense'"
            )


def assign_rounds(df: pl.DataFrame, rounds: pl.DataFrame, tick_col: str) -> pl.DataFrame:
    """Fill ``round_num`` from ``rounds`` intervals for rows where it is null.

    A row belongs to the round whose ``start_tick`` is the latest one at or before
    the row's tick, as long as the tick is not past that round's end (with a
    post-round grace equal to the gap to the next round).
    """
    if df.is_empty() or rounds.is_empty():
        return df
    if "round_num" in df.columns and df["round_num"].null_count() == 0:
        return df
    bounds = (
        rounds.select("round_num", "start_tick")
        .sort("start_tick")
        .rename({"round_num": "_rn", "start_tick": "_rs"})
        .with_columns(pl.col("_rs").cast(pl.Int64))
    )
    idx = "_unitick_row"
    left = df.with_row_index(idx).with_columns(pl.col(tick_col).cast(pl.Int64).alias("_t"))
    joined = left.sort("_t").join_asof(bounds, left_on="_t", right_on="_rs", strategy="backward")
    joined = joined.sort(idx)
    rn = pl.coalesce(pl.col("round_num"), pl.col("_rn")) if "round_num" in df.columns else pl.col("_rn")
    return joined.with_columns(rn.cast(pl.Int32).alias("round_num")).drop(idx, "_t", "_rn", "_rs")
