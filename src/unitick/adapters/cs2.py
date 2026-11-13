"""Counter-Strike 2 adapter, built on awpy 3 (Rust-backed demo parser)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl

from ..match import Match
from ..schema import ATTACK, DEFENSE

SIDE_MAP = {"terrorist": ATTACK, "counter-terrorist": DEFENSE, "t": ATTACK, "ct": DEFENSE}
TEAM_NUM = {2: ATTACK, 3: DEFENSE}

EYE_STAND = 64.0
EYE_CROUCH = 46.0

_BOMB_EVENTS = {"start_plant": "plant_start", "finish_plant": "plant", "defuse": "defuse"}
_GRENADE_KIND = {"flashbang": "flash", "he": "he", "smoke": "smoke", "molotov": "fire",
                 "incendiary": "fire", "decoy": "decoy"}


def _require_awpy():
    try:
        import awpy  # noqa: F401
        from awpy import Demo
    except ImportError as e:  # pragma: no cover
        raise ImportError("CS2 support needs awpy: pip install 'unitick[cs2]'") from e
    return Demo


def _side(col: str) -> pl.Expr:
    return pl.col(col).str.to_lowercase().replace_strict(SIDE_MAP, default=None)


def _pid(steamid: str, name: str) -> pl.Expr:
    """Stable player id. Bots share SteamID 0, so they fall back to their name."""
    sid = pl.col(steamid)
    return (
        pl.when(sid.is_null() | (sid == 0))
        .then(pl.when(pl.col(name).is_not_null()).then(pl.lit("bot:") + pl.col(name)))
        .otherwise(sid.cast(pl.String))
    )


def _actor(prefix: str, pos: bool = True) -> list[pl.Expr]:
    out = [
        _pid(f"{prefix}_steamid", f"{prefix}_name").alias(f"{prefix}_id"),
        pl.col(f"{prefix}_name"),
        _side(f"{prefix}_side").alias(f"{prefix}_side"),
    ]
    if pos:
        out += [pl.col(f"{prefix}_{a}") for a in "xyz"]
    return out


def _rounds(demo, include_knife: bool) -> pl.DataFrame:
    r = demo.rounds
    if not include_knife and "is_knife_round" in r.columns:
        r = r.filter(~pl.col("is_knife_round").fill_null(False))
    r = r.sort("round_num").filter(pl.col("end_tick").is_not_null())
    # Demos recorded from mid-round have no start or freeze-end for the first round:
    # start at the previous round's end, or the first tick of the recording.
    prev_end = pl.col("end_tick").shift(1).fill_null(0)
    return r.select(
        "round_num",
        pl.coalesce("start_tick", "freeze_end_tick", prev_end).alias("start_tick"),
        pl.coalesce("freeze_end_tick", "start_tick", prev_end).alias("live_tick"),
        "end_tick",
        pl.col("winner").replace_strict(TEAM_NUM, default=None).alias("winner_side"),
        pl.col("reason_name").alias("reason"),
        pl.col("official_end_tick") if "official_end_tick" in r.columns
        else pl.lit(None, pl.Int32).alias("official_end_tick"),
    )


def _clip_to_rounds(df: pl.DataFrame, rounds: pl.DataFrame, tick: str = "tick",
                    post_round_ticks: int = 0) -> pl.DataFrame:
    """Keep rows inside [start_tick, end_tick + post_round_ticks] and label round_num."""
    if df.is_empty():
        return df.with_columns(pl.lit(None, pl.Int32).alias("round_num"))
    end = pl.col("end_tick") + post_round_ticks
    if "official_end_tick" in rounds.columns:
        # Post-round kills / damage belong to the round until it officially ends.
        end = pl.max_horizontal(end, pl.col("official_end_tick").fill_null(end))
    b = rounds.select(
        pl.col("round_num").alias("_rn"),
        pl.col("start_tick").cast(pl.Int64).alias("_s"),
        end.cast(pl.Int64).alias("_e"),
    ).sort("_s")
    out = (
        df.with_columns(pl.col(tick).cast(pl.Int64).alias("_t")).sort("_t")
        .join_asof(b, left_on="_t", right_on="_s", strategy="backward")
        .filter(pl.col("_t") <= pl.col("_e"))
        .with_columns(pl.col("_rn").cast(pl.Int32).alias("round_num"))
        .drop("_t", "_rn", "_s", "_e")
    )
    return out


def _frames(demo, rounds: pl.DataFrame, every: int) -> pl.DataFrame:
    rounds = rounds.drop("official_end_tick", strict=False)
    snap = demo.snapshots(every=every, start_tick=int(rounds["start_tick"].min()),
                          end_tick=int(rounds["end_tick"].max()))
    snap = snap.select(
        "tick", _pid("steamid", "name").alias("player_id"), pl.col("name").alias("player_name"),
        _side("side").alias("side"), "x", "y", "z", "pitch", "yaw", "health",
        (pl.col("health") > 0).alias("is_alive"), pl.col("is_crouched"),
        pl.col("velocity"), pl.col("active_weapon"),
    ).filter(pl.col("side").is_not_null() & pl.col("player_id").is_not_null())
    return _clip_to_rounds(snap, rounds, post_round_ticks=int(demo.tick_rate * 2))


_THROW_WEAPON = {"weapon_hegrenade": "he", "weapon_smokegrenade": "smoke",
                 "weapon_flashbang": "flashbang", "weapon_incgrenade": "molotov",
                 "weapon_molotov": "molotov", "weapon_decoy": "decoy"}


def _projectiles(demo) -> pl.DataFrame:
    """One row per grenade projectile with origin, landing, and resolved thrower.

    awpy leaves ``thrower_*`` null on many GOTV demos, so throwers are attributed by
    matching the projectile's first trajectory point to the nearest same-type
    ``weapon_fire`` (throw) event in the preceding half second.
    """
    g = demo.grenades
    if g.is_empty():
        return pl.DataFrame()
    key = ["entity_id", "entity_serial"]
    proj = (g.sort("tick").group_by(key).agg(
        pl.col("type").first(),
        pl.col("tick").first().alias("t0"), pl.col("tick").last().alias("t1"),
        pl.col("x").first().alias("origin_x"), pl.col("y").first().alias("origin_y"),
        pl.col("z").first().alias("origin_z"),
        pl.col("x").last(), pl.col("y").last(), pl.col("z").last(),
        pl.col("thrower_name").drop_nulls().first(), pl.col("thrower_steamid").drop_nulls().first(),
        pl.col("thrower_side").drop_nulls().first()))
    throws = (demo.shots.filter(pl.col("weapon").is_in(list(_THROW_WEAPON)))
              .select(pl.col("tick").alias("_tt"),
                      pl.col("weapon").replace_strict(_THROW_WEAPON).alias("type"),
                      pl.col("steamid").alias("_sid"), pl.col("name").alias("_sname"),
                      pl.col("side").alias("_sside"), pl.col("x").alias("_sx"),
                      pl.col("y").alias("_sy"), pl.col("z").alias("_sz")))
    half = int(demo.tick_rate * 0.5)
    if not throws.is_empty():
        cand = (proj.select(*key, "type", "t0", "origin_x", "origin_y", "origin_z")
                .join(throws, on="type")
                .filter(pl.col("_tt").is_between(pl.col("t0") - half, pl.col("t0") + 2))
                .with_columns(((pl.col("_sx") - pl.col("origin_x")) ** 2
                               + (pl.col("_sy") - pl.col("origin_y")) ** 2).sqrt().alias("_d"))
                .filter(pl.col("_d") < 250)
                .sort(pl.col("_d") + (pl.col("t0") - pl.col("_tt")).abs() * 4)
                .group_by(key).first()
                .select(*key, "_sid", "_sname", "_sside"))
        proj = proj.join(cand, on=key, how="left", maintain_order="left").with_columns(
            pl.coalesce("thrower_steamid", "_sid").alias("thrower_steamid"),
            pl.coalesce("thrower_name", "_sname").alias("thrower_name"),
            pl.coalesce("thrower_side", "_sside").alias("thrower_side"),
        ).drop("_sid", "_sname", "_sside")
    return proj


def _link_area(df: pl.DataFrame, proj: pl.DataFrame, types: list[str], tick_rate: float) -> pl.DataFrame:
    """Attach thrower + origin to smoke / fire areas via their projectile.

    Smokes share the projectile entity; infernos do not, so they are matched to the
    projectile that landed closest in time and space.
    """
    key = ["entity_id", "entity_serial"]
    cols = ["thrower_name", "thrower_steamid", "thrower_side", "origin_x", "origin_y", "origin_z"]
    p = proj.filter(pl.col("type").is_in(types))
    df = df.with_row_index("_area")
    by_key = df.select("_area", *key).join(p.select(*key, *cols), on=key)
    win = int(tick_rate * 1.0)
    by_land = (df.select("_area", "start_tick", "x", "y")
               .join(p.select("t1", pl.col("x").alias("_px"), pl.col("y").alias("_py"), *cols),
                     how="cross")
               .filter(pl.col("t1").is_between(pl.col("start_tick") - win, pl.col("start_tick") + 4))
               .with_columns(((pl.col("_px") - pl.col("x")) ** 2
                              + (pl.col("_py") - pl.col("y")) ** 2).sqrt().alias("_d"))
               .filter(pl.col("_d") < 300).sort("_d").group_by("_area").first()
               .select("_area", *cols))
    link = pl.concat([by_key.select("_area", *cols), by_land], how="vertical_relaxed") \
        .group_by("_area", maintain_order=True).first()
    out = df.drop([c for c in cols if c in df.columns]).join(link, on="_area", how="left",
                                                             maintain_order="left")
    return out.drop("_area")


def _utility(demo) -> pl.DataFrame:
    proj = _projectiles(demo)
    parts = []

    def emit(df: pl.DataFrame, kind: pl.Expr, start: str, end: str) -> None:
        parts.append(df.select(
            pl.col(start).alias("start_tick"), pl.col(end).alias("end_tick"),
            _pid("thrower_steamid", "thrower_name").alias("player_id"),
            pl.col("thrower_name").alias("player_name"), _side("thrower_side").alias("side"),
            kind.alias("kind"), "x", "y", "z", "origin_x", "origin_y", "origin_z"))

    if not proj.is_empty():
        # Flash / HE / decoy detonate where their trajectory ends.
        burst = proj.filter(pl.col("type").is_in(["flashbang", "he", "decoy"]))
        emit(burst, pl.col("type").replace_strict(_GRENADE_KIND, default="other"), "t1", "t1")
    for name, kind, types in (("smokes", "smoke", ["smoke"]), ("fires", "fire", ["molotov"])):
        df = getattr(demo, name)
        if df.is_empty():
            continue
        if not proj.is_empty():
            df = _link_area(df, proj, types, demo.tick_rate)
        else:
            df = df.with_columns(*[pl.lit(None, pl.Float32).alias(f"origin_{a}") for a in "xyz"])
        emit(df, pl.lit(kind), "start_tick", "end_tick")
    if not parts:
        return pl.DataFrame()
    return pl.concat(parts, how="vertical_relaxed").sort("start_tick")


def _objectives(demo, rounds_raw: pl.DataFrame) -> pl.DataFrame:
    b = demo.bomb
    parts = []
    if not b.is_empty():
        parts.append(
            b.filter(pl.col("event").is_in(list(_BOMB_EVENTS)))
            .select("tick", pl.col("event").replace_strict(_BOMB_EVENTS).alias("event"),
                    _pid("steamid", "name").alias("player_id"), pl.col("name").alias("player_name"),
                    pl.col("bombsite").alias("site"), "x", "y", "z"))
    boom = rounds_raw.filter(pl.col("reason") == "target_bombed")
    if not boom.is_empty():
        parts.append(boom.select(pl.col("end_tick").alias("tick"), pl.lit("explode").alias("event")))
    if not parts:
        return pl.DataFrame()
    return pl.concat(parts, how="diagonal_relaxed").sort("tick")


def _fill_sites(obj: pl.DataFrame) -> pl.DataFrame:
    """Within each round, give plant_start/defuse/explode the site of the plant."""
    site = (pl.col("site").forward_fill().backward_fill()).over("round_num", order_by="tick")
    return obj.with_columns(pl.coalesce(pl.col("site"), site).alias("site"))


def load(path: str | Path, *, frame_every: int = 4, include_knife: bool = False) -> Match:
    """Parse a CS2 ``.dem`` into a canonical ``Match``.

    ``frame_every`` is the snapshot stride in ticks (4 = 16 Hz at 64 tick). Lower is
    smoother replays and more precise aim metrics at the cost of memory.
    """
    Demo = _require_awpy()
    demo = Demo(str(path))
    demo.load("rounds", "kills", "damages", "shots", "bomb", "smokes", "fires", "grenades")
    return from_demo(demo, frame_every=frame_every, include_knife=include_knife,
                     source=str(path))


def from_demo(demo, *, frame_every: int = 4, include_knife: bool = False,
              source: str | None = None) -> Match:
    rounds = _rounds(demo, include_knife)
    if rounds.is_empty():
        raise ValueError("demo has no rounds")
    grace = int(demo.tick_rate * 6)  # fallback when official_end_tick is missing

    kills = _clip_to_rounds(
        demo.kills.select("tick", *_actor("attacker"), *_actor("victim"),
                          *_actor("assister", pos=False), "weapon", "headshot"),
        rounds, post_round_ticks=grace)
    damages = _clip_to_rounds(
        demo.damages.select("tick", *_actor("attacker"), *_actor("victim"), "weapon",
                            "dmg_health", "health_post",
                            pl.col("hitgroup_name").alias("hitgroup")),
        rounds, post_round_ticks=grace)
    # Canonical damage is *effective* damage: capped at the victim's health before
    # the hit (previous reported health in the round, 100 at round start).
    prev = (pl.col("health_post").shift(1).over(["round_num", "victim_id"], order_by="tick")
            .fill_null(100))
    damages = damages.with_columns(
        pl.min_horizontal("dmg_health", prev).clip(lower_bound=0).alias("damage")
    ).drop("dmg_health", "health_post")
    shots = _clip_to_rounds(
        demo.shots.select("tick", _pid("steamid", "name").alias("player_id"),
                          pl.col("name").alias("player_name"), _side("side").alias("side"),
                          "x", "y", "z", "pitch", "yaw", "weapon"),
        rounds, post_round_ticks=grace)
    utility = _utility(demo)
    if not utility.is_empty():
        utility = _clip_to_rounds(utility, rounds, tick="start_tick", post_round_ticks=grace)
    objectives = _objectives(demo, rounds)
    if not objectives.is_empty():
        objectives = _fill_sites(_clip_to_rounds(objectives, rounds, post_round_ticks=grace))
        from ..analytics.sites import fill_missing
        objectives = fill_missing(objectives)
    rounds = rounds.drop("official_end_tick")

    teams = {}
    try:
        pl_df = demo.players
        if "team_clan_name" in pl_df.columns:
            for row in pl_df.drop_nulls("team_clan_name").iter_rows(named=True):
                teams.setdefault(row["team_clan_name"], []).append(row["name"])
    except Exception:  # noqa: BLE001, S110 - roster is cosmetic
        pass

    meta = {
        "game": "cs2",
        "map_name": demo.header.get("map_name"),
        "tick_rate": float(demo.tick_rate),
        "source": source,
        "frame_every": frame_every,
        "server": demo.header.get("server_name"),
        "teams": teams,
        "eye_height": {"stand": EYE_STAND, "crouch": EYE_CROUCH},
    }
    return Match.build(
        meta,
        rounds=rounds,
        frames=_frames(demo, rounds, frame_every),
        kills=kills,
        damages=damages,
        shots=shots,
        utility=utility,
        objectives=objectives,
    )


def bundled_mesh(map_name: str) -> Path | None:
    """Unpack the line-of-sight mesh shipped with unitick (once, into the cache) so CS2 works offline."""
    import lzma

    from ..render.images import cache_dir

    src = Path(__file__).resolve().parents[1] / "data" / "meshes" / f"{map_name}.mesh.xz"
    if not src.exists():
        return None
    dest = cache_dir() / "meshes" / f"{map_name}.mesh"
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(lzma.decompress(src.read_bytes()))
        tmp.replace(dest)
    return dest


def visibility(map_name_or_mesh: str | Path) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """Return ``fn(a, b) -> bool[N]`` line-of-sight checker backed by awpy's BVH.

    Accepts a map name (resolved through awpy's asset cache) or a ``.mesh`` path.
    Static geometry only: smokes, doors and wallbangs are not modelled.
    """
    from awpy import VisibilityChecker

    target = map_name_or_mesh if str(map_name_or_mesh).endswith(".mesh") else bundled_mesh(str(map_name_or_mesh))
    vc = VisibilityChecker(Path(target) if target else str(map_name_or_mesh))

    def check(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        a = np.ascontiguousarray(a, dtype=np.float64)
        b = np.ascontiguousarray(b, dtype=np.float64)
        if len(b) == 0:
            return np.zeros(0, dtype=bool)
        return vc.is_visible_many(a, b).to_numpy()

    return check
