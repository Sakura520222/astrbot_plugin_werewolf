"""Wolf-only decisions use the same rulebook and personality as public turns."""

from typing import TYPE_CHECKING

from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class WerewolfAction(BaseAction):
    """Choose a legal attack or send a complete private message."""

    async def decide_kill(self, player: "Player", room: "GameRoom") -> int | None:
        """Choose from living targets, including deliberate wolf attacks.

        Args:
            player: Living werewolf.
            room: Active room.

        Returns:
            Legal target number, or None for explicit abstention or failed generation.
        """
        if not player.is_werewolf or not player.is_alive:
            return None
        decision = await self._request_decision(player, room, "werewolf_kill")
        return decision.target_number if decision else None

    async def decide_chat(self, player: "Player", room: "GameRoom") -> str | None:
        """Generate private coordination without using another role's secrets.

        Args:
            player: Living werewolf.
            room: Active room.

        Returns:
            Complete private message, or None.
        """
        if not player.is_werewolf or not player.is_alive:
            return None
        decision = await self._request_decision(player, room, "werewolf_chat")
        return decision.speech if decision else None
