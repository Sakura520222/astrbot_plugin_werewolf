"""Validated ballots distinguish abstention from failed generation."""

from typing import TYPE_CHECKING

from ..context import ContextBuilder
from ..prompts import VOTE_TIPS
from .base import BaseAction
from .speech import SpeechAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class VoteAction(BaseAction):
    """Keep the public explanation and submitted target in one decision."""

    async def decide_vote(
        self,
        player: "Player",
        room: "GameRoom",
        is_pk: bool = False,
        pk_candidates: list[int] | None = None,
    ) -> tuple[str, int | None]:
        """Repair malformed ballots with a finite retry budget.

        Args:
            player: Voter.
            room: Current room.
            is_pk: Whether voting is restricted to runoff candidates.
            pk_candidates: Allowed seats; missing candidates allow abstention only.

        Returns:
            Complete public speech and a legal target, or an observable failure
            fallback. Only valid explicit abstention is classified as voluntary.
        """
        role = ContextBuilder.get_role_key(player)
        decision = await self._request_decision(
            player,
            room,
            "day_vote",
            guidance=VOTE_TIPS.get(role, VOTE_TIPS["villager"]),
            candidates=(pk_candidates or []) if is_pk else None,
        )
        if decision is None:
            return (SpeechAction.fallback_speech(player, room, "day_vote"), None)
        speech = decision.speech
        if not speech:
            speech = (
                "这轮我主动弃票。"
                if decision.abstain
                else f"这票投{decision.target_number}号。"
            )
        return (speech, decision.target_number)
