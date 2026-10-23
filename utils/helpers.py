"""工具函数"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import GameRoom, Player


def format_player_list(
    players: list["Player"], exclude_ids: list[str] | None = None
) -> str:
    """格式化玩家列表"""
    exclude_ids = exclude_ids or []
    lines = []
    for p in players:
        if p.id not in exclude_ids:
            lines.append(f"  • {p.display_name}")
    return "\n".join(lines)


def parse_target(target_str: str, room: "GameRoom") -> str | None:
    """解析目标玩家ID"""
    return room.parse_target(target_str)
