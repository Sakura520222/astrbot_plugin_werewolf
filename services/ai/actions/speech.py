"""Structured intent followed by complete, uncut public expression."""

from typing import TYPE_CHECKING

from ..context import ContextBuilder
from ..prompts import LAST_WORDS_TIPS, PK_TIPS, SPEECH_TIPS
from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class SpeechAction(BaseAction):
    """Generate speech without character slicing or shared stock strategies."""

    async def generate_speech(
        self, player: "Player", room: "GameRoom", is_pk: bool = False
    ) -> str:
        """Decide a stance before rendering the player's public words.

        Args:
            player: Current speaker.
            room: Active room.
            is_pk: Whether this is runoff defence.

        Returns:
            The entire speech field, or an explicit neutral failure fallback.
        """
        role = ContextBuilder.get_role_key(player)
        tips = PK_TIPS if is_pk else SPEECH_TIPS
        decision = await self._request_decision(
            player,
            room,
            "pk_speech" if is_pk else "day_speech",
            guidance=tips.get(role, tips["villager"]),
        )
        if decision:
            return decision.speech
        return "这轮我还没有形成可靠判断。"

    async def generate_last_words(self, player: "Player", room: "GameRoom") -> str:
        """Leave complete role information within an authorized last-words turn.

        Args:
            player: The explicitly selected last-words speaker.
            room: Active room.

        Returns:
            Complete last words, preserving all seer results on failure as well.
        """
        role = ContextBuilder.get_role_key(player)
        decision = await self._request_decision(
            player,
            room,
            "last_words",
            guidance=LAST_WORDS_TIPS.get(role, LAST_WORDS_TIPS["villager"]),
        )
        if decision:
            return decision.speech
        if role == "seer" and player.ai_context and player.ai_context.seer_results:
            results = [
                f"第{item['round']}夜我验{item['target']}，结果是{'狼人' if item['is_werewolf'] else '好人'}"
                for item in player.ai_context.seer_results
            ]
            return "我是预言家。" + "；".join(results) + "。其余身份我不能确认。"
        return "我的判断还请结合实际发言和票型继续核对。"
