"""猎人行动 - 开枪决策"""
from typing import Optional, TYPE_CHECKING
from astrbot.api import logger

from .base import BaseAction
from ..validators import TargetValidator
from ..context import ContextBuilder
from ..prompts import (
    ANTI_HALLUCINATION_PROTOCOL,
    ROLE_SOUL_SETTINGS,
    ROLE_PROMPTS
)

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class HunterAction(BaseAction):
    """猎人行动"""

    async def decide_shoot(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI猎人决定开枪目标"""
        context = ContextBuilder.build_context(player, room)
        role_key = ContextBuilder.get_role_key(player)
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")

        prompt = ROLE_PROMPTS["hunter_shoot"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context
        )

        response = await self._call_llm(prompt, player)
        if response:
            if "不开枪" in response or "不开" in response:
                return None
            target = self.extract_number(response)
            if target:
                # 验证开枪目标
                validated = TargetValidator.validate_kill_target(room, target, player)
                if validated:
                    return validated
                logger.warning(f"[狼人杀AI] 猎人 {player.name} 选择的目标 {target} 无效，取消开枪")
        return None
