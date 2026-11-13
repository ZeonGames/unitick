"""Game-agnostic fight analytics: trades, opening duels, and a player scoreboard."""

from __future__ import annotations

import polars as pl

from ..match import Match

DEFAULT_TRADE_WINDOW_S = {"cs2": 5.25, "valorant": 3.0, "deadlock": 5.0}


def trades(match: Match, *, window_s: float | None = None) -> pl.DataFrame:
    """Kills annotated with ``is_trade`` and ``victim_traded``.

    Kill J *trades* kill K when J's victim is K's attacker, J's attacker is K's
    victim's teammate, both happen in the same round, and J comes within
    ``window_s`` after K. Each J trades only the earliest qualifying K, and each K
    is traded at most once (matching awpy's definition for CS2).
    """
    if window_s is None:
        window_s = DEFAULT_TRADE_WINDOW_S.get(match.game, 5.0)
    w = match.ticks(window_s)
    k = (match.kills.with_row_index("kill_id")
         .with_columns(pl.col("tick").cast(pl.Int64)))
    if k.is_empty():
        return k.with_columns(pl.lit(False).alias("is_trade"), pl.lit(False).alias("victim_traded"))
    left = k.select("kill_id", "round_num", "tick", "attacker_id", "attacker_side").rename(
        {"kill_id": "j", "tick": "tj", "attacker_id": "j_att", "attacker_side": "j_side"})
    right = k.select("kill_id", "round_num", "tick", "attacker_id", "victim_side").rename(
        {"kill_id": "k", "tick": "tk", "attacker_id": "k_att", "victim_side": "k_vside"})
    jv = k.select(pl.col("kill_id").alias("j"), pl.col("victim_id").alias("j_vic"))
    cand = (left.join(jv, on="j").join(right, left_on=["round_num", "j_vic"],
                                       right_on=["round_num", "k_att"])
            .filter((pl.col("k") != pl.col("j")) & (pl.col("j_side") == pl.col("k_vside"))
                    & (pl.col("tj") >= pl.col("tk")) & (pl.col("tj") - pl.col("tk") <= w)
                    & ((pl.col("tj") > pl.col("tk")) | (pl.col("j") > pl.col("k"))))
            .sort(["j", "tk", "k"]))
    # Greedy one-to-one matching in kill order (cheap: a handful of candidates per round).
    used: set[int] = set()
    is_trade: set[int] = set()
    for j, kk in cand.select("j", "k").iter_rows():
        if j in is_trade or kk in used:
            continue
        is_trade.add(j)
        used.add(kk)
    return k.with_columns(
        pl.col("kill_id").is_in(list(is_trade)).alias("is_trade"),
        pl.col("kill_id").is_in(list(used)).alias("victim_traded"),
    ).with_columns(pl.col("tick").cast(pl.Int32))


def openings(match: Match) -> pl.DataFrame:
    """The first kill of every round, with whether that side went on to win."""
    k = match.kills.filter(pl.col("attacker_id").is_not_null()
                           & (pl.col("attacker_side") != pl.col("victim_side")))
    first = k.sort("tick").group_by("round_num", maintain_order=True).first()
    return first.join(match.rounds.select("round_num", "winner_side"), on="round_num", how="left") \
        .with_columns((pl.col("attacker_side") == pl.col("winner_side")).alias("opener_side_won")) \
        .select("round_num", "tick", "attacker_id", "attacker_name", "attacker_side",
                "victim_id", "victim_name", "weapon", "opener_side_won")


def scoreboard(match: Match, *, trade_window_s: float | None = None) -> pl.DataFrame:
    """Per-player K / D / A, ADR, HS%, KAST, opening duels and trades."""
    k = trades(match, window_s=trade_window_s)
    rounds_played = match.rounds.height or 1
    enemy = pl.col("attacker_side") != pl.col("victim_side")
    kills = (k.filter(enemy & pl.col("attacker_id").is_not_null()).group_by("attacker_id")
             .agg(pl.len().alias("kills"), pl.col("headshot").fill_null(False).mean().alias("hs"),
                  pl.col("is_trade").sum().alias("trade_kills"))
             .rename({"attacker_id": "player_id"}))
    deaths = (k.group_by("victim_id")
              .agg(pl.len().alias("deaths"), pl.col("victim_traded").sum().alias("traded_deaths"))
              .rename({"victim_id": "player_id"}))
    assists = (k.filter(pl.col("assister_id").is_not_null()).group_by("assister_id").len("assists")
               .rename({"assister_id": "player_id"}))
    dmg = (match.damages.filter(pl.col("attacker_side") != pl.col("victim_side"))
           .group_by("attacker_id").agg(pl.col("damage").sum().alias("_dmg"))
           .rename({"attacker_id": "player_id"}))
    op = openings(match)
    op_w = op.group_by("attacker_id").len("opening_kills").rename({"attacker_id": "player_id"})
    op_l = op.group_by("victim_id").len("opening_deaths").rename({"victim_id": "player_id"})

    # KAST: rounds with a Kill, Assist, Survived, or Traded death.
    per_round = []
    for col, flag in (("attacker_id", "k"), ("assister_id", "a")):
        per_round.append(k.filter(pl.col(col).is_not_null()).select(
            "round_num", pl.col(col).alias("player_id")).unique().with_columns(pl.lit(flag).alias("f")))
    per_round.append(k.filter(pl.col("victim_traded")).select(
        "round_num", pl.col("victim_id").alias("player_id")).unique().with_columns(pl.lit("t").alias("f")))
    players = match.players().select("player_id", "player_name", "first_side")
    present = (match.frames.select("round_num", "player_id").unique()
               if not match.frames.is_empty() else
               match.rounds.select("round_num").join(players.select("player_id"), how="cross"))
    died = k.select("round_num", pl.col("victim_id").alias("player_id")).unique()
    survived = present.join(died, on=["round_num", "player_id"], how="anti").with_columns(
        pl.lit("s").alias("f"))
    per_round.append(survived)
    kast = (pl.concat(per_round).select("round_num", "player_id").unique()
            .group_by("player_id").len("_kast_rounds"))
    rp = present.group_by("player_id").len("rounds")

    out = players
    for t in (rp, kills, deaths, assists, dmg, op_w, op_l, kast):
        out = out.join(t, on="player_id", how="left", maintain_order="left")
    fill = ["kills", "deaths", "assists", "trade_kills", "traded_deaths", "opening_kills",
            "opening_deaths", "_kast_rounds", "_dmg"]
    out = out.with_columns(pl.col(fill).fill_null(0), pl.col("rounds").fill_null(rounds_played))
    has_damage = not match.damages.is_empty()
    adr = (pl.col("_dmg") / pl.col("rounds")).round(1) if has_damage else pl.lit(None, pl.Float64)
    # Some replays (VALORANT) record neither headshots nor assists: report unknown, not zero.
    has_hs = "headshot" in match.kills.columns and match.kills["headshot"].null_count() < match.kills.height
    has_assists = "assister_id" in match.kills.columns and match.kills["assister_id"].null_count() < match.kills.height
    hs = (pl.col("hs").fill_null(0) * 100).round(1) if has_hs else pl.lit(None, pl.Float64)
    out = out if has_assists else out.with_columns(pl.lit(None, pl.UInt32).alias("assists"))
    return (out.with_columns(
        adr.alias("adr"),
        hs.alias("hs_pct"),
        (pl.col("_kast_rounds") / pl.col("rounds") * 100).round(1).alias("kast"),
        (pl.col("kills").cast(pl.Int64) - pl.col("deaths").cast(pl.Int64)).alias("diff"),
    ).drop("hs", "_dmg", "_kast_rounds").sort(["adr", "kills"], descending=True, nulls_last=True))
