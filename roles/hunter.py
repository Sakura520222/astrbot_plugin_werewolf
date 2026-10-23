"""猎人角色"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..utils import cmd
from .base import BaseRole

if TYPE_CHECKING:
    from ..models import GameRoom, Player, Role


class HunterDeathType(Enum):
    """猎人死亡类型"""

    WOLF = "wolf"  # 被狼人杀死 - 可以开枪
    VOTE = "vote"  # 被投票放逐 - 可以开枪
    POISON = "poison"  # 被女巫毒死 - 不能开枪


@dataclass
class HunterState:
    """猎人状态"""

    has_shot: bool = False  # 是否已开枪
    pending_shot_player_id: str | None = None  # 待开枪的猎人ID
    death_type: HunterDeathType | None = None  # 死亡方式

    def can_shoot(self) -> bool:
        """判断是否可以开枪"""
        if self.has_shot:
            return False
        if self.death_type == HunterDeathType.POISON:
            return False
        return True

    def reset(self) -> None:
        """重置状态"""
        self.pending_shot_player_id = None
        self.death_type = None


class HunterRole(BaseRole):
    """猎人角色"""

    @property
    def role_type(self) -> "Role":
        from ..models import Role

        return Role.HUNTER

    @property
    def name(self) -> str:
        return "猎人"

    @property
    def emoji(self) -> str:
        return "🔫"

    @property
    def description(self) -> str:
        return "死亡时（非毒杀）可以开枪带走一名玩家"

    @property
    def goal(self) -> str:
        return "帮助好人获胜"

    def get_role_info(self, player: "Player", room: "GameRoom") -> str:
        """获取猎人角色信息"""
        # 可选目标列表（除了自己）
        other_players = [p for p in room.players.values() if p.id != player.id]
        players_list = self.format_player_list(other_players)

        return (
            f"🎭 游戏开始！你的角色是：\n\n"
            f"{self.emoji} {self.name}\n\n"
            f"你的目标：{self.goal}！\n\n"
            f"你的技能：\n"
            f"• 被狼人办掉时可以开枪带走一人\n"
            f"• 被投票放逐时可以开枪带走一人\n"
            f"• 被女巫毒死时不能开枪（死的太突然）\n\n"
            f"📋 可选目标列表：\n{players_list}\n\n"
            f"💡 当你死亡时（非毒死），私聊使用命令：\n"
            f"  {cmd('开枪')} 编号 - 带走一个人\n"
            f"示例：{cmd('开枪')} 1"
        )

    def get_night_commands(self) -> list[str]:
        return [f"{cmd('开枪')} 编号"]

    def get_death_prompt(self, death_type: HunterDeathType) -> str:
        """获取死亡时的开枪提示"""
        if death_type == HunterDeathType.WOLF:
            reason = "你被狼人办掉了！"
        elif death_type == HunterDeathType.VOTE:
            reason = "你被投票放逐了！"
        else:
            return ""

        return (
            f"💀 {reason}\n\n"
            f"🔫 你可以选择开枪带走一个人！\n\n"
            f"请私聊使用命令：\n"
            f"  {cmd('开枪')} 编号\n"
            f"示例：{cmd('开枪')} 1\n\n"
            f"⏰ 限时2分钟"
        )
