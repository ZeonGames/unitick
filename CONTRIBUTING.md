# Contributing

## Dev setup

Always use a local `.venv`. A bare `pip install` into the system/user Python often leaves `unitick` off PATH on Windows.

**Windows (PowerShell):**

```powershell
git clone https://github.com/<you>/unitick
cd unitick
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activate is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[all]"
pytest            # ~5 s, no game files needed
ruff check src tests
unitick samples --open                    # reports for every real sample in samples/
```

**macOS / Linux:**

```bash
git clone https://github.com/<you>/unitick
cd unitick
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
pytest
ruff check src tests
unitick samples --open
```

**Web app** (venv still active, Node 18+):

```bash
cd web && npm install && npm run build && cd ..
unitick app                         # http://localhost:8765
```

If `unitick` is not recognized after activate, use `python -m unitick ...` (or `.venv\Scripts\python.exe -m unitick ...` / `.venv/bin/python -m unitick ...` without activate).

Optional: run the real-demo check with `UNITICK_DEMO=path/to/match.dem pytest`. It compares unitick's numbers against awpy's on that demo.

## How the code is laid out

```
src/unitick/
  schema.py          the 7 canonical tables and their conventions  <- start here
  match.py           Match: holds the tables, save/load parquet
  maps.py            world -> radar pixel transforms (CS2 radar, VALORANT affine)
  adapters/          one file per source, each turns raw data into a Match
    cs2.py           .dem via awpy
    valorant.py      .vrf via vrfkit (tools/vrfkit.exe)
    r6.py            .rec via r6-dissect (tools/r6-dissect.exe)
    table.py         any CSV/Parquet/JSON via a JSON spec
    synthetic.py     scripted matches with known answers (used by tests)
  analytics/         game-agnostic: only read Match tables
    engagements.py   hits -> fights
    crosshair.py     CrosshairIQ placement score
    fights.py        trades, openings, scoreboard (K/D/A, ADR, KAST)
    executes.py      site executes + playbook clustering
  render/            report.py builds the payload; app.html is the whole UI
  watch.py           folder watcher (auto-analyze new replays)
  cli.py             `unitick` command
tests/               pytest; vrf_fixture.py writes a fake vrfkit export
samples/             real CS2 + R6 matches, synthetic VALORANT; download_samples.py for raw files
tools/               bundled third-party parser binaries (MIT), see SOURCES.txt
```

The one rule: **adapters know about games, and nothing else does.** Analytics and rendering only see canonical tables, so a feature you add there works for every game automatically.

## Adding a game

1. Find out what the game saves locally and whether an open-source parser exists. Open a "New game support" issue with what you find.
2. Create `src/unitick/adapters/<game>.py` with a `load(path) -> Match`. Map what you have onto `schema.py`:
   - `side` must be `attack` or `defense`
   - `yaw` is degrees counter-clockwise from +x, and `pitch` is positive when looking down
   - ids are strings
   - only `rounds` is required; leave out tables the format cannot provide
3. Build the Match with `Match.build(meta, rounds=..., frames=..., kills=...)`. Rounds get assigned automatically.
4. Register the file type in `cli.load_any`, and its replay folder in `watch.default_dirs` and `watch.scan`.
5. Add a test with a small fixture that has known answers. See `tests/vrf_fixture.py` and `R6_JSON` in the tests.

## Adding a metric

Write it in `analytics/` against canonical tables only. Test it on `synthetic.generate()`, scripting the answer you expect into the generator if needed. Then surface it in `render/report.py` (payload) and `render/app.html` (UI).

## Pull requests

- Keep each PR to one change and include its tests. `pytest` and `ruff check` must pass, and CI runs on Windows, macOS and Linux.
- Never read game memory, inject into a game process, or analyze the screen while a match is running. unitick only reads replay files after the match. This keeps users safe from anti-cheat bans, and PRs that break this rule will not be merged.
