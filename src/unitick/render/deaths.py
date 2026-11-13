"""Payload for the Deaths tab: autopsy per death, what other players did in the same spot,
and the what-if: survival odds for every play the player could have made."""

from __future__ import annotations

import numpy as np
import polars as pl

from ..analytics import autopsy as _autopsy
from ..analytics import situations as S
from ..analytics import whatif as W
from ..match import Match

LEAD_S = 1.5      # look at the situation this long before the death, before the fight starts
STATS_K = 12      # matches used for the "what others did" numbers
SHOW_K = 3        # matches drawn as minimaps


def _px(t, x, y):
    px, py = t.to_pixels(np.asarray(x, float), np.asarray(y, float))
    return np.round(px, 1).tolist(), np.round(py, 1).tolist()


def _heading(t, yaw):
    return [round(float(t.heading(a)), 1) if a is not None else None for a in yaw]


def build(match: Match, *, visibility=None, library: S.Library | None = None,
          pidx: dict | None = None) -> dict | None:
    if match.frames.is_empty() or match.kills.is_empty():
        return None
    a = _autopsy.autopsy(match, visibility=visibility)
    if a.is_empty():
        return None
    t = match.transform
    pidx = pidx or {}
    snaps = S.snapshots(match)
    key = S._match_key(match)
    have_lib = library is not None and not library.snaps.is_empty() and match.map_name in library.maps()
    lead = match.ticks(LEAD_S)

    deaths, owners = [], []
    for r in a.iter_rows(named=True):
        q_tick = r["tick"] - lead
        me = snaps.filter((pl.col("player_id") == r["victim_id"]) & (pl.col("round_num") == r["round_num"])
                          & (pl.col("tick") <= q_tick)).sort("tick").tail(1)
        d = {k: r[k] for k in ("round_num", "tick", "weapon", "headshot", "cause", "cause_text",
                               "shots_back", "crossfire", "tradeable")}
        d.update(victim=pidx.get(r["victim_id"], -1), killer=pidx.get(r["attacker_id"], -1),
                 victim_name=r["victim_name"], killer_name=r["attacker_name"], side=r["victim_side"],
                 exposed_s=_r(r["exposed_s"], 2), facing_err=_r(r["facing_err"], 1),
                 killer_err=_r(r["killer_err"], 1))
        # The moment of death, drawn on the minimap.
        vx, vy = _px(t, [r["vx"]], [r["vy"]]) if r["vx"] is not None else ([None], [None])
        kx, ky = _px(t, [r["kx"]], [r["ky"]]) if r["kx"] is not None else ([None], [None])
        d["at"] = {"v": [vx[0], vy[0]], "k": [kx[0], ky[0]]}
        if not me.is_empty():
            row = me.row(0, named=True)
            ctx = snaps.filter((pl.col("round_num") == row["round_num"]) & (pl.col("tick") == row["tick"]))
            d["before"] = _scene(t, ctx, row["player_id"], pidx)
            d["t_live"] = round(float(row["t_live"]), 1)
            if have_lib:
                d["others"] = _others(match, library, t, row, ctx, key)
            d["whatif"] = _whatif(match, library, row, ctx, key) if have_lib else None
        deaths.append(d)
        owners.append(r["victim_id"])

    summ = _autopsy.summary(a)
    per_player = [{**{k: s[k] for k in s if k != "victim_id"}, "pi": pidx.get(s["victim_id"], -1)}
                  for s in summ.iter_rows(named=True)] if not summ.is_empty() else []
    by_victim: dict = {}
    for vid, d in zip(owners, deaths, strict=True):
        by_victim.setdefault(vid, []).append(d.get("whatif"))
    if not summ.is_empty():  # deaths where another play had clearly better odds
        for p, vid in zip(per_player, summ["victim_id"].to_list(), strict=True):
            mine = by_victim.get(vid, [])
            p["better_play"] = W.summary(mine)["better"] if any(mine) else None
    lib_info = None
    if have_lib:
        ls = library.snaps.filter((pl.col("map") == match.map_name) & (pl.col("match_key") != key))
        ms = ls.group_by("match_key").agg(pl.col("match_label").first(), pl.col("pro").first(),
                                          pl.col("event").first())
        lib_info = {"matches": ms.sort("pro", descending=True).select("match_label", "pro", "event").to_dicts()}
    return {"deaths": deaths, "players": per_player, "causes": _autopsy.CAUSES,
            "used_los": bool(a["used_los"][0]), "library": lib_info,
            "whatif": W.summary([d.get("whatif") for d in deaths]) if have_lib else None}


def _r(v, n):
    return None if v is None else round(float(v), n)


def _scene(t, ctx: pl.DataFrame, me_id: str, pidx: dict) -> list:
    """[px, py, heading, side(0/1), player index, is_me] for every living player."""
    px, py = _px(t, ctx["x"].to_list(), ctx["y"].to_list())
    hd = _heading(t, ctx["yaw"].to_list())
    return [[px[i], py[i], hd[i], 0 if s == "attack" else 1, pidx.get(p, -1), int(p == me_id)]
            for i, (s, p) in enumerate(zip(ctx["side"].to_list(), ctx["player_id"].to_list(), strict=True))]


def _whatif(match: Match, lib: S.Library, row: dict, ctx: pl.DataFrame, key: str) -> dict | None:
    """Survival odds for holding, pushing and falling back, and which one this player chose."""
    side = row["side"]
    en = ctx.filter(pl.col("side") != side).select("x", "y").to_numpy().astype(float)
    ma = ctx.filter((pl.col("side") == side) & (pl.col("player_id") != row["player_id"])) \
        .select("x", "y").to_numpy().astype(float)
    you = S.move_kind(row["x"], row["y"], row["fx"], row["fy"], en)
    return W.odds(lib, map_name=match.map_name, side=side, pos=(row["x"], row["y"]), enemies=en, mates=ma,
                  t_live=row["t_live"], planted=row["planted"], exclude_match=key, you=you)


def _others(match: Match, lib: S.Library, t, row: dict, ctx: pl.DataFrame, key: str) -> dict | None:
    side = row["side"]
    en = ctx.filter(pl.col("side") != side).select("x", "y").to_numpy().astype(float)
    ma = ctx.filter((pl.col("side") == side) & (pl.col("player_id") != row["player_id"])) \
        .select("x", "y").to_numpy().astype(float)
    q = {"map_name": match.map_name, "side": side, "pos": (row["x"], row["y"]), "enemies": en, "mates": ma,
             "t_live": row["t_live"], "planted": row["planted"], "exclude_match": key, "k": STATS_K}
    hits = lib.search(**q, pro_only=True)
    pro = True
    if hits.is_empty():
        hits, pro = lib.search(**q), False
    if hits.is_empty():
        return None
    moves = []
    other = {"attack": "defense", "defense": "attack"}[side]
    for h in hits.iter_rows(named=True):
        enemies = lib.bodies(match.map_name, h["match_key"], h["round_num"], h["tick"], other)
        moves.append(S.move_kind(h["x"], h["y"], h["fx"], h["fy"], enemies))
    hits = hits.with_columns(pl.Series("move", moves))
    n = hits.height
    survived = int((~hits["died5"]).sum())
    by_move = []
    for mv, g in hits.group_by("move"):
        by_move.append({"move": mv[0], "n": g.height, "survived": int((~g["died5"]).sum()),
                        "kills": int(g["kills5"].sum())})
    by_move.sort(key=lambda m: (-m["n"], m["move"]))
    shown = []
    # Prefer different matches, so the examples are not three rounds of one team.
    rows = list(hits.iter_rows(named=True))
    seen, pick = set(), []
    for h in rows:
        if h["match_key"] not in seen:
            seen.add(h["match_key"])
            pick.append(h)
    pick += [h for h in rows if h not in pick]
    for h in pick[:SHOW_K]:
        c = lib.context(match.map_name, h["match_key"], h["round_num"], h["tick"])
        scene = _scene(t, c, h["pid"], {})
        fx, fy = _px(t, [h["fx"] if h["fx"] is not None else h["x"]], [h["fy"] if h["fy"] is not None else h["y"]])
        shown.append({"label": h["match_label"], "event": h["event"], "round": h["round_num"],
                      "t_live": round(float(h["t_live"]), 1), "player": h["player_name"],
                      "score": round(float(h["score"]), 2), "died": bool(h["died5"]), "kills": int(h["kills5"]),
                      "move": h["move"], "scene": scene, "to": [fx[0], fy[0]]})
    return {"n": n, "pro": pro, "survived": survived, "by_move": by_move, "shown": shown,
            "kills": int(hits["kills5"].sum())}
