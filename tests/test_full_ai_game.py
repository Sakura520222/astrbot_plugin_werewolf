"""Exercise the complete all-AI simulation harness before spending model calls."""

import ast
import asyncio
import copy
import importlib
import importlib.util
import json
import re
import types
import unittest
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from evaluate_game import evaluate_game
from test_issue_1_regressions import PluginTestCase, load_plugin


class WolfChatMemoryTests(PluginTestCase):
    """Retain a wolf's own complete words without changing teammate source IDs."""

    async def test_public_speech_coverage_excludes_private_future_and_old_rounds(self):
        context = self.players[0].ai_context
        context.current_round = 1
        context.add_speech(self.players[1].display_name, "过去原话")
        context.current_round = 2
        context.add_vote_discussion(self.players[0].display_name, "当前公开讨论")
        context.current_round = 3
        context.add_speech(self.players[2].display_name, "未来原话")
        context.current_round = 2
        context.add_wolf_chat(self.players[2].display_name, "私聊不算公开原话", 2)
        context.alive_players = [player.display_name for player in self.players]
        text = context.to_prompt_context(layered=True)
        coverage = text.split("【公开原话覆盖】", 1)[1].split("【公开证据】", 1)[0]
        self.assertIn("本轮已有原话：1号.Alice", coverage)
        self.assertIn("没有公开原话记录：3号.Carol", coverage)
        self.assertNotIn("2号.Bob", coverage)
        self.assertEqual(text.count("当前公开讨论"), 1)

    async def test_own_wolf_chat_is_complete_private_and_keeps_teammate_ids_stable(
        self,
    ):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.room.current_round = 1
        for player in self.players[:2]:
            player.role = self.plugin.models.Role.WEREWOLF
            player.is_ai = True
        first, second = self.players[:2]
        first.ai_context.add_wolf_chat(second.display_name, "队友此前的原话", 1)
        original = (
            "我建议今晚先考虑3号，队友可以补充。" * 20 + "自己的末尾原话必须保留。"
        )
        peer = "我听到了，先比较一下其他选择。"
        service = self.plugin.services.AIPlayerService(Mock())
        self.ai.update_ai_context = service.update_ai_context
        self.ai.decide_werewolf_chat = AsyncMock(side_effect=[original, peer])
        phase = self.plugin.phases.NightWolfPhase(self.manager)
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._handle_ai_werewolf_chat(self.room)
        records = first.ai_context.evidence_records()
        self.assertEqual(records["C1"]["text"], "队友此前的原话")
        self.assertEqual(records["C2"]["text"], peer)
        self.assertEqual(records["O1"]["text"], original)
        self.assertEqual(len(first.ai_context.own_wolf_chat_messages), 1)
        self.assertEqual(second.ai_context.evidence_records()["C1"]["text"], original)
        text = first.ai_context.to_prompt_context(layered=True)
        self.assertEqual(text.count(original), 1)
        self.assertIn("[我的密谋原话]", text)
        self.assertIn("O1", first.ai_context.visible_evidence_ids)
        with self.assertRaisesRegex(ValueError, "unavailable public"):
            first.ai_context.resolve_public_evidence(["O1"])
        outsider = copy.deepcopy(first.ai_context)
        outsider.is_werewolf = False
        outsider.role_name = "平民"
        outside_text = outsider.to_prompt_context(layered=True)
        self.assertNotIn(original, outside_text)
        self.assertNotIn(peer, outside_text)
        self.assertNotIn("O1", outsider.visible_evidence_ids)
        self.messages.send_group_message.assert_not_awaited()

    async def test_action_array_still_needs_repair_with_an_explicit_string_protocol(
        self,
    ):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.room.current_round = 1
        player = self.players[0]
        player.role = self.plugin.models.Role.WEREWOLF
        service = self.plugin.services.AIPlayerService(Mock())
        action = service._werewolf_action
        final = "首夜信息有限，先讨论3号这个目标。"
        data = {
            "action": ["werewolf_chat"],
            "intent": "讨论目标",
            "reason": "首夜偏好，身份仍未知",
            "speech": final,
            "target_number": 3,
            "abstain": False,
            "evidence_ids": [],
            "assessments": [],
        }
        invalid = json.dumps(data, ensure_ascii=False)
        data["action"] = "werewolf_chat"
        action._call_llm = AsyncMock(
            side_effect=[invalid, json.dumps(data, ensure_ascii=False)]
        )
        self.assertEqual(await action.decide_chat(player, self.room), final)
        self.assertEqual(action._call_llm.await_count, 2)
        self.assertIn(
            "一个字符串，不能输出数组", action._call_llm.call_args_list[0].args[0]
        )
        self.assertEqual(player.ai_context.decision_audit[0]["response"], invalid)
        self.assertEqual(player.ai_context.decision_audit[0]["status"], "format_error")
        self.assertEqual(len(player.ai_context.decision_history), 1)

    async def test_public_wait_hint_is_independent_of_human_wolf_assignment(self):
        service = self.plugin.services.MessageService(Mock())
        self.room.config.timeout_wolf = 73
        first = service.phase_wait_text(self.room, self.room.config.timeout_wolf)
        self.players[1].role = self.plugin.models.Role.WEREWOLF
        second = service.phase_wait_text(self.room, self.room.config.timeout_wolf)
        self.assertEqual(first, second)
        self.assertIn("73秒", first)
        for player in self.players:
            player.is_ai = True
        automatic = service.phase_wait_text(self.room, self.room.config.timeout_wolf)
        self.assertIn("自动处理", automatic)
        self.assertNotIn("73秒", automatic)

    async def test_wolf_can_bluff_a_seer_claim_without_gaining_a_real_skill(self):
        actor = self.players[0]
        actor.role = self.plugin.models.Role.WEREWOLF
        self.room.current_round = 1
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        service = self.plugin.services.AIPlayerService(Mock())
        data = {
            "action": "day_speech",
            "intent": "假跳预言家误导",
            "reason": "我是狼人，故意编造查验，3号实际上不是队友",
            "speech": "我是预言家，昨晚验3号是狼，今天我想先出3。",
            "target_number": 3,
            "abstain": False,
            "evidence_ids": [],
            "assessments": [],
            "change_reason": "",
        }
        service._speech_action._call_llm = AsyncMock(
            return_value=json.dumps(data, ensure_ascii=False)
        )
        self.assertEqual(
            await service.generate_speech(actor, self.room), data["speech"]
        )
        self.assertTrue(actor.is_werewolf)
        self.assertFalse(actor.ai_context.seer_results)
        self.assertFalse(actor.ai_context.private_actions)
        self.assertEqual(actor.ai_context.decision_history[0]["action"], "day_speech")

    async def test_wolf_may_propose_self_attack_without_submitting_a_kill_vote(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.room.current_round = 1
        player = self.players[0]
        player.role = self.plugin.models.Role.WEREWOLF
        service = self.plugin.services.AIPlayerService(Mock())
        data = {
            "action": "werewolf_chat",
            "intent": "向队友提出自刀方案",
            "reason": "讨论策略，尚未正式投刀票",
            "speech": "如果你们认可，也可以考虑先刀我1号。",
            "target_number": 1,
            "abstain": False,
            "evidence_ids": [],
            "assessments": [],
            "change_reason": "",
        }
        service._werewolf_action._call_llm = AsyncMock(
            return_value=json.dumps(data, ensure_ascii=False)
        )
        self.assertEqual(
            await service.decide_werewolf_chat(player, self.room), data["speech"]
        )
        service._werewolf_action._call_llm.assert_awaited_once()
        self.assertFalse(self.room.vote_state.night_votes)

    async def test_single_bold_json_envelope_preserves_speech_and_strict_fields(self):
        prefix = self.plugin.models.__package__.rsplit(".", 1)[0]
        decision_type = importlib.import_module(
            f"{prefix}.models.ai_decision"
        ).AIDecision
        speech = "这里有**保留的强调**。" * 30 + "末尾不能裁掉。"
        body = json.dumps(
            {
                "action": "day_vote",
                "target_number": 2,
                "abstain": False,
                "intent": "选择2号",
                "reason": "机制测试",
                "speech": speech,
            },
            ensure_ascii=False,
        )
        wrapped = "**" + body + "**"
        parsed = decision_type.parse(wrapped, "day_vote")
        self.assertEqual(parsed.speech, speech)
        self.assertEqual(parsed.target_number, 2)
        for invalid in (
            "前言" + wrapped,
            wrapped + "后记",
            "****" + body + "****",
            '**{"target_number":2,"target_number":3}**',
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    decision_type.parse(invalid, "day_vote")
        service = self.plugin.services.AIPlayerService(Mock())
        service._vote_action._call_llm = AsyncMock(return_value=wrapped)
        self.assertEqual(
            await service.decide_vote(self.players[0], self.room), (speech, 2)
        )
        service._vote_action._call_llm.assert_awaited_once()
        self.assertEqual(
            self.players[0].ai_context.decision_audit[-1]["response"], wrapped
        )

    async def test_wolf_bluff_vote_is_kept_but_cannot_overwrite_known_factions(self):
        self.room.current_round = 1
        actor, teammate, target = self.players
        actor.role = teammate.role = self.plugin.models.Role.WEREWOLF
        actor.ai_context.add_speech(target.display_name, "我还没选好目标。")
        service = self.plugin.services.AIPlayerService(Mock())
        fields = {
            "action": "day_vote",
            "target_number": target.number,
            "abstain": False,
            "intent": "对外制造怀疑",
            "reason": "我知道3号是好人，对外伪装怀疑以推进狼队目标",
            "speech": "3号还没落定，我这票投3号。",
            "evidence_ids": ["S1"],
            "change_reason": "",
            "assessments": [
                {
                    "player_number": target.number,
                    "suspicion": 8,
                    "confidence": 0.6,
                    "reason": "试刀和做推目标",
                    "evidence_ids": ["S1"],
                },
                {
                    "player_number": teammate.number,
                    "suspicion": 0,
                    "confidence": 0.8,
                    "reason": "对外保队友",
                    "evidence_ids": ["S1"],
                },
            ],
        }
        service._vote_action._call_llm = AsyncMock(
            return_value=json.dumps(fields, ensure_ascii=False)
        )
        self.assertEqual(
            await service.decide_vote(actor, self.room),
            (fields["speech"], target.number),
        )
        self.assertFalse(actor.ai_context.player_suspicions)
        self.assertEqual(len(actor.ai_context.decision_history), 1)
        self.assertEqual(
            len(actor.ai_context.decision_history[0]["belief_rejections"]), 2
        )
        self.assertEqual(actor.ai_context.decision_audit[-1]["status"], "accepted")

    async def test_seer_knowledge_is_preserved_without_using_future_or_hidden_roles(
        self,
    ):
        self.room.current_round = 1
        actor, unknown, checked = self.players
        actor.role = self.plugin.models.Role.SEER
        unknown.role = self.plugin.models.Role.WEREWOLF
        actor.ai_context.add_speech(unknown.display_name, "我还想听听。")
        actor.ai_context.add_seer_result(checked.display_name, False)
        actor.ai_context.seer_results.append(
            {"round": 2, "target": unknown.display_name, "is_werewolf": True}
        )
        service = self.plugin.services.AIPlayerService(Mock())
        fields = {
            "action": "day_vote",
            "target_number": unknown.number,
            "abstain": False,
            "intent": "核对公开发言",
            "reason": "按当前已有信息判断",
            "speech": "我这票投2号。",
            "evidence_ids": ["S1", "Q1"],
            "change_reason": "",
            "assessments": [
                {
                    "player_number": checked.number,
                    "suspicion": 7,
                    "confidence": 0.6,
                    "reason": "主观怀疑",
                    "evidence_ids": ["Q1"],
                },
                {
                    "player_number": unknown.number,
                    "suspicion": 6,
                    "confidence": 0.5,
                    "reason": "目前只有公开发言",
                    "evidence_ids": ["S1"],
                },
            ],
        }
        service._vote_action._call_llm = AsyncMock(
            return_value=json.dumps(fields, ensure_ascii=False)
        )
        self.assertEqual(
            (await service.decide_vote(actor, self.room))[1], unknown.number
        )
        self.assertNotIn(checked.display_name, actor.ai_context.player_suspicions)
        self.assertEqual(
            actor.ai_context.player_suspicions[unknown.display_name]["level"], 6
        )
        self.assertEqual(
            len(actor.ai_context.decision_history[-1]["belief_rejections"]), 1
        )
        fields["assessments"] = [
            {
                "player_number": checked.number,
                "suspicion": 0,
                "confidence": 0.8,
                "reason": "本人真实查验为好人",
                "evidence_ids": ["Q1"],
            }
        ]
        service._vote_action._call_llm = AsyncMock(
            return_value=json.dumps(fields, ensure_ascii=False)
        )
        await service.decide_vote(actor, self.room)
        self.assertEqual(
            actor.ai_context.player_suspicions[checked.display_name]["level"], 0
        )


class RuntimeObservationTests(PluginTestCase):
    """Keep completed hunter actions and the review's final roster authoritative."""

    async def test_ai_declined_shot_is_terminal_public_knowledge_for_every_observer(
        self,
    ):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.current_round = 3
        hunter = self.players[0]
        hunter.role = self.plugin.models.Role.HUNTER
        hunter.is_alive = False
        for player in self.players:
            player.is_ai = True
        self.room.hunter_state.pending_shot_player_id = hunter.id
        self.room.hunter_state.death_type = "wolf"
        self.ai.decide_hunter_shoot = AsyncMock(return_value=None)
        phase = self.plugin.phases.PhaseManager(self.manager)
        phase._after_hunter_shot = AsyncMock()
        with patch("random.uniform", return_value=0):
            await phase.wait_for_hunter_shot(self.room, "wolf")
        self.assertTrue(self.room.hunter_state.has_shot)
        self.assertIsNone(self.room.hunter_state.pending_shot_player_id)
        for player in self.players:
            events = player.ai_context.game_events
            self.assertIn("尚未行动", events[-2])
            self.assertIn("选择不开枪，开枪机会已结束", events[-1])
            self.assertEqual(events.count(events[-1]), 1)
        phase._after_hunter_shot.assert_awaited_once()

    async def test_human_shot_timeout_is_terminal_public_knowledge_for_ai(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.current_round = 3
        hunter = self.players[0]
        hunter.role = self.plugin.models.Role.HUNTER
        hunter.is_alive = False
        hunter.is_ai = False
        self.players[1].is_ai = True
        self.room.hunter_state.pending_shot_player_id = hunter.id
        self.room.hunter_state.death_type = "wolf"
        self.room.config.timeout_hunter = 0.001
        phase = self.plugin.phases.PhaseManager(self.manager)
        phase._after_hunter_timeout = AsyncMock()
        await phase.wait_for_hunter_shot(self.room, "wolf")
        await asyncio.wait_for(self.room.timer_task, 1)
        self.assertTrue(self.room.hunter_state.has_shot)
        self.assertIsNone(self.room.hunter_state.pending_shot_player_id)
        self.assertIn(
            "开枪超时，放弃开枪，开枪机会已结束",
            self.players[1].ai_context.game_events[-1],
        )
        phase._after_hunter_timeout.assert_awaited_once()

    async def test_review_separates_surviving_wolves_from_good_players(self):
        self.room.phase = self.plugin.models.GamePhase.FINISHED
        self.players[0].role = self.plugin.models.Role.HUNTER
        self.players[0].is_alive = False
        for player in self.players[1:]:
            player.role = self.plugin.models.Role.WEREWOLF
        for number in (4, 5):
            self.room.add_player(
                self.plugin.models.Player(
                    id=f"good-{number}",
                    name=f"Good{number}",
                    number=number,
                    role=self.plugin.models.Role.VILLAGER,
                )
            )
        self.room.game_log = ["完整日志末尾_REVIEW_SOURCE"]
        reviewer = self.plugin.services.AIReviewer(Mock())
        data = reviewer._format_game_data(self.room, "werewolf")
        self.assertIn("全部存活4人；狼人2人；好人2人（其中神职0人）", data)
        self.assertIn("存活狼人：2号.Bob、3号.Carol", data)
        self.assertIn("存活好人：4号.Good4、5号.Good5", data)
        self.assertIn("1号.Alice - 猎人 - 出局", data)
        self.assertIn("[L1] 完整日志末尾_REVIEW_SOURCE", data)
        self.assertIn("狼人胜利！好人数量不足！", data)


class FullAIGameTests(unittest.IsolatedAsyncioTestCase):
    """Drive actual startup, roles, phases, review and interruption cleanup."""

    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("astrbot") is None:
            raise unittest.SkipTest(
                "The full-game harness needs the installed AstrBot SDK"
            )

    async def test_failed_start_does_not_resurrect_room_or_count_as_victory(self):
        provider = types.SimpleNamespace(text_chat=AsyncMock())
        context = types.SimpleNamespace(get_using_provider=lambda: provider)

        @contextmanager
        def aborted_start():
            with load_plugin() as plugin:

                async def start(manager, room):
                    await manager.cleanup_room(room.group_id)

                with patch.object(plugin.services.GameManager, "start_game", new=start):
                    yield plugin

        with TemporaryDirectory(prefix="werewolf-aborted-start-") as directory:
            output = Path(directory) / "game.json"
            with patch("evaluate_game.load_plugin", new=aborted_start):
                with self.assertRaisesRegex(RuntimeError, "announced victory"):
                    await evaluate_game(
                        lambda _: context, output, 43, all_ai=True, watchdog_seconds=5
                    )
            game = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(game["finished"])
        self.assertIsNone(game["winner"])
        self.assertEqual(game["phase"], "已结束")
        self.assertEqual(game["model_calls"], 0)
        self.assertTrue(game["cleanup_complete"])
        self.assertIn("初始化失败", game["messages"][-1]["command_reply"])
        provider.text_chat.assert_not_awaited()

    async def test_all_ai_game_reaches_victory_and_waits_for_complete_review(self):
        long_speech = "各自把依据说清楚，别急着认定身份。" * 20 + "末尾原文必须保留。"

        async def respond(prompt, **kwargs):
            if "action必须是" not in prompt:
                await asyncio.sleep(0.1)
                facts = json.loads(
                    re.findall(r"可选highlight_ids：(\[[^\n]*\])", prompt)[-1]
                )
                reply = (
                    json.dumps({"highlight_ids": facts, "advice_ids": []})
                    + "\n" * 10000
                )
            else:
                allowed = ast.literal_eval(
                    re.findall(r"action必须是(\[[^\n]*\])", prompt)[-1]
                )
                targets = ast.literal_eval(
                    re.findall(r"本次合法目标编号：(\[[^\n]*\])", prompt)[-1]
                )
                action = "pass" if "pass" in allowed else allowed[0]
                targeted = action in ("day_vote", "werewolf_kill", "seer_check")
                target = targets[0] if targeted and targets else None
                abstain = action in ("pass", "hunter_shoot") or (
                    targeted and target is None
                )
                speech = (
                    f"我投{target}号。"
                    if action == "day_vote" and target
                    else "我这轮弃票。"
                    if action == "day_vote"
                    else long_speech
                )
                reply = json.dumps(
                    {
                        "action": action,
                        "intent": "机制测试动作",
                        "reason": "仅验证合法流程",
                        "speech": speech,
                        "target_number": target,
                        "abstain": abstain,
                        "evidence_ids": [],
                        "assessments": [],
                        "change_reason": "",
                    },
                    ensure_ascii=False,
                )
            return types.SimpleNamespace(
                result_chain=types.SimpleNamespace(get_plain_text=lambda: reply)
            )

        context = types.SimpleNamespace(
            get_using_provider=lambda: types.SimpleNamespace(text_chat=respond)
        )
        with TemporaryDirectory(prefix="werewolf-full-ai-") as directory:
            output = Path(directory) / "game.json"
            summary = await evaluate_game(
                lambda _: context, output, 43, all_ai=True, watchdog_seconds=15
            )
            game = json.loads(output.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in output.with_suffix(".calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        self.assertTrue(summary["finished"])
        self.assertTrue(game["cleanup_complete"])
        self.assertFalse(game["stopped_at_round_limit"])
        self.assertTrue(game["all_ai"])
        self.assertEqual(len(game["players"]), 9)
        self.assertIn("弃票", game["messages"][0]["command_reply"])
        self.assertIn("全AI对局自动处理", game["messages"][0]["command_reply"])
        self.assertTrue(all(player["is_ai"] for player in game["players"]))
        self.assertEqual(
            Counter(player["role"] for player in game["players"]),
            {"werewolf": 3, "villager": 3, "seer": 1, "witch": 1, "hunter": 1},
        )
        self.assertEqual(len({player["personality"] for player in game["players"]}), 9)
        self.assertEqual(game["phase_history"][0]["round"], 0)
        self.assertEqual(
            next(
                item["round"]
                for item in game["phase_history"]
                if item["phase"] == "夜晚-狼人行动"
            ),
            1,
        )
        self.assertEqual(game["review"]["audit"][-1]["status"], "accepted")
        self.assertTrue(game["review"]["audit"][-1]["response"].endswith("\n" * 10000))
        self.assertTrue(
            any("AI复盘" in entry.get("message", "") for entry in game["messages"])
        )
        self.assertTrue(
            any(long_speech in entry.get("message", "") for entry in game["messages"])
        )
        self.assertIsNone(game["error"])
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(
            sum(item["event"] == "request" for item in calls),
            sum(item["event"] == "response" for item in calls),
        )
        self.assertEqual(sum(item["event"] == "review_response" for item in calls), 1)

    async def test_watchdog_retains_partial_game_and_cancels_pending_model_work(self):
        waiting = asyncio.Event()

        async def blocked(**kwargs):
            await waiting.wait()

        context = types.SimpleNamespace(
            get_using_provider=lambda: types.SimpleNamespace(text_chat=blocked)
        )
        with TemporaryDirectory(prefix="werewolf-full-ai-stop-") as directory:
            output = Path(directory) / "partial.json"
            with self.assertRaisesRegex(TimeoutError, "finite watchdog"):
                await evaluate_game(
                    lambda _: context, output, 43, all_ai=True, watchdog_seconds=0.2
                )
            game = json.loads(output.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in output.with_suffix(".calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        self.assertFalse(game["finished"])
        self.assertEqual(game["error"]["type"], "TimeoutError")
        self.assertEqual(len(game["players"]), 9)
        self.assertFalse(game["night_votes"])
        self.assertTrue(any(item["event"] == "request" for item in calls))
        self.assertTrue(
            any(
                item["event"] == "error" and item["error_type"] == "CancelledError"
                for item in calls
            )
        )
