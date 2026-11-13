"""The what-if engine: survival odds for every play at the moment of a death."""
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from unitick.adapters import synthetic
from unitick.analytics import situations
from unitick.analytics import whatif as W
from unitick.match import Match
from unitick.render import deaths, report

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def m():
    return synthetic.generate(12)


def _twin(m, mid="twin", **meta):
    return Match.build({**m.meta, "match_id": mid, **meta}, rounds=m.rounds, frames=m.frames, kills=m.kills,
                       damages=m.damages, shots=m.shots, utility=m.utility, objectives=m.objectives)


# ------------------------------------------------------------------ the math
def test_wilson_interval():
    lo, hi = W.wilson(8, 10)
    assert 0 < lo < 0.8 < hi < 1
    assert W.wilson(0, 0) == (0.0, 1.0)
    narrow, wide = W.wilson(80, 100), W.wilson(8, 10)
    assert narrow[1] - narrow[0] < wide[1] - wide[0]


def test_estimate_prefers_the_play_that_survived():
    n = 60
    moves = np.array(["held"] * 30 + ["pushed"] * 30)
    died = np.array([0.0] * 30 + [1.0] * 30)          # every holder lived, every pusher died
    scores = np.linspace(1.0, 2.0, n)
    kills = np.zeros(n)
    opts = {o["move"]: o for o in W.estimate(died, moves, scores, kills, base=0.9)}
    assert opts["held"]["rate"] > opts["pushed"]["rate"]
    assert opts["held"]["enough"] and opts["pushed"]["enough"] and not opts["fell back"]["enough"]
    assert opts["fell back"]["n"] == 0
    for o in opts.values():
        assert 0 <= o["lo"] <= o["rate"] <= o["hi"] <= 1


def test_estimate_shrinks_rare_plays_and_weights_close_matches():
    scores = np.linspace(1.0, 2.0, 60)
    kills = np.zeros(60)
    # a play chosen twice, both survived: pulled toward the spot's odds, never 100%
    moves = np.array(["fell back"] * 2 + ["held"] * 58)
    died = np.array([0.0, 0.0] + [1.0] * 29 + [0.0] * 29)
    rare = {o["move"]: o for o in W.estimate(died, moves, scores, kills, base=0.9)}["fell back"]
    assert rare["rate"] < 0.95 and not rare["enough"]
    # same counts, but the deaths sit in the closest matches: lower odds than when they sit far away
    moves = np.array(["held"] * 60)
    near = np.array([1.0] * 20 + [0.0] * 40)
    far = near[::-1].copy()
    r_near = W.estimate(near, moves, scores, kills, 0.9)[0]["rate"]
    r_far = W.estimate(far, moves, scores, kills, 0.9)[0]["rate"]
    assert r_near < r_far


def test_judge_verdicts():
    def o(move, rate, lo, n=20):
        return {"move": move, "n": n, "rate": rate, "lo": lo, "hi": min(1, rate + .1), "enough": n >= W.MIN_N}
    opts = [o("held", .60, .52), o("pushed", .85, .78), o("fell back", .70, .60)]
    assert W.judge(opts, "held") == ("pushed", "better", .25)
    assert W.judge(opts, "pushed")[1] == "best"
    assert W.judge(opts, "fell back")[1] == "better"  # +15 points, low estimate 0.78 above 0.70
    close = [o("held", .80, .70), o("pushed", .86, .79)]
    assert W.judge(close, "held")[1] == "close"       # +6 points is not clearly better
    wide = [o("held", .60, .50), o("pushed", .75, .58)]
    assert W.judge(wide, "held")[1] == "close"        # +15, but the range still overlaps 0.60
    rare = [o("held", .60, .5, n=3), o("pushed", .85, .78)]
    assert W.judge(rare, "held") == ("pushed", "rare", None)
    assert W.judge([o("held", .6, .5, n=2)], "held") == (None, None, None)


# ------------------------------------------------------------------ in the payload
def test_deaths_payload_has_whatif(m):
    lib = situations.Library.from_matches([_twin(m, event="test cup")])
    p = deaths.build(m, library=lib)
    rated = [d["whatif"] for d in p["deaths"] if d.get("whatif")]
    assert rated, "expected what-if odds when the library has the same situations"
    for w in rated:
        assert w["you"] in W.OPTIONS and [o["move"] for o in w["options"]] == list(W.OPTIONS)
        assert w["pool"] == "pro" and w["horizon_s"] == situations.OUTCOME_S
        assert w["verdict"] in (None, "best", "better", "close", "rare")
        for o in w["options"]:
            assert o["rate"] is None or 0 <= o["lo"] <= o["rate"] <= o["hi"] <= 1
    s = p["whatif"]
    assert s["rated"] >= s["better"] >= 0 and s["min_n"] == W.MIN_N
    assert all("better_play" in pl_ for pl_ in p["players"])
    json.dumps(p)  # payload must stay JSON-serializable


def test_no_library_means_no_whatif(m):
    p = deaths.build(m, library=None)
    assert p["whatif"] is None and all(d.get("whatif") is None for d in p["deaths"])
    assert all(pl_["better_play"] is None for pl_ in p["players"])


def test_report_renders_whatif(tmp_path, m):
    lib = situations.Library.from_matches([_twin(m, event="test cup")])
    html = report.render(m, tmp_path / "r.html", library=lib).read_text(encoding="utf-8")
    assert '"whatif"' in html and "What if" in html


def test_cli_deaths(capsys):
    from unitick.cli import main
    assert main(["deaths", "synthetic"]) == 0
    out = capsys.readouterr().out
    assert "hold %" in out or "no deaths" in out


# ------------------------------------------------------------------ library hygiene
def test_library_order_is_deterministic():
    a = situations.default_library(refresh=True).snaps
    b = situations.default_library(refresh=True).snaps
    if a.is_empty():
        pytest.skip("no library")
    assert a.head(2000).equals(b.head(2000))


def test_samples_never_match_their_own_library_copy():
    """A pro sample that is also inside cs2_pro.parquet must be excluded from its own search."""
    lib_file = ROOT / "samples" / "library" / "cs2_pro.parquet"
    if not lib_file.exists():
        pytest.skip("pro library not built")
    keys = set(pl.read_parquet(lib_file, columns=["match_key"])["match_key"].unique())
    for meta in sorted((ROOT / "samples" / "cs2").glob("*/meta.json")):
        mt = json.loads(meta.read_text(encoding="utf-8"))
        stem = "hltv:" + meta.parent.name.removeprefix("pro_").replace("_", "-")
        if stem in keys:
            assert mt.get("library_key") == stem, meta.parent.name


def test_fight_base_is_a_probability():
    lib = situations.default_library()
    if "de_mirage" not in lib.maps():
        pytest.skip("pro library not built")
    b = W.fight_base(lib, "de_mirage")
    assert 0.7 < b < 1.0
