"""Chinese public-promise boundaries and finite repair for Issue #4."""

import importlib
import types
import unittest
from unittest.mock import AsyncMock, Mock

from evaluate_ai import create_scenario
from test_issue_1_regressions import load_plugin
from test_issue_3_ai import model_reply


class AIScenarioTestCase(unittest.IsolatedAsyncioTestCase):
    """Share the existing nine-player evaluation fixture across AI regressions."""

    @classmethod
    def setUpClass(cls):
        cls.scope = load_plugin()
        cls.plugin = cls.scope.__enter__()
        prefix = cls.plugin.models.__package__.rsplit(".", 1)[0]
        cls.validate = staticmethod(
            importlib.import_module(
                f"{prefix}.services.ai.validators"
            ).validate_vote_statement
        )

    @classmethod
    def tearDownClass(cls):
        cls.scope.__exit__(None, None, None)

    async def asyncSetUp(self):
        self.room, self.service, self.actor = create_scenario(self.plugin, Mock(), 17)
        self.room.phase = self.plugin.models.GamePhase.DAY_VOTE
        self.action = self.service._vote_action


class VoteStatementTests(AIScenarioTestCase):
    """Keep the complete public expression consistent with the committed ballot."""

    async def test_issue_four_reproductions_and_matching_controls(self):
        for speech, number, code in (
            ("这轮我弃票。", 8, "abstention_conflict"),
            ("我今天投2号。", 8, "target_mismatch"),
            ("我今天投2号。", None, "target_vs_abstention"),
        ):
            with self.subTest(speech=speech, ballot=number):
                with self.assertRaisesRegex(ValueError, code):
                    self.validate(speech, number, number is None)
        for speech, number in (
            ("有人刚才说「我投2号」，我这票投8号。", 8),
            ("我投8号。", 8),
            ("我今天投2号。", 2),
            ("这一轮我弃票。", None),
        ):
            with self.subTest(speech=speech):
                self.validate(speech, number, number is None)

    async def test_quotes_denials_and_past_votes_are_not_current_positive_votes(self):
        for speech in (
            "2号刚才说“我投2号”，我这票投8号。",
            "有人说『我投2号』。我今天投8号。",
            '有人说"我投2号"，我这轮投8号。',
            "引用：‘我弃票’。我投8号。",
            "他说「她说『我投2号』」，我投8号。",
            "> 我投2号\n我投8号。",
            "我不投2号，投8号。",
            "我不会弃票，我投8号。",
            "我没说过我投2号，我投8号。",
            "2号要求我投2号，我投8号。",
            "我昨天投2号，今天投8号。",
            "昨天我投2号，今天我投8号。",
            "昨天投2号，今天投8号。",
            "我原本投2号，现在我改投8号。",
        ):
            with self.subTest(speech=speech):
                self.validate(speech, 8, False)

    async def test_uncertain_hypothetical_and_reported_numbers_do_not_form_a_ballot(
        self,
    ):
        for speech in (
            "我可能投2号，还没决定。",
            "如果我是狼，投2号会怎么样？",
            "如果2号解释不了，我今天投2号。",
            "2号说：我投2号。",
            "我觉得2号可能会投3号。",
            "我投2号？这是你说的，我还没选。",
            "我倾向2号，但还想核对。",
            "我引用2号的话：我弃票。",
            "只剩2号和8号值得再聊。",
            "我给2号一点解释的机会。",
            "我投2号或8号，还没决定。",
            "我投2号或弃票，还没想好。",
            "我投2号的话，就投2号。",
            "我投2号才怪。",
        ):
            with self.subTest(speech=speech):
                self.validate(speech, 8, False)

    async def test_common_current_promises_and_large_seat_numbers(self):
        for speech in (
            "我这轮投12号。",
            "今天我投12号。",
            "本轮我决定投12号。",
            "我这票投12号。",
            "这一票投12号。",
            "我把票投给12号。",
            "所以我最后投12号。",
            "这票我投12号。",
            "这一票我给12号。",
            "我给12号一票。",
            "我会先投12号。",
            "我决定今天投12号。",
        ):
            with self.subTest(speech=speech):
                self.validate(speech, 12, False)
                with self.assertRaisesRegex(ValueError, "target_mismatch"):
                    self.validate(speech, 8, False)
        with self.assertRaisesRegex(ValueError, "excluded_target"):
            self.validate("我不投2号，投8号。", 2, False)
        with self.assertRaisesRegex(ValueError, "target_mismatch"):
            self.validate("昨天投2号，今天投8号。", 2, False)
        with self.assertRaisesRegex(ValueError, "abstention_conflict"):
            self.validate("我不会弃票。", None, True)

    async def test_finite_repairs_publish_only_the_final_full_speech_and_ballot(self):
        final_speech = (
            "有人刚才说「我投2号」，"
            + "我想把他的依据核对清楚。" * 30
            + "我今天投8号。"
        )
        self.action._call_llm = AsyncMock(
            side_effect=[
                model_reply(target=8, speech="这轮我弃票。"),
                model_reply(target=8, speech="我今天投2号。"),
                model_reply(target=8, speech=final_speech),
            ]
        )
        for player in self.room.players.values():
            player.is_ai = player.id == self.actor.id
        messages = types.SimpleNamespace(
            send_group_message=AsyncMock(return_value=True)
        )
        manager = types.SimpleNamespace(
            rooms={self.room.group_id: self.room},
            config=self.room.config,
            ai_player_service=self.service,
            message_service=messages,
        )
        manager.get_room = manager.rooms.get
        phase = self.plugin.phases.DayVotePhase(manager)
        await phase._handle_ai_discussion_and_votes(self.room, generation=0)
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertEqual(
            self.room.vote_state.day_votes,
            {self.actor.id: self.room.get_player_by_number(8).id},
        )
        self.assertEqual(
            self.room.vote_discussion,
            [{"player": self.actor.display_name, "content": final_speech}],
        )
        self.assertEqual(messages.send_group_message.await_count, 2)
        self.assertEqual(
            messages.send_group_message.call_args_list[0].args[1],
            f"{self.actor.display_name}：{final_speech}",
        )
        self.assertEqual(len(self.actor.ai_context.decision_history), 1)
        self.assertEqual(len(self.actor.ai_context.vote_history), 1)

    async def test_failed_repairs_remain_failure_and_never_announce_invalid_speech(
        self,
    ):
        self.action._call_llm = AsyncMock(
            return_value=model_reply(target=8, speech="这一轮我弃票。")
        )
        speech, number = await self.action.decide_vote(self.actor, self.room)
        self.assertIsNone(number)
        self.assertNotEqual(speech, "这一轮我弃票。")
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "failed_fallback"
        )
        self.assertNotIn(
            "explicit_abstain",
            {item["status"] for item in self.actor.ai_context.decision_audit},
        )
        self.assertFalse(self.actor.ai_context.decision_history)

    async def test_pk_references_do_not_bypass_candidates_or_change_reason(self):
        self.actor.ai_context.vote_intention = {
            "round": 1,
            "target_number": 2,
            "abstain": False,
        }
        self.room.vote_state.is_pk_vote = True
        self.room.vote_state.pk_players = [self.room.get_player_by_number(8).id]
        final = "2号说「我投2号」，我今天投8号。"
        self.action._call_llm = AsyncMock(
            side_effect=[
                model_reply(
                    target=2, speech="我今天投2号。", change_reason="核对了新说法"
                ),
                model_reply(target=8, speech=final),
                model_reply(
                    target=8, speech=final, change_reason="2号的解释让我改选8号"
                ),
            ]
        )
        self.assertEqual(
            await self.action.decide_vote(self.actor, self.room, True, [8]), (final, 8)
        )
        self.assertEqual(self.action._call_llm.await_count, 3)

    async def test_stale_repairs_do_not_commit_or_produce_fallback_text(self):
        async def stale(*args, **kwargs):
            self.room.phase = self.plugin.models.GamePhase.DAY_PK
            self.room.vote_state.generation += 1
            return model_reply(target=8, speech="我今天投8号。")

        replies = iter([model_reply(target=8, speech="我今天投2号。")])

        async def complete(*args, **kwargs):
            reply = next(replies, None)
            return reply if reply is not None else await stale()

        self.action._call_llm = AsyncMock(side_effect=complete)
        self.assertEqual(
            await self.action.decide_vote(self.actor, self.room), ("", None)
        )
        self.assertEqual(self.action._call_llm.await_count, 2)
        self.assertEqual(self.actor.ai_context.decision_audit[-1]["status"], "stale")
        self.assertFalse(self.actor.ai_context.decision_history)
        self.assertFalse(self.room.used_fallback_speeches)
