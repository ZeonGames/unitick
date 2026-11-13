# What if: your odds with every other play

At every death, unitick asks: if this player had held the spot, pushed toward the enemy, or fallen back, how likely were they to still be alive five seconds later?

## How it works

1. **The moment.** Take the situation 1.5 seconds before the death: where the player stood, their side, where every living teammate and enemy was, the round clock and whether the bomb was down.
2. **Similar moments.** Search the pro library for the 60 closest moments on the same map (same side and bomb state, one moment per round), with the same matcher the Deaths tab uses for "In this spot". A match is never compared with itself.
3. **What each player did.** Sort those players by what they did over the next three seconds: *held* (stayed within 120 units), *pushed* (moved toward the enemies) or *fell back* (moved away).
4. **Odds per play.** For each play, the share of players still alive five seconds later, with three corrections:
   - closer matches count more (weight `e^(-Δscore / 0.5)`)
   - a play few players chose leans toward the spot's overall odds (prior weight 10)
   - the odds are anchored 30% to how often players on that map survive a fight at all
5. **Verdict.** A play only counts as clearly better when it has at least 10 points better odds *and* its low estimate (80% range) is still above the odds of what the player did. A play needs 8 players before it gets a percentage.

Each number shows how many players it rests on. These are what happened to players who made each choice, not a guarantee and not a simulation.

## Does it work?

`python scripts/validate_whatif.py --per-map 600` takes 3,600 fight moments (an enemy within 1,200 units) from the pro library, and for each one predicts whether that player survived the next five seconds from the play they actually made, using only the *other* matches. Lower is better:

| Estimator | Brier score | Log loss |
| --- | --- | --- |
| **What if (unitick)** | **0.083** | **0.290** |
| Same, but told a random play instead of the real one | 0.084 | 0.293 |
| The map's base survival rate | 0.090 | 0.324 |
| Raw counts of the same play | 0.098 | 0.402 |

Calibration, predicted survival against what happened:

| Predicted | Actual | Moments |
| --- | --- | --- |
| 0.60 | 0.64 | 230 |
| 0.76 | 0.78 | 345 |
| 0.86 | 0.88 | 913 |
| 0.93 | 0.94 | 1,077 |
| 0.96 | 0.97 | 1,035 |

What this says, honestly:

- The odds are well calibrated: when it says 76%, about 78% of players lived.
- Most of the accuracy comes from reading the spot you were in. Knowing the play you chose helps, but less: told a random play, the engine is only slightly worse. In most deaths no play is clearly better, and the page says so.
- That is why the "clearly better" call is strict. When it appears, the gap is at least 10 points and survives the uncertainty, so it is worth acting on.

## Limits

- **CS2 only for now.** VALORANT and Siege X need pro replays on the same map; the what-if turns on for them as those are added to the library.
- **Maps.** The bundled library covers Mirage, Inferno, Dust2, Ancient, Anubis and Train. Other maps show no odds until matches are added with `unitick library`.
- **Five seconds.** Survival is measured over the next five seconds, which captures the fight you were about to take, not the rest of the round.
- **Three plays.** Hold, push and fall back are judged by movement only. Utility, peeks and rotations are not separate plays yet.
