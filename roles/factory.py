"""角色工厂"""

from typing import TYPE_CHECKING

from .base import BaseRole
from .hunter import HunterRole
from .seer import SeerRole
from .villager import VillagerRole
from .werewolf import WerewolfRole
from .witch import WitchRole

if TYPE_CHECKING:
    from ..models import GameRoom, Player, Role


class RoleFactory:
    """角色工厂"""

    _role_classes: dict["Role", type[BaseRole]] = {}
    _instances: dict["Role", BaseRole] = {}

    @classmethod
    def _init_role_classes(cls) -> None:
        """初始化角色类映射"""
        if cls._role_classes:
            return

        from ..models import Role

        cls._role_classes = {
            Role.WEREWOLF: WerewolfRole,
            Role.SEER: SeerRole,
            Role.WITCH: WitchRole,
            Role.HUNTER: HunterRole,
            Role.VILLAGER: VillagerRole,
        }

    @classmethod
    def get(cls, role: "Role") -> BaseRole:
        """获取角色实例（单例）"""
        cls._init_role_classes()

        if role not in cls._instances:
            role_class = cls._role_classes.get(role)
            if role_class:
                cls._instances[role] = role_class()
            else:
                raise ValueError(f"未知角色类型: {role}")

        return cls._instances[role]

    @classmethod
    def get_role_info(cls, role: "Role", player: "Player", room: "GameRoom") -> str:
        """获取角色信息文本"""
        role_instance = cls.get(role)
        return role_instance.get_role_info(player, room)
