import json
import math
import os
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from unitick import Match, maps
from unitick.adapters import synthetic, table, valorant
from unitick.analytics import crosshair, engagements, executes, fights, geometry
from unitick.render import images, report, svg
from unitick.schema import SCHEMAS, SchemaError, conform


@pytest.fixture(scope="module")
def m():
    return synthetic.generate(12)


# ---------------------------------------------------------------- schema / io
def test_conform_adds_missing_and_rejects_bad_sides():
    df = pl.DataFrame({"tick": [1], "player_id": [7], "side": ["attack"], "x": [0.0], "y": [1.0]})
    out = conform(df, "frames")
    assert out.columns == list(SCHEMAS["frames"])
    assert out["player_id"].dtype == pl.String and out["pitch"].is_null().all()
    with pytest.raises(SchemaError):
        conform(df.with_columns(pl.lit("T").alias("side")), "frames")
    with pytest.raises(SchemaError):
        conform(df.drop("x"), "frames")


def test_round_trip(tmp_path, m):
    m.save(tmp_path / "m")
    m2 = Match.load(tmp_path / "m")
    assert m2.summary() == m.summary()
    assert m2.frames.equals(m.frames)


def test_round_slice(m):
    r = m.round(3)
    assert r.rounds.height == 1 and set(r.kills["round_num"]) == {3}


# ---------------------------------------------------------------- maps / geometry
def test_cs2_transform_bundled():
    t = maps.get("de_mirage")
    assert t is not None and t.scale == 5.0
    px, py = t.to_pixels(t.pos_x + 500, t.pos_y - 250)
    assert (float(px), float(py)) == (100.0, 50.0)
    assert "de_nuke" in maps.known_maps() and maps.get("de_nuke").lower_below_z is not None


def test_fit_transform_contains_points():
    xs, ys = np.array([-300.0, 900.0]), np.array([50.0, 400.0])
    u, v = maps.MapTransform.fit(xs, ys).to_unit(xs, ys)
    assert (u > 0).all() and (u < 1).all() and (v > 0).all() and (v < 1).all()


def test_angular_error_components():
    src = np.array([[0.0, 0.0, 0.0]])
    dst = np.array([[100.0, 0.0, 0.0]])
    e = geometry.angular_error([0.0], [10.0], src, dst)
    assert math.isclose(e["error_deg"][0], 10.0, abs_tol=1e-6)
    assert math.isclose(e["yaw_err"][0], 10.0, abs_tol=1e-6)
    # Target 100 below at distance 100: required pitch +45 (looking down).
    e = geometry.angular_error([0.0], [0.0], src, np.array([[100.0, 0.0, -100.0]]))
    assert math.isclose(e["pitch_err"][0], -45.0, abs_tol=1e-6)
    assert math.isclose(geometry.wrap180(190.0), -170.0)


# ---------------------------------------------------------------- analytics
def test_engagements(m):
    e = engagements.engagements(m)
    assert e.height == 24  # one shooter->a0 and one a1->shooter fight per round
    assert e["killed"].all()
    first = e.filter(pl.col("victim_id") == "a0")
    assert ((first["start_tick"] - first["first_shot_tick"]) == 4).all()


def test_crosshair_recovers_scripted_error(m):
    lb = crosshair.leaderboard(crosshair.placement(m), min_engagements=1)
    got = dict(zip(lb["player"], lb["median_err_deg"], strict=True))
    for j, err in enumerate((1.0, 3.0, 6.0, 10.0, 15.0)):
        assert math.isclose(got[f"d{j}"], err, abs_tol=0.05), (j, got)
    assert lb.filter(pl.col("player") == "d0")["cps"][0] > lb.filter(pl.col("player") == "d4")["cps"][0]


def test_crosshair_line_of_sight_mode(m):
    # Everyone can see everyone: every engagement is a full-window "held" angle.
    per = crosshair.placement(m, visibility=lambda a, b: np.ones(len(b), dtype=bool))
    assert set(per["ref_source"]) == {"held"}
    # Nobody can see anyone: falls back to reaction lead.
    per = crosshair.placement(m, visibility=lambda a, b: np.zeros(len(b), dtype=bool))
    assert set(per["ref_source"]) == {"lead"}


def test_trades_and_scoreboard(m):
    k = fights.trades(m)
    assert k["is_trade"].sum() == 12 and k["victim_traded"].sum() == 12
    sb = fights.scoreboard(m)
    a1 = sb.filter(pl.col("player_id") == "a1").row(0, named=True)
    assert a1["kills"] == 12 and a1["trade_kills"] == 12 and a1["deaths"] == 0
    assert sb["diff"].dtype == pl.Int64 and sb["diff"].min() < 0
    op = fights.openings(m)
    assert op.height == 12 and (op["victim_id"] == "a0").all()


def test_executes_and_playbook(m):
    s = executes.summary(m)
    assert s.height == 12 and (s["smokes"] == 2).all() and (s["flashes"] == 1).all()
    pb = executes.playbook(m)
    plays = pb.group_by("site").agg(pl.col("play_id").n_unique())
    assert dict(plays.iter_rows()) == {"A": 1, "B": 1}
    paths = executes.execute_paths(m, 1)
    assert paths["t_rel_s"].min() >= -20.0 and paths["t_rel_s"].max() <= 0.0
    assert (paths["round_num"] == 1).all()


def test_execute_window_never_crosses_live_start(m):
    paths = executes.execute_paths(m, 1, window_s=500)
    live = m.rounds.filter(pl.col("round_num") == 1)["live_tick"][0]
    assert paths["tick"].min() >= live


# ---------------------------------------------------------------- table adapter
def test_table_adapter_units_sides_angles(tmp_path):
    kills = pl.DataFrame({"t_ms": [1000, 2500], "killer": ["p1", "p2"], "victim": ["p2", "p1"],
                          "team_k": ["Attacker", "Defender"], "team_v": ["Defender", "Attacker"],
                          "gun": ["vandal", "phantom"]})
    frames = pl.DataFrame({"t_ms": [0, 0, 1000], "puuid": ["p1", "p2", "p1"],
                           "team": ["Attacker", "Defender", "Attacker"], "x": [0.0, 10, 5],
                           "y": [0.0, 10, 5], "yaw_raw": [90.0, 0.0, 45.0], "health": [100, 100, 0]})
    frames.write_csv(tmp_path / "frames.csv")
    spec = {
        "game": "valorant", "map_name": "Ascent", "tick_rate": 128, "time_unit": "ms",
        "sides": {"Attacker": "attack", "Defender": "defense"},
        "angles": {"yaw_sign": -1, "yaw_offset": 90},
        "tables": {
            "kills": {"data": kills, "columns": {"t_ms": "tick", "killer": "attacker_id",
                                                 "victim": "victim_id", "team_k": "attacker_side",
                                                 "team_v": "victim_side", "gun": "weapon"}},
            "frames": {"path": "frames.csv", "columns": {"t_ms": "tick", "puuid": "player_id",
                                                         "team": "side", "yaw_raw": "yaw"}},
        },
    }
    (tmp_path / "spec.json").write_text(json.dumps({**spec, "tables": {"frames": spec["tables"]["frames"]}}), encoding="utf-8")
    m = table.from_tables(spec["tables"], base_dir=tmp_path, **{k: v for k, v in spec.items() if k != "tables"})
    assert m.kills["tick"].to_list() == [128, 320]
    assert m.kills["attacker_side"].to_list() == ["attack", "defense"]
    assert m.frames["yaw"].to_list() == [0.0, 90.0, 45.0]
    assert m.frames["is_alive"].to_list() == [True, True, False]
    assert m.rounds.height == 1  # inferred pseudo-round
    # p2 kills p1 1.5 s after p1 killed p2: inside the 3 s Valorant window -> one trade.
    assert fights.trades(m)["is_trade"].sum() == 1
    m2 = table.from_spec(tmp_path / "spec.json")
    assert m2.frames.height == 3 and m2.transform.name == "Ascent"


# ---------------------------------------------------------------- rendering
def test_png_encoder_roundtrip_header():
    png = images.png_bytes(np.zeros((4, 6, 4), dtype=np.uint8))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"IHDR" in png and b"IEND" in png


def test_svg_and_report(tmp_path, m):
    s = svg.execute_svg(m, 1)
    assert s.startswith("<svg") and "polyline" in s and "__" not in s
    out = report.render(m, tmp_path / "r.html", radar=None)
    html = out.read_text(encoding="utf-8")
    assert "/*__PAYLOAD__*/" not in html and '"crosshair_mode":"reaction-lead"' in html
    assert html.count("<script") == 1 and "export function" not in html.split("<script", 1)[0]
    assert "mountReport(" in html and "/*__REPORT_JS__*/" not in html and "/*__REPORT_CSS__*/" not in html


def test_cli_synthetic(tmp_path):
    from unitick.cli import main
    assert main(["report", "synthetic", "-o", str(tmp_path / "s.html")]) == 0
    assert main(["parse", "synthetic", "-o", str(tmp_path / "m")]) == 0
    assert (tmp_path / "m" / "frames.parquet").exists()


# ---------------------------------------------------------------- real demo (opt-in)
DEMO = os.environ.get("UNITICK_DEMO")


@pytest.mark.demo
@pytest.mark.skipif(not DEMO or not Path(DEMO).exists(), reason="set UNITICK_DEMO to a CS2 .dem")
def test_real_demo_matches_awpy():
    from awpy import Demo

    from unitick.adapters import cs2

    m = cs2.load(DEMO)
    d = Demo(DEMO)
    d.load("kills", "stats")
    # Same scoreboard as awpy (awpy additionally sees warmup kills before round 1).
    sb = fights.scoreboard(m).join(d.stats.select(pl.col("name").alias("player_name"), "kills", "deaths",
                                                  "adr", "flashes_thrown"), on="player_name", suffix="_aw")
    assert (sb["kills"] == sb["kills_aw"]).all()
    assert (sb["deaths"] == sb["deaths_aw"]).all()
    assert ((sb["adr"] - sb["adr_aw"]).abs() < 0.2).all()
    flashes = m.utility.filter(pl.col("kind") == "flash").group_by("player_name").len("ours")
    fl = flashes.join(sb.select("player_name", "flashes_thrown"), on="player_name")
    assert (fl["ours"] == fl["flashes_thrown"]).all()
    # Trades agree with awpy kill-for-kill.
    ours = fights.trades(m).select("tick", "victim_name", "is_trade")
    j = ours.join(d.kills.select("tick", "victim_name", pl.col("is_trade").alias("aw")),
                  on=["tick", "victim_name"])
    assert j.height == m.kills.height and (j["is_trade"] == j["aw"]).all()


# ---------------------------------------------------------------- valorant (vrfkit export layout)
@pytest.fixture(scope="module")
def val(tmp_path_factory):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import vrf_fixture

    from unitick.adapters import valorant
    d = tmp_path_factory.mktemp("vrf")
    info = vrf_fixture.write(d)
    return valorant.from_export(d), info


def test_valorant_rounds_sides_winners(val):
    m, _ = val
    assert m.game == "valorant" and m.map_name == "Ascent" and m.tick_rate == 128.0
    assert m.rounds["winner_side"].to_list() == ["attack", "defense"]
    assert m.rounds["reason"].to_list() == ["detonate", "elimination"]
    assert (m.frames["x"] > -40000).all()  # park slot filtered
    k = m.kills
    assert k.height == 7 and (k["attacker_side"] != k["victim_side"]).all()
    r2 = k.filter(pl.col("round_num") == 2)
    assert set(r2["attacker_side"]) == {"defense"}
    plant = m.objectives.filter(pl.col("event") == "plant").row(0, named=True)
    assert plant["site"] == "B" and plant["player_name"] == "Reyna"


def test_valorant_names_from_agents(val):
    m, _ = val
    names = set(m.players()["player_name"])
    assert {"Jett", "Jett 2", "Sage", "Sova 2"} <= names


def test_valorant_flick_crosshair(val):
    m, _ = val
    per = crosshair.placement(m)
    fl = per.filter(pl.col("ref_source") == "flick")
    assert fl.height == 1
    row = fl.row(0, named=True)
    assert abs(row["error_deg"] - 30.0) < 1.5 and abs(row["reaction_s"] - 0.2) < 0.02


def test_valorant_affine_transform():
    t = maps.get("Ascent")
    assert t is not None and t.affine is not None and maps.get("Ascent") is maps.get("/Game/Maps/Ascent/Ascent")
    u, v = t.to_unit(0.0, 0.0)
    assert math.isclose(float(u), 0.813895) and math.isclose(float(v), 0.573242)
    # Axes cross: +y world moves right on screen, +x world moves up (yMultiplier < 0).
    assert float(t.to_unit(0.0, 1000.0)[0]) > float(u) and float(t.to_unit(1000.0, 0.0)[1]) < float(v)
    assert math.isclose(float(t.heading(90.0)), 0.0, abs_tol=1e-9)   # facing +y -> screen right
    assert math.isclose(float(maps.get("de_mirage").heading(90.0)), -90.0)  # facing +y -> screen up


def test_valorant_report(tmp_path, val):
    m, _ = val
    out = report.render(m, tmp_path / "v.html", radar=None)
    assert out.stat().st_size > 10_000


# ---------------------------------------------------------------- r6 (r6-dissect JSON)
R6_JSON = {
    "map": {"name": "Villa"}, "site": "2F Aviator Room", "matchID": "m1",
    "teams": [{"name": "YOUR TEAM", "won": True, "winCondition": "KilledOpponents", "role": "Attack"},
              {"name": "OPPONENTS", "won": False, "role": "Defense"}],
    "players": [{"username": u, "profileID": f"p-{u}", "teamIndex": t}
                for u, t in (("a1", 0), ("a2", 0), ("d1", 1), ("d2", 1))],
    "matchFeedback": [
        {"type": "Kill", "username": "d1", "target": "a1", "headshot": True, "timeInSeconds": 120},
        {"type": "Kill", "username": "a2", "target": "d1", "headshot": False, "timeInSeconds": 118},
        {"type": "DefuserPlantComplete", "username": "a2", "timeInSeconds": 60},
        {"type": "Kill", "username": "a2", "target": "d2", "headshot": True, "timeInSeconds": 30},
    ],
}


def test_r6_feed_and_trades():
    from unitick.adapters import r6
    m = r6.from_json({"rounds": [R6_JSON, R6_JSON]})
    assert m.rounds.height == 2 and set(m.rounds["winner_side"]) == {"attack"}
    k = m.kills.filter(pl.col("round_num") == 1).sort("tick")
    assert k["attacker_name"].to_list() == ["d1", "a2", "a2"]
    # Post-plant kill is timed on the defuser clock, after the plant.
    plant = m.objectives.filter((pl.col("event") == "plant") & (pl.col("round_num") == 1))["tick"][0]
    assert k["tick"][2] > plant > k["tick"][1]
    tr = fights.trades(m)
    assert tr.filter(pl.col("round_num") == 1)["is_trade"].to_list() == [False, True, False]
    sb = fights.scoreboard(m)
    assert sb.filter(pl.col("player_name") == "a2")["kills"][0] == 4


# ---------------------------------------------------------------- watcher
def test_watcher_processes_new_files(tmp_path, m):
    from unitick.watch import Watcher
    src = tmp_path / "games" / "valorant"
    src.mkdir(parents=True)
    calls = []

    class W(Watcher):
        def process(self, it):
            calls.append(self._archive(it))
            return tmp_path / "r.html"

    (src / "old.vrf").write_bytes(b"x" * 10)
    w = W(tmp_path / "out", {"valorant": [src]}, settle_s=0.0, log=lambda *_: None)
    assert w.baseline() == 1
    (src / "new.vrf").write_bytes(b"y" * 20)
    w.tick()
    assert calls == []          # first sighting: wait for the file to settle
    w.tick()
    assert [c.name for c in calls] == ["new.vrf"] and calls[0].exists()  # archived copy
    w.tick()
    assert len(calls) == 1      # never processed twice
    w2 = W(tmp_path / "out", {"valorant": [src]}, settle_s=0.0, log=lambda *_: None)
    w2.tick(); w2.tick()
    assert len(calls) == 1      # state persists across restarts


# ---------------------------------------------------------------- real samples shipped in samples/
SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.mark.parametrize("path", sorted((SAMPLES / "r6").glob("*.json")), ids=lambda p: p.stem)
def test_r6_real_samples_match_r6dissect_stats(path):
    """Kills / deaths / headshots agree with r6-dissect's own stats on real replays.
    unitick (like HLTV) does not credit team kills, so those are subtracted."""
    from unitick.adapters import r6
    doc = json.loads(path.read_text(encoding="utf-8"))
    m = r6.from_json(doc)
    rounds = doc.get("rounds", [doc])
    assert m.rounds.height == len(rounds)
    assert m.kills["round_num"].n_unique() == len({rd.get("roundNumber") for rd in rounds})
    sb = {r["player_name"]: r for r in fights.scoreboard(m).iter_rows(named=True)}
    team_kills = m.kills.filter(pl.col("attacker_side") == pl.col("victim_side"))
    tk = dict(team_kills.group_by("attacker_name").len().iter_rows())
    for st in doc["stats"]:
        me = sb.get(st["username"])
        k = me["kills"] if me else 0
        assert k + tk.get(st["username"], 0) == st["kills"], st["username"]
        deaths = st["deaths"] if "deaths" in st else int(st.get("died", False))
        assert (me["deaths"] if me else 0) == deaths, st["username"]


def test_r6_multi_round_numbering():
    from unitick.adapters import r6
    doc = json.loads((SAMPLES / "r6" / "pro_liquid_vs_magicsquad_oregon.json").read_text(encoding="utf-8"))
    m = r6.from_json(doc)
    assert m.rounds["round_num"].to_list() == [3, 8, 10]
    assert m.kills.group_by("round_num").len().sort("round_num")["len"].to_list() == [8, 7, 7]
    from unitick.render.report import _teams
    teams = {t["name"]: t for t in _teams(m, m.players())}
    assert teams["TEAM LIQUID"]["score"] == 2 and teams["MAGIC SQUAD"]["score"] == 1
    assert len(teams["TEAM LIQUID"]["player_ids"]) == 5


@pytest.mark.parametrize("name", ["valve_mm_mirage", "faceit_mirage"])
def test_cs2_presparsed_samples_load(name):
    m = Match.load(SAMPLES / "cs2" / name)
    assert m.game == "cs2" and m.map_name == "de_mirage" and m.kills.height > 50
    sb = fights.scoreboard(m)
    assert sb.height == 10 and sb["adr"].max() > 100  # valve_mm_mirage is a 13-0: some players truly do 0 ADR


def test_samples_command(tmp_path):
    from unitick.samples import build
    index = build(tmp_path, SAMPLES, log=lambda *_: None)
    page = index.read_text(encoding="utf-8")
    assert 'class="err"' not in page and page.count("<li>") >= 15


def test_discover_skips_raw_vrf_without_a_native_parser(monkeypatch):
    from unitick import samples
    monkeypatch.setattr("unitick.adapters.valorant.find_vrfkit", lambda: None)
    labels = [label for label, _ in samples.discover(SAMPLES, raw=False)]
    assert any(label.startswith("VALORANT:") and "pre-parsed" in label for label in labels)
    assert not any(label.startswith("VALORANT: solo_") for label in labels)


# ---------------------------------------------------------------- video pairing
def _tiny_mp4(path: Path, seconds: int = 7, scale: int = 1000) -> Path:
    """Minimal ISO-BMFF file with just ftyp + moov/mvhd: enough for duration()."""
    import struct
    mvhd_body = struct.pack(">B3xIIII", 0, 0, 0, scale, seconds * scale) + b"\0" * 80
    mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0)
    path.write_bytes(ftyp + moov)
    return path


def test_mp4_duration_and_recording_discovery(tmp_path, m):
    import os

    from unitick import video
    clip = _tiny_mp4(tmp_path / "Counter-strike 2 2026.11.12 - 20.15.03.01.mp4", seconds=30)
    assert video.duration(clip) == 30.0
    mm = m.round(*range(1, 13))
    end_tick = int(mm.rounds["end_tick"].max())
    T = 1_800_000_000.0
    mm.meta.update(wall_end=T, wall_end_tick=end_tick)
    r3 = mm.rounds.filter(pl.col("round_num") == 3).row(0, named=True)
    clip_end = T - (end_tick - r3["live_tick"]) / mm.tick_rate + 30  # clip ends 30 s after r3 live
    os.utime(clip, (clip_end, clip_end))
    (tmp_path / "unrelated.mp4").write_bytes(b"x")
    os.utime(tmp_path / "unrelated.mp4", (T - 86400, T - 86400))
    win = video.match_wall_window(mm)
    found = video.find_recordings(win[0], win[1], [tmp_path])
    assert [v.title for v in found] == [clip.name]
    v = found[0]
    # round 3 going live is exactly second 0 of the clip
    assert abs(T - (end_tick - r3["live_tick"]) / mm.tick_rate - v.wall_start) < 1e-6


def test_video_sidecar_and_payload(tmp_path, m):
    from unitick import video
    from unitick.render.report import build_payload
    (tmp_path / "video.json").write_text(json.dumps({"videos": [
        {"kind": "youtube", "id": "abc123", "title": "broadcast", "anchors": [[1, 61.5]]},
        {"kind": "file", "src": "rec.mp4", "offset": 1.5}]}), encoding="utf-8")
    _tiny_mp4(tmp_path / "rec.mp4")
    vids = video.sidecar(tmp_path)
    assert vids[0].anchors == [[1, 61.5]] and Path(vids[1].src).is_absolute()
    p = build_payload(m, radar=None, include_replay=False, videos=vids)
    assert p["videos"][0]["url"].endswith("v=abc123") and p["videos"][1]["media"]
    assert "src" not in p["videos"][1]  # local paths never leak into the page, only file_url / media key


def test_video_sidecar_rejects_path_escape(tmp_path):
    from unitick import video
    outside = tmp_path / "secret.mp4"
    _tiny_mp4(outside)
    match = tmp_path / "match"
    match.mkdir()
    (match / "video.json").write_text(json.dumps({"videos": [
        {"kind": "file", "src": str(outside)},
        {"kind": "file", "src": "../secret.mp4"},
        {"kind": "youtube", "id": "ok123", "title": "broadcast"},
    ]}), encoding="utf-8")
    vids = video.sidecar(match)
    assert len(vids) == 1 and vids[0].kind == "youtube" and vids[0].id == "ok123"


def test_pro_sample_ships_broadcast_link():
    vids = __import__("unitick.video", fromlist=["sidecar"]).sidecar(
        SAMPLES / "cs2" / "pro_vitality_vs_b8_mirage_iem_atlanta_2026")
    assert vids and vids[0].kind == "youtube" and vids[0].id
    m = Match.load(SAMPLES / "cs2" / "pro_vitality_vs_b8_mirage_iem_atlanta_2026")
    wins = m.rounds.group_by("winner_side").len()
    assert sorted(wins["len"].to_list()) == [5, 13]  # Vitality 13-5 B8, IEM Atlanta 2026


# ------------------------------------------------------------- deaths + situations
from unitick.analytics import autopsy, situations


def test_autopsy_causes(m):
    every = autopsy.autopsy(m, visibility=lambda a, b: np.ones(len(b), dtype=bool))
    assert every.height > 0 and set(every["cause"]) <= set(autopsy.CAUSES)
    assert "no_sight" not in set(every["cause"])
    assert every["crossfire"].min() >= 1  # the killer himself can always see the victim
    blind = autopsy.autopsy(m, visibility=lambda a, b: np.zeros(len(b), dtype=bool))
    assert set(blind["cause"]) == {"no_sight"}
    nolos = autopsy.autopsy(m)
    assert not nolos["used_los"][0] and nolos["crossfire"].null_count() == nolos.height
    s = autopsy.summary(every)
    assert s["deaths"].sum() == every.height


def test_set_distance_is_order_invariant():
    a = np.array([[0.0, 0.0], [100.0, 0.0], [0.0, 300.0]])
    assert situations._set_distance(a, a[::-1].copy(), 900) == pytest.approx(0.0)
    assert situations._set_distance(a, a[:2], 900) == pytest.approx(300.0)  # one missing body
    assert situations.move_kind(0, 0, 10, 10, a) == "held"
    assert situations.move_kind(0, 0, 400, 0, np.array([[1000.0, 0.0]])) == "pushed"
    assert situations.move_kind(0, 0, -400, 0, np.array([[1000.0, 0.0]])) == "fell back"


def test_situation_search_finds_the_same_moment(m):
    twin = Match.build({**m.meta, "match_id": "twin"}, rounds=m.rounds, frames=m.frames, kills=m.kills,
                       damages=m.damages, shots=m.shots, utility=m.utility, objectives=m.objectives)
    lib = situations.Library.from_matches([m, twin])
    snaps = situations.snapshots(m)
    row = snaps.filter(pl.col("t_live") > 10).row(0, named=True)
    ctx = snaps.filter((pl.col("round_num") == row["round_num"]) & (pl.col("tick") == row["tick"]))
    en = ctx.filter(pl.col("side") != row["side"]).select("x", "y").to_numpy()
    ma = ctx.filter((pl.col("side") == row["side"]) & (pl.col("player_id") != row["player_id"])).select("x", "y").to_numpy()
    hits = lib.search(map_name=m.map_name, side=row["side"], pos=(row["x"], row["y"]), enemies=en, mates=ma,
                      t_live=row["t_live"], planted=row["planted"], exclude_match=situations._match_key(m), k=3)
    assert set(hits["match_key"]) == {"twin"}
    best = hits.row(0, named=True)
    assert best["score"] < 0.05 and best["round_num"] == row["round_num"]


def test_report_has_deaths_tab(tmp_path, m):
    twin = Match.build({**m.meta, "match_id": "twin", "event": "test cup"}, rounds=m.rounds, frames=m.frames,
                       kills=m.kills, damages=m.damages, shots=m.shots, utility=m.utility,
                       objectives=m.objectives)
    lib = situations.Library.from_matches([twin])
    p = report.build_payload(m, library=lib, include_replay=False)
    d = p["deaths"]
    assert d and len(d["deaths"]) == m.kills.filter(pl.col("attacker_side") != pl.col("victim_side")).height
    with_others = [x for x in d["deaths"] if x.get("others")]
    assert with_others and with_others[0]["others"]["pro"] is True
    html = report.render(m, tmp_path / "r.html", library=lib).read_text(encoding="utf-8")
    assert 'data-t="deaths"' in html


def test_bundled_pro_library_loads():
    f = Path(__file__).resolve().parents[1] / "samples" / "library" / "cs2_pro.parquet"
    if not f.exists():
        pytest.skip("pro library not built")
    lib = situations.Library.load(f)
    assert {"de_mirage", "de_inferno"} <= set(lib.maps())
    assert lib.snaps["pro"].all() and lib.snaps["match_key"].n_unique() >= 10


# ------------------------------------------------------------- map control
from unitick.analytics import control


def test_walkable_grid_and_reach():
    # a horizontal corridor, 200 px tall, across the whole 1024 px map
    xs = np.linspace(5, 1019, 400)
    pts = np.array([(x, y) for x in xs for y in np.linspace(420, 600, 20)])
    g = control.walkable(pts, n=64)
    assert g[32, :].all() and not g[5, :].any()
    assert (control.unpack(control.pack(g), 64) == g).all()
    own = control.reach(g, [(50, 500)], [(970, 500)])
    assert own[32, 2] == 0 and own[32, 61] == 1 and (own[32, 30:34] == 2).any()
    sh = control.shares(own)
    assert sh["attack"] == pytest.approx(sh["defense"], abs=0.05)
    # burning fire in the middle of the corridor cuts the defense off from the left half
    fire = np.zeros_like(g)
    fire[:, 40:43] = True
    own2 = control.reach(g, [(50, 500)], [(970, 500)], blocked=fire)
    assert control.shares(own2)["attack"] > sh["attack"] - 0.01


def test_report_has_control_and_back_link(tmp_path, m):
    p = report.build_payload(m, library=None)
    assert p["control"] and p["control"]["n"] == control.GRID
    assert control.unpack(p["control"]["cells"]).sum() > 50
    html = report.render(m, tmp_path / "r.html", library=None, back="index.html").read_text(encoding="utf-8")
    assert '"back":"index.html"' in html and 'id="bCtl"' in html


def test_match_list_page(tmp_path, m):
    from unitick.render import index
    r = report.render(m, tmp_path / "a.html", library=None, back="index.html")
    index.record(r, m, label="test match")
    page = index.rescan(tmp_path).read_text(encoding="utf-8")
    assert 'href="a.html"' in page and "test match" in page


# ------------------------------------------------------------- real VALORANT replays
VRF_DIR = Path(__file__).resolve().parents[1] / "samples" / "valorant"


def _vrfkit_runs() -> bool:
    import subprocess
    exe = valorant.find_vrfkit()
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--help"], capture_output=True, check=False, timeout=20).returncode in (0, 1, 2)
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _vrfkit_runs(), reason="vrfkit not runnable on this platform")
def test_real_vrf_follows_every_character(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITICK_CACHE", str(tmp_path))
    m = valorant.load(VRF_DIR / "solo_ascent_viper_wall.vrf")
    assert m.map_name == "Ascent" and m.game == "valorant"
    # one player, three agents over 148 s: positions must cover the whole recording
    assert m.frames["player_id"].n_unique() == 1
    span_s = (m.frames["tick"].max() - m.frames["tick"].min()) / m.tick_rate
    assert span_s > 140
    assert m.frames["side"].null_count() == 0
    t = m.transform
    px, py = t.to_pixels(m.frames["x"].to_numpy(), m.frames["y"].to_numpy())
    assert ((px >= 0) & (px < t.size) & (py >= 0) & (py < t.size)).mean() == 1.0
    html = report.render(m, tmp_path / "v.html", library=None).read_text(encoding="utf-8")
    assert '"control":{' in html


def test_anonymized_hides_account_ids(m):
    a = m.anonymized(salt="x")
    real = set(m.frames["player_id"].unique())
    fake = set(a.frames["player_id"].unique())
    assert len(fake) == len(real) and not (real & fake)
    assert all(p.startswith("anon:") for p in fake)
    assert set(a.kills["attacker_id"].drop_nulls()) <= fake | {None}
    # same input, same salt: same ids, so a whole season of matches still lines up
    assert set(m.anonymized(salt="x").frames["player_id"].unique()) == fake
    assert fights.scoreboard(a)["kills"].sum() == fights.scoreboard(m)["kills"].sum()


# ------------------------------------------------------------- tracers, sites, 3D payload
from unitick.analytics import sites, tracers


def test_tracers_from_real_shots():
    m = Match.load(Path(__file__).resolve().parents[1] / "samples" / "cs2" / "pro_vitality_vs_b8_mirage_iem_atlanta_2026")
    tr = tracers.tracers(m)
    assert set(tr["kind"].unique()) <= {"miss", "hit", "kill"}
    kills = tr.filter(pl.col("kind") == "kill").height
    assert kills >= 0.9 * m.kills.height  # nearly every kill is matched to the shot that made it
    hits = tr.filter(pl.col("kind") != "miss")
    assert hits["victim_id"].null_count() == 0
    # a hit ends on the victim: within a body length of where the victim stood
    d = ((hits["x1"] - hits["x0"]) ** 2 + (hits["y1"] - hits["y0"]) ** 2).sqrt()
    assert d.max() < 6000 and tr.filter(pl.col("kind") == "miss")["victim_id"].null_count() == \
        tr.filter(pl.col("kind") == "miss").height


def test_tracers_kill_only_source(m):
    no_shots = Match.build(m.meta, rounds=m.rounds, frames=m.frames, kills=m.kills, objectives=m.objectives)
    tr = tracers.tracers(no_shots)
    assert tr.height == no_shots.kills.filter(pl.col("attacker_id") != pl.col("victim_id")).height
    assert set(tr["kind"]) == {"kill"}


def test_missing_sites_are_recovered():
    obj = pl.DataFrame({"round_num": [1, 1, 2, 3], "tick": [10, 20, 30, 40],
                        "event": ["plant", "explode", "plant", "plant"],
                        "site": ["A", None, None, None], "x": [0.0, 0.0, 50.0, 9000.0], "y": [0.0, 0.0, 0.0, 9000.0]})
    out = sites.fill_missing(obj, [(3000.0, 0.0, "B")])
    assert out["site"].to_list() == ["A", "A", "A", None]  # the far plant stays unknown rather than guessed


def test_report_carries_3d_data(m):
    p = report.build_payload(m, library=None)
    assert p["view3d"]["ppu"] > 0 and p["view3d"]["eye"] > 0
    assert any(r["tracers"] for r in p["rounds"])
    assert all(len(t) == 10 for r in p["rounds"] for t in r["tracers"])
