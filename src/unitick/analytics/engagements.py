"""Turn hit-level damage into engagements: one attacker vs one victim, one fight."""

from __future__ import annotations

import polars as pl

from ..match import Match

# Weapons whose damage says nothing about crosshair placement.
NON_AIM = ("grenade", "inferno", "molotov", "incendiary", "knife", "decoy", "flash",
           "world", "bomb", "c4", "taser", "zeus", "fall", "ability")


def is_aim_weapon(col: str = "weapon") -> pl.Expr:
    w = pl.col(col).fill_null("").str.to_lowercase()
    bad = pl.lit(False)
    for token in NON_AIM:
        bad = bad | w.str.contains(token, literal=True)
    return ~bad & (w != "")


def engagements(match: Match, *, gap_s: float = 3.0, burst_gap_s: float = 0.5,
                lookback_s: float = 1.5, aim_only: bool = True) -> pl.DataFrame:
    """One row per engagement.

    Hits from the same attacker on the same victim in the same round belong to one
    engagement until a gap longer than ``gap_s``. ``first_shot_tick`` is the start of
    the attacker's shot burst that produced the first hit (shots separated by less
    than ``burst_gap_s``, at most ``lookback_s`` earlier); it falls back to the first
    hit tick when shot data is missing.
    """
    d = match.damages.filter(pl.col("attacker_id").is_not_null() & pl.col("victim_id").is_not_null()
                             & (pl.col("attacker_side") != pl.col("victim_side")))
    if aim_only:
        d = d.filter(is_aim_weapon())
    schema = {"engagement_id": pl.UInt32, "round_num": pl.Int32, "attacker_id": pl.String,
              "attacker_name": pl.String, "attacker_side": pl.String, "victim_id": pl.String,
              "victim_name": pl.String, "victim_side": pl.String, "start_tick": pl.Int32,
              "end_tick": pl.Int32, "first_shot_tick": pl.Int32, "hits": pl.UInt32,
              "damage": pl.Int64, "weapon": pl.String, "killed": pl.Boolean}
    if d.is_empty():
        return _from_kills(match, schema)
    gap = match.ticks(gap_s)
    pair = ["round_num", "attacker_id", "victim_id"]
    d = d.sort([*pair, "tick"]).with_columns(
        ((pl.col("tick").diff().over(pair).fill_null(gap + 1)) > gap).cum_sum().over(pair)
        .alias("_seg"))
    e = (d.group_by([*pair, "_seg"], maintain_order=True)
         .agg(pl.col("attacker_name").first(), pl.col("attacker_side").first(),
              pl.col("victim_name").first(), pl.col("victim_side").first(),
              pl.col("tick").min().alias("start_tick"), pl.col("tick").max().alias("end_tick"),
              pl.len().alias("hits"), pl.col("damage").sum().cast(pl.Int64),
              pl.col("weapon").mode().first())
         .drop("_seg").sort("start_tick").with_row_index("engagement_id"))

    # Kill credit: the attacker killed this victim during or just after the fight.
    k = match.kills.select("round_num", "attacker_id", "victim_id", pl.col("tick").alias("_kt"))
    e = (e.join(k, on=pair, how="left", maintain_order="left")
         .with_columns(((pl.col("_kt") >= pl.col("start_tick"))
                        & (pl.col("_kt") <= pl.col("end_tick") + match.ticks(1.0))).fill_null(False)
                       .alias("killed"))
         .group_by("engagement_id", maintain_order=True)
         .agg(pl.all().exclude("killed", "_kt").first(), pl.col("killed").any()))

    e = e.with_columns(_first_shot(match, e, burst_gap_s, lookback_s).alias("first_shot_tick"))
    return e.select(list(schema)).cast(schema)


def _first_shot(match: Match, e: pl.DataFrame, burst_gap_s: float, lookback_s: float) -> pl.Series:
    s = match.shots.select(pl.col("player_id").alias("attacker_id"), pl.col("tick").alias("_st"))
    if s.is_empty():
        return e["start_tick"]
    burst, look = match.ticks(burst_gap_s), match.ticks(lookback_s)
    cand = (e.select("engagement_id", "attacker_id", "start_tick")
            .join(s, on="attacker_id")
            .filter((pl.col("_st") <= pl.col("start_tick"))
                    & (pl.col("_st") >= pl.col("start_tick") - look))
            .sort(["engagement_id", "_st"], descending=[False, True]))
    # Walk back from the hit while consecutive shots are within the burst gap.
    cand = cand.with_columns(
        (pl.col("_st").shift(1).over("engagement_id").fill_null(pl.col("start_tick"))
         - pl.col("_st")).alias("_gap"))
    cand = cand.with_columns((pl.col("_gap") > burst).cum_sum().over("engagement_id").alias("_brk"))
    first = (cand.filter(pl.col("_brk") == 0).group_by("engagement_id")
             .agg(pl.col("_st").min().alias("_fs")))
    out = e.select("engagement_id", "start_tick").join(first, on="engagement_id", how="left", maintain_order="left")
    return out.select(pl.coalesce("_fs", "start_tick"))["_fs"].cast(pl.Int32)


def _from_kills(match: Match, schema: dict) -> pl.DataFrame:
    """Engagements from kills alone, for sources without hit-level damage (VALORANT
    replays, R6). One engagement per kill; ``first_shot_tick`` = kill tick."""
    k = match.kills.filter(pl.col("attacker_id").is_not_null() & pl.col("victim_id").is_not_null()
                           & (pl.col("attacker_side") != pl.col("victim_side")))
    if k.is_empty():
        return pl.DataFrame(schema=schema)
    return (k.sort("tick").with_row_index("engagement_id").select(
        "engagement_id", "round_num", "attacker_id", "attacker_name", "attacker_side", "victim_id",
        "victim_name", "victim_side", pl.col("tick").alias("start_tick"), pl.col("tick").alias("end_tick"),
        pl.col("tick").alias("first_shot_tick"), pl.lit(1, pl.UInt32).alias("hits"),
        pl.lit(None, pl.Int64).alias("damage"), "weapon", pl.lit(True).alias("killed"))
        .cast(schema).with_columns(pl.lit("kill").alias("source")))
