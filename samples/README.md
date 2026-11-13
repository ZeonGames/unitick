# samples

Real matches only. Each one is checked against an independent parser's stats, and comes with video where a public broadcast exists.

```
unitick samples                                 # build a report for every sample + an index page
unitick serve ~/unitick/reports/samples         # open them with video playback (YouTube needs http)
python samples/download_samples.py              # fetch the raw replays (~140 MB), sha256-verified
unitick samples --raw                           # include the raw downloads
```

On Windows, START_HERE.bat menu options 5 and 6 do the same.

| Folder | Match | Video | Checked against |
| --- | --- | --- | --- |
| `cs2/pro_vitality_vs_b8_mirage_iem_atlanta_2026/` | **Vitality 13-5 B8**, IEM Atlanta 2026, Group A lower bracket final, 2026-05-13 | ESL broadcast on YouTube (`video.json`) | final score; awpy |
| `cs2/pro_9z_vs_parivision_inferno_xse_2026_final/` | **9z 13-10 PARIVISION**, XSE Pro League Guangzhou 2026 grand final, map 2 | broadcast VOD linked from the HLTV match page | final score and HUASOPEEK 21-12 / dgt 20-7, as reported |
| `cs2/faceit_mirage/` | 30-round FACEIT pug on Mirage | none | awpy, kill for kill |
| `cs2/valve_mm_mirage/` | Valve matchmaking game on Mirage (13-0) | none | awpy |
| `cs2/pro_astralis_vs_betboom_m1_mirage/` | **Astralis vs BetBoom Team** on Mirage, 23 rounds, HLTV demo | none | none |
| `cs2/pro_complexity_vs_saw_m1_anubis/` | **SAW vs Complexity** on Anubis, 21 rounds, HLTV demo | none | none |
| `cs2/pro_gamerlegion_vs_betboom_m1_dust2/` | **BetBoom Team vs GamerLegion** on Dust2, 22 rounds, HLTV demo | none | none |
| `cs2/pro_imperial_vs_legacy_m1_train/` | **Legacy vs Imperial** on Train, 22 rounds, HLTV demo | none | none |
| `cs2/pro_tyloo_vs_3dmax_m1_ancient/` | **TEAM 3DMAX vs Tyloo** on Ancient, 22 rounds, HLTV demo | none | none |
| `cs2/pro_tyloo_vs_3dmax_m2_inferno/` | **TEAM 3DMAX vs Tyloo** on Inferno, 20 rounds, HLTV demo | none | none |
| `valorant/<map>_<mode>_<patch>/` | 6 full VALORANT matches (one Competitive, five Swiftplay) on Sunset, Corrode, Haven, Icebox and Split, patches 11.06 to 13.04. Account ids removed, players shown by agent | none | kill feed |
| `r6/pro_liquid_vs_magicsquad_oregon.json` | **Team Liquid vs Magic Squad**, Brazil League 2023 Stage 1, Group B, 2023-03-25, Oregon (3 recorded rounds) | none found | r6-dissect stats |
| `r6/*.json` | 11 more Siege rounds: ranked, quick, standard (Vega vs Needone), custom | none | r6-dissect stats |
| `cs2/raw/`, `r6/raw/` | the original `.dem` / `.rec` files | | downloaded with `download_samples.py` |
| `valorant/*.vrf` | short real `.vrf` sessions from ValorantReplayParser tests (see [valorant/README.md](valorant/README.md)) | | |

The CS2 and VALORANT matches are pre-parsed unitick tables (`Match.load(dir)`), so reports build instantly, with no 300-500 MB demo download.

## Syncing a broadcast (and sharing it)

Broadcasts pause, replay and cut away, so their video is synced per round:

1. Open the report through `unitick serve`, go to Replay, and pick the broadcast in the video panel.
2. Play the video to the moment a round goes live, set the map to the same moment, and press **Sync here**.
3. Repeat for a few rounds. Rounds you have not synced are estimated from the nearest synced round.
4. Press **Export sync** and save the result as `video.json` in the match folder. Open a PR so nobody has to sync this match again.

Your own recordings (NVIDIA app / ShadowPlay, OBS) need none of this. They are found and synced automatically by file time.

## Adding your own VALORANT match

Public competitive `.vrf` files are rare (personal, patch-locked). For one of yours, the easiest way is the web app: `unitick app`, then **Analyze a replay**. To add it as a sample: Career → Match History → Download, copy from `%LOCALAPPDATA%\VALORANT\Saved\Demos` into `samples/valorant/`, then `unitick samples`.

## Sources and licenses

- CS2 demos come from awpy-fixtures, which hosts public match recordings (HLTV, FACEIT, Valve). The tables here are derived from them.
- The R6 replays come from r6-dissect's MIT-licensed test data.
- Broadcast videos are linked, never copied.
