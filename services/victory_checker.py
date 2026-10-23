"""胜负判定服务"""

from typing import TYPE_CHECKING

from ..models.game_rules import GameRules

if TYPE_CHECKING:
    from ..models import GameRoom


class VictoryChecker:
    """胜负判定服务"""

    @staticmethod
    def check(room: "GameRoom") -> tuple[str | None, str | None]:
        """Apply the shared authoritative victory rules.

        Args:
            room: Current room.

        Returns:
            Victory message and faction, or (None, None) while play continues.
        """
        return GameRules.outcome(room)

    @staticmethod
    def get_all_players_roles(room: "GameRoom") -> str:
        """获取所有玩家的身份列表"""
        from ..models import Role

        result = "📜 身份公布：\n\n"

        # 按角色分组
        role_groups = {
            Role.WEREWOLF: [],
            Role.SEER: [],
            Role.WITCH: [],
            Role.HUNTER: [],
            Role.VILLAGER: [],
        }

        for player in room.players.values():
            if player.role in role_groups:
                role_groups[player.role].append(player.display_name)

        # 格式化输出
        role_info = [
            (Role.WEREWOLF, "🐺 狼人"),
            (Role.SEER, "🔮 预言家"),
            (Role.WITCH, "💊 女巫"),
            (Role.HUNTER, "🔫 猎人"),
            (Role.VILLAGER, "👤 平民"),
        ]

        for role, title in role_info:
            players = role_groups.get(role, [])
            if players:
                result += f"{title}：\n"
                for name in players:
                    result += f"  • {name}\n"
                result += "\n"

        return result
