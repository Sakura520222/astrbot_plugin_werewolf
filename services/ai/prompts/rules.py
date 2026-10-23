"""Compatibility export for the shared executable rulebook."""

from ....models.config import GameConfig
from ....models.game_rules import GameRules

GAME_RULES = GameRules.describe(GameConfig.default())
