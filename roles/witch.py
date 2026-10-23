"""女巫角色"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..utils import cmd
from .base import BaseRole

if TYPE_CHECKING:
    from ..models import GameRoom, Player, Role


@dataclass
class WitchState:
    """女巫状态"""

    poison_used: bool = False  # 毒药是否已使用
    antidote_used: bool = False  # 解药是否已使用
    saved_player_id: str | None = None  # 本晚救的玩家ID
    poisoned_player_id: str | None = None  # 本晚毒的玩家ID
    has_acted: bool = False  # 是否已行动

    def reset_night(self) -> None:
        """重置每晚状态"""
        self.saved_player_id = None
        self.poisoned_player_id = None
        self.has_acted = False

    def can_save(self) -> bool:
        """是否可以救人"""
        return not self.antidote_used

    def can_poison(self) -> bool:
        """是否可以毒人"""
        return not self.poison_used


class WitchRole(BaseRole):
    """女巫角色"""

    @property
    def role_type(self) -> "Role":
        from ..models import Role

        return Role.WITCH

    @property
    def name(self) -> str:
        return "女巫"

    @property
    def emoji(self) -> str:
        return "💊"

    @property
    def description(self) -> str:
        return "拥有一瓶解药和一瓶毒药，各只能使用一次"

    @property
    def goal(self) -> str:
        return "帮助好人获胜"

    def get_role_info(self, player: "Player", room: "GameRoom") -> str:
        """获取女巫角色信息"""
        return (
            f"🎭 游戏开始！你的角色是：\n\n"
            f"{self.emoji} {self.name}\n\n"
            f"你的目标：{self.goal}！\n\n"
            f"你拥有两种药：\n"
            f"💉 解药：可以救活当晚被杀的人（只能用一次）\n"
            f"💊 毒药：可以毒杀任何人（只能用一次）\n\n"
            f"⚠️ 注意：\n"
            f"• 同一晚不能同时使用两种药\n"
            f"• 解药只能救当晚被杀的人\n"
            f"• 每晚女巫行动时会告知谁被杀\n\n"
            f"💡 夜晚私聊使用命令：\n"
            f"  {cmd('救人')} - 救活被杀的人\n"
            f"  {cmd('毒人')} 编号 - 毒杀某人\n"
            f"  {cmd('不操作')} - 不使用任何药"
        )

    def get_night_commands(self) -> list[str]:
        return [cmd("救人"), f"{cmd('毒人')} 编号", cmd("不操作")]

    def get_action_prompt(self, room: "GameRoom") -> str:
        """获取女巫行动提示"""
        witch_state = room.witch_state

        if not room.last_killed_id:
            killed_info = "今晚没有人被杀！"
        else:
            killed_player = room.get_player(room.last_killed_id)
            killed_name = killed_player.display_name if killed_player else "未知"
            killed_info = f"今晚被杀的是：{killed_name}"

        poison_status = "已使用" if witch_state.poison_used else "可用"
        antidote_status = "已使用" if witch_state.antidote_used else "可用"

        return (
            f"💊 女巫行动阶段\n\n"
            f"{killed_info}\n\n"
            f"💊 毒药状态：{poison_status}\n"
            f"💉 解药状态：{antidote_status}\n\n"
            f"命令：\n"
            f"  {cmd('救人')} - 使用解药救此人\n"
            f"  {cmd('毒人')} 编号 - 使用毒药\n"
            f"  {cmd('不操作')} - 不使用道具"
        )
