"""行动模块 - AI玩家的各种行动决策"""
from .base import BaseAction
from .werewolf import WerewolfAction
from .seer import SeerAction
from .witch import WitchAction
from .hunter import HunterAction
from .speech import SpeechAction
from .vote import VoteAction

__all__ = [
    'BaseAction',
    'WerewolfAction',
    'SeerAction',
    'WitchAction',
    'HunterAction',
    'SpeechAction',
    'VoteAction',
]
