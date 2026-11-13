# VALORANT samples

Real `.vrf` replays from the integration tests of
[ValorantReplayParser](https://github.com/michel-giehl/ValorantReplayParser)
(MIT, commit fbf24b6, see `LICENSE.ValorantReplayParser`). They are short
single-player sessions used to test abilities, which keeps the test suite fast:

| File | Map | Patch | Length | What is in it |
| --- | --- | --- | --- | --- |
| `solo_ascent_viper_wall.vrf` | Ascent | 13.01 | 2:28 | one player swapping Astra, Iso, Viper |
| `solo_ascent_harbor.vrf` | Ascent | 13.01 | 1:24 | Harbor abilities |
| `solo_ascent_omen.vrf` | Ascent | 13.01 | 1:08 | Omen abilities |
| `solo_fracture_13_00.vrf` | Fracture | 13.00 | 0:39 | short solo session |
| `solo_ascent_12_10.vrf` | Ascent | 12.10 | 1:10 | short solo session |
| `solo_ascent_12_11.vrf` | Ascent | 12.11 | 0:24 | short solo session |

They exercise the full pipeline on real data: the vrfkit export, agent names, every character a player
controlled, utility, the 2D replay and map control. Every recorded position
lands on the Ascent minimap.

Add your own matches: Career, then Match History, then download the replay. It
lands in `%LOCALAPPDATA%\VALORANT\Saved\Demos`. Copy the `.vrf` here, or run
`unitick report` on it directly.
