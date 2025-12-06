"""预言家行动 - 验人"""
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


class SeerAction(BaseAction):
    """预言家行动"""

    async def decide_check(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI预言家选择验人目标"""
        context = ContextBuilder.build_context(player, room)
        role_key = ContextBuilder.get_role_key(player)
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")

        prompt = ROLE_PROMPTS["seer_check"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context
        )

        response = await self._call_llm(prompt, player)
        if response:
            target = self.extract_number(response)
            if target:
                # 预言家可以验存活的人
                validated = TargetValidator.validate_check_target(room, target, player)
                if validated:
                    return validated
                # 如果验证失败，随机选择
                logger.warning(f"[狼人杀AI] 预言家 {player.name} 选择的目标无效，随机选择")
                valid_targets = TargetValidator.get_valid_targets(room, exclude_player=player)
                # 排除已经验过的人
                if player.ai_context and player.ai_context.seer_results:
                    checked = [r.get('target_number') for r in player.ai_context.seer_results]
                    valid_targets = [t for t in valid_targets if t not in checked]
                if valid_targets:
                    import random
                    return random.choice(valid_targets)
        return None
