"""The local web app's JSON API: listing, upload jobs, payloads and deletes."""

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from unitick import app

R6 = Path(__file__).resolve().parents[1] / "samples" / "r6"
FULL = R6 / "pro_liquid_vs_magicsquad_oregon.json"
ONE_ROUND = R6 / "y8s1_quick_house.json"

pytestmark = pytest.mark.skipif(
    not FULL.exists() or not ONE_ROUND.exists(), reason="Siege samples not present"
)


@pytest.fixture
def server(tmp_path):
    samples = tmp_path / "samples" / "r6"
    samples.mkdir(parents=True)
    shutil.copy(FULL, samples)
    shutil.copy(ONE_ROUND, samples)
    store = app.Store(root=tmp_path / "app", watch_dir=tmp_path / "watch", samples=tmp_path / "samples")
    httpd = app.make_server(0, store)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, method="GET", data=None):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_listing_hides_single_round_samples(server):
    status, rows = call(server + "/api/matches")
    assert status == 200
    assert [r["game"] for r in rows] == ["r6"]
    (row,) = rows
    assert row["kind"] == "sample" and row["rounds"] >= 3 and len(row["teams"]) == 2
    assert "path" not in row and "mtime" not in row


def test_payload_is_built_once_then_cached(server):
    (row,) = call(server + "/api/matches")[1]
    t0 = time.time()
    status, payload = call(f"{server}/api/matches/{row['id']}")
    first = time.time() - t0
    assert status == 200 and payload["meta"]["game"] == "r6" and payload["rounds"]
    t0 = time.time()
    assert call(f"{server}/api/matches/{row['id']}")[1] == payload
    assert time.time() - t0 < max(first, 0.5)


def test_upload_job_to_report(server):
    status, job = call(server + "/api/jobs", "POST", b"")
    assert status == 201 and job["state"] == "uploading"
    jid = job["id"]
    assert call(f"{server}/api/jobs/{jid}/files/notes.txt", "PUT", b"x")[0] == 400
    assert call(f"{server}/api/jobs/{jid}/files/{ONE_ROUND.name}", "PUT", ONE_ROUND.read_bytes())[0] == 200
    assert call(f"{server}/api/jobs/{jid}/run", "POST", b"")[0] == 200
    deadline = time.time() + 120
    while (job := call(f"{server}/api/jobs/{jid}")[1])["state"] not in ("done", "failed"):
        assert time.time() < deadline
        time.sleep(0.2)
    assert job["state"] == "done", job
    rows = call(server + "/api/matches")[1]
    assert rows[0]["kind"] == "upload" and rows[0]["id"] == job["match_id"]
    assert call(f"{server}/api/matches/{job['match_id']}")[0] == 200
    assert call(f"{server}/api/matches/{job['match_id']}", "DELETE")[0] == 200
    assert all(r["kind"] != "upload" for r in call(server + "/api/matches")[1])


def test_errors_are_json(server):
    assert call(server + "/api/matches/doesnotexist")[0] == 404
    sample = call(server + "/api/matches")[1][0]
    assert call(f"{server}/api/matches/{sample['id']}", "DELETE")[0] in (400, 403, 404)


def test_map_assets_are_bundled_for_offline_use():
    from unitick import maps
    from unitick.adapters import cs2
    from unitick.render import images

    for name in ("de_mirage", "de_inferno", "de_dust2", "de_ancient", "de_anubis", "de_nuke", "de_train", "de_overpass"):
        assert images.find_radar(name).parent == images.BUNDLED_RADARS
    for name in ("Juliett", "Ascent", "Bonsai", "Triad", "Port"):
        t = maps.get(name)
        if t is not None and t.radar_url:
            assert images.find_radar(name, t).parent == images.BUNDLED_RADARS
    assert (Path(cs2.__file__).parents[1] / "data" / "meshes" / "de_mirage.mesh.xz").exists()


def test_valorant_samples_are_anonymized():
    root = Path(__file__).resolve().parents[1] / "samples" / "valorant"
    metas = sorted(root.glob("*/meta.json"))
    if not metas:
        pytest.skip("no VALORANT samples")
    for p in metas:
        meta = json.loads(p.read_text(encoding="utf-8"))
        assert meta.get("anonymized") is True and "source" not in meta
