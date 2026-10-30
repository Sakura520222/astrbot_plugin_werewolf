"""Compose a role-scoped, layered view of real room state."""

from typing import TYPE_CHECKING

from ....models.ai_player import AIPlayerContext
from ....models.game_rules import GameRules
from ..prompts import DOUBLE_DEATH_TIPS, PEACEFUL_NIGHT_TIPS

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class ContextBuilder:
    """Refresh authoritative identity before every model call."""

    @staticmethod
    def refresh_context(player: "Player", room: "GameRoom") -> AIPlayerContext:
        """Synchronize public state and only the actor's permitted private state.

        Args:
            player: Player receiving a prompt.
            room: Authoritative room.

        Returns:
            The player's own context, with original records preserved.
        """
        if player.ai_context is None:
            player.ai_context = AIPlayerContext()
        ctx = player.ai_context
        ctx.player_number = player.number
        ctx.player_name = player.name
        ctx.role_name = player.role.display_name if player.role else "未知"
        ctx.is_werewolf = bool(player.role and player.role.value == "werewolf")
        ctx.current_round = room.current_round
        ctx.current_phase = room.phase.value
        if room.vote_state.is_pk_vote or room.phase.value == "PK发言":
            names = [
                p.display_name
                for p in room.get_alive_players()
                if p.id in room.vote_state.pk_players
            ]
            ctx.current_phase += "；公开PK候选：" + "、".join(names)
        ctx.rulebook = GameRules.describe(room.config)
        ctx.update_alive_players(
            [
                p.display_name
                for p in sorted(room.get_alive_players(), key=lambda p: p.number)
            ],
            [
                p.display_name
                for p in sorted(room.players.values(), key=lambda p: p.number)
                if not p.is_alive
            ],
        )
        if ctx.is_werewolf:
            ctx.werewolf_teammates = [
                p.display_name for p in room.get_werewolves() if p.id != player.id
            ]
        if player.role and player.role.value == "witch":
            ctx.witch_antidote_used = room.witch_state.antidote_used
            ctx.witch_poison_used = room.witch_state.poison_used
            if room.phase.value == "夜晚-女巫行动":
                killed = room.get_player(room.last_killed_id)
                ctx.last_killed_player = killed.display_name if killed else None
        if player.role and player.role.value == "hunter":
            ctx.can_shoot = (
                room.hunter_state.pending_shot_player_id == player.id
                and room.hunter_state.can_shoot()
            )
        return ctx

    @staticmethod
    def build_context(
        player: "Player",
        room: "GameRoom",
        *,
        extra_evidence_ids: list[str] | None = None,
    ) -> str:
        """Render the current view, retaining all source records in memory.

        Args:
            player: Acting player.
            room: Current room.
            extra_evidence_ids: Optional public S/D records to expand completely.

        Returns:
            Rules, identity, personality, public evidence, private facts and beliefs.
        """
        return ContextBuilder.refresh_context(player, room).to_prompt_context(
            layered=True, extra_evidence_ids=extra_evidence_ids
        )

    @staticmethod
    def get_role_key(player: "Player") -> str:
        """Return the authoritative role key.

        Args:
            player: Acting player.

        Returns:
            Role identifier, defaulting to villager before assignment.
        """
        return player.role.value if player.role else "villager"

    @staticmethod
    def get_peaceful_night_tip(player: "Player", room: "GameRoom") -> str:
        """Describe a publicly announced peaceful night without inventing rescue.

        Args:
            player: Player with an individual event history.
            room: Current room.

        Returns:
            Optional guidance scoped to the player's role.
        """
        if (
            not player.ai_context
            or f"第{room.current_round}夜：平安夜" not in player.ai_context.game_events
        ):
            return ""
        role = ContextBuilder.get_role_key(player)
        return PEACEFUL_NIGHT_TIPS.get(role, PEACEFUL_NIGHT_TIPS["good"])

    @staticmethod
    def get_double_death_tip(player: "Player", room: "GameRoom") -> str:
        """Use the announcement alone to identify a multiple-death scenario.

        Args:
            player: Player with the public announcement.
            room: Current room.

        Returns:
            Optional guidance without querying secret death causes.
        """
        if not player.ai_context:
            return ""
        prefix = f"第{room.current_round}夜死亡："
        multiple = any(
            event.startswith(prefix)
            and len(event.removeprefix(prefix).split(", ")) >= 2
            for event in player.ai_context.game_events
        )
        if not multiple:
            return ""
        role = ContextBuilder.get_role_key(player)
        return DOUBLE_DEATH_TIPS.get(role, DOUBLE_DEATH_TIPS["good"])

    @staticmethod
    def get_special_event_tip(player: "Player", room: "GameRoom") -> str:
        """Return event guidance derived only from observed announcements.

        Args:
            player: Acting player.
            room: Current room.

        Returns:
            Multiple-death or peaceful-night guidance, if applicable.
        """
        tip = ContextBuilder.get_double_death_tip(
            player, room
        ) or ContextBuilder.get_peaceful_night_tip(player, room)
        if not tip:
            return ""
        return (
            "【特殊事件的内部核对】"
            + tip
            + "\n这些是reason中的信息边界，不是必须在speech复述的发言任务；前面已有人解释过时直接承接实际争议，没有新内容也可简短过。"
        )
