"""Coordinate game phase transitions."""

import asyncio
import random
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase
from ..roles import HunterDeathType, HunterRole
from ..services import BanService

if TYPE_CHECKING:
    from ..models import GameRoom
    from ..services import GameManager


class PhaseManager:
    """Coordinate phase entry and hunter resolution."""

    def __init__(self, game_manager: "GameManager"):
        self.game_manager = game_manager
        self.message_service = game_manager.message_service

    async def enter_night_phase(self, room: "GameRoom") -> None:
        """Start a new night and discard obsolete phase tasks."""
        room.cancel_timer()
        room.start_new_night()
        round_number = room.current_round
        room.log_round_start()
        await BanService.set_group_whole_ban(room, True)
        if not self._is_current(room, GamePhase.NIGHT_WOLF, round_number):
            return
        await self.message_service.announce_night_start(room)
        if not self._is_current(room, GamePhase.NIGHT_WOLF, round_number):
            return
        from .night_wolf import NightWolfPhase

        await NightWolfPhase(self.game_manager).on_enter(room)

    async def enter_seer_phase(self, room: "GameRoom") -> None:
        """Enter the seer's night phase."""
        from .night_seer import NightSeerPhase

        await NightSeerPhase(self.game_manager).on_enter(room)

    async def enter_witch_phase(self, room: "GameRoom") -> None:
        """Enter the witch's night phase."""
        from .night_witch import NightWitchPhase

        await NightWitchPhase(self.game_manager).on_enter(room)

    async def enter_speaking_phase(self, room: "GameRoom") -> None:
        """Enter daytime speaking."""
        from .day_speaking import DaySpeakingPhase

        await DaySpeakingPhase(self.game_manager).on_enter(room)

    async def enter_vote_phase(self, room: "GameRoom") -> None:
        """Enter daytime voting."""
        from .day_vote import DayVotePhase

        await DayVotePhase(self.game_manager).on_enter(room)

    async def enter_pk_vote_phase(self, room: "GameRoom") -> None:
        """Enter runoff voting."""
        from .day_vote import DayVotePhase

        await DayVotePhase(self.game_manager).enter_pk_vote(room)

    async def enter_last_words_phase(self, room: "GameRoom") -> None:
        """Enter last words for the explicitly selected speaker."""
        from .last_words import LastWordsPhase

        await LastWordsPhase(self.game_manager).on_enter(room)

    def _is_current(
        self, room: "GameRoom", phase: GamePhase, round_number: int
    ) -> bool:
        """Reject work belonging to a replaced room, phase, or round.

        Args:
            room: Original room instance.
            phase: Expected phase before an awaited operation.
            round_number: Expected round before an awaited operation.

        Returns:
            Whether the original phase is still active.
        """
        return (
            self.game_manager.rooms.get(room.group_id) is room
            and room.phase == phase
            and room.current_round == round_number
        )

    @staticmethod
    def _record_public_event(room: "GameRoom", event: str) -> None:
        """Mirror an announced hunter outcome into every AI observer's memory.

        Args:
            room: Room owning the public announcement.
            event: Complete event visible to every player.
        """
        for observer in room.players.values():
            if observer.is_ai and observer.ai_context:
                observer.ai_context.current_round = room.current_round
                observer.ai_context.add_event(event)

    async def wait_for_hunter_shot(self, room: "GameRoom", death_type) -> None:
        """Notify the hunter and wait without retaining an obsolete phase timer."""
        death_type = HunterDeathType(death_type)
        if death_type == HunterDeathType.POISON:
            return
        hunter_id = room.hunter_state.pending_shot_player_id
        hunter = room.get_player(hunter_id)
        if not hunter or room.hunter_state.has_shot:
            return
        room.cancel_timer()
        phase, round_number = room.phase, room.current_round
        await self.message_service.announce_hunter_can_shoot(room, hunter.display_name)
        if (
            not self._is_current(room, phase, round_number)
            or room.hunter_state.pending_shot_player_id != hunter_id
        ):
            return
        self._record_public_event(
            room,
            f"系统公布：{hunter.display_name} 是猎人，获得一次开枪机会（尚未行动）",
        )
        if hunter.is_ai:
            await self._handle_ai_hunter_shot(room, hunter, death_type)
            return
        prompt = HunterRole().get_death_prompt(death_type)
        await self.message_service.send_private_message(room, hunter_id, prompt)
        if (
            not self._is_current(room, phase, round_number)
            or room.hunter_state.pending_shot_player_id != hunter_id
        ):
            return
        timeout = self.game_manager.config.timeout_hunter

        async def hunter_timer():
            """Claim the hunter's opportunity before announcing its expiration."""
            try:
                await asyncio.sleep(timeout)
                if (
                    not self._is_current(room, phase, round_number)
                    or room.hunter_state.pending_shot_player_id != hunter_id
                    or room.hunter_state.has_shot
                ):
                    return
                room.hunter_state.pending_shot_player_id = None
                room.hunter_state.has_shot = True
                room.log(f"🔫 {hunter.display_name}（猎人）超时未开枪")
                self._record_public_event(
                    room,
                    f"系统公布：{hunter.display_name} 开枪超时，放弃开枪，开枪机会已结束",
                )
                await self.message_service.send_group_message(
                    room, f"⏰ {hunter.display_name} 开枪超时！放弃开枪机会。"
                )
                if not self._is_current(room, phase, round_number):
                    return
                await self._after_hunter_timeout(room, death_type)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("[Werewolf] Hunter timeout handling failed")

        room.set_timer(asyncio.create_task(hunter_timer()))

    async def _handle_ai_hunter_shot(
        self, room: "GameRoom", hunter, death_type
    ) -> None:
        """Resolve an AI shot only while its original opportunity is pending."""
        phase, round_number = room.phase, room.current_round
        await asyncio.sleep(random.uniform(2, 4))
        if (
            not self._is_current(room, phase, round_number)
            or room.hunter_state.pending_shot_player_id != hunter.id
            or room.hunter_state.has_shot
        ):
            return
        target_number = await self.game_manager.ai_player_service.decide_hunter_shoot(
            hunter, room
        )
        if (
            not self._is_current(room, phase, round_number)
            or room.hunter_state.pending_shot_player_id != hunter.id
            or room.hunter_state.has_shot
        ):
            return
        target = room.get_player_by_number(target_number) if target_number else None
        if target and target.is_alive and target.id != hunter.id:
            await self.on_hunter_shot(room, target.id)
            return

        room.hunter_state.has_shot = True
        room.hunter_state.pending_shot_player_id = None
        room.log(f"🔫 {hunter.display_name}（猎人AI）选择不开枪")
        self._record_public_event(
            room, f"系统公布：{hunter.display_name} 选择不开枪，开枪机会已结束"
        )
        await self.message_service.send_group_message(
            room, f"🔫 {hunter.display_name} 选择不开枪！"
        )
        if not self._is_current(room, phase, round_number):
            return
        await self._after_hunter_shot(room, death_type)

    async def on_hunter_shot(self, room: "GameRoom", target_id: str) -> None:
        """Claim and apply a hunter shot once before any asynchronous cleanup."""
        hunter_id = room.hunter_state.pending_shot_player_id
        hunter = room.get_player(hunter_id)
        target = room.get_player(target_id)
        if (
            self.game_manager.rooms.get(room.group_id) is not room
            or room.phase not in (GamePhase.NIGHT_WITCH, GamePhase.DAY_VOTE)
            or room.hunter_state.has_shot
            or not hunter
            or not target
            or not target.is_alive
            or target.id == hunter_id
        ):
            return
        phase, round_number = room.phase, room.current_round
        death_type = room.hunter_state.death_type
        room.cancel_timer()
        room.hunter_state.has_shot = True
        room.hunter_state.pending_shot_player_id = None
        room.kill_player(target_id)
        room.log(f"🔫 {hunter.display_name}（猎人）开枪带走 {target.display_name}")
        self._record_public_event(
            room, f"猎人 {hunter.display_name} 开枪带走 {target.display_name}"
        )
        await BanService.ban_player(room, target_id)
        if not self._is_current(room, phase, round_number):
            return
        await self.message_service.announce_hunter_shot(room, target.display_name)
        if not self._is_current(room, phase, round_number):
            return
        await self._after_hunter_shot(room, death_type)

    async def _after_hunter_timeout(self, room: "GameRoom", death_type) -> None:
        """Advance after the hunter declines to shoot in time."""
        await self._after_hunter_shot(room, death_type)

    async def _after_hunter_shot(self, room: "GameRoom", death_type) -> None:
        """Check victory after the shot, then preserve the original last-words speaker."""
        phase, round_number = room.phase, room.current_round
        if not self._is_current(room, phase, round_number):
            return
        if await self.game_manager.check_and_handle_victory(room):
            return
        if not self._is_current(room, phase, round_number):
            return
        if death_type in (HunterDeathType.VOTE, "vote"):
            room.last_words_from_vote = True
            await self.enter_last_words_phase(room)
        elif death_type in (HunterDeathType.WOLF, "wolf"):
            if (
                room.is_first_night
                and room.last_killed_id
                and room.last_killed_id != room.witch_state.poisoned_player_id
            ):
                room.last_words_from_vote = False
                room.last_words_player_id = room.last_killed_id
                await self.enter_last_words_phase(room)
            else:
                if room.last_killed_id:
                    await BanService.ban_player(room, room.last_killed_id)
                    if not self._is_current(room, phase, round_number):
                        return
                room.end_first_night()
                await self.enter_speaking_phase(room)
