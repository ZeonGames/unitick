"""Bring any game's exported telemetry into unitick with a declarative spec.

Use this for Valorant, Deadlock, Rocket League, a custom engine, or anything that
already exports CSV / Parquet / JSON tables. You describe how source columns map to
canonical ones, which values mean attack/defense, the time unit, and the angle
convention. No code per game.

Example spec (JSON or a dict)::

    {
      "game": "valorant",
      "map_name": "Ascent",
      "tick_rate": 128,
      "time_unit": "ms",
      "sides": {"Attacker": "attack", "Defender": "defense"},
      "angles": {"yaw_offset": 0, "yaw_sign": 1, "pitch_sign": 1},
      "eye_height": {"stand": 160, "crouch": 110},
      "tables": {
        "kills": {"path": "kills.csv",
                  "columns": {"time_ms": "tick", "killer_puuid": "attacker_id", ...}}
      }
    }
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import polars as pl

from ..maps import MapTransform
from ..match import TABLES, Match
from ..schema import SCHEMAS

TIME_COLS = ("tick", "start_tick", "live_tick", "end_tick")
_UNIT = {"tick": None, "ticks": None, "s": 1.0, "seconds": 1.0, "ms": 1e-3}


def read_any(path: str | Path) -> pl.DataFrame:
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".parquet":
        return pl.read_parquet(path)
    if suf in (".csv", ".tsv"):
        return pl.read_csv(path, separator="\t" if suf == ".tsv" else ",", infer_schema_length=10000)
    if suf in (".ndjson", ".jsonl"):
        return pl.read_ndjson(path)
    if suf == ".json":
        return pl.read_json(path)
    raise ValueError(f"unsupported table format: {path}")


def _convert(df: pl.DataFrame, table: str, spec: Mapping) -> pl.DataFrame:
    cols = (spec.get("columns") or {}) if isinstance(spec, Mapping) else {}
    if cols:
        df = df.rename({k: v for k, v in cols.items() if k in df.columns})
    return df


def from_tables(
    tables: Mapping[str, pl.DataFrame | str | Path | Mapping],
    *,
    game: str,
    map_name: str | None = None,
    tick_rate: float = 64.0,
    time_unit: str = "tick",
    sides: Mapping[str, str] | None = None,
    angles: Mapping[str, float] | None = None,
    eye_height: Mapping[str, float] | None = None,
    map_transform: MapTransform | Mapping | None = None,
    base_dir: str | Path | None = None,
    **meta,
) -> Match:
    """Build a canonical ``Match`` from arbitrary tables.

    ``tables`` maps canonical table names to a DataFrame, a file path, or a dict
    ``{"path" | "data": ..., "columns": {source: canonical}}``.
    """
    if time_unit not in _UNIT:
        raise ValueError(f"time_unit must be one of {sorted(_UNIT)}")
    unknown = set(tables) - set(TABLES)
    if unknown:
        raise ValueError(f"unknown tables {sorted(unknown)}; expected {TABLES}")
    side_map = {str(k).lower(): v for k, v in (sides or {}).items()}
    ang = {"yaw_offset": 0.0, "yaw_sign": 1.0, "pitch_sign": 1.0, "pitch_offset": 0.0,
           **(angles or {})}
    base = Path(base_dir) if base_dir else None
    scale = _UNIT[time_unit]

    out: dict[str, pl.DataFrame] = {}
    for name, src in tables.items():
        spec: Mapping = {}
        if isinstance(src, Mapping):
            spec = src
            src = spec.get("data", spec.get("path"))
        if isinstance(src, (str, Path)):
            p = Path(src)
            src = read_any(base / p if base and not p.is_absolute() else p)
        df = _convert(src, name, spec)
        schema = SCHEMAS[name]
        exprs = []
        for c in df.columns:
            if c not in schema:
                continue
            e = pl.col(c)
            if c in TIME_COLS and scale is not None:
                e = (e.cast(pl.Float64) * scale * tick_rate).round()
            elif c.endswith("_id"):
                e = e.cast(pl.String)
            elif c == "side" or c.endswith("_side"):
                if side_map:
                    e = e.cast(pl.String).str.to_lowercase().replace_strict(side_map, default=None)
            elif c == "yaw" or c.endswith("_yaw"):
                e = e.cast(pl.Float64) * ang["yaw_sign"] + ang["yaw_offset"]
            elif c == "pitch" or c.endswith("_pitch"):
                e = e.cast(pl.Float64) * ang["pitch_sign"] + ang["pitch_offset"]
            exprs.append(e.alias(c))
        df = df.with_columns(exprs)
        if name == "frames" and "is_alive" not in df.columns and "health" in df.columns:
            df = df.with_columns((pl.col("health") > 0).alias("is_alive"))
        out[name] = df

    m = {"game": game, "map_name": map_name, "tick_rate": float(tick_rate), **meta}
    if eye_height:
        m["eye_height"] = dict(eye_height)
    if map_transform is not None:
        m["map_transform"] = (map_transform.to_dict() if isinstance(map_transform, MapTransform)
                              else dict(map_transform))
    if "rounds" not in out:
        out["rounds"] = _infer_rounds(out)
    return Match.build(m, **out)


def _infer_rounds(tables: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    """Single pseudo-round spanning everything, for round-less games (deathmatch, MOBA)."""
    ticks = [df[c] for df in tables.values() for c in ("tick", "start_tick") if c in df.columns]
    if not ticks:
        return pl.DataFrame({"round_num": [1], "start_tick": [0], "end_tick": [0]})
    allt = pl.concat([t.cast(pl.Int64) for t in ticks])
    return pl.DataFrame({"round_num": [1], "start_tick": [int(allt.min())],
                         "live_tick": [int(allt.min())], "end_tick": [int(allt.max())]})


def from_spec(spec: str | Path | Mapping) -> Match:
    """Load from a JSON spec file (paths inside resolve relative to the spec)."""
    base = None
    if not isinstance(spec, Mapping):
        base = Path(spec).parent
        spec = json.loads(Path(spec).read_text(encoding="utf-8"))
    spec = dict(spec)
    tables = spec.pop("tables")
    return from_tables(tables, base_dir=base, **spec)
