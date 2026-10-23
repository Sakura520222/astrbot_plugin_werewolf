"""Only an actually pending hunter opportunity can produce a shot."""

from typing import TYPE_CHECKING

from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class HunterAction(BaseAction):
    """Respect poison, already used shots and explicit refusal."""

    async def decide_shoot(self, player: "Player", room: "GameRoom") -> int | None:
        """Choose a legal target only while the system grants a shot.

        Args:
            player: Hunter with a pending opportunity.
            room: Current room.

        Returns:
            Target number, or None for refusal, invalid opportunity or failure.
        """
        if (
            not player.role
            or player.role.value != "hunter"
            or room.hunter_state.pending_shot_player_id != player.id
            or not room.hunter_state.can_shoot()
        ):
            return None
        decision = await self._request_decision(player, room, "hunter_shoot")
        return decision.target_number if decision else None
