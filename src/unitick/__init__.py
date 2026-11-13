"""unitick: one schema for tactical-shooter telemetry, with aim and strategy analytics.

>>> from unitick.adapters import cs2
>>> from unitick.analytics import crosshair, executes, fights
>>> m = cs2.load("match.dem")
>>> crosshair.leaderboard(crosshair.placement(m, visibility=cs2.visibility(m.map_name)))
"""

__version__ = "0.1.0"

from .match import Match
from .schema import ATTACK, DEFENSE, SCHEMAS

__all__ = ["ATTACK", "DEFENSE", "SCHEMAS", "Match", "__version__"]
