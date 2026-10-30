"""Bounded, role-scoped public-history reads for Issue #5."""

import copy
import importlib
import json
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from evaluate_ai import create_scenario, evaluate
from test_issue_3_ai import model_reply
from test_issue_4_vote_statements import AIScenarioTestCase


class EvidenceReadTests(AIScenarioTestCase):
    """Read complete historical material without committing an intermediate turn."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.room, self.service, self.actor = create_scenario(
            self.plugin, Mock(), 17, scenario="history_clarification"
        )
        self.room.phase = self.plugin.models.GamePhase.DAY_VOTE
        self.action = self.service._vote_action
        prefix = self.plugin.models.__package__.rsplit(".", 1)[0]
        self.builder = importlib.import_module(
            f"{prefix}.services.ai.context.builder"
        ).ContextBuilder
        self.ctx = self.actor.ai_context
        self.original = self.ctx.speeches[0]["content"]
        self.request = json.dumps({"request_evidence_ids": ["S1"]})

    async def test_index_expansion_retains_full_original_and_does_not_mutate_storage(
        self,
    ):
        stored = copy.deepcopy(self.ctx.speeches)
        initial = self.builder.build_context(self.actor, self.room)
        self.assertNotIn(self.original, initial)
        self.assertIn(
            f"[S1 第1轮 玩家发言] {self.room.get_player_by_number(2).display_name}",
            initial,
        )
        self.assertNotIn("S1", self.ctx.visible_evidence_ids)
        expanded = self.builder.build_context(
            self.actor, self.room, extra_evidence_ids=["S1", "S1"]
        )
        self.assertGreater(len(self.original), 100)
        self.assertEqual(expanded.count(self.original), 1)
        self.assertIn("我没有自称预言家，也没有说自己验过3号。", expanded)
        self.assertIn("S1", self.ctx.visible_evidence_ids)
        self.assertEqual(self.ctx.speeches, stored)
        self.assertNotIn(
            self.original, self.builder.build_context(self.actor, self.room)
        )

    async def test_public_discussion_ids_are_retrievable_and_deduplicated(self):
        actor = self.room.get_player_by_number(6).display_name
        self.ctx.current_round = 1
        self.ctx.add_vote_discussion(actor, "旧投票讨论原话" * 40 + "我当时没有定票。")
        self.ctx.current_round = 2
        self.ctx.add_vote_discussion(actor, "更新的讨论")
        self.ctx.current_round = 3
        records = self.ctx.resolve_public_evidence(["S1", "D1", "D1"])
        self.assertEqual(list(records), ["S1", "D1"])
        self.assertTrue(records["D1"]["text"].endswith("我当时没有定票。"))
        text = self.builder.build_context(
            self.actor, self.room, extra_evidence_ids=["D1"]
        )
        self.assertIn(records["D1"]["text"], text)

    async def test_unavailable_private_future_and_unsupported_ids_are_rejected_atomically(
        self,
    ):
        self.ctx.current_round = 4
        self.ctx.add_speech(
            self.room.get_player_by_number(2).display_name, "FUTURE_SECRET_PUBLIC_WORDS"
        )
        future = f"S{len(self.ctx.speeches)}"
        self.ctx.current_round = 3
        self.builder.build_context(self.actor, self.room)
        visible = set(self.ctx.visible_evidence_ids)
        for key in ("S999", "Q1", "W1", "C1", "A1", "V1", "E1", future):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "unavailable public"):
                    self.ctx.resolve_public_evidence(["S1", key])
                self.assertEqual(self.ctx.visible_evidence_ids, visible)
        for invalid in ([], "S1", [1], ["S1", "S2", "S3", "S4"]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.ctx.resolve_public_evidence(invalid)

    async def test_read_request_payload_cannot_execute_its_speech_ballot_or_assessments(
        self,
    ):
        messages = types.SimpleNamespace(
            send_group_message=AsyncMock(return_value=True)
        )
        manager = types.SimpleNamespace(
            rooms={self.room.group_id: self.room},
            config=self.room.config,
            message_service=messages,
            ai_player_service=self.service,
        )
        manager.get_room = manager.rooms.get
        for player in self.room.players.values():
            player.is_ai = player.id == self.actor.id
        payload = json.loads(
            model_reply(target=8, speech="INTERMEDIATE_WORDS_MUST_NOT_BE_PUBLISHED")
        )
        payload["request_evidence_ids"] = ["S1", "S1"]
        payload["assessments"] = [{"player_number": 2, "suspicion": 10}]
        prompts = []

        async def respond(prompt, *args, **kwargs):
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps(payload)
            self.assertIn(self.original, prompt)
            self.assertEqual(prompt.count(self.original), 1)
            self.assertIn("S1", self.ctx.visible_evidence_ids)
            self.assertFalse(self.ctx.vote_intention)
            self.assertFalse(self.ctx.player_suspicions)
            self.assertFalse(self.ctx.decision_history)
            self.assertFalse(self.room.vote_state.day_votes)
            messages.send_group_message.assert_not_awaited()
            return model_reply(target=8, speech="我这票投8号。", evidence_ids=["S1"])

        self.action._call_llm = AsyncMock(side_effect=respond)
        await self.plugin.phases.DayVotePhase(manager)._handle_ai_discussion_and_votes(
            self.room, generation=0
        )
        self.assertEqual(self.action._call_llm.await_count, 2)
        self.assertEqual(
            self.room.vote_state.day_votes[self.actor.id],
            self.room.get_player_by_number(8).id,
        )
        self.assertEqual(len(self.ctx.decision_history), 1)
        self.assertFalse(self.ctx.player_suspicions)
        self.assertEqual(messages.send_group_message.await_count, 2)
        self.assertNotIn(
            "INTERMEDIATE_WORDS", str(messages.send_group_message.call_args_list)
        )
        self.assertEqual(self.ctx.decision_audit[0]["expanded_evidence_ids"], ["S1"])

    async def test_cross_round_retraction_can_use_old_words_only_after_reading(self):
        self.room, self.service, self.actor = create_scenario(
            self.plugin, Mock(), 17, scenario="history_retraction"
        )
        self.room.phase = self.plugin.models.GamePhase.DAY_VOTE
        self.action = self.service._vote_action
        ctx = self.actor.ai_context
        assessment = {
            "player_number": 2,
            "suspicion": 7,
            "confidence": 0.6,
            "reason": "比较了旧声明与这轮否认，仍不能据此验明身份",
            "evidence_ids": ["S1", "S4"],
            "counter_evidence_ids": [],
        }
        self.action._call_llm = AsyncMock(
            side_effect=[
                self.request,
                model_reply(
                    target=2,
                    speech="2号第一天说过自己查3号，现在却否认，这两句对不上。我投2号。",
                    evidence_ids=["S1", "S4"],
                    assessments=[assessment],
                ),
            ]
        )
        _, number = await self.action.decide_vote(self.actor, self.room)
        self.assertEqual(number, 2)
        self.assertEqual(
            ctx.player_suspicions[self.room.get_player_by_number(2).display_name][
                "level"
            ],
            7,
        )
        self.assertEqual(len(ctx.decision_history), 1)
        self.assertEqual(ctx.decision_history[0]["evidence_ids"], ["S1", "S4"])

    async def test_clarification_preserves_quote_and_denial_in_complete_final_prompt(
        self,
    ):
        stored = copy.deepcopy(self.ctx.speeches)
        speech = (
            "2号当时转述的是5号的话，末尾还否认自称预言家。这个指控先撤回，我这轮弃票。"
        )
        self.action._call_llm = AsyncMock(
            side_effect=[
                self.request,
                model_reply(target=None, speech=speech, evidence_ids=["S1"]),
            ]
        )
        self.assertEqual(
            await self.action.decide_vote(self.actor, self.room), (speech, None)
        )
        first, second = self.action._call_llm.call_args_list
        self.assertNotIn(self.original, first.args[0])
        self.assertIn(self.original, second.args[0])
        self.assertEqual(self.ctx.speeches, stored)
        self.assertFalse(self.ctx.seer_results)
        self.assertEqual(self.ctx.role_name, "平民")
        self.assertEqual(self.ctx.decision_audit[-1]["status"], "explicit_abstain")

    async def test_read_keeps_reporter_and_quoted_speaker_distinct(self):
        self.action._call_llm = AsyncMock(
            side_effect=[
                self.request,
                model_reply(
                    target=None,
                    speech="2号原话是在转述，不能直接算成2号或5号亲口报验的记录。我这轮弃票。",
                    evidence_ids=["S1"],
                ),
            ]
        )
        await self.action.decide_vote(self.actor, self.room)
        retrieved = self.ctx.decision_audit[0]["evidence_records"]["S1"]
        self.assertEqual(
            retrieved["actor"], self.room.get_player_by_number(2).display_name
        )
        prompt = self.action._call_llm.call_args.args[0]
        self.assertIn("不能当成被引述人亲口说过的记录", prompt)
        self.assertIn("不能把引文中的身份声明算到转述者头上", prompt)
        self.assertIn("speech也必须保留", prompt)
        self.assertIn("口语化也不能省略这一限定", prompt)
        self.assertEqual(prompt.count(self.original), 1)

    async def test_no_read_or_empty_optional_list_adds_no_calls(self):
        for fields in ({}, {"request_evidence_ids": []}):
            with self.subTest(fields=fields):
                self.action._call_llm = AsyncMock(return_value=model_reply(**fields))
                await self.action.decide_vote(self.actor, self.room)
                self.action._call_llm.assert_awaited_once()

    async def test_second_read_is_rejected_and_still_shares_three_generation_budget(
        self,
    ):
        self.action._call_llm = AsyncMock(
            side_effect=[
                self.request,
                json.dumps({"request_evidence_ids": ["S2"]}),
                model_reply(evidence_ids=["S1"]),
            ]
        )
        with patch.object(
            self.ctx, "resolve_public_evidence", wraps=self.ctx.resolve_public_evidence
        ) as read:
            self.assertEqual(
                (await self.action.decide_vote(self.actor, self.room))[1], 8
            )
        read.assert_called_once()
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertEqual(
            [a["status"] for a in self.ctx.decision_audit],
            ["evidence_retrieved", "evidence_error", "accepted"],
        )
        self.assertEqual(
            self.action._call_llm.call_args.args[0].count(self.original), 1
        )

    async def test_excessive_or_private_read_never_partially_injects_text(self):
        for requested in (["S1", "S2", "S3", "S4"], ["S1", "Q1"]):
            with self.subTest(requested=requested):
                self.ctx.decision_audit.clear()
                self.ctx.decision_history.clear()
                self.action._call_llm = AsyncMock(
                    side_effect=[
                        json.dumps({"request_evidence_ids": requested}),
                        model_reply(),
                    ]
                )
                await self.action.decide_vote(self.actor, self.room)
                self.assertNotIn(self.original, self.action._call_llm.call_args.args[0])
                self.assertNotIn("S1", self.ctx.visible_evidence_ids)
                self.assertEqual(self.ctx.decision_audit[0]["status"], "evidence_error")

    async def test_duplicate_ids_consume_one_read_and_are_injected_once(self):
        self.action._call_llm = AsyncMock(
            side_effect=[
                json.dumps({"request_evidence_ids": ["S1"] * 6}),
                model_reply(evidence_ids=["S1"]),
            ]
        )
        await self.action.decide_vote(self.actor, self.room)
        self.assertEqual(
            self.action._call_llm.call_args.args[0].count(self.original), 1
        )
        self.assertEqual(self.ctx.decision_audit[0]["request_evidence_ids"], ["S1"])

    async def test_three_unique_ids_are_allowed_without_repeating_visible_text(self):
        self.action._call_llm = AsyncMock(
            side_effect=[
                json.dumps({"request_evidence_ids": ["S1", "S2", "S3", "S1"]}),
                model_reply(evidence_ids=["S1", "S2", "S3"]),
            ]
        )
        await self.action.decide_vote(self.actor, self.room)
        self.assertEqual(
            self.ctx.decision_audit[0]["request_evidence_ids"], ["S1", "S2", "S3"]
        )
        self.assertEqual(self.ctx.decision_audit[0]["expanded_evidence_ids"], ["S1"])
        for key in ("S1", "S2", "S3"):
            original = self.ctx.evidence_records()[key]["text"]
            self.assertEqual(self.action._call_llm.call_args.args[0].count(original), 1)

    async def test_malformed_read_protocol_does_not_execute_an_action(self):
        for requested in ("S1", None, [1], True, {"S1": True}):
            with self.subTest(requested=requested):
                self.ctx.decision_audit.clear()
                self.action._call_llm = AsyncMock(
                    side_effect=[
                        model_reply(request_evidence_ids=requested),
                        model_reply(),
                    ]
                )
                self.assertEqual(
                    (await self.action.decide_vote(self.actor, self.room))[1], 8
                )
                self.assertEqual(self.action._call_llm.await_count, 2)
                self.assertEqual(self.ctx.decision_audit[0]["status"], "format_error")
                self.assertEqual(self.ctx.decision_audit[-1]["status"], "accepted")

    async def test_read_and_conflict_repair_share_the_same_three_call_limit(self):
        self.action._call_llm = AsyncMock(
            side_effect=[
                self.request,
                model_reply(target=8, speech="这轮我弃票。", evidence_ids=["S1"]),
                model_reply(evidence_ids=["S1"]),
            ]
        )
        self.assertEqual((await self.action.decide_vote(self.actor, self.room))[1], 8)
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertEqual(len(self.ctx.decision_history), 1)

    async def test_read_on_last_attempt_does_not_expand_or_create_a_fourth_call(self):
        self.action._call_llm = AsyncMock(
            side_effect=["malformed", "malformed", self.request]
        )
        _, number = await self.action.decide_vote(self.actor, self.room)
        self.assertIsNone(number)
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertNotIn("S1", self.ctx.visible_evidence_ids)
        self.assertEqual(self.ctx.decision_audit[-1]["status"], "failed_fallback")
        self.assertIn("no generation budget", self.ctx.decision_audit[-2]["error"])

    async def test_repeated_reads_cannot_become_voluntary_abstention(self):
        self.action._call_llm = AsyncMock(return_value=self.request)
        _, number = await self.action.decide_vote(self.actor, self.room)
        self.assertIsNone(number)
        self.assertEqual(self.action._call_llm.await_count, 3)
        self.assertFalse(self.ctx.decision_history)
        self.assertEqual(self.ctx.decision_audit[-1]["status"], "failed_fallback")
        self.assertNotIn(
            "explicit_abstain", {a["status"] for a in self.ctx.decision_audit}
        )

    async def test_new_same_round_record_is_outside_the_decision_start_read_scope(self):
        calls = 0

        async def respond(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.ctx.add_speech(
                    self.room.get_player_by_number(3).display_name,
                    "POST_SNAPSHOT_WORDS",
                )
                return json.dumps(
                    {"request_evidence_ids": [f"S{len(self.ctx.speeches)}"]}
                )
            return model_reply()

        self.action._call_llm = AsyncMock(side_effect=respond)
        await self.action.decide_vote(self.actor, self.room)
        self.assertNotIn("POST_SNAPSHOT_WORDS", self.action._call_llm.call_args.args[0])
        self.assertEqual(self.ctx.decision_audit[0]["status"], "evidence_error")

    async def test_stale_read_before_injection_is_discarded_in_finished_or_pk_phase(
        self,
    ):
        for phase in (
            self.plugin.models.GamePhase.FINISHED,
            self.plugin.models.GamePhase.DAY_PK,
        ):
            with self.subTest(phase=phase):
                self.room.phase = self.plugin.models.GamePhase.DAY_VOTE
                self.ctx.decision_audit.clear()

                async def stale(*args, **kwargs):
                    self.room.phase = phase
                    self.room.vote_state.generation += 1
                    return self.request

                self.action._call_llm = AsyncMock(side_effect=stale)
                with patch.object(
                    self.ctx,
                    "resolve_public_evidence",
                    wraps=self.ctx.resolve_public_evidence,
                ) as read:
                    self.assertEqual(
                        await self.action.decide_vote(self.actor, self.room), ("", None)
                    )
                read.assert_not_called()
                self.assertFalse(self.ctx.decision_history)
                self.assertEqual(self.ctx.decision_audit[-1]["status"], "stale")
                self.assertFalse(self.room.used_fallback_speeches)

    async def test_stale_final_after_read_does_not_commit_a_vote_or_stance(self):
        calls = 0

        async def respond(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.request
            self.room.vote_state.generation += 1
            return model_reply(evidence_ids=["S1"])

        self.action._call_llm = AsyncMock(side_effect=respond)
        self.assertEqual(
            await self.action.decide_vote(self.actor, self.room), ("", None)
        )
        self.assertFalse(self.ctx.decision_history)
        self.assertFalse(self.ctx.vote_intention)
        self.assertFalse(self.room.vote_state.day_votes)
        self.assertEqual(self.ctx.decision_audit[-1]["status"], "stale")

    async def test_evaluation_captures_four_complete_reads_and_real_committed_ballots(
        self,
    ):
        async def text_chat(prompt, **kwargs):
            if "【历史原文补读】" not in prompt:
                reply = self.request
            else:
                reply = model_reply(evidence_ids=["S1"])
            return types.SimpleNamespace(
                result_chain=types.SimpleNamespace(get_plain_text=lambda: reply)
            )

        context = types.SimpleNamespace(
            get_using_provider=lambda: types.SimpleNamespace(text_chat=text_chat)
        )
        with TemporaryDirectory(prefix="werewolf-evidence-evaluation-") as directory:
            output = Path(directory) / "samples.jsonl"
            summary = await evaluate(
                lambda _: context,
                output,
                [17, 43],
                matrix=False,
                mixed=False,
                scenario_names=["history_retraction", "history_clarification"],
            )
            samples = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(summary["samples"], 4)
        self.assertEqual(summary["failures"], 0)
        for sample in samples:
            self.assertEqual(len(sample["audit"]), 2)
            self.assertEqual(sample["audit"][0]["status"], "evidence_retrieved")
            self.assertEqual(
                sample["submitted_vote"]["target_number"], sample["target_number"]
            )
            self.assertEqual(len(sample["submitted_vote"]["public_messages"]), 2)
            self.assertEqual(sample["decisions"][-1]["evidence_ids"], ["S1"])
            original = sample["audit"][0]["evidence_records"]["S1"]["text"]
            self.assertIn(original, sample["audit"][1]["prompt"])
