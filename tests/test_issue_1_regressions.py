"""Behavioral regressions for voting, stale phase work, and full AI context.

Run with ``python -m unittest discover -s tests -v``. No running bot or LLM is
required; installed AstrBot is preferred, with temporary import stubs otherwise.
"""

import asyncio
import importlib
import logging
import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_werewolf_issue_1_tests"


@contextmanager
def load_plugin():
    """Load the real plugin modules without executing the plugin entry point.

    Yields:
        Namespace containing the modules exercised by these tests.
    """
    root = types.ModuleType(PACKAGE)
    root.__path__ = [str(ROOT)]
    # Both fallback modules and all test imports disappear when this scope ends.
    with patch.dict(sys.modules, {PACKAGE: root}):
        try:
            # AstrBot imports initialize relative data files; keep them disposable.
            original_directory = Path.cwd()
            with TemporaryDirectory(prefix="werewolf-unittest-") as directory:
                try:
                    os.chdir(directory)
                    importlib.import_module("astrbot.api")
                    importlib.import_module("astrbot.core.message.message_event_result")
                finally:
                    os.chdir(original_directory)
        except ImportError:
            modules = {}
            for name in (
                "astrbot",
                "astrbot.api",
                "astrbot.core",
                "astrbot.core.message",
                "astrbot.core.message.message_event_result",
            ):
                module = types.ModuleType(name)
                module.__path__ = []
                modules[name] = module
            modules["astrbot.api"].logger = logging.getLogger(__name__)

            class MessageChain:
                """Small fallback implementing the message API used by the plugin."""

                def __init__(self):
                    self.text = ""

                def message(self, text):
                    self.text += text
                    return self

                def at(self, name, player_id):
                    return self

                def get_plain_text(self):
                    return self.text

            modules[
                "astrbot.core.message.message_event_result"
            ].MessageChain = MessageChain
            sys.modules.update(modules)
        yield types.SimpleNamespace(
            models=importlib.import_module(f"{PACKAGE}.models"),
            services=importlib.import_module(f"{PACKAGE}.services"),
            phases=importlib.import_module(f"{PACKAGE}.phases"),
            vote=importlib.import_module(f"{PACKAGE}.services.ai.actions.vote"),
            base=importlib.import_module(f"{PACKAGE}.services.ai.actions.base"),
            roles=importlib.import_module(f"{PACKAGE}.roles"),
        )


class PluginTestCase(unittest.IsolatedAsyncioTestCase):
    """Provide real room/model objects and deterministic asynchronous boundaries."""

    @classmethod
    def setUpClass(cls):
        cls.import_scope = load_plugin()
        cls.plugin = cls.import_scope.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.import_scope.__exit__(None, None, None)

    async def asyncSetUp(self):
        models = self.plugin.models
        self.room = models.GameRoom("group", "creator", models.GameConfig.default())
        self.room.phase = models.GamePhase.DAY_VOTE
        self.room.vote_state.generation = 1
        self.players = []
        for number, name in enumerate(("Alice", "Bob", "Carol"), 1):
            player = models.Player(
                id=str(123456780 + number),
                name=name,
                number=number,
                role=models.Role.VILLAGER,
                is_ai=number == 1,
                ai_context=models.AIPlayerContext(
                    player_number=number, role_name="村民", personality="Fixed style"
                ),
            )
            self.room.add_player(player)
            self.room.number_to_player[number] = player.id
            self.players.append(player)
        self.messages = types.SimpleNamespace(
            send_group_message=AsyncMock(return_value=True),
            send_group_at_message=AsyncMock(return_value=True),
            announce_vote_result=AsyncMock(return_value=True),
            announce_vote_start=AsyncMock(return_value=True),
            announce_pk_vote_start=AsyncMock(return_value=True),
            announce_hunter_can_shoot=AsyncMock(return_value=True),
            send_private_message=AsyncMock(return_value=True),
        )
        self.ai = types.SimpleNamespace(
            update_ai_context=Mock(),
            decide_vote=AsyncMock(return_value=("One coherent intention", 2)),
            generate_speech=AsyncMock(return_value="Full speech"),
            generate_last_words=AsyncMock(return_value="Full last words"),
            clear_player_data=Mock(),
        )
        self.manager = types.SimpleNamespace(
            rooms={self.room.group_id: self.room},
            config=self.room.config,
            message_service=self.messages,
            ai_player_service=self.ai,
            check_and_handle_victory=AsyncMock(return_value=False),
        )
        self.manager.get_room = self.manager.rooms.get
        self.phase = self.plugin.phases.DayVotePhase(self.manager)
        self.phase._enter_night = AsyncMock()
        self.phase._enter_last_words = AsyncMock()
        self.phase._wait_for_hunter_shot = AsyncMock()
        self.tasks = []

    async def asyncTearDown(self):
        self.room.cancel_timer()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def start(self, coroutine):
        """Track a concurrent operation so every test releases its tasks.

        Args:
            coroutine: Operation to schedule.

        Returns:
            The scheduled task.
        """
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def blocked(self, result):
        """Create an async mock controlled by explicit entry and release events.

        Args:
            result: Value returned after the release event.

        Returns:
            Mock, entry event, and release event.
        """
        entered, release = asyncio.Event(), asyncio.Event()

        async def run(*args, **kwargs):
            entered.set()
            await release.wait()
            return result

        return AsyncMock(side_effect=run), entered, release

    async def wait_entered(self, event):
        """Fail promptly if the expected asynchronous boundary is not reached.

        Args:
            event: Boundary notification to await.
        """
        await asyncio.wait_for(event.wait(), 2)


class VotingRegressionTests(PluginTestCase):
    async def test_concurrent_manual_and_timer_share_worker_and_settlement(self):
        room = self.room
        self.ai.decide_vote, entered, release = self.blocked(("Unique discussion", 2))
        room.vote_state.day_votes.update(
            {self.players[1].id: self.players[0].id, self.players[2].id: "ABSTAIN"}
        )
        self.phase._process_vote_result = AsyncMock()
        timer = self.start(self.phase.trigger_ai_votes(room, generation=1))
        room.timer_task = timer
        await self.wait_entered(entered)
        manual = self.start(self.phase.trigger_ai_votes(room, manual=True))
        other = self.start(self.phase.trigger_ai_votes(room, manual=True))
        release.set()
        await asyncio.gather(timer, manual, other, return_exceptions=True)
        self.ai.decide_vote.assert_awaited_once()
        self.phase._process_vote_result.assert_awaited_once()
        self.assertEqual(
            room.vote_discussion,
            [{"player": self.players[0].display_name, "content": "Unique discussion"}],
        )
        self.assertEqual(len(self.players[0].ai_context.vote_discussions), 1)
        self.assertEqual(len(self.players[0].ai_context.vote_history), 1)
        discussion_messages = [
            call.args[1]
            for call in self.messages.send_group_message.await_args_list
            if "Unique discussion" in call.args[1]
        ]
        self.assertEqual(len(discussion_messages), 1)
        self.assertTrue(room.vote_state.settled)

    async def test_timer_waits_for_slow_ai_before_starting_human_grace(self):
        self.ai.decide_vote, entered, release = self.blocked(("Slow response", 2))
        grace_started, grace_release = asyncio.Event(), asyncio.Event()
        delays = []

        async def controlled_sleep(delay):
            delays.append(delay)
            if len(delays) > 1:
                grace_started.set()
                await grace_release.wait()

        self.phase.on_timeout = AsyncMock()
        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await self.phase._start_vote_timer(self.room, has_ai=True)
            timer = self.room.timer_task
            self.tasks.append(timer)
            await self.wait_entered(entered)
            self.assertFalse(grace_started.is_set())
            self.phase.on_timeout.assert_not_awaited()
            self.assertFalse(self.room.vote_state.settled)
            release.set()
            await self.wait_entered(grace_started)
            self.assertEqual(
                self.room.vote_state.day_votes[self.players[0].id], self.players[1].id
            )
            self.phase.on_timeout.assert_not_awaited()
            grace_release.set()
            await timer
        self.phase.on_timeout.assert_awaited_once_with(self.room, generation=1)

    async def test_slow_vote_does_not_mutate_new_pk_generation_or_replaced_room(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.room.vote_state.generation = 1
                self.room.vote_state.settled = False
                self.ai.decide_vote, entered, release = self.blocked(
                    ("Stale discussion", 2)
                )
                task = self.start(
                    self.phase._handle_ai_discussion_and_votes(self.room, generation=1)
                )
                await self.wait_entered(entered)
                if replacement:
                    self.manager.rooms[self.room.group_id] = (
                        self.plugin.models.GameRoom(
                            self.room.group_id, "new creator", self.room.config
                        )
                    )
                else:
                    self.room.vote_state.generation = 2
                    self.room.vote_state.is_pk_vote = True
                    self.room.vote_state.pk_players = [
                        self.players[1].id,
                        self.players[2].id,
                    ]
                release.set()
                await task
                self.assertEqual(self.room.vote_state.day_votes, {})
                self.assertEqual(self.room.vote_discussion, [])
                self.messages.send_group_message.assert_not_awaited()
                self.manager.rooms[self.room.group_id] = self.room

    async def test_cancelled_trigger_does_not_cancel_shared_ai(self):
        self.ai.decide_vote, entered, release = self.blocked(("Still valid", 2))
        trigger = self.start(self.phase.trigger_ai_votes(self.room))
        await self.wait_entered(entered)
        worker = self.room.day_ai_task
        trigger.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await trigger
        self.assertFalse(worker.cancelled())
        release.set()
        await worker
        self.assertEqual(
            self.room.vote_state.day_votes[self.players[0].id], self.players[1].id
        )

    async def test_cancelled_manual_waiter_leaves_timer_to_finish_shared_worker(self):
        self.ai.decide_vote, entered, release = self.blocked(("Timer fallback", 2))
        grace_started, grace_release = asyncio.Event(), asyncio.Event()
        delays = []

        async def controlled_sleep(delay):
            delays.append(delay)
            if len(delays) > 1:
                grace_started.set()
                await grace_release.wait()

        self.phase.on_timeout = AsyncMock()
        with patch("asyncio.sleep", side_effect=controlled_sleep):
            await self.phase._start_vote_timer(self.room, has_ai=True)
            timer = self.room.timer_task
            self.tasks.append(timer)
            await self.wait_entered(entered)
            manual = self.start(self.phase.trigger_ai_votes(self.room, manual=True))
            # Notify only once the manual command is awaiting the shared worker.
            reached = asyncio.Event()
            asyncio.get_running_loop().call_soon(reached.set)
            await reached.wait()
            manual.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await manual
            self.assertFalse(timer.cancelled())
            self.assertFalse(self.room.day_ai_task.cancelled())
            release.set()
            await self.wait_entered(grace_started)
            self.assertEqual(
                self.room.vote_state.day_votes[self.players[0].id], self.players[1].id
            )
            grace_release.set()
            await timer
        self.ai.decide_vote.assert_awaited_once()
        self.phase.on_timeout.assert_awaited_once_with(self.room, generation=1)

    async def test_settlement_uses_snapshot_before_first_await(self):
        self.room.vote_state.day_votes = {self.players[0].id: self.players[1].id}
        self.messages.send_group_message, entered, release = self.blocked(True)
        self.phase._process_vote_result = AsyncMock()
        task = self.start(self.phase.settle(self.room, timed_out=True))
        await self.wait_entered(entered)
        self.room.vote_state.day_votes.clear()
        self.room.vote_state.day_votes[self.players[2].id] = self.players[0].id
        self.room.vote_state.is_pk_vote = True
        release.set()
        await task
        self.phase._process_vote_result.assert_awaited_once_with(
            self.room, {self.players[0].id: self.players[1].id}, False, 1
        )

    async def test_all_voted_requires_every_living_player_and_settles_once(self):
        self.phase._process_vote_result = AsyncMock()
        self.room.vote_state.day_votes = {self.players[0].id: "ABSTAIN"}
        await self.phase.on_all_voted(self.room)
        self.assertFalse(self.room.vote_state.settled)
        self.room.vote_state.day_votes.update({p.id: "ABSTAIN" for p in self.players})
        await asyncio.gather(
            self.phase.on_all_voted(self.room), self.phase.on_all_voted(self.room)
        )
        self.phase._process_vote_result.assert_awaited_once()

    async def test_invalid_ai_intentions_abstain_without_random_target(self):
        for number in (None, 0, 999, 1, 3):
            with self.subTest(number=number):
                self.room.vote_state.day_votes.clear()
                self.room.vote_state.is_pk_vote = True
                self.room.vote_state.pk_players = [self.players[1].id]
                self.ai.decide_vote.return_value = ("", number)
                await self.phase._handle_ai_discussion_and_votes(
                    self.room, generation=1
                )
                self.assertEqual(
                    self.room.vote_state.day_votes, {self.players[0].id: "ABSTAIN"}
                )

    async def test_peaceful_night_exile_sets_explicit_last_words_player(self):
        self.room.last_killed_id = None
        await self.phase._process_vote_result(
            self.room, {self.players[0].id: self.players[1].id}, False, 1
        )
        self.assertFalse(self.players[1].is_alive)
        self.assertIsNone(self.room.last_killed_id)
        self.assertEqual(self.room.last_words_player_id, self.players[1].id)
        self.assertTrue(self.room.last_words_from_vote)
        self.phase._enter_last_words.assert_awaited_once_with(self.room)

    async def test_pk_tie_and_no_votes_enter_night_without_last_words(self):
        for votes, is_pk in (
            (
                {
                    self.players[0].id: self.players[1].id,
                    self.players[1].id: self.players[0].id,
                },
                True,
            ),
            ({}, True),
            ({}, False),
        ):
            with self.subTest(votes=votes, is_pk=is_pk):
                self.phase._enter_night.reset_mock()
                await self.phase._process_vote_result(self.room, votes, is_pk, 1)
                self.phase._enter_night.assert_awaited_once_with(self.room)
                self.phase._enter_last_words.assert_not_awaited()
                self.assertIsNone(self.room.last_words_player_id)
                self.assertTrue(all(p.is_alive for p in self.players))

    async def test_exiled_hunter_is_pending_before_victory_check(self):
        hunter = self.players[1]
        hunter.role = self.plugin.models.Role.HUNTER

        async def inspect_pending(room):
            self.assertEqual(room.hunter_state.pending_shot_player_id, hunter.id)
            self.assertEqual(
                room.hunter_state.death_type, self.plugin.roles.HunterDeathType.VOTE
            )
            self.assertFalse(hunter.is_alive)
            self.manager.check_and_handle_victory.assert_not_awaited()

        self.phase._wait_for_hunter_shot.side_effect = inspect_pending
        await self.phase._process_vote_result(
            self.room, {self.players[0].id: hunter.id}, False, 1
        )
        self.phase._wait_for_hunter_shot.assert_awaited_once()
        self.manager.check_and_handle_victory.assert_not_awaited()

    async def test_forced_cleanup_cancels_worker_before_async_cleanup(self):
        manager = self.plugin.services.GameManager(Mock(), self.room.config)
        manager.rooms[self.room.group_id] = self.room
        manager.ai_player_service = self.ai
        self.manager = manager
        self.phase.game_manager = manager
        self.ai.decide_vote, entered, release = self.blocked(("Too late", 2))
        trigger = self.start(self.phase.trigger_ai_votes(self.room))
        await self.wait_entered(entered)
        worker = self.room.day_ai_task
        ban = self.plugin.services.BanService
        with (
            patch.object(ban, "set_group_whole_ban", new=AsyncMock()),
            patch.object(ban, "unban_all_players", new=AsyncMock()),
            patch.object(ban, "clear_temp_admins", new=AsyncMock()),
            patch.object(ban, "restore_player_cards", new=AsyncMock()),
        ):
            await manager.cleanup_room(self.room.group_id)
        await asyncio.gather(trigger, worker, return_exceptions=True)
        self.assertTrue(worker.cancelled())
        self.assertEqual(self.room.phase, self.plugin.models.GamePhase.FINISHED)
        self.assertNotIn(self.room.group_id, manager.rooms)
        self.assertEqual(self.room.vote_state.day_votes, {})
        self.messages.send_group_message.assert_not_awaited()


class SpeechAndMessagesTests(PluginTestCase):
    async def test_stale_speech_is_not_published_after_skip_or_room_replacement(self):
        phase = self.plugin.phases.DaySpeakingPhase(self.manager)
        phase.on_finish_speaking = AsyncMock()
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        self.room.speaking_state.current_speaker_id = self.players[0].id
        for change in ("skip", "replacement", "finished"):
            with self.subTest(change=change):
                self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
                self.manager.rooms[self.room.group_id] = self.room
                self.ai.generate_speech, entered, release = self.blocked("Stale speech")
                task = self.start(phase._handle_ai_speech(self.room, self.players[0]))
                await self.wait_entered(entered)
                if change == "skip":
                    self.room.speaking_generation += 1
                elif change == "replacement":
                    self.manager.rooms[self.room.group_id] = object()
                else:
                    self.room.phase = self.plugin.models.GamePhase.FINISHED
                release.set()
                await task
                self.messages.send_group_message.assert_not_awaited()
                phase.on_finish_speaking.assert_not_awaited()

    async def test_vote_result_serializes_public_names_not_objects_or_platform_ids(
        self,
    ):
        service = self.plugin.services.MessageService(Mock())
        service.send_group_message = AsyncMock(return_value=True)
        await service.announce_vote_result(
            self.room,
            {self.players[1].id: 1},
            {self.players[1].id: [self.players[0].id]},
            self.players[1].display_name,
            False,
        )
        text = service.send_group_message.await_args.args[1]
        self.assertIsInstance(text, str)
        self.assertIn(self.players[0].display_name, text)
        self.assertIn(self.players[1].display_name, text)
        for player in self.players:
            self.assertNotIn(player.id, text)
        for marker in ("Player(", "ai_context=", "ai_config=", "Role."):
            self.assertNotIn(marker, text)

    async def test_public_sender_rejects_internal_models_and_repr_payloads(self):
        context = types.SimpleNamespace(send_message=AsyncMock())
        service = self.plugin.services.MessageService(context)
        self.room.msg_origin = "test:group"
        with self.assertRaises(TypeError):
            await service.send_group_message(self.room, self.players[0])
        for payload in ("Player(id='123')", "ai_context=secret", "ai_config=secret"):
            self.assertFalse(await service.send_group_message(self.room, payload))
        context.send_message.assert_not_awaited()
        self.assertNotIn(self.players[0].id, repr(self.players[0]))
        self.assertNotIn("Player(", repr(self.players[0]))


class VictoryIdentityRegressionTests(PluginTestCase):
    async def test_slow_review_never_publishes_to_or_cleans_up_replacement_room(self):
        manager = self.plugin.services.GameManager(Mock(), self.room.config)
        manager.rooms[self.room.group_id] = self.room
        manager.message_service = types.SimpleNamespace(
            announce_victory=AsyncMock(), send_group_message=AsyncMock()
        )
        review, entered, release = self.blocked("OLD_GAME_REVIEW")
        manager.ai_reviewer = types.SimpleNamespace(generate_review=review)
        # Wrap the real cleanup so an incorrect call would actually remove the new room.
        manager.cleanup_room = AsyncMock(wraps=manager.cleanup_room)
        task = self.start(manager.check_and_handle_victory(self.room))
        await self.wait_entered(entered)
        replacement = self.plugin.models.GameRoom(
            self.room.group_id, "new creator", self.room.config
        )
        replacement.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        replacement.game_log = ["NEW_GAME_UNTOUCHED"]
        manager.rooms[self.room.group_id] = replacement
        release.set()
        self.assertTrue(await task)
        manager.message_service.announce_victory.assert_awaited_once()
        manager.message_service.send_group_message.assert_not_awaited()
        manager.cleanup_room.assert_not_awaited()
        self.assertIs(manager.rooms[self.room.group_id], replacement)
        self.assertEqual(replacement.phase, self.plugin.models.GamePhase.DAY_SPEAKING)
        self.assertEqual(replacement.game_log, ["NEW_GAME_UNTOUCHED"])
        self.assertEqual(replacement.creator_id, "new creator")


class NamingAndOrderingTests(PluginTestCase):
    async def test_same_name_ai_gets_unique_opaque_ids_and_unmodified_names(self):
        manager = self.plugin.services.GameManager(Mock(), self.room.config)
        manager.ai_player_service = types.SimpleNamespace(
            assign_personality=Mock(return_value="Hidden style")
        )
        added = [
            manager.add_ai_player(
                self.room, "小咪", self.plugin.models.AIPlayerConfig(name="小咪")
            )
            for _ in range(6)
        ]
        self.assertEqual(len({player.id for player in added}), 6)
        self.assertEqual(self.room.player_count, 9)
        for player in added:
            self.assertRegex(player.id, r"^ai_[0-9a-f]{32}$")
            self.assertEqual(player.name, "小咪")
            self.assertNotIn("Hidden style", player.display_name)

    async def test_start_game_and_public_lists_preserve_ascending_seat_order(self):
        manager = self.plugin.services.GameManager(Mock(), self.room.config)
        manager.ai_player_service = types.SimpleNamespace(initialize_ai_context=Mock())
        manager._send_roles_to_players = AsyncMock()
        original_ids = [player.id for player in self.room.players.values()]
        ban = self.plugin.services.BanService
        with (
            patch.object(ban, "set_player_numbers", new=AsyncMock()),
            patch.object(ban, "set_group_whole_ban", new=AsyncMock()),
            patch("random.shuffle", side_effect=lambda values: values.reverse()),
        ):
            await manager.start_game(self.room)
        players = list(self.room.players.values())
        self.assertEqual(
            [player.id for player in players], list(reversed(original_ids))
        )
        self.assertEqual([player.number for player in players], [1, 2, 3])
        self.assertEqual(list(self.room.number_to_player), [1, 2, 3])
        helpers = importlib.import_module(f"{PACKAGE}.utils.helpers")
        text = helpers.format_player_list(list(reversed(players)))
        self.assertEqual(
            [line.strip() for line in text.splitlines()],
            [f"• {player.display_name}" for player in players],
        )


class NightDeathRegressionTests(PluginTestCase):
    async def test_string_wolf_death_sends_nonempty_human_hunter_prompt(self):
        hunter = self.players[1]
        hunter.role = self.plugin.models.Role.HUNTER
        hunter.kill()
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.hunter_state.pending_shot_player_id = hunter.id
        phase_manager = self.plugin.phases.PhaseManager(self.manager)
        await phase_manager.wait_for_hunter_shot(self.room, "wolf")
        self.tasks.append(self.room.timer_task)
        self.messages.send_private_message.assert_awaited_once()
        room, player_id, prompt = self.messages.send_private_message.await_args.args
        self.assertIs(room, self.room)
        self.assertEqual(player_id, hunter.id)
        self.assertTrue(prompt.strip())
        self.assertIn("开枪", prompt)
        self.assertIn("狼人", prompt)

    async def test_poison_overrides_simultaneous_wolf_attack_on_hunter(self):
        hunter = self.players[1]
        hunter.role = self.plugin.models.Role.HUNTER
        hunter.kill()
        self.room.last_killed_id = hunter.id
        self.room.witch_state.poisoned_player_id = hunter.id
        self.room.hunter_state.pending_shot_player_id = hunter.id
        phase = self.plugin.phases.NightWitchPhase(self.manager)
        await phase._handle_hunter_death(self.room)
        self.assertIsNone(self.room.hunter_state.pending_shot_player_id)
        self.assertEqual(
            self.room.hunter_state.death_type, self.plugin.roles.HunterDeathType.POISON
        )

    async def test_poison_only_and_double_target_dawn_are_nonpeaceful_and_unique(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        victim = self.players[1]
        victim.kill()
        self.room.witch_state.poisoned_player_id = victim.id
        service = self.plugin.services.MessageService(Mock())
        service.send_group_message = AsyncMock(return_value=True)
        self.messages.announce_dawn = service.announce_dawn
        phase = self.plugin.phases.NightWitchPhase(self.manager)
        for wolf_victim in (None, victim.id):
            with self.subTest(wolf_victim=wolf_victim):
                self.room.last_killed_id = wolf_victim
                await phase._announce_dawn(self.room)
                text = service.send_group_message.await_args.args[1]
                self.assertNotIn("平安夜", text)
                self.assertEqual(text.count(victim.display_name), 1)
                event = self.players[0].ai_context.game_events[-1]
                self.assertNotIn("平安夜", event)
                self.assertEqual(event.count(victim.display_name), 1)

    async def test_poisoned_first_night_wolf_victim_gets_no_last_words(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.is_first_night = True
        self.room.last_killed_id = self.players[1].id
        self.room.witch_state.poisoned_player_id = self.players[1].id
        self.players[1].kill()
        phase = self.plugin.phases.NightWitchPhase(self.manager)
        with (
            patch.object(
                self.plugin.services.BanService, "ban_player", new=AsyncMock()
            ),
            patch.object(
                self.plugin.phases.PhaseManager,
                "enter_last_words_phase",
                new=AsyncMock(),
            ) as last_words,
            patch.object(
                self.plugin.phases.PhaseManager, "enter_speaking_phase", new=AsyncMock()
            ) as speaking,
        ):
            await phase._enter_day_phase(self.room)
        last_words.assert_not_awaited()
        speaking.assert_awaited_once_with(self.room)
        self.assertIsNone(self.room.last_words_player_id)

    async def test_wolf_worker_cancellation_never_advances_or_falls_back(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        phase._process_all_ai_wolves, entered, release = self.blocked(None)
        phase._finish_and_next = AsyncMock()
        phase._fallback_wolf_vote = AsyncMock()
        task = self.start(phase._process_all_ai_wolves_with_timeout(self.room))
        await self.wait_entered(entered)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        phase._finish_and_next.assert_not_awaited()
        phase._fallback_wolf_vote.assert_not_awaited()

    async def test_slow_wolf_vote_cannot_write_after_round_change_or_cleanup(self):
        wolf = self.players[0]
        wolf.role = self.plugin.models.Role.WEREWOLF
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        for change in ("round", "cleanup"):
            with self.subTest(change=change):
                self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
                self.ai.decide_werewolf_kill, entered, release = self.blocked(2)
                with patch("asyncio.sleep", new=AsyncMock()):
                    task = self.start(phase._handle_ai_werewolf_vote(self.room))
                    await self.wait_entered(entered)
                    if change == "round":
                        self.room.current_round += 1
                    else:
                        self.room.phase = self.plugin.models.GamePhase.FINISHED
                    release.set()
                    await task
                self.assertEqual(self.room.vote_state.night_votes, {})
                self.messages.send_private_message.assert_not_awaited()


class LastWordsRegressionTests(PluginTestCase):
    async def test_peaceful_night_last_words_uses_explicit_exile_not_night_victim(self):
        self.room.last_killed_id = None
        self.room.last_words_player_id = self.players[1].id
        self.room.last_words_from_vote = True
        self.players[1].kill()
        phase = self.plugin.phases.LastWordsPhase(self.manager)
        phase.start_timer = AsyncMock()
        ban = self.plugin.services.BanService
        with (
            patch.object(ban, "set_group_whole_ban", new=AsyncMock()),
            patch.object(ban, "set_temp_admin", new=AsyncMock()) as set_admin,
        ):
            await phase.on_enter(self.room)
        set_admin.assert_awaited_once_with(self.room, self.players[1].id)
        self.assertIs(
            self.messages.send_group_at_message.await_args.args[1], self.players[1]
        )
        phase.start_timer.assert_awaited_once_with(self.room)

    async def test_concurrent_last_words_finish_records_and_advances_once(self):
        self.room.phase = self.plugin.models.GamePhase.LAST_WORDS
        self.room.last_words_player_id = self.players[1].id
        self.room.last_words_from_vote = True
        self.room.last_words_generation = 1
        long_words = "LAST_WORDS_BEGIN" + "complete evidence " * 400 + "LAST_WORDS_END"
        self.room.speaking_state.current_speech = [long_words]
        phase = self.plugin.phases.LastWordsPhase(self.manager)
        remove_admin, entered, release = self.blocked(None)
        ban = self.plugin.services.BanService
        with (
            patch.object(ban, "remove_temp_admin", new=remove_admin),
            patch.object(ban, "ban_player", new=AsyncMock()),
            patch.object(ban, "set_group_whole_ban", new=AsyncMock()),
            patch.object(
                self.plugin.phases.PhaseManager, "enter_night_phase", new=AsyncMock()
            ) as enter_night,
        ):
            first = self.start(phase.on_finish(self.room, generation=1))
            await self.wait_entered(entered)
            await phase.on_finish(self.room, generation=1)
            release.set()
            await first
        enter_night.assert_awaited_once_with(self.room)
        self.assertEqual(sum(long_words in line for line in self.room.game_log), 1)
        self.assertEqual(
            sum(long_words in line for line in self.players[0].ai_context.game_events),
            1,
        )
        self.assertEqual(self.room.speaking_state.current_speech, [])
        self.assertIsNone(self.room.last_words_player_id)
        self.assertFalse(self.room.last_words_from_vote)

    async def test_slow_ai_last_words_cannot_publish_after_generation_change(self):
        self.room.phase = self.plugin.models.GamePhase.LAST_WORDS
        self.room.last_words_player_id = self.players[0].id
        self.room.last_words_generation = 1
        phase = self.plugin.phases.LastWordsPhase(self.manager)
        phase._finish_last_words = AsyncMock()
        self.ai.generate_last_words, entered, release = self.blocked("Stale last words")
        with patch("asyncio.sleep", new=AsyncMock()):
            task = self.start(phase._handle_ai_last_words(self.room, self.players[0]))
            await self.wait_entered(entered)
            self.room.last_words_generation += 1
            release.set()
            await task
        phase._finish_last_words.assert_not_awaited()
        self.messages.send_group_message.assert_not_awaited()


class ContextAndParsingTests(PluginTestCase):
    async def test_death_and_exile_events_render_exactly_once(self):
        ctx = self.players[0].ai_context
        ctx.current_round = 2
        events = (
            "第2夜死亡：2号.Bob 被狼人杀害",
            "投票放逐：3号.Carol 被放逐出局",
        )
        for event in events:
            ctx.add_event(event)
        prompt = ctx.to_prompt_context()
        for event in events:
            self.assertEqual(prompt.count(event), 1, event)

    async def test_long_public_history_is_complete_once_in_context(self):
        ctx = self.players[0].ai_context
        ctx.current_round = 50
        records = []
        for index in range(45):
            ctx.current_round = index + 1
            for kind, append in (
                ("speech", lambda value: ctx.add_speech("Speaker", value)),
                ("discussion", lambda value: ctx.add_vote_discussion("Speaker", value)),
                ("event", ctx.add_event),
                ("summary", ctx.add_round_summary),
                ("note", ctx.add_personal_note),
            ):
                value = f"<{kind}-{index}>" + "完整记录" * 250 + f"</{kind}-{index}>"
                records.append(value)
                append(value)
            target = f"<unique-vote-target-{index}>"
            records.append(target)
            ctx.add_vote("Voter", target)
        prompt = ctx.to_prompt_context()
        for record in records:
            self.assertEqual(prompt.count(record), 1, record[:60])
        self.assertEqual(len(ctx.speeches), 45)
        self.assertEqual(len(ctx.vote_discussions), 45)
        self.assertEqual(len(ctx.vote_history), 45)

    async def test_private_context_is_role_scoped(self):
        for role in ("村民", "狼人", "预言家", "女巫"):
            with self.subTest(role=role):
                ctx = self.plugin.models.AIPlayerContext(
                    role_name=role, is_werewolf=role == "狼人"
                )
                ctx.werewolf_teammates = ["SECRET_TEAM"]
                ctx.add_wolf_chat("Wolf", "SECRET_CHAT", 1)
                ctx.add_seer_result("SECRET_CHECK", True)
                ctx.last_killed_player = "SECRET_KILL"
                ctx.witch_saved_player = "SECRET_SAVE"
                ctx.witch_poisoned_player = "SECRET_POISON"
                prompt = ctx.to_prompt_context()
                for secret in ("SECRET_TEAM", "SECRET_CHAT"):
                    self.assertEqual(secret in prompt, role == "狼人")
                self.assertEqual("SECRET_CHECK" in prompt, role == "预言家")
                for secret in ("SECRET_KILL", "SECRET_SAVE", "SECRET_POISON"):
                    self.assertEqual(secret in prompt, role == "女巫")

    async def test_vote_parser_accepts_only_explicit_or_standalone_ballot(self):
        action = self.plugin.vote.VoteAction(Mock())
        cases = (
            ("[发言]我怀疑3号但决定投2号[投票]2", 2),
            ("2", 2),
            ("[投票]弃票", None),
            ("弃票", None),
            ("我怀疑2号和3号", None),
            ("[发言]2号值得观察", None),
            ("[投票]2或3", None),
            ("[投票]2\n[投票]3", None),
            ("[投票]1", None),
            ("[投票]999", None),
            ("", None),
            (None, None),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                action._call_llm = AsyncMock(return_value=response)
                _, target = await action.decide_vote(self.players[0], self.room)
                self.assertEqual(target, expected)
        self.assertEqual(self.players[0].ai_context.vote_history, [])
        self.assertEqual(self.players[0].ai_context.voting_patterns, {})

    async def test_pk_parser_never_falls_back_to_non_candidate(self):
        action = self.plugin.vote.VoteAction(Mock())
        for response, candidates, expected in (
            ("[投票]2", [2], 2),
            ("[投票]3", [2], None),
            ("[投票]2", [], None),
            ("[投票]2", None, None),
            ("[投票]弃票", [2], None),
            (None, [2], None),
        ):
            with self.subTest(response=response, candidates=candidates):
                action._call_llm = AsyncMock(return_value=response)
                _, target = await action.decide_vote(
                    self.players[0], self.room, True, candidates
                )
                self.assertEqual(target, expected)

    async def test_reviewer_keeps_full_logs_and_sanitizes_composed_prompts_only(self):
        provider = types.SimpleNamespace(
            text_chat=AsyncMock(
                return_value=types.SimpleNamespace(
                    result_chain=types.SimpleNamespace(
                        get_plain_text=lambda: "Complete review"
                    )
                )
            )
        )
        reviewer = self.plugin.services.AIReviewer(
            types.SimpleNamespace(get_using_provider=lambda: provider)
        )
        self.room.config.enable_ai_review = True
        self.players[0].name = "Alice🐺"
        long_log = "LOG_BEGIN" + "unabridged evidence " * 1200 + "LOG_END"
        self.room.game_log = ["🌙 Night started", long_log, "🗳️ Vote finished"]
        original_logs = list(self.room.game_log)
        for custom_prompt in ("", "🔮Custom analysis {winning_faction}\n{game_data}⚠️"):
            with self.subTest(custom=bool(custom_prompt)):
                self.room.config.ai_review_prompt = custom_prompt
                review = await reviewer.generate_review(self.room, "good")
                self.assertIn("Complete review", review)
                prompts = provider.text_chat.await_args.kwargs
                self.assertEqual(prompts["prompt"].count(long_log), 1)
                if custom_prompt:
                    self.assertEqual(prompts["system_prompt"].count(long_log), 1)
                for prompt in prompts.values():
                    self.assertNotRegex(prompt, r"[\U0001F000-\U0001FAFF☀-➿‍️]")
                self.assertEqual(self.room.game_log, original_logs)
                self.assertEqual(self.players[0].name, "Alice🐺")
                self.assertEqual(self.room.config.ai_review_prompt, custom_prompt)

    async def test_llm_boundary_strips_emoji_without_truncating_records(self):
        provider = types.SimpleNamespace(
            text_chat=AsyncMock(
                return_value=types.SimpleNamespace(
                    result_chain=types.SimpleNamespace(
                        get_plain_text=lambda: "Full result " + "x" * 1500
                    )
                )
            )
        )
        action = self.plugin.base.BaseAction(
            types.SimpleNamespace(get_using_provider=lambda: provider)
        )
        record = "BEGIN" + "long record " * 1000 + "END"
        prompt = "🐺⚠️🔮 角色🧑‍⚖️\n" + record
        result = await action._call_llm(prompt, self.players[0], max_retries=1)
        sent = provider.text_chat.await_args.kwargs["prompt"]
        self.assertIn(record, sent)
        self.assertEqual(sent.count(record), 1)
        self.assertNotRegex(sent, r"[\U0001F000-\U0001FAFF☀-➿‍️]")
        self.assertEqual(result, "Full result " + "x" * 1500)
        self.assertEqual(self.players[0].name, "Alice")


if __name__ == "__main__":
    unittest.main()
