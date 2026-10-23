"""Witch output parsing never confuses 'do not save' with saving."""

from typing import TYPE_CHECKING

from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class WitchAction(BaseAction):
    """Validate one available potion action against real inventory."""

    async def decide_action(
        self,
        player: "Player",
        room: "GameRoom",
        can_save: bool,
        can_poison: bool,
        killed_player_name: str | None = None,
    ) -> tuple[str, int | None]:
        """Choose save, poison or pass without creating unavailable medicine.

        Args:
            player: Acting witch.
            room: Current room.
            can_save: Caller-provided permission, checked against inventory.
            can_poison: Caller-provided permission, checked against inventory.
            killed_player_name: Private attack notification for this turn.

        Returns:
            Validated action and optional poison target.
        """
        if not player.role or player.role.value != "witch" or not player.is_alive:
            return ("pass", None)
        state = room.witch_state
        actions = ["pass"]
        if (
            can_save
            and not state.antidote_used
            and room.last_killed_id
            and not state.has_acted
        ):
            actions.append("save")
        if can_poison and not state.poison_used and not state.has_acted:
            actions.append("poison")
        guidance = (
            f"本次被刀提示：{killed_player_name or '无人被刀'}；可选操作：{actions}。"
        )
        decision = await self._request_decision(
            player,
            room,
            "witch_action",
            guidance=guidance,
            available_actions=actions,
        )
        return (decision.action, decision.target_number) if decision else ("pass", None)
