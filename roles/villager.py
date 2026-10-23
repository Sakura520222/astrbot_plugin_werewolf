"""平民角色"""

from typing import TYPE_CHECKING

from .base import BaseRole

if TYPE_CHECKING:
    from ..models import GameRoom, Player, Role


class VillagerRole(BaseRole):
    """平民角色"""

    @property
    def role_type(self) -> "Role":
        from ..models import Role

        return Role.VILLAGER

    @property
    def name(self) -> str:
        return "平民"

    @property
    def emoji(self) -> str:
        return "👤"

    @property
    def description(self) -> str:
        return "普通村民，没有特殊技能"

    @property
    def goal(self) -> str:
        return "找出并放逐所有狼人"

    def get_role_info(self, player: "Player", room: "GameRoom") -> str:
        """获取平民角色信息"""
        return (
            f"🎭 游戏开始！你的角色是：\n\n"
            f"{self.emoji} {self.name}\n\n"
            f"你的目标：{self.goal}！\n"
            f"白天投票时使用 /投票 编号 放逐可疑玩家。"
        )

    def get_night_commands(self) -> list[str]:
        return []
