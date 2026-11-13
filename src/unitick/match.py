"""The ``Match`` container: canonical tables plus metadata, with parquet IO."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from . import maps
from .schema import SCHEMAS, TICK_COLUMN, assign_rounds, conform

TABLES = tuple(SCHEMAS)
FORMAT_VERSION = 1


@dataclass
class Match:
    """One map of one game, in the canonical unitick schema.

    ``meta`` keys: ``game`` (e.g. "cs2"), ``map_name``, ``tick_rate``, ``source``,
    optional ``map_transform`` (dict) and ``teams`` ({side_at_round_1: name}).
    """

    meta: dict
    rounds: pl.DataFrame = field(default_factory=lambda: conform(None, "rounds"))
    frames: pl.DataFrame = field(default_factory=lambda: conform(None, "frames"))
    kills: pl.DataFrame = field(default_factory=lambda: conform(None, "kills"))
    damages: pl.DataFrame = field(default_factory=lambda: conform(None, "damages"))
    shots: pl.DataFrame = field(default_factory=lambda: conform(None, "shots"))
    utility: pl.DataFrame = field(default_factory=lambda: conform(None, "utility"))
    objectives: pl.DataFrame = field(default_factory=lambda: conform(None, "objectives"))

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, meta: dict, **tables: pl.DataFrame | None) -> Match:
        """Conform raw tables, assign rounds, sort. The one constructor adapters use."""
        unknown = set(tables) - set(TABLES)
        if unknown:
            raise ValueError(f"unknown tables: {sorted(unknown)}")
        meta = {"tick_rate": 64.0, "game": "unknown", "map_name": None, **meta}
        rounds = conform(tables.get("rounds"), "rounds").sort("round_num")
        out = {"rounds": rounds}
        for name in TABLES[1:]:
            df = conform(tables.get(name), name)
            tick = TICK_COLUMN[name]
            df = assign_rounds(df, rounds, tick)
            out[name] = df.sort(tick, maintain_order=True)
        return cls(meta=meta, **out)

    # ------------------------------------------------------------------ props
    @property
    def tick_rate(self) -> float:
        return float(self.meta.get("tick_rate", 64.0))

    @property
    def game(self) -> str:
        return self.meta.get("game", "unknown")

    @property
    def map_name(self) -> str | None:
        return self.meta.get("map_name")

    def seconds(self, ticks: float) -> float:
        return ticks / self.tick_rate

    def ticks(self, seconds: float) -> int:
        return round(seconds * self.tick_rate)

    def anonymized(self, salt: str = "") -> Match:
        """A copy with every account id replaced by a stable, meaningless one.

        Replays carry real account ids (Steam ids, Riot PUUIDs, Ubisoft profile
        ids) for everyone in the match, strangers included. Use this before
        sharing a report or a saved match. Display names are left alone, so
        drop or rename them too if they are account names.
        """
        import hashlib

        ids = set()
        for t, cols in ((self.frames, ["player_id"]), (self.kills, ["attacker_id", "victim_id", "assister_id"]),
                        (self.damages, ["attacker_id", "victim_id"]), (self.shots, ["player_id"]),
                        (self.utility, ["player_id"]), (self.objectives, ["player_id"])):
            for c in cols:
                if c in t.columns:
                    ids |= set(t[c].drop_nulls().to_list())
        rs = self.meta.get("roster_sides") or []
        ids |= {r["player_id"] for r in rs if isinstance(r, dict) and r.get("player_id")}
        new = {i: "anon:" + hashlib.sha256((salt + str(i)).encode()).hexdigest()[:10] for i in ids}

        def sub(df: pl.DataFrame, cols) -> pl.DataFrame:
            return df.with_columns([pl.col(c).replace_strict(new, default=pl.col(c)) for c in cols if c in df.columns])

        meta = {k: v for k, v in self.meta.items() if k not in ("roster", "source")}
        if rs:
            meta["roster_sides"] = [{**r, "player_id": new.get(r.get("player_id"), r.get("player_id"))}
                                    for r in rs if isinstance(r, dict)]
        meta["anonymized"] = True
        return Match.build(meta, rounds=self.rounds, frames=sub(self.frames, ["player_id"]),
                           kills=sub(self.kills, ["attacker_id", "victim_id", "assister_id"]),
                           damages=sub(self.damages, ["attacker_id", "victim_id"]),
                           shots=sub(self.shots, ["player_id"]), utility=sub(self.utility, ["player_id"]),
                           objectives=sub(self.objectives, ["player_id"]))

    @property
    def transform(self) -> maps.MapTransform:
        """Best available world->radar transform (meta, bundled, or fitted)."""
        if self.meta.get("map_transform"):
            return maps.MapTransform.from_dict(self.meta["map_transform"])
        t = maps.get(self.map_name)
        if t is not None:
            return t
        src = self.frames if not self.frames.is_empty() else self.kills.rename(
            {"attacker_x": "x", "attacker_y": "y"}, strict=False)
        if "x" not in src.columns:
            return maps.MapTransform.fit([], [])
        return maps.MapTransform.fit(src["x"].to_numpy(), src["y"].to_numpy(),
                                     name=self.map_name or "fitted")

    def tables(self) -> dict[str, pl.DataFrame]:
        return {name: getattr(self, name) for name in TABLES}

    def round(self, *round_nums: int) -> Match:
        """A Match restricted to the given rounds."""
        keep = pl.col("round_num").is_in(list(round_nums))
        return Match(meta=dict(self.meta), **{n: df.filter(keep) for n, df in self.tables().items()})

    def players(self) -> pl.DataFrame:
        """One row per player: id, last name, and side in their first round."""
        if not self.frames.is_empty():
            src = self.frames
        elif self.meta.get("roster_sides"):  # event-only sources with a full roster (R6)
            rs = pl.DataFrame(self.meta["roster_sides"])
            names = {}
            for w in ("attacker", "victim"):
                names.update(dict(self.kills.select(f"{w}_id", f"{w}_name").drop_nulls().iter_rows()))
            src = rs.with_columns(pl.col("player_id").replace_strict(names, default=pl.col("player_id"))
                                  .alias("player_name"), pl.col("round_num").cast(pl.Int64).alias("tick"))
        else:  # event-only sources: everyone who appears in the kill feed
            cols = lambda w: [pl.col(f"{w}_id").alias("player_id"), pl.col(f"{w}_name").alias("player_name"),
                              pl.col(f"{w}_side").alias("side"), "round_num", "tick"]
            src = pl.concat([self.kills.select(cols("attacker")), self.kills.select(cols("victim"))])
        if src.is_empty():
            return pl.DataFrame(schema={"player_id": pl.String, "player_name": pl.String,
                                        "first_side": pl.String})
        return (
            src.drop_nulls("player_id").sort("tick")
            .group_by("player_id", maintain_order=True)
            .agg(pl.col("player_name").last(), pl.col("side").first().alias("first_side"))
        )

    def summary(self) -> dict:
        score = (self.rounds.group_by("winner_side").len().to_dicts()
                 if not self.rounds.is_empty() else [])
        return {
            "game": self.game,
            "map": self.map_name,
            "tick_rate": self.tick_rate,
            "rounds": self.rounds.height,
            "rounds_won": {d["winner_side"]: d["len"] for d in score},
            **{f"n_{n}": df.height for n, df in self.tables().items() if n != "rounds"},
        }

    def __repr__(self) -> str:
        s = self.summary()
        return (f"Match(game={s['game']!r}, map={s['map']!r}, rounds={s['rounds']}, "
                f"frames={s['n_frames']}, kills={s['n_kills']})")

    # --------------------------------------------------------------------- io
    def save(self, path: str | Path) -> Path:
        """Write a directory of parquet files plus ``meta.json``."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        for name, df in self.tables().items():
            df.write_parquet(path / f"{name}.parquet", compression="zstd")
        meta = {**self.meta, "unitick_format": FORMAT_VERSION}
        (path / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> Match:
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        tables = {}
        for name in TABLES:
            f = path / f"{name}.parquet"
            tables[name] = pl.read_parquet(f) if f.exists() else None
        return cls.build(meta, **tables)
