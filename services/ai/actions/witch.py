"""女巫行动 - 用药决策"""
from typing import Optional, Tuple, TYPE_CHECKING
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


class WitchAction(BaseAction):
    """女巫行动"""

    async def decide_action(
        self,
        player: "Player",
        room: "GameRoom",
        can_save: bool,
        can_poison: bool,
        killed_player_name: Optional[str] = None
    ) -> Tuple[str, Optional[int]]:
        """AI女巫决定用药"""
        context = ContextBuilder.build_context(player, room)
        role_key = ContextBuilder.get_role_key(player)
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")

        available_actions = []
        if killed_player_name and can_save:
            available_actions.append(f"💊 今晚 {killed_player_name} 被狼人杀害，你可以使用【解药】救他")
        if can_poison:
            available_actions.append("☠️ 你可以使用【毒药】毒死一个人")
        if not available_actions:
            available_actions.append("❌ 你的药都用完了，今晚无法行动")

        prompt = ROLE_PROMPTS["witch_action"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            available_actions="\n".join(available_actions)
        )

        response = await self._call_llm(prompt, player)
        if response:
            response_lower = response.lower()
            if "救" in response_lower or "save" in response_lower:
                return ("save", None)
            elif "毒" in response_lower or "poison" in response_lower:
                target = self.extract_number(response)
                if target:
                    # 验证毒药目标
                    validated = TargetValidator.validate_poison_target(room, target, player)
                    if validated:
                        return ("poison", validated)
                    # 如果目标无效，不毒
                    logger.warning(f"[狼人杀AI] 女巫 {player.name} 毒的目标 {target} 无效，取消操作")
        return ("pass", None)
