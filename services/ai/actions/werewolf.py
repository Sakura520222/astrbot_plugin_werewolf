"""狼人行动 - 杀人和密谋"""
from typing import Optional, TYPE_CHECKING
from astrbot.api import logger

from .base import BaseAction
from ..validators import TargetValidator
from ..context import ContextBuilder, SituationAnalyzer
from ..prompts import (
    ANTI_HALLUCINATION_PROTOCOL,
    HUMAN_STYLE_TIPS,
    ROLE_SOUL_SETTINGS,
    ROLE_PROMPTS
)

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class WerewolfAction(BaseAction):
    """狼人行动"""

    async def decide_kill(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI狼人选择击杀目标"""
        context = ContextBuilder.build_context(player, room)
        role_key = ContextBuilder.get_role_key(player)
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")
        tactical_directive = SituationAnalyzer.get_tactical_directive(player, room)

        prompt = ROLE_PROMPTS["werewolf_kill"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            tactical_directive=tactical_directive
        )

        response = await self._call_llm(prompt, player)
        if response:
            target = self.extract_number(response)
            if target:
                # 使用验证器确保目标有效
                validated = TargetValidator.validate_kill_target(room, target, player)
                if validated:
                    return validated
                # 如果验证失败，尝试随机选择存活目标
                logger.warning(f"[狼人杀AI] 狼人 {player.name} 选择的目标无效，随机选择")
                valid_targets = TargetValidator.get_valid_targets(room, exclude_player=player)
                # 排除狼队友
                wolves = room.get_alive_werewolves()
                wolf_numbers = [w.number for w in wolves]
                valid_targets = [t for t in valid_targets if t not in wolf_numbers]
                if valid_targets:
                    import random
                    return random.choice(valid_targets)
        return None

    async def decide_chat(self, player: "Player", room: "GameRoom") -> Optional[str]:
        """AI狼人生成密谋消息"""
        context = ContextBuilder.build_context(player, room)

        prompt = ROLE_PROMPTS["werewolf_chat"].format(
            context=context,
            human_style=HUMAN_STYLE_TIPS
        )

        response = await self._call_llm(prompt, player)
        if response:
            return response[:50]
        return None
