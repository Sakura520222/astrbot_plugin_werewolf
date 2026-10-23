"""Last-words phase."""

import asyncio
import random
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase
from ..services import BanService
from ..utils import cmd
from .base import BasePhase

if TYPE_CHECKING:
    from ..models import GameRoom


class LastWordsPhase(BasePhase):
    """Manage the explicitly selected dead player's last words."""

    @property
    def name(self) -> str:
        return "遗言阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_speaking

    def _is_current_phase(
        self, room: "GameRoom", generation: int | None = None
    ) -> bool:
        """Check room identity and, when supplied, the last-words generation."""
        return (
            self.game_manager.rooms.get(room.group_id) is room
            and room.phase == GamePhase.LAST_WORDS
            and (generation is None or room.last_words_generation == generation)
        )

    async def on_enter(self, room: "GameRoom") -> None:
        """Enter last words for the player explicitly selected by settlement."""
        room.cancel_timer()
        room.phase = GamePhase.LAST_WORDS
        room.last_words_generation += 1
        room.last_words_finishing = False
        generation = room.last_words_generation
        player = room.get_player(room.last_words_player_id)
        if not player:
            await self._finish_last_words(room)
            return

        room.speaking_state.current_speech.clear()
        await BanService.set_group_whole_ban(room, True)
        if not self._is_current_phase(room, generation) or room.last_words_finishing:
            return
        if player.is_ai:
            await self._handle_ai_last_words(room, player)
            return

        await BanService.set_temp_admin(room, player.id)
        if not self._is_current_phase(room, generation) or room.last_words_finishing:
            return
        await self.message_service.send_group_at_message(
            room,
            player,
            f" 现在请你留遗言\n\n⏰ 遗言时间：{self.timeout_seconds}秒\n"
            f"💡 遗言完毕后请使用：{cmd('遗言完毕')}",
        )
        if not self._is_current_phase(room, generation) or room.last_words_finishing:
            return
        await self.start_timer(room)

    async def _handle_ai_last_words(self, room: "GameRoom", player) -> None:
        """Generate last words without allowing a stale result to be published."""
        generation = room.last_words_generation
        await asyncio.sleep(random.uniform(3, 6))
        if not self._is_current_phase(room, generation) or room.last_words_finishing:
            return
        last_words = await self.game_manager.ai_player_service.generate_last_words(
            player, room
        )
        if not self._is_current_phase(room, generation) or room.last_words_finishing:
            return
        await self._finish_last_words(room, ai_last_words=last_words)

    async def on_timeout(self, room: "GameRoom") -> None:
        """Finish last words after the timer expires."""
        await self._finish_last_words(room, timed_out=True)

    async def on_finish(
        self, room: "GameRoom", *, generation: int | None = None
    ) -> None:
        """Finish last words only for the generation captured by the command."""
        if generation is not None and not self._is_current_phase(room, generation):
            return
        await self._finish_last_words(room)

    def _record_last_words(self, room: "GameRoom") -> None:
        """Record the complete speech and synchronize public AI context."""
        player = room.get_player(room.last_words_player_id)
        if not player:
            return
        speech_list = room.speaking_state.current_speech
        if speech_list:
            full_speech = " ".join(speech_list)
            room.log(f"💀遗言：{player.display_name} - {full_speech}")
            for participant in room.players.values():
                if participant.is_ai and participant.ai_context:
                    participant.ai_context.add_event(
                        f"遗言 {player.display_name}：{full_speech}"
                    )
        else:
            room.log(f"💀遗言：{player.display_name} - [未捕获到文字内容]")
        speech_list.clear()

    async def _finish_last_words(
        self,
        room: "GameRoom",
        *,
        timed_out: bool = False,
        ai_last_words: str | None = None,
    ) -> None:
        """Claim completion before any await, then clean up and advance once.

        Args:
            room: Room whose last words are being completed.
            timed_out: Whether to announce expiration of the speech timer.
            ai_last_words: Complete AI speech to publish and record, if generated.
        """
        if not self._is_current_phase(room) or room.last_words_finishing:
            return
        room.last_words_finishing = True
        generation = room.last_words_generation
        player_id = room.last_words_player_id
        player = room.get_player(player_id)
        from_vote = room.last_words_from_vote
        room.cancel_timer()

        if ai_last_words is not None and player:
            room.speaking_state.current_speech = [ai_last_words]
        self._record_last_words(room)
        if ai_last_words is not None and player:
            await self.message_service.send_group_message(
                room, f"💀 {player.display_name} 的遗言：{ai_last_words}"
            )
            if not self._is_current_phase(room, generation):
                return
            logger.info("[Werewolf] AI last words completed for %s", player.name)

        if player_id:
            await BanService.remove_temp_admin(room, player_id)
            if not self._is_current_phase(room, generation):
                return
            await BanService.ban_player(room, player_id)
            if not self._is_current_phase(room, generation):
                return
        await BanService.set_group_whole_ban(room, True)
        if not self._is_current_phase(room, generation):
            return
        if timed_out:
            await self.message_service.announce_timeout(room, "遗言")
            if not self._is_current_phase(room, generation):
                return

        room.last_words_from_vote = False
        room.last_words_player_id = None
        if await self.game_manager.check_and_handle_victory(room):
            return
        if not self._is_current_phase(room, generation):
            return

        from .phase_manager import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        room.end_first_night()
        if from_vote:
            await phase_manager.enter_night_phase(room)
        else:
            room.last_killed_id = None
            await phase_manager.enter_speaking_phase(room)
