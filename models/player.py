"""玩家数据模型"""
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .ai_player import AIPlayerConfig, AIPlayerContext

from .enums import Role


@dataclass
class Player:
    """玩家"""
    id: str                          # 玩家ID（QQ号，AI玩家使用 ai_{name} 格式）
    name: str                        # 玩家昵称
    number: int = 0                  # 玩家编号（1-9）
    role: Optional[Role] = None      # 角色
    is_alive: bool = True            # 是否存活
    original_card: str = ""          # 原始群昵称（用于恢复）

    # AI玩家相关
    is_ai: bool = False              # 是否是AI玩家
    ai_config: Optional["AIPlayerConfig"] = None    # AI玩家配置
    ai_context: Optional["AIPlayerContext"] = None  # AI玩家游戏上下文

    @property
    def display_name(self) -> str:
        """格式化显示名称：编号.昵称"""
        return f"{self.number}号.{self.name}"

    @property
    def is_werewolf(self) -> bool:
        """是否是狼人"""
        return self.role == Role.WEREWOLF

    @property
    def is_god(self) -> bool:
        """是否是神职"""
        return self.role and self.role.is_god

    @property
    def is_good(self) -> bool:
        """是否是好人阵营"""
        return self.role and self.role.is_good

    def kill(self) -> None:
        """标记玩家死亡"""
        self.is_alive = False

    def assign_role(self, role: Role) -> None:
        """分配角色"""
        self.role = role

    def assign_number(self, number: int) -> None:
        """分配编号"""
        self.number = number
