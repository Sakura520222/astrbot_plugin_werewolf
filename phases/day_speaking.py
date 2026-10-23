"""Daytime and PK speaking with ownership checks around asynchronous work."""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase
from ..services import BanService
from ..utils import cmd
from .base import BasePhase

if TYPE_CHECKING:
    from ..models import GameRoom


class DaySpeakingPhase(BasePhase):
    """Advance living speakers without letting stale AI replies restart the day."""

    @property
    def name(self) -> str:
        return "发言阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_speaking

    def _is_current_phase(self, room: "GameRoom") -> bool:
        return self.game_manager.get_room(room.group_id) is room and room.phase in (
            GamePhase.DAY_SPEAKING,
            GamePhase.DAY_PK,
        )

    def _is_active(self, room: "GameRoom", generation: int) -> bool:
        """Check ownership of the current speaking turn.

        Args:
            room: Room whose speaker is active.
            generation: Generation captured before asynchronous work.

        Returns:
            Whether this task still owns the current speaking turn.
        """
        return self._is_current_phase(room) and room.speaking_generation == generation

    async def on_enter(self, room: "GameRoom") -> None:
        """Begin the day's speaking order.

        Args:
            room: Room entering ordinary daytime speaking.
        """
        if self.game_manager.get_room(room.group_id) is not room:
            return
        room.cancel_timer()
        room.speaking_generation += 1
        generation = room.speaking_generation
        room.phase = GamePhase.DAY_SPEAKING
        room.speaking_state.reset()
        room.speaking_state.order = [p.id for p in room.get_alive_players()]
        await BanService.set_group_whole_ban(room, True)
        if self._is_active(room, generation):
            await self._next_speaker(room)

    async def on_timeout(self, room: "GameRoom") -> None:
        """Finish the current turn after expiry.

        Args:
            room: Room whose current turn expired.
        """
        await self.on_finish_speaking(room, timed_out=True)

    async def on_finish_speaking(
        self,
        room: "GameRoom",
        *,
        generation: int | None = None,
        timed_out: bool = False,
    ) -> None:
        """Claim one turn's completion before awaiting platform calls.

        Args:
            room: Room whose current speaker is finishing.
            generation: Expected speaker generation captured by the caller.
            timed_out: Whether this completion was caused by expiry.
        """
        if generation is None:
            generation = room.speaking_generation
        if not self._is_active(room, generation):
            return
        speaker_id = room.speaking_state.current_speaker_id
        if speaker_id is None:
            return
        room.speaking_state.current_speaker_id = None
        room.cancel_timer()
        self._record_speech(room, speaker_id)
        await BanService.remove_temp_admin(room, speaker_id)
        if not self._is_active(room, generation):
            return
        if timed_out:
            player = room.get_player(speaker_id)
            await self.message_service.send_group_message(
                room,
                f"⏰ {player.display_name if player else '当前玩家'} 发言超时！自动进入下一位。",
            )
            if not self._is_active(room, generation):
                return
        room.speaking_state.current_index += 1
        await self._next_speaker(room)

    def _record_speech(self, room: "GameRoom", player_id: str) -> None:
        """Record the complete public speech once and broadcast it to AI contexts.

        Args:
            room: Room containing the current speech buffer.
            player_id: Player who supplied the buffered speech.
        """
        player = room.get_player(player_id)
        if player is None:
            return
        speech = " ".join(room.speaking_state.current_speech)
        room.speaking_state.current_speech.clear()
        if not speech:
            return
        is_pk = room.phase == GamePhase.DAY_PK
        room.log(f"💬{'PK' if is_pk else ''}发言：{player.display_name} - {speech}")
        for observer in room.players.values():
            if observer.is_ai and observer.ai_context:
                observer.ai_context.current_round = room.current_round
                observer.ai_context.add_speech(player.display_name, speech, is_pk)

    async def _next_speaker(self, room: "GameRoom") -> None:
        """Select a living ordinary or PK speaker and launch their owned turn.

        Args:
            room: Room advancing its speaking order.
        """
        if not self._is_current_phase(room):
            return
        speaking = room.speaking_state
        is_pk = room.phase == GamePhase.DAY_PK
        order = room.vote_state.pk_players if is_pk else speaking.order
        while speaking.current_index < len(order):
            player = room.get_player(order[speaking.current_index])
            if player and player.is_alive:
                break
            speaking.current_index += 1
        else:
            speaking.current_speaker_id = None
            if is_pk:
                await self._enter_pk_vote(room)
            else:
                await self._enter_vote_phase(room)
            return

        room.speaking_generation += 1
        generation = room.speaking_generation
        speaking.current_speaker_id = player.id
        speaking.current_speech.clear()
        if player.is_ai:
            await self._handle_ai_speech(room, player, is_pk)
            return
        await BanService.set_temp_admin(room, player.id)
        if not self._is_active(room, generation):
            return
        await self.message_service.send_group_at_message(
            room,
            player,
            f" {'PK发言：' if is_pk else ''}现在轮到你发言\n\n"
            f"⏰ 发言时间：{self.timeout_seconds}秒\n"
            f"💡 发言完毕后请使用：{cmd('发言完毕')}\n\n"
            f"进度：{speaking.current_index + 1}/{len(order)}",
        )
        if not self._is_active(room, generation):
            return

        async def speaker_timer():
            try:
                await asyncio.sleep(self.timeout_seconds)
                await self.on_finish_speaking(
                    room, generation=generation, timed_out=True
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Werewolf] Speaker timer failed")

        room.set_timer(asyncio.create_task(speaker_timer()))

    async def _handle_ai_speech(
        self, room: "GameRoom", player, is_pk: bool = False
    ) -> None:
        """Discard a slow AI response if its speaker, phase or room changed.

        Args:
            room: Room containing the active turn.
            player: AI player whose speech is being generated.
            is_pk: Whether the speech belongs to PK.
        """
        generation = room.speaking_generation
        if not self._is_active(room, generation) or not player.is_alive:
            return
        try:
            service = self.game_manager.ai_player_service
            service.update_ai_context(player, room)
            speech = await service.generate_speech(player, room, is_pk)
        except Exception:
            logger.exception("[Werewolf] AI speech generation failed")
            from ..services.ai.actions.speech import SpeechAction

            speech = SpeechAction.fallback_speech(
                player, room, "pk_speech" if is_pk else "day_speech"
            )
        if (
            not self._is_active(room, generation)
            or room.speaking_state.current_speaker_id != player.id
            or not player.is_alive
        ):
            return
        await self.message_service.send_group_message(
            room, f"{'[PK发言]' if is_pk else ''}{player.display_name}：{speech}"
        )
        if not self._is_active(room, generation) or not player.is_alive:
            return
        room.speaking_state.current_speech = [speech]
        await self.on_finish_speaking(room, generation=generation)

    async def _next_pk_speaker(self, room: "GameRoom") -> None:
        """Advance the shared speaking implementation for a PK round.

        Args:
            room: Room whose PK speaker is advancing.
        """
        if room.phase == GamePhase.DAY_PK:
            await self._next_speaker(room)

    async def enter_pk_phase(self, room: "GameRoom", pk_player_ids: list) -> None:
        """Begin PK speeches with a fresh generation and sorted candidates.

        Args:
            room: Room entering PK speeches.
            pk_player_ids: Tied players permitted to speak and receive PK votes.
        """
        if self.game_manager.get_room(room.group_id) is not room:
            return
        room.cancel_timer()
        room.phase = GamePhase.DAY_PK
        room.speaking_generation += 1
        generation = room.speaking_generation
        room.speaking_state.reset()
        room.vote_state.pk_players = [
            p.id for p in room.get_alive_players() if p.id in pk_player_ids
        ]
        room.vote_state.is_pk_vote = False
        names = [
            room.get_player(pid).display_name for pid in room.vote_state.pk_players
        ]
        await self.message_service.announce_pk_start(room, names)
        if not self._is_active(room, generation):
            return
        await BanService.set_group_whole_ban(room, True)
        if self._is_active(room, generation):
            await self._next_speaker(room)

    async def _enter_vote_phase(self, room: "GameRoom") -> None:
        """Enter ordinary voting after speaking finishes."""
        from .phase_manager import PhaseManager

        await PhaseManager(self.game_manager).enter_vote_phase(room)

    async def _enter_pk_vote(self, room: "GameRoom") -> None:
        """Enter PK voting after all tied candidates have spoken."""
        from .phase_manager import PhaseManager

        await PhaseManager(self.game_manager).enter_pk_vote_phase(room)
