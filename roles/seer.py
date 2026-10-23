"""预言家角色"""

from typing import TYPE_CHECKING

from ..utils import cmd
from .base import BaseRole

if TYPE_CHECKING:
    from ..models import GameRoom, Player, Role


class SeerRole(BaseRole):
    """预言家角色"""

    @property
    def role_type(self) -> "Role":
        from ..models import Role

        return Role.SEER

    @property
    def name(self) -> str:
        return "预言家"

    @property
    def emoji(self) -> str:
        return "🔮"

    @property
    def description(self) -> str:
        return "每晚可以查验一名玩家的身份"

    @property
    def goal(self) -> str:
        return "找出狼人，帮助好人获胜"

    def get_role_info(self, player: "Player", room: "GameRoom") -> str:
        """获取预言家角色信息"""
        # 可验证玩家列表（除了自己）
        other_players = [p for p in room.players.values() if p.id != player.id]
        players_list = self.format_player_list(other_players)

        # 找一个示例编号
        example_number = other_players[0].number if other_players else 3

        return (
            f"🎭 游戏开始！你的角色是：\n\n"
            f"{self.emoji} {self.name}\n\n"
            f"你的目标：{self.goal}！\n\n"
            f"📋 可验证玩家列表：\n{players_list}\n\n"
            f"💡 夜晚私聊使用命令：\n"
            f"{cmd('验人')} 编号\n"
            f"示例：{cmd('验人')} {example_number}\n\n"
            f"⚠️ 注意：每晚只能验证一个人！"
        )

    def get_night_commands(self) -> list[str]:
        return [f"{cmd('验人')} 编号"]
