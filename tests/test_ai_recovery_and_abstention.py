"""Regression scenarios from real-game timeouts, repeated fallbacks and ballots."""

import asyncio
import importlib
import types
from unittest.mock import AsyncMock, Mock, patch

from test_issue_1_regressions import PluginTestCase
from test_issue_3_ai import model_reply


class RecoveryAndAbstentionTests(PluginTestCase):
    """Exercise actual handlers, workers and settlement without an external model."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.room.current_round = 1
        self.prefix = self.plugin.models.__package__.rsplit(".", 1)[0]
        self.service = self.plugin.services.AIPlayerService(Mock())
        self.actor = self.players[0]
        self.actor.ai_config = self.plugin.models.AIPlayerConfig(name=self.actor.name)
        self.runtime = self.plugin.services.GameManager(Mock(), self.room.config)
        self.runtime.rooms[self.room.group_id] = self.room
        self.manager.get_room_by_player = lambda pid: (
            (self.room.group_id, self.room)
            if pid in self.room.players
            else (None, None)
        )

    def event(self, player=None, *, private=False):
        """Build the message boundary used by several command scenarios.

        Args:
            player: Sender, defaulting to the second player.
            private: Whether the command was sent privately.

        Returns:
            Message object exposing the real handlers' required API.
        """
        player = player or self.players[1]
        return types.SimpleNamespace(
            get_group_id=lambda: "" if private else self.room.group_id,
            get_sender_id=lambda: player.id,
            is_private_chat=lambda: private,
            plain_result=lambda text: text,
        )

    def handler_module(self, name):
        """Import command handlers when the real AstrBot SDK is installed.

        Args:
            name: Handler module name.

        Returns:
            Loaded module.
        """
        try:
            return importlib.import_module(f"{self.prefix}.handlers.{name}")
        except ModuleNotFoundError as error:
            if error.name and error.name.startswith("astrbot"):
                self.skipTest("Command handlers require the installed AstrBot SDK")
            raise

    async def test_request_allows_slow_response_and_retains_explicit_timeout(self):
        provider = Mock()
        provider.text_chat = AsyncMock(
            return_value=types.SimpleNamespace(
                result_chain=types.SimpleNamespace(get_plain_text=lambda: "complete")
            )
        )
        action = self.plugin.base.BaseAction(Mock())
        action._get_provider = Mock(return_value=provider)
        budgets = []

        async def virtual_wait(request, timeout):
            budgets.append(timeout)
            if len(budgets) == 1 and timeout < 95:
                request.close()
                raise asyncio.TimeoutError
            return await request

        with patch("asyncio.wait_for", side_effect=virtual_wait):
            self.assertEqual(
                await action._call_llm("prompt", self.actor, max_retries=1), "complete"
            )
            await action._call_llm("prompt", self.actor, max_retries=1, timeout=12)
        self.assertEqual(budgets, [180, 12])

    async def test_review_uses_the_same_180_second_request_budget(self):
        reviewer = self.runtime.ai_reviewer
        reviewer._get_provider = Mock(
            return_value=types.SimpleNamespace(
                text_chat=AsyncMock(
                    return_value=types.SimpleNamespace(
                        result_chain=types.SimpleNamespace(
                            get_plain_text=lambda: (
                                '{"highlight_ids":[],"advice_ids":[]}'
                            )
                        )
                    )
                )
            )
        )
        real_wait = asyncio.wait_for
        with patch("asyncio.wait_for", wraps=real_wait) as wait:
            winner = self.plugin.services.VictoryChecker.check(self.room)[1]
            review = await reviewer.generate_review(self.room, winner)
        self.assertIn("AI复盘", review)
        self.assertEqual(wait.call_args.kwargs["timeout"], 180)

    async def test_serialized_wolf_workflow_survives_more_than_60_seconds(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        for player in self.players:
            player.role = self.plugin.models.Role.WEREWOLF
            player.is_ai = True
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        phase._process_all_ai_wolves = AsyncMock()
        phase._finish_and_next = AsyncMock()
        phase._fallback_wolf_vote = AsyncMock()

        async def virtual_wait(worker, timeout):
            if timeout < 600:
                worker.close()
                raise asyncio.TimeoutError
            await worker

        with patch("asyncio.wait_for", side_effect=virtual_wait):
            await phase._process_all_ai_wolves_with_timeout(self.room)
        phase._process_all_ai_wolves.assert_awaited_once()
        phase._fallback_wolf_vote.assert_not_awaited()
        phase._finish_and_next.assert_awaited_once()

    async def test_wolf_workflow_timeout_preserves_submitted_votes(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        for player in self.players:
            player.role = self.plugin.models.Role.WEREWOLF
        self.players[2].is_ai = True
        self.room.vote_state.night_votes.update(
            {self.actor.id: self.players[2].id, self.players[1].id: "ABSTAIN"}
        )
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        phase._finish_and_next = AsyncMock()

        async def expire(worker, timeout):
            worker.close()
            raise asyncio.TimeoutError

        with (
            patch("asyncio.wait_for", side_effect=expire),
            patch("random.choice", side_effect=AssertionError("No random fallback")),
        ):
            await phase._process_all_ai_wolves_with_timeout(self.room)
        self.assertEqual(
            self.room.vote_state.night_votes,
            {
                self.actor.id: self.players[2].id,
                self.players[1].id: "ABSTAIN",
                self.players[2].id: "ABSTAIN",
            },
        )
        phase._finish_and_next.assert_awaited_once()

    async def test_personality_fallbacks_vary_and_do_not_repeat_consecutively(self):
        speech_type = type(self.service._speech_action)
        banks = importlib.import_module(f"{self.prefix}.services.ai.prompts.fallbacks")
        personas = importlib.import_module(f"{self.prefix}.models.personality")
        self.assertFalse(set(personas.PERSONALITIES) - set(banks.PERSONALITY_GROUPS))
        for key in ("quiet", "aggressive", "logical", "funny", "mediator"):
            self.actor.ai_context.personality_key = key
            text = speech_type.fallback_speech(self.actor, self.room)
            self.assertIn(text, banks.DAY_FALLBACKS[banks.PERSONALITY_GROUPS[key]])
        self.room.used_fallback_speeches.clear()
        self.actor.ai_context.personality_key = "quiet"
        texts = [speech_type.fallback_speech(self.actor, self.room) for _ in range(30)]
        self.assertEqual(len(set(texts[:8])), 8)
        self.assertTrue(all(a != b for a, b in zip(texts, texts[1:])))
        self.assertNotIn("这轮我还没有形成可靠判断。", texts)

    async def test_generation_failure_uses_stage_wording_and_keeps_failure_audit(self):
        speech = self.service._speech_action
        speech._call_llm = AsyncMock(return_value=None)
        self.actor.ai_context.personality_key = "quiet"
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        first = await speech.generate_speech(self.actor, self.room)
        second = await speech.generate_speech(self.actor, self.room)
        self.assertNotEqual(first, second)
        self.room.phase = self.plugin.models.GamePhase.DAY_PK
        self.room.vote_state.pk_players = [p.id for p in self.players[:2]]
        pk = await speech.generate_speech(self.actor, self.room, is_pk=True)
        banks = importlib.import_module(f"{self.prefix}.services.ai.prompts.fallbacks")
        self.assertIn(pk, banks.PK_FALLBACKS["quiet"])
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "failed_fallback"
        )
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["fallback_speech"], pk
        )

    async def test_last_words_failure_retains_all_true_seer_results(self):
        self.room.phase = self.plugin.models.GamePhase.LAST_WORDS
        self.room.last_words_player_id = self.actor.id
        self.actor.role = self.plugin.models.Role.SEER
        self.actor.ai_context.role_name = "预言家"
        self.actor.ai_context.seer_results = [
            {"round": 1, "target": "2号.Bob", "is_werewolf": True},
            {"round": 2, "target": "3号.Carol", "is_werewolf": False},
        ]
        self.room.current_round = 2
        action = self.service._speech_action
        action._call_llm = AsyncMock(return_value=None)
        text = await action.generate_last_words(self.actor, self.room)
        self.assertIn("第1夜我验2号.Bob，结果是狼人", text)
        self.assertIn("第2夜我验3号.Carol，结果是好人", text)

    async def test_failed_ballots_vary_but_never_acquire_a_target(self):
        action = self.service._vote_action
        action._call_llm = AsyncMock(return_value=None)
        self.actor.ai_context.personality_key = "quiet"
        first, first_target = await action.decide_vote(self.actor, self.room)
        second, second_target = await action.decide_vote(
            self.actor, self.room, True, [2]
        )
        self.assertIsNone(first_target)
        self.assertIsNone(second_target)
        self.assertNotEqual(first, second)
        self.assertEqual(
            sum(
                a["status"] == "failed_fallback"
                for a in self.actor.ai_context.decision_audit
            ),
            2,
        )
        self.assertNotIn(
            "explicit_abstain",
            {a["status"] for a in self.actor.ai_context.decision_audit},
        )
        action._call_llm = AsyncMock(return_value=model_reply(target=None))
        await action.decide_vote(self.actor, self.room, True, [2])
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "explicit_abstain"
        )

    async def test_stale_reply_does_not_consume_or_publish_fallback_wording(self):
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING

        async def stale(*args, **kwargs):
            self.room.current_round += 1
            return model_reply("day_speech", target=None, speech="Old speech")

        action = self.service._speech_action
        action._call_llm = AsyncMock(side_effect=stale)
        self.assertEqual(await action.generate_speech(self.actor, self.room), "")
        self.assertEqual(self.actor.ai_context.decision_audit[-1]["status"], "stale")
        self.assertFalse(self.room.used_fallback_speeches)

    async def test_human_day_and_pk_abstention_are_completed_ballots(self):
        handler = self.handler_module("day_commands").DayCommandHandler(self.manager)
        event = self.event()
        for is_pk in (False, True):
            self.room.vote_state.day_votes.clear()
            self.room.vote_state.is_pk_vote = is_pk
            self.room.vote_state.pk_players = [self.actor.id]
            replies = [r async for r in handler.day_vote(event, abstain=True)]
            self.assertEqual(
                self.room.vote_state.day_votes, {self.players[1].id: "ABSTAIN"}
            )
            self.assertIn("弃票成功", replies[0])
            self.assertIn("1/3", replies[0])
            self.assertEqual(self.actor.ai_context.vote_history[-1]["target"], "弃票")
            self.assertEqual(self.actor.ai_context.vote_history[-1]["is_pk"], is_pk)

    async def test_repeated_abstention_is_idempotent_and_can_change_before_settlement(
        self,
    ):
        handler = self.handler_module("day_commands").DayCommandHandler(self.manager)
        event = self.event()
        for _ in range(2):
            [r async for r in handler.day_vote(event, abstain=True)]
        self.assertEqual(len(self.actor.ai_context.vote_history), 1)
        handler.get_target_user = lambda _: "3"
        [r async for r in handler.day_vote(event)]
        self.assertEqual(
            self.room.vote_state.day_votes[self.players[1].id], self.players[2].id
        )
        handler.get_target_user = lambda _: "弃票"
        [r async for r in handler.day_vote(event)]
        self.assertEqual(self.room.vote_state.day_votes[self.players[1].id], "ABSTAIN")
        self.assertEqual(len(self.actor.ai_context.vote_history), 3)

    async def test_human_abstention_rejects_dead_nonmember_and_wrong_phase(self):
        handler = self.handler_module("day_commands").DayCommandHandler(self.manager)
        self.players[1].is_alive = False
        replies = [r async for r in handler.day_vote(self.event(), abstain=True)]
        self.assertIn("已经出局", replies[0])
        self.players[1].is_alive = True
        outsider = types.SimpleNamespace(id="outsider")
        replies = [
            r async for r in handler.day_vote(self.event(outsider), abstain=True)
        ]
        self.assertIn("不在游戏", replies[0])
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        replies = [r async for r in handler.day_vote(self.event(), abstain=True)]
        self.assertIn("不是投票阶段", replies[0])
        self.assertFalse(self.room.vote_state.day_votes)

    async def test_final_human_abstention_settles_once_and_old_reply_cannot_settle_pk(
        self,
    ):
        handler = self.handler_module("day_commands").DayCommandHandler(self.manager)
        self.room.vote_state.day_votes.update(
            {self.actor.id: "ABSTAIN", self.players[2].id: "ABSTAIN"}
        )
        with patch.object(
            self.plugin.phases.DayVotePhase, "_enter_night", new=AsyncMock()
        ) as enter:
            [r async for r in handler.day_vote(self.event(), abstain=True)]
            [r async for r in handler.day_vote(self.event(), abstain=True)]
        enter.assert_awaited_once()
        self.messages.announce_vote_result.assert_awaited_once()
        self.assertEqual(self.room.alive_count, 3)
        self.room.vote_state.settled = False
        self.room.vote_state.generation += 1
        self.room.vote_state.day_votes.update(
            {self.actor.id: "ABSTAIN", self.players[2].id: "ABSTAIN"}
        )
        pending = handler.day_vote(self.event(), abstain=True)
        await anext(pending)
        self.room.vote_state.generation += 1
        self.room.vote_state.day_votes.clear()
        self.room.vote_state.day_votes.update({p.id: "ABSTAIN" for p in self.players})
        with patch.object(
            self.plugin.phases.DayVotePhase, "_process_vote_result", new=AsyncMock()
        ) as settle:
            with self.assertRaises(StopAsyncIteration):
                await anext(pending)
        settle.assert_not_awaited()

    async def test_all_abstentions_skip_pk_and_advance_even_during_pk(self):
        for is_pk in (False, True):
            self.room.vote_state.settled = False
            self.room.vote_state.is_pk_vote = is_pk
            self.room.vote_state.pk_players = [self.players[1].id, self.players[2].id]
            self.room.vote_state.day_votes = {p.id: "ABSTAIN" for p in self.players}
            await self.phase.settle(self.room)
            self.assertEqual(self.room.alive_count, 3)
        self.assertEqual(self.phase._enter_night.await_count, 2)
        self.phase._enter_last_words.assert_not_awaited()
        self.assertEqual(self.messages.announce_vote_result.call_args.args[1], {})
        self.assertEqual(
            set(self.messages.announce_vote_result.call_args.args[2]["ABSTAIN"]),
            {p.id for p in self.players},
        )

    async def test_one_effective_vote_exiles_target_despite_more_abstentions(self):
        self.room.vote_state.day_votes = {
            self.actor.id: self.players[2].id,
            self.players[1].id: "ABSTAIN",
            self.players[2].id: "ABSTAIN",
        }
        await self.phase.settle(self.room)
        self.assertFalse(self.players[2].is_alive)
        self.assertEqual(self.room.last_words_player_id, self.players[2].id)
        self.phase._enter_last_words.assert_awaited_once()

    async def test_result_distinguishes_abstentions_missing_ballots_and_no_effective_votes(
        self,
    ):
        messages = self.runtime.message_service
        messages.send_group_message = AsyncMock(return_value=True)
        await messages.announce_vote_result(
            self.room,
            {self.players[2].id: 1},
            {self.players[2].id: [self.actor.id], "ABSTAIN": [self.players[1].id]},
            self.players[2].display_name,
        )
        text = messages.send_group_message.call_args.args[1]
        self.assertIn(f"弃票（1人）：{self.players[1].display_name}", text)
        self.assertIn(f"未投票（1人）：{self.players[2].display_name}", text)
        await messages.announce_vote_result(
            self.room, {}, {"ABSTAIN": [p.id for p in self.players]}, is_pk=True
        )
        text = messages.send_group_message.call_args.args[1]
        self.assertIn("无有效票", text)
        self.assertNotIn("平票", text)
        self.assertNotIn("未投票", text)

    async def test_human_wolf_can_abstain_privately_but_other_roles_cannot(self):
        handler = self.handler_module("night_commands").NightCommandHandler(
            self.manager
        )
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        wolf = self.players[1]
        wolf.role = self.plugin.models.Role.WEREWOLF
        with patch.object(
            self.plugin.phases.NightWolfPhase, "on_all_voted", new=AsyncMock()
        ) as finish:
            replies = [
                r
                async for r in handler.werewolf_kill(
                    self.event(wolf, private=True), abstain=True
                )
            ]
        self.assertIn("已弃票", replies[0])
        self.assertEqual(self.room.vote_state.night_votes, {wolf.id: "ABSTAIN"})
        finish.assert_awaited_once()
        self.room.vote_state.night_votes.clear()
        replies = [
            r async for r in handler.werewolf_kill(self.event(wolf), abstain=True)
        ]
        self.assertIn("私聊", replies[0])
        replies = [
            r
            async for r in handler.werewolf_kill(
                self.event(self.players[2], private=True), abstain=True
            )
        ]
        self.assertIn("不是狼人", replies[0])
        self.assertFalse(self.room.vote_state.night_votes)

    async def test_ai_wolf_can_explicitly_abstain_and_failure_never_randomizes(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.actor.role = self.plugin.models.Role.WEREWOLF
        self.actor.ai_context.role_name = "狼人"
        self.actor.ai_context.is_werewolf = True
        self.manager.ai_player_service = self.service
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        action = self.service._werewolf_action
        action._call_llm = AsyncMock(
            return_value=model_reply("werewolf_kill", target=None, speech="")
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._handle_ai_werewolf_vote(self.room)
        self.assertEqual(self.room.vote_state.night_votes[self.actor.id], "ABSTAIN")
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "explicit_abstain"
        )
        self.room.vote_state.night_votes.clear()
        action._call_llm = AsyncMock(return_value=None)
        with (
            patch("asyncio.sleep", new=AsyncMock()),
            patch("random.choice", side_effect=AssertionError("No random fallback")),
        ):
            await phase._handle_ai_werewolf_vote(self.room)
        self.assertEqual(self.room.vote_state.night_votes[self.actor.id], "ABSTAIN")
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "failed_fallback"
        )

    async def test_wolf_settlement_filters_abstention_and_resets_stale_kill(self):
        self.actor.role = self.plugin.models.Role.WEREWOLF
        self.players[1].role = self.plugin.models.Role.WEREWOLF
        self.room.vote_state.night_votes = {p.id: "ABSTAIN" for p in self.players[:2]}
        self.room.last_killed_id = self.players[2].id
        with patch("random.choice", side_effect=AssertionError("No valid target")):
            self.assertIsNone(await self.runtime.process_night_kill(self.room))
        self.assertIsNone(self.room.last_killed_id)
        self.assertFalse(self.room.vote_state.night_votes)
        self.room.vote_state.night_votes = {
            self.actor.id: "ABSTAIN",
            self.players[1].id: self.players[2].id,
        }
        self.assertEqual(
            await self.runtime.process_night_kill(self.room), self.players[2].id
        )

    async def test_scheduled_ai_abstention_waits_for_unsubmitted_human_wolf(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.actor.role = self.plugin.models.Role.WEREWOLF
        self.players[1].role = self.plugin.models.Role.WEREWOLF
        self.ai.decide_werewolf_kill = AsyncMock(return_value=None)
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        phase._finish_and_next = AsyncMock()
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._trigger_ai_vote_and_finish(self.room)
        self.assertEqual(self.room.vote_state.night_votes, {self.actor.id: "ABSTAIN"})
        phase._finish_and_next.assert_not_awaited()

    async def test_human_command_and_expiry_share_pending_ai_wolf_vote(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.actor.role = self.plugin.models.Role.WEREWOLF
        self.players[1].role = self.plugin.models.Role.WEREWOLF
        self.room.vote_state.night_votes[self.players[1].id] = "ABSTAIN"
        self.ai.decide_werewolf_kill, entered, release = self.blocked(3)
        expiry_entered = asyncio.Event()

        async def announce(*args):
            expiry_entered.set()

        self.messages.announce_timeout = AsyncMock(side_effect=announce)
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        phase._finish_and_next = AsyncMock()
        with patch("asyncio.sleep", new=AsyncMock()):
            command = self.start(phase.trigger_ai_wolf_vote(self.room, reconsider=True))
            await self.wait_entered(entered)
            timer = self.start(phase.on_timeout(self.room))
            await self.wait_entered(expiry_entered)
            release.set()
            await asyncio.gather(command, timer)
        self.ai.decide_werewolf_kill.assert_awaited_once()
        self.assertEqual(
            self.room.vote_state.night_votes[self.actor.id], self.players[2].id
        )
        self.assertEqual(
            self.room.vote_state.night_votes[self.players[1].id], "ABSTAIN"
        )
        phase._finish_and_next.assert_awaited_once()

    async def test_ai_day_worker_failure_submits_abstention_and_counts_completion(self):
        self.ai.decide_vote = AsyncMock(
            side_effect=RuntimeError("Provider unavailable")
        )
        self.room.vote_state.day_votes = {
            self.players[1].id: "ABSTAIN",
            self.players[2].id: "ABSTAIN",
        }
        with patch("random.choice", side_effect=AssertionError("No random fallback")):
            await self.phase.trigger_ai_votes(self.room)
        self.phase._enter_night.assert_awaited_once()
        self.assertEqual(self.room.alive_count, 3)
        snapshot = self.messages.announce_vote_result.call_args.args[2]
        self.assertEqual(set(snapshot["ABSTAIN"]), {p.id for p in self.players})

    async def test_tied_effective_votes_still_enter_pk_with_an_abstainer(self):
        self.room.vote_state.day_votes = {
            self.actor.id: self.players[1].id,
            self.players[1].id: self.actor.id,
            self.players[2].id: "ABSTAIN",
        }
        with patch.object(
            self.plugin.phases.DaySpeakingPhase, "enter_pk_phase", new=AsyncMock()
        ) as enter:
            await self.phase.settle(self.room)
        enter.assert_awaited_once()
        self.assertEqual(
            set(enter.call_args.args[1]), {self.actor.id, self.players[1].id}
        )
        self.phase._enter_night.assert_not_awaited()

    async def test_cleanup_cancels_the_shared_wolf_worker(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.actor.role = self.plugin.models.Role.WEREWOLF
        self.ai.decide_werewolf_kill, entered, release = self.blocked(3)
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        with patch("asyncio.sleep", new=AsyncMock()):
            waiting = self.start(phase.trigger_ai_wolf_vote(self.room))
            await self.wait_entered(entered)
            worker = self.room.wolf_ai_ballot_task
            self.room.phase = self.plugin.models.GamePhase.FINISHED
            self.room.cancel_timer()
            release.set()
            await waiting
        self.assertTrue(worker.cancelled())
        self.assertFalse(self.room.vote_state.night_votes)

    async def test_abstain_entry_command_routes_group_and_private_messages(self):
        self.handler_module("day_commands")
        module = importlib.import_module(f"{self.prefix}.main")
        plugin_type = next(
            obj
            for obj in vars(module).values()
            if isinstance(obj, type)
            and obj.__module__ == module.__name__
            and hasattr(obj, "abstain_vote")
        )
        calls = []

        async def handler(event, *, abstain):
            calls.append((event.is_private_chat(), abstain))
            yield "ok"

        plugin = types.SimpleNamespace(
            day_handler=types.SimpleNamespace(day_vote=handler),
            night_handler=types.SimpleNamespace(werewolf_kill=handler),
        )
        for private in (False, True):
            replies = [
                r
                async for r in plugin_type.abstain_vote(
                    plugin, self.event(private=private)
                )
            ]
            self.assertEqual(replies, ["ok"])
        self.assertEqual(calls, [(False, True), (True, True)])

    async def test_virtual_players_do_not_reach_qq_moderation_apis(self):
        service = importlib.import_module(
            f"{self.prefix}.services.ban_service"
        ).BanService
        self.room.group_id = "1029776854"
        old_id = self.actor.id
        del self.room.players[old_id]
        self.actor.id = "ai_virtual_player"
        self.room.players[self.actor.id] = self.actor
        self.room.bot = types.SimpleNamespace(
            set_group_ban=AsyncMock(),
            set_group_admin=AsyncMock(),
            set_group_card=AsyncMock(),
        )
        self.room.banned_player_ids.add(self.actor.id)
        self.room.temp_admin_ids.add(self.actor.id)
        for action in (
            service.ban_player,
            service.unban_player,
            service.set_temp_admin,
            service.remove_temp_admin,
        ):
            self.assertTrue(await action(self.room, self.actor.id))
        self.assertTrue(await service.set_group_card(self.room, self.actor.id, "1号"))
        for method in vars(self.room.bot).values():
            method.assert_not_awaited()
        self.assertNotIn(self.actor.id, self.room.banned_player_ids)
        self.assertNotIn(self.actor.id, self.room.temp_admin_ids)
        human_id = self.players[1].id
        self.assertTrue(await service.ban_player(self.room, human_id))
        self.assertTrue(await service.set_temp_admin(self.room, human_id))
        self.assertTrue(await service.remove_temp_admin(self.room, human_id))
        self.assertTrue(await service.set_group_card(self.room, human_id, "2号"))
        self.assertEqual(
            self.room.bot.set_group_ban.call_args.kwargs["user_id"], int(human_id)
        )
