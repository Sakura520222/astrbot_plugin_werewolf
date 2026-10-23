"""Structured intent followed by complete, uncut public expression."""

import random
from typing import TYPE_CHECKING

from ..context import ContextBuilder
from ..prompts import LAST_WORDS_TIPS, PK_TIPS, SPEECH_TIPS
from ..prompts.fallbacks import (
    DAY_FALLBACKS,
    LAST_WORDS_FALLBACKS,
    PERSONALITY_GROUPS,
    PK_FALLBACKS,
    VOTE_FALLBACKS,
)
from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class SpeechAction(BaseAction):
    """Generate speech without character slicing or shared stock strategies."""

    @staticmethod
    def fallback_speech(
        player: "Player", room: "GameRoom", stage: str = "day_speech"
    ) -> str:
        """Choose safe, personality-aware wording without repeating recent fallbacks.

        Args:
            player: Player whose style should be retained.
            room: Room owning the per-game recovery wording history.
            stage: Ordinary speech, PK defence, ballot or last words.

        Returns:
            Complete fallback text, or empty text for a discarded stale decision.
        """
        ctx = player.ai_context
        previous = ctx.decision_audit[-1] if ctx and ctx.decision_audit else {}
        if previous.get("status") == "stale" and previous.get("task") == stage:
            return ""
        group = PERSONALITY_GROUPS.get(ctx.personality_key if ctx else "", "careful")
        if stage == "last_words":
            pool = LAST_WORDS_FALLBACKS
        elif stage == "pk_speech":
            pool = PK_FALLBACKS[group]
        elif stage == "day_vote":
            pool = VOTE_FALLBACKS[group]
        else:
            pool = DAY_FALLBACKS[group]
        choices = [text for text in pool if text not in room.used_fallback_speeches]
        if not choices:
            neutral = (
                LAST_WORDS_FALLBACKS
                if stage == "last_words"
                else (
                    PK_FALLBACKS["careful"]
                    if stage == "pk_speech"
                    else (
                        VOTE_FALLBACKS["careful"]
                        if stage == "day_vote"
                        else DAY_FALLBACKS["careful"]
                    )
                )
            )
            choices = [
                text for text in neutral if text not in room.used_fallback_speeches
            ]
        if not choices:
            choices = [
                text for text in pool if text != room.last_fallback_speech
            ] or list(pool)
        speech = random.choice(choices)
        room.used_fallback_speeches.add(speech)
        room.last_fallback_speech = speech
        if ctx:
            if (
                previous.get("round") != room.current_round
                or previous.get("task") != stage
                or previous.get("status") != "failed_fallback"
            ):
                ctx.decision_audit.append(
                    {
                        "round": room.current_round,
                        "task": stage,
                        "status": "failed_fallback",
                    }
                )
            ctx.decision_audit[-1]["fallback_speech"] = speech
        return speech

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
        return self.fallback_speech(
            player, room, "pk_speech" if is_pk else "day_speech"
        )

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
        return self.fallback_speech(player, room, "last_words")
