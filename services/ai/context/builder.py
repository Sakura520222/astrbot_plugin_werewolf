"""上下文构建器 - 构建AI玩家的游戏上下文"""
import re
from typing import TYPE_CHECKING
from astrbot.api import logger

from ..prompts import (
    PEACEFUL_NIGHT_TIPS,
    DOUBLE_DEATH_TIPS
)

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class ContextBuilder:
    """AI上下文构建器"""

    @staticmethod
    def build_context(player: "Player", room: "GameRoom") -> str:
        """构建游戏上下文"""
        if player.ai_context:
            return player.ai_context.to_prompt_context()
        return f"你是{player.number}号玩家"

    @staticmethod
    def get_role_key(player: "Player") -> str:
        """获取角色key"""
        if not player.role:
            return "villager"
        role_map = {
            "werewolf": "werewolf",
            "seer": "seer",
            "witch": "witch",
            "hunter": "hunter",
            "villager": "villager"
        }
        return role_map.get(player.role.value, "villager")

    @staticmethod
    def get_peaceful_night_tip(player: "Player", room: "GameRoom") -> str:
        """获取平安夜特殊提示词"""
        if not player.ai_context:
            return ""

        events = player.ai_context.game_events
        current_round = room.current_round
        is_peaceful_night = False
        has_death_event = False
        saved_player_name = None
        killed_target_name = None

        for event in events[-10:]:
            if f"第{current_round}夜：平安夜" in event:
                is_peaceful_night = True
            if f"第{current_round}夜死亡" in event:
                has_death_event = True
            if "救了" in event or "使用解药" in event:
                match = re.search(r'救了\s*(\S+)', event)
                if match:
                    saved_player_name = match.group(1)

        # 防御性检查
        if is_peaceful_night and has_death_event:
            logger.error(f"[BUG检测] 第{current_round}夜同时存在平安夜和死亡事件！")
            return ""

        if has_death_event or not is_peaceful_night:
            return ""

        role_key = ContextBuilder.get_role_key(player)

        if role_key == "witch":
            if player.ai_context.last_killed_player:
                saved_player_name = player.ai_context.last_killed_player
            if saved_player_name:
                return PEACEFUL_NIGHT_TIPS["witch"].format(saved_player=saved_player_name)
            return ""

        if role_key == "werewolf":
            for event in events[-10:]:
                if "选择刀" in event or "击杀" in event:
                    match = re.search(r'刀\s*(\S+)|击杀\s*(\S+)', event)
                    if match:
                        killed_target_name = match.group(1) or match.group(2)
            if killed_target_name:
                return PEACEFUL_NIGHT_TIPS["werewolf"].format(killed_target=killed_target_name)
            return PEACEFUL_NIGHT_TIPS["werewolf"].format(killed_target="某人（你们昨晚的目标）")

        return PEACEFUL_NIGHT_TIPS["good"]

    @staticmethod
    def get_double_death_tip(player: "Player", room: "GameRoom") -> str:
        """获取双死特殊提示词"""
        if not player.ai_context:
            return ""

        events = player.ai_context.game_events
        dead_players = []
        wolf_killed_name = None
        witch_poisoned_name = None
        current_round = room.current_round

        for event in events[-10:]:
            if "死亡" in event and f"第{current_round}夜" in event:
                death_match = re.search(r'死亡[：:]\s*(.+)', event)
                if death_match:
                    dead_str = death_match.group(1)
                    names = [n.strip() for n in dead_str.split(',') if n.strip()]
                    for name in names:
                        if name and name not in dead_players:
                            dead_players.append(name)

            if "选择刀" in event or "击杀" in event or "狼人杀" in event:
                match = re.search(r'刀\s*(\S+)|击杀\s*(\S+)|杀.*?(\d+号)', event)
                if match:
                    wolf_killed_name = match.group(1) or match.group(2) or match.group(3)

            if "毒" in event and ("使用" in event or "毒了" in event or "毒死" in event):
                match = re.search(r'毒.*?(\d+号\S*|\S+)', event)
                if match:
                    witch_poisoned_name = match.group(1)

        if len(dead_players) < 2:
            return ""

        dead_player_a = dead_players[-2] if len(dead_players) >= 2 else dead_players[0]
        dead_player_b = dead_players[-1]

        role_key = ContextBuilder.get_role_key(player)

        if role_key == "witch":
            if witch_poisoned_name:
                other_dead = dead_player_b if witch_poisoned_name in dead_player_a else dead_player_a
                return DOUBLE_DEATH_TIPS["witch"].format(
                    dead_player_a=dead_player_a,
                    dead_player_b=dead_player_b,
                    poisoned_player=witch_poisoned_name,
                    other_dead=other_dead
                )
            return ""

        if role_key == "werewolf":
            if wolf_killed_name:
                witch_poisoned = dead_player_b if wolf_killed_name in dead_player_a else dead_player_a
                return DOUBLE_DEATH_TIPS["werewolf"].format(
                    dead_player_a=dead_player_a,
                    dead_player_b=dead_player_b,
                    wolf_killed=wolf_killed_name,
                    witch_poisoned=witch_poisoned
                )
            return DOUBLE_DEATH_TIPS["werewolf"].format(
                dead_player_a=dead_player_a,
                dead_player_b=dead_player_b,
                wolf_killed="你们刀的那个",
                witch_poisoned="另一个死者"
            )

        return DOUBLE_DEATH_TIPS["good"].format(
            dead_player_a=dead_player_a,
            dead_player_b=dead_player_b
        )

    @staticmethod
    def get_special_event_tip(player: "Player", room: "GameRoom") -> str:
        """获取特殊事件提示词（平安夜或双死）"""
        # 先检查双死
        double_death_tip = ContextBuilder.get_double_death_tip(player, room)
        if double_death_tip:
            return double_death_tip

        # 再检查平安夜
        peaceful_tip = ContextBuilder.get_peaceful_night_tip(player, room)
        if peaceful_tip:
            return peaceful_tip

        return ""
