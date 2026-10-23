"""行动模块 - AI玩家的各种行动决策"""

from .base import BaseAction
from .hunter import HunterAction
from .seer import SeerAction
from .speech import SpeechAction
from .vote import VoteAction
from .werewolf import WerewolfAction
from .witch import WitchAction

__all__ = [
    "BaseAction",
    "WerewolfAction",
    "SeerAction",
    "WitchAction",
    "HunterAction",
    "SpeechAction",
    "VoteAction",
]
