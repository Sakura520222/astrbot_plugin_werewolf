"""角色层"""

from .base import BaseRole
from .factory import RoleFactory
from .hunter import HunterDeathType, HunterRole, HunterState
from .seer import SeerRole
from .villager import VillagerRole
from .werewolf import WerewolfRole
from .witch import WitchRole, WitchState

__all__ = [
    "BaseRole",
    "WerewolfRole",
    "SeerRole",
    "WitchRole",
    "WitchState",
    "HunterRole",
    "HunterState",
    "HunterDeathType",
    "VillagerRole",
    "RoleFactory",
]
