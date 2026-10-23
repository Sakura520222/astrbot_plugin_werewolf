"""Seer choices retain their actual, time-bounded reason."""

from typing import TYPE_CHECKING

from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class SeerAction(BaseAction):
    """Choose a living player without guessing a number from explanation prose."""

    async def decide_check(self, player: "Player", room: "GameRoom") -> int | None:
        """Choose an authorized target.

        Args:
            player: Living seer.
            room: Current room.

        Returns:
            Legal target number or None after bounded failure.
        """
        if not player.role or player.role.value != "seer" or not player.is_alive:
            return None
        decision = await self._request_decision(player, room, "seer_check")
        return decision.target_number if decision else None
