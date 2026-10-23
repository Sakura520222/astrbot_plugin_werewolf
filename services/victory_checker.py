"""胜负判定服务"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import GameRoom


class VictoryChecker:
    """胜负判定服务"""

    @staticmethod
    def check(room: "GameRoom") -> tuple[str | None, str | None]:
        """
        检查胜利条件

        返回: (胜利消息, 胜利阵营) 或 (None, None) 表示游戏继续
        胜利阵营: "werewolf" 或 "villager"
        """
        alive_players = room.get_alive_players()
        alive_werewolves = [p for p in alive_players if p.is_werewolf]
        alive_goods = [p for p in alive_players if p.is_good]
        alive_gods = [p for p in alive_players if p.is_god]

        werewolf_count = len(alive_werewolves)
        good_count = len(alive_goods)

        # 狼人全灭 -> 好人胜利
        if werewolf_count == 0:
            return "好人胜利！所有狼人已被放逐！", "villager"

        # 好人数量 <= 狼人数量 -> 狼人胜利
        if good_count <= werewolf_count:
            return "狼人胜利！好人数量不足！", "werewolf"

        # 神职全灭 -> 狼人胜利
        if len(alive_gods) == 0 and werewolf_count > 0:
            return "狼人胜利！所有神职人员已出局！", "werewolf"

        # 游戏继续
        return None, None

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
