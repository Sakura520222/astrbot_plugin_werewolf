"""Daytime voting with one AI worker and one settlement per vote generation."""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase, Role
from ..roles import HunterDeathType
from ..services import BanService
from .base import BasePhase

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..models import GameRoom

AI_VOTE_BEFORE_TIMEOUT_SECONDS = 30


class DayVotePhase(BasePhase):
    """Coordinate ordinary and PK votes without letting old work mutate new rounds."""

    @property
    def name(self) -> str:
        return "投票阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_vote

    def _is_current_phase(self, room: "GameRoom") -> bool:
        return (
            self.game_manager.get_room(room.group_id) is room
            and room.phase == GamePhase.DAY_VOTE
        )

    def _is_active(self, room: "GameRoom", generation: int) -> bool:
        """Check whether a vote worker still owns a writable generation.

        Args:
            room: Room whose vote is being processed.
            generation: Generation captured before asynchronous work began.

        Returns:
            Whether the room, phase and unsettled generation are still current.
        """
        return (
            self._is_current_phase(room)
            and room.vote_state.generation == generation
            and not room.vote_state.settled
        )

    async def on_enter(self, room: "GameRoom") -> None:
        """Enter a new ordinary vote.

        Args:
            room: Room entering the voting phase.
        """
        await self._enter_vote(room, is_pk=False)

    async def enter_pk_vote(self, room: "GameRoom") -> None:
        """Enter a new PK vote, retaining only its candidate IDs.

        Args:
            room: Room entering the PK voting phase.
        """
        await self._enter_vote(room, is_pk=True)

    async def _enter_vote(self, room: "GameRoom", is_pk: bool) -> None:
        """Reset vote ownership before announcing the new phase.

        Args:
            room: Room entering an ordinary or PK vote.
            is_pk: Whether to preserve the previous tie's candidates.
        """
        if self.game_manager.get_room(room.group_id) is not room:
            return
        room.cancel_timer()
        room.speaking_generation += 1
        candidates = list(room.vote_state.pk_players) if is_pk else []
        room.vote_state.clear_day_votes()
        room.vote_state.pk_players = candidates
        room.vote_state.is_pk_vote = is_pk
        room.vote_state.generation += 1
        generation = room.vote_state.generation
        room.vote_state.settled = False
        room.phase = GamePhase.DAY_VOTE
        room.day_ai_voted = False
        room.vote_discussion.clear()
        room.speaking_state.reset()

        if is_pk:
            names = [
                p.display_name for p in room.get_alive_players() if p.id in candidates
            ]
            await self.message_service.announce_pk_vote_start(room, names)
        else:
            await self.message_service.announce_vote_start(room)
        if not self._is_active(room, generation):
            return
        await BanService.set_group_whole_ban(room, False)
        if not self._is_active(room, generation):
            return

        if all(p.is_ai for p in room.get_alive_players()):
            await self.trigger_ai_votes(room, generation=generation)
        else:
            await self._start_vote_timer(
                room, has_ai=any(p.is_ai for p in room.get_alive_players())
            )

    async def trigger_ai_votes(
        self, room: "GameRoom", *, generation: int | None = None, manual: bool = False
    ) -> None:
        """Reuse the single AI worker instead of starting competing discussions.

        Args:
            room: Room whose AI players should vote.
            generation: Expected vote generation, or the current one.
            manual: Whether a command requests a fresh human window after AI votes.
        """
        if generation is None:
            generation = room.vote_state.generation
        if not self._is_active(room, generation):
            return

        # Keep the timer as a fallback if the command waiter is cancelled. It will
        # await this same worker rather than settle an incomplete AI discussion.
        # Task creation and assignment happen without yielding to another trigger.
        if room.day_ai_task is None:
            room.day_ai_task = asyncio.create_task(
                self._handle_ai_discussion_and_votes(room, generation=generation)
            )
        task = room.day_ai_task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled() and not self._is_active(room, generation):
                return
            raise
        if not self._is_active(room, generation):
            return
        if await self._check_all_voted(room):
            await self.settle(room, generation=generation)
        elif manual:
            # AI latency never consumes the remaining human voting window.
            await self._start_vote_timer(room, has_ai=False)

    async def _handle_ai_discussion_and_votes(
        self, room: "GameRoom", *, generation: int
    ) -> None:
        """Generate one coherent intention per AI, then commit validated votes.

        Args:
            room: Room sharing the discussion and committed votes.
            generation: Vote generation owned by this worker.
        """
        ai_service = self.game_manager.ai_player_service
        players = [p for p in room.get_alive_players() if p.is_ai]
        is_pk = room.vote_state.is_pk_vote
        candidates = (
            [
                p.number
                for p in room.get_alive_players()
                if p.id in room.vote_state.pk_players
            ]
            if is_pk
            else None
        )
        intentions = []
        for player in players:
            if not self._is_active(room, generation):
                return
            if not player.is_alive or player.id in room.vote_state.day_votes:
                continue
            try:
                ai_service.update_ai_context(player, room)
                discussion, number = await ai_service.decide_vote(
                    player, room, is_pk, candidates
                )
                if not self._is_active(room, generation):
                    return
                if not player.is_alive:
                    continue
                if discussion:
                    await self.message_service.send_group_message(
                        room, f"{player.display_name}：{discussion}"
                    )
                    if not self._is_active(room, generation):
                        return
                    if not player.is_alive:
                        continue
                    # Each public event is appended and broadcast exactly once.
                    room.vote_discussion.append(
                        {"player": player.display_name, "content": discussion}
                    )
                    for observer in room.players.values():
                        if observer.is_ai and observer.ai_context:
                            observer.ai_context.current_round = room.current_round
                            observer.ai_context.add_vote_discussion(
                                player.display_name, discussion
                            )
                intentions.append((player, number))
            except Exception:
                logger.exception("[Werewolf] AI vote generation failed")
                if not self._is_active(room, generation):
                    return
                if player.is_alive:
                    intentions.append((player, None))

        for player, number in intentions:
            if not self._is_active(room, generation):
                return
            if not player.is_alive or player.id in room.vote_state.day_votes:
                continue
            target = room.get_player_by_number(number) if number is not None else None
            valid = (
                target is not None
                and target.is_alive
                and target.id != player.id
                and (not is_pk or target.id in room.vote_state.pk_players)
            )
            target_id = target.id if valid else "ABSTAIN"
            target_name = target.display_name if valid else "弃票"
            room.vote_state.day_votes[player.id] = target_id
            room.log(f"🗳️ {player.display_name} → {target_name}")
            for observer in room.players.values():
                if observer.is_ai and observer.ai_context:
                    observer.ai_context.current_round = room.current_round
                    observer.ai_context.add_vote(
                        player.display_name, target_name, is_pk
                    )
                    if valid:
                        observer.ai_context.analyze_voting_pattern(
                            player.display_name, target_name, is_pk
                        )
            await self.message_service.send_group_message(
                room, f"🗳️ {player.display_name} → {target_name}"
            )
            if not self._is_active(room, generation):
                return
        if self._is_active(room, generation):
            room.day_ai_voted = True

    async def _start_vote_timer(self, room: "GameRoom", has_ai: bool = False) -> None:
        """Start AI before expiry and await completion before the human grace period.

        Args:
            room: Room whose current vote is timed.
            has_ai: Whether the timer should trigger AI decisions.
        """
        generation = room.vote_state.generation

        async def vote_timer():
            try:
                delay = max(self.timeout_seconds - AI_VOTE_BEFORE_TIMEOUT_SECONDS, 0)
                if has_ai:
                    await asyncio.sleep(delay)
                    if not self._is_active(room, generation):
                        return
                    await self.trigger_ai_votes(room, generation=generation)
                    if not self._is_active(room, generation):
                        return
                    await asyncio.sleep(
                        min(self.timeout_seconds, AI_VOTE_BEFORE_TIMEOUT_SECONDS)
                    )
                else:
                    await asyncio.sleep(self.timeout_seconds)
                if self._is_active(room, generation):
                    await self.on_timeout(room, generation=generation)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Werewolf] Vote timer failed")

        if self._is_active(room, generation):
            room.set_timer(asyncio.create_task(vote_timer()))

    async def _check_all_voted(self, room: "GameRoom") -> bool:
        """Return whether every living player has submitted a vote."""
        return room.get_alive_player_ids().issubset(room.vote_state.day_votes)

    async def on_all_voted(
        self, room: "GameRoom", generation: int | None = None
    ) -> None:
        """Settle a complete vote through the shared settlement entry.

        Args:
            room: Room receiving the final vote.
            generation: Vote generation captured before replying to the command.
        """
        if await self._check_all_voted(room):
            await self.settle(room, generation=generation)

    async def settle(
        self,
        room: "GameRoom",
        *,
        generation: int | None = None,
        timed_out: bool = False,
    ) -> None:
        """Claim settlement atomically and process an immutable vote snapshot.

        Args:
            room: Room whose vote should be settled.
            generation: Expected vote generation.
            timed_out: Whether to announce expiry before the result.
        """
        if generation is None:
            generation = room.vote_state.generation
        async with room.day_vote_lock:
            if not self._is_active(room, generation):
                return
            room.vote_state.settled = True
            votes = {
                voter: target
                for voter, target in room.vote_state.day_votes.items()
                if room.is_player_alive(voter)
                and (
                    target == "ABSTAIN"
                    or (
                        room.is_player_alive(target)
                        and (
                            not room.vote_state.is_pk_vote
                            or target in room.vote_state.pk_players
                        )
                    )
                )
            }
            is_pk = room.vote_state.is_pk_vote
            room.cancel_timer()
        if timed_out:
            await self.message_service.send_group_message(
                room,
                f"⏰ 投票超时！已有 {len(votes)}/{room.alive_count} 人投票，自动结算。",
            )
        if not self._is_current_phase(room) or room.vote_state.generation != generation:
            return
        await self._process_vote_result(room, votes, is_pk, generation)

    async def on_finish(self, event: "AstrMessageEvent") -> None:
        """Handle vote completion requested by an event.

        Args:
            event: Event identifying the room to settle.
        """
        room = self.game_manager.get_room(event.get_group_id())
        if room:
            await self.on_all_voted(room)

    async def on_timeout(self, room: "GameRoom", generation: int | None = None) -> None:
        """Await the shared AI worker before settling an expired vote.

        Args:
            room: Room whose vote window expired.
            generation: Vote generation captured by the timer.
        """
        if generation is None:
            generation = room.vote_state.generation
        if not self._is_active(room, generation):
            return
        if any(p.is_ai for p in room.get_alive_players()) and not room.day_ai_voted:
            await self.trigger_ai_votes(room, generation=generation)
        if self._is_active(room, generation):
            await self.settle(room, generation=generation, timed_out=True)

    async def _process_vote_result(
        self, room: "GameRoom", votes: dict[str, str], is_pk: bool, generation: int
    ) -> None:
        """Resolve the captured result, then continue exactly one game branch.

        Args:
            room: Room that claimed settlement.
            votes: Validated snapshot, not the mutable live vote dictionary.
            is_pk: Whether the snapshot is a PK vote.
            generation: Generation owning this result.
        """
        counts = {}
        voters = {}
        for voter, target in votes.items():
            voters.setdefault(target, []).append(voter)
            if target == "ABSTAIN":
                continue
            counts[target] = counts.get(target, 0) + 1
        highest = max(counts.values(), default=0)
        leaders = (
            [p.id for p in room.get_alive_players() if counts.get(p.id) == highest]
            if highest
            else []
        )
        exiled = room.get_player(leaders[0]) if len(leaders) == 1 else None
        await self.message_service.announce_vote_result(
            room, counts, voters, exiled.display_name if exiled else None, is_pk
        )
        if not self._is_current_phase(room) or room.vote_state.generation != generation:
            return
        room.vote_state.clear_day_votes()
        if len(leaders) > 1 and not is_pk:
            room.vote_state.pk_players = leaders
            for observer in room.players.values():
                if observer.is_ai and observer.ai_context:
                    observer.ai_context.add_event("投票平票，进入PK发言")
            from .day_speaking import DaySpeakingPhase

            await DaySpeakingPhase(self.game_manager).enter_pk_phase(room, leaders)
            return
        if exiled is None:
            if not await self.game_manager.check_and_handle_victory(room):
                await self._enter_night(room)
            return

        room.kill_player(exiled.id)
        room.last_words_from_vote = True
        room.last_words_player_id = exiled.id
        room.log(f"📊 投票放逐：{exiled.display_name} 被放逐出局")
        for observer in room.players.values():
            if observer.is_ai and observer.ai_context:
                observer.ai_context.add_event(
                    f"投票放逐：{exiled.display_name} 被放逐出局"
                )
        if exiled.role == Role.HUNTER and not room.hunter_state.has_shot:
            # A dying hunter's shot must resolve before victory is evaluated.
            room.hunter_state.pending_shot_player_id = exiled.id
            room.hunter_state.death_type = HunterDeathType.VOTE
            await self._wait_for_hunter_shot(room)
        elif not await self.game_manager.check_and_handle_victory(room):
            await self._enter_last_words(room)

    async def _enter_night(self, room: "GameRoom") -> None:
        """Continue to the next night."""
        from .phase_manager import PhaseManager

        await PhaseManager(self.game_manager).enter_night_phase(room)

    async def _enter_last_words(self, room: "GameRoom") -> None:
        """Continue to the explicitly identified dead player's last words."""
        from .phase_manager import PhaseManager

        await PhaseManager(self.game_manager).enter_last_words_phase(room)

    async def _wait_for_hunter_shot(self, room: "GameRoom") -> None:
        """Resolve the exiled hunter's shot before last words."""
        from .phase_manager import PhaseManager

        await PhaseManager(self.game_manager).wait_for_hunter_shot(room, "vote")
