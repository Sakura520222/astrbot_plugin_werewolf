"""Deterministic regressions for Issue #3; no external model is required."""

import ast
import copy
import importlib
import json
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from test_issue_1_regressions import load_plugin


def model_reply(task="day_vote", target=8, **changes):
    """Build a protocol reply with explicit intent and expression.

    Args:
        task: Stage action.
        target: Chosen seat, or None.
        **changes: Fields needed by the regression scenario.

    Returns:
        Complete JSON model response.
    """
    data = {
        "action": task,
        "intent": "独立选择",
        "reason": "仍需核对玩家声明",
        "speech": f"这票投{target}号。" if target else "这轮我主动弃票。",
        "target_number": target,
        "abstain": target is None,
        "evidence_ids": [],
        "assessments": [],
        "change_reason": "",
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


class AIRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real models, action classes and committed skill paths."""

    @classmethod
    def setUpClass(cls):
        cls.scope = load_plugin()
        cls.plugin = cls.scope.__enter__()
        prefix = cls.plugin.models.__package__.rsplit(".", 1)[0]
        cls.rules = importlib.import_module(f"{prefix}.models.game_rules").GameRules
        cls.personas = importlib.import_module(f"{prefix}.models.personality")
        cls.decision = importlib.import_module(
            f"{prefix}.models.ai_decision"
        ).AIDecision
        cls.builder = importlib.import_module(
            f"{prefix}.services.ai.context.builder"
        ).ContextBuilder
        cls.analyzer = importlib.import_module(f"{prefix}.services.ai.context.analyzer")
        cls.validator = importlib.import_module(
            f"{prefix}.services.ai.validators"
        ).TargetValidator

    @classmethod
    def tearDownClass(cls):
        cls.scope.__exit__(None, None, None)

    async def asyncSetUp(self):
        models = self.plugin.models
        self.room = models.GameRoom("test", "human", models.GameConfig.default())
        self.room.current_round = 1
        self.room.phase = models.GamePhase.DAY_VOTE
        roles = [
            models.Role.VILLAGER,
            models.Role.WEREWOLF,
            models.Role.SEER,
            models.Role.VILLAGER,
            models.Role.VILLAGER,
            models.Role.WITCH,
            models.Role.HUNTER,
            models.Role.WEREWOLF,
            models.Role.WEREWOLF,
        ]
        self.service = self.plugin.services.AIPlayerService(Mock())
        self.players = []
        for number, role in enumerate(roles, 1):
            player = models.Player(
                id=f"p{number}",
                name=f"名字{number}",
                number=number,
                role=role,
                is_ai=number != 1,
                ai_config=models.AIPlayerConfig(name=f"名字{number}"),
            )
            self.room.add_player(player)
            self.room.number_to_player[number] = player.id
            self.players.append(player)
        for player in self.players[1:]:
            self.service.initialize_ai_context(player, self.room)
        self.actor = self.players[3]

    async def test_dynamic_rulebook_and_targets_above_nine(self):
        self.room.config.total_players = 12
        self.room.config.villager_count = 6
        for number in range(10, 13):
            p = self.plugin.models.Player(
                id=f"p{number}",
                name=str(number),
                number=number,
                role=self.plugin.models.Role.VILLAGER,
            )
            self.room.add_player(p)
            self.room.number_to_player[number] = p.id
        book = self.builder.build_context(self.actor, self.room)
        self.assertIn("本局12人", book)
        self.assertIn("平民6人", book)
        self.assertNotIn("1-9", book)
        self.assertEqual(
            self.validator.validate_vote_target(self.room, 12, self.actor), 12
        )
        self.players[1].is_alive = False
        self.assertNotIn(2, self.rules.legal_targets(self.room, self.actor, "check"))
        self.assertNotIn(4, self.rules.legal_targets(self.room, self.actor, "vote"))
        self.assertIn(8, self.rules.legal_targets(self.room, self.players[7], "kill"))

    async def test_rule_sources_exist_and_all_victory_combinations_match(self):
        root = Path(__file__).resolve().parents[1]
        for source in self.rules.SOURCES.values():
            path, qualified = source.split(":", 1)
            tree = ast.parse((root / path).read_text(encoding="utf-8"))
            current = tree
            for part in qualified.split("."):
                current = next(
                    node
                    for node in current.body
                    if isinstance(
                        node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    and node.name == part
                )
        for mask in range(1 << len(self.players)):
            for index, player in enumerate(self.players):
                player.is_alive = bool(mask & (1 << index))
            alive = self.room.get_alive_players()
            wolves = sum(p.is_werewolf for p in alive)
            gods = sum(p.is_god for p in alive)
            expected = (
                "villager"
                if wolves == 0
                else "werewolf"
                if len(alive) - wolves <= wolves or gods == 0
                else None
            )
            self.assertEqual(
                self.plugin.services.VictoryChecker.check(self.room)[1], expected, mask
            )

    async def test_personalities_are_complete_unique_stable_and_independent(self):
        profiles = self.personas.PERSONALITIES
        self.assertGreaterEqual(len(profiles), 40)
        self.assertEqual(len({p.to_prompt() for p in profiles.values()}), len(profiles))
        for profile in profiles.values():
            for value in (
                profile.voice,
                profile.judgement,
                profile.risk,
                profile.emotion,
            ):
                self.assertTrue(value.strip())
        styles = [p.ai_context.personality_key for p in self.players[1:]]
        self.assertEqual(len(set(styles)), 8)
        before = self.actor.ai_context.personality
        for action in (
            self.service._speech_action,
            self.service._vote_action,
            self.service._werewolf_action,
            self.service._witch_action,
            self.service._seer_action,
            self.service._hunter_action,
        ):
            self.assertEqual(action._get_player_personality(self.actor), before)
        self.assertEqual(self.actor.name, "名字4")
        self.assertNotIn("AI", self.actor.display_name)

    async def test_weighted_custom_pool_exhausts_before_repeating(self):
        self.room.config.ai_personality_pool = ["quiet", "custom"]
        self.room.config.ai_personality_overrides = json.dumps(
            {
                "quiet": {"weight": 0},
                "custom": {"name": "自定义", "voice": "轻声", "weight": 2},
            }
        )
        service = self.plugin.services.AIPlayerService(Mock())
        for player in self.players[1:]:
            service.initialize_ai_context(player, self.room)
            self.assertEqual(player.ai_context.personality_key, "custom")
            self.assertIn("轻声", player.ai_context.personality)
        self.assertEqual(
            self.personas.load_personalities("not-json"), self.personas.PERSONALITIES
        )

    async def test_context_separates_role_private_evidence_even_if_other_fields_exist(
        self,
    ):
        for player in self.players[1:]:
            ctx = player.ai_context
            ctx.wolf_chat_messages.append(
                {"round": 1, "sender": "WOLF", "content": "SECRET_WOLF_CHAT"}
            )
            ctx.seer_results.append(
                {"round": 1, "target": "SECRET_CHECK", "is_werewolf": True}
            )
            ctx.witch_saved_player = "SECRET_SAVE"
            ctx.witch_poisoned_player = "SECRET_POISON"
            ctx.private_actions.append(
                {
                    "round": 1,
                    "phase": "night",
                    "action": "seer_check",
                    "target": "SECRET_SKILL_CHECK",
                }
            )
            text = self.builder.build_context(player, self.room)
            self.assertEqual("SECRET_WOLF_CHAT" in text, player.is_werewolf)
            self.assertEqual("SECRET_CHECK" in text, player.role.value == "seer")
            self.assertEqual("SECRET_SKILL_CHECK" in text, player.role.value == "seer")
            self.assertEqual("SECRET_SAVE" in text, player.role.value == "witch")
            self.assertEqual("SECRET_POISON" in text, player.role.value == "witch")
            self.assertLess(text.index("本局权威规则"), text.index("我的身份锚点"))
            self.assertLess(text.index("公开证据"), text.index("本人合法私密知识"))

    async def test_self_anchor_and_committed_vote_use_first_person_markers(self):
        ctx = self.actor.ai_context
        ctx.add_vote(self.actor.display_name, self.players[5].display_name)
        self.room.current_round = 2
        text = self.builder.build_context(self.actor, self.room)
        self.assertIn("我是4号.名字4", text)
        self.assertIn("4号就是我", text)
        self.assertIn("[我的记录]4号.名字4", text)
        self.assertIn("6号.名字6", text)

    async def test_layered_view_keeps_originals_and_referenced_history(self):
        ctx = self.actor.ai_context
        ctx.add_speech("2号.名字2", "OLD_ORIGINAL" * 80)
        ctx.add_speech("2号.名字2", "LATEST_ORIGINAL" * 80)
        ctx.add_speech(self.actor.display_name, "MY_OLD_WORDS")
        original = copy.deepcopy(ctx.speeches)
        self.room.current_round = 2
        text = self.builder.build_context(self.actor, self.room)
        self.assertNotIn("OLD_ORIGINAL", text)
        self.assertIn("LATEST_ORIGINAL" * 80, text)
        self.assertIn("MY_OLD_WORDS", text)
        self.assertEqual(ctx.speeches, original)
        self.assertIn("OLD_ORIGINAL" * 80, ctx.to_prompt_context())
        ctx.player_suspicions["2号.名字2"] = {"level": 6, "evidence_ids": ["S1"]}
        self.assertIn(
            "OLD_ORIGINAL" * 80, self.builder.build_context(self.actor, self.room)
        )

    async def test_rebuttal_quote_and_hypothesis_do_not_create_duel(self):
        ctx = self.actor.ai_context
        ctx.add_speech("5号.名字5", "我是预言家，8号查杀。")
        for text in (
            "我不信5号，你的理由是什么？",
            "5号说我是预言家。",
            "如果我是预言家，我会验5号。",
        ):
            ctx.add_speech("8号.名字8", text)
            self.assertEqual(
                self.analyzer.SituationAnalyzer.get_duel_context(self.actor, self.room),
                "",
            )
        ctx.add_speech("8号.名字8", "我是预言家，昨晚验5号。")
        duel = self.analyzer.SituationAnalyzer.get_duel_context(self.actor, self.room)
        self.assertIn("可能一真一假或都是假的", duel)
        self.assertIn("S1", duel)
        labels = self.analyzer.BehaviorAnalyzer.get_behavior_analysis_prompt(
            self.actor, self.room
        )
        self.assertNotIn("保人", labels)
        self.assertNotIn("攻击预言家", labels)

    async def test_unknown_death_causes_do_not_query_hidden_room_state(self):
        ctx = self.actor.ai_context
        ctx.add_event("第1夜死亡：3号.名字3, 7号.名字7")
        before = self.builder.build_context(self.actor, self.room)
        tip = self.builder.get_special_event_tip(self.actor, self.room)
        self.room.last_killed_id = self.players[2].id
        self.room.witch_state.poisoned_player_id = self.players[6].id
        after = self.builder.build_context(self.actor, self.room)
        self.assertEqual(before, after)
        self.assertIn("没有公布具体死因或身份", tip)
        self.assertNotIn("99%", tip)
        ctx.game_events.clear()
        ctx.event_rounds.clear()
        ctx.add_event("第1夜：平安夜")
        self.assertIn(
            "不知道确切获救对象",
            self.builder.get_special_event_tip(self.actor, self.room),
        )

    async def test_malformed_vote_retries_then_submits_complete_matching_speech(self):
        action = self.service._vote_action
        long_speech = "这票投8号。" + "完整表达" * 500 + "END_MARKER"
        action._call_llm = AsyncMock(
            side_effect=["我怀疑8号但没输出投票字段", model_reply(speech=long_speech)]
        )
        speech, target = await action.decide_vote(self.actor, self.room)
        self.assertEqual((speech, target), (long_speech, 8))
        self.assertEqual(action._call_llm.await_count, 2)
        self.assertEqual(
            [a["status"] for a in self.actor.ai_context.decision_audit],
            ["format_error", "accepted"],
        )
        self.assertEqual(self.actor.ai_context.vote_history, [])
        self.assertEqual(self.actor.ai_context.voting_patterns, {})

    async def test_explicit_abstention_is_distinct_from_bounded_failure(self):
        action = self.service._vote_action
        action._call_llm = AsyncMock(return_value=model_reply(target=None))
        _, target = await action.decide_vote(self.actor, self.room)
        self.assertIsNone(target)
        self.assertEqual(action._call_llm.await_count, 1)
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "explicit_abstain"
        )
        self.actor.ai_context.vote_intention.clear()
        action._call_llm = AsyncMock(return_value="[投票]2或8")
        self.actor.ai_config.max_retries = 99
        speech, target = await action.decide_vote(self.actor, self.room)
        self.assertIsNone(target)
        self.assertNotIn("主动弃票", speech)
        self.assertEqual(action._call_llm.await_count, 3)
        self.assertEqual(
            self.actor.ai_context.decision_audit[-1]["status"], "failed_fallback"
        )

    async def test_public_target_conflict_and_illegal_pk_are_repaired(self):
        action = self.service._vote_action
        action._call_llm = AsyncMock(
            side_effect=[model_reply(target=2, speech="我投8号。"), model_reply()]
        )
        self.assertEqual((await action.decide_vote(self.actor, self.room))[1], 8)
        self.actor.ai_context.vote_intention.clear()
        action._call_llm = AsyncMock(side_effect=[model_reply(target=2), model_reply()])
        self.assertEqual(
            (await action.decide_vote(self.actor, self.room, True, [8]))[1], 8
        )
        self.assertEqual(action._call_llm.await_count, 2)

    async def test_changed_public_intention_requires_actual_reason(self):
        self.actor.ai_context.vote_intention = {
            "round": 1,
            "target_number": 8,
            "abstain": False,
            "speech": "我投8号",
        }
        action = self.service._vote_action
        action._call_llm = AsyncMock(
            side_effect=[
                model_reply(target=2),
                model_reply(
                    target=2,
                    change_reason="重新核对了2号先前说法，这个解释改变了我的判断",
                ),
            ]
        )
        self.assertEqual((await action.decide_vote(self.actor, self.room))[1], 2)
        self.assertEqual(action._call_llm.await_count, 2)
        self.assertIn(
            "重新核对", self.actor.ai_context.decision_history[-1]["change_reason"]
        )

    async def test_evidence_and_beliefs_are_individual_and_sourced(self):
        ctx = self.actor.ai_context
        ctx.add_speech("8号.名字8", "先听5号讲清楚。")
        assessment = {
            "player_number": 8,
            "suspicion": 6,
            "confidence": 0.4,
            "reason": "他的说法仍有疑点，不是确认狼",
            "evidence_ids": ["S1"],
            "counter_evidence_ids": [],
        }
        action = self.service._vote_action
        action._call_llm = AsyncMock(
            side_effect=[
                model_reply(evidence_ids=["Q999"]),
                model_reply(evidence_ids=["S1"], assessments=[assessment]),
            ]
        )
        await action.decide_vote(self.actor, self.room)
        self.assertEqual(ctx.player_suspicions["8号.名字8"]["level"], 6)
        self.assertEqual(self.players[4].ai_context.player_suspicions, {})
        self.assertEqual(action._call_llm.await_count, 2)
        changed = {**assessment, "suspicion": 9}
        action._call_llm = AsyncMock(return_value=model_reply(assessments=[changed]))
        await action.decide_vote(self.actor, self.room)
        self.assertEqual(ctx.player_suspicions["8号.名字8"]["level"], 6)

    async def test_stale_model_completion_does_not_write_new_beliefs_or_intentions(
        self,
    ):
        action = self.service._vote_action

        async def stale(*args, **kwargs):
            self.room.current_round = 2
            return model_reply()

        action._call_llm = AsyncMock(side_effect=stale)
        self.assertIsNone((await action.decide_vote(self.actor, self.room))[1])
        self.assertEqual(self.actor.ai_context.decision_history, [])
        self.assertEqual(self.actor.ai_context.vote_intention, {})
        self.assertEqual(self.actor.ai_context.decision_audit[-1]["status"], "stale")

    async def test_duplicate_keys_wrong_types_and_ambiguous_numbers_are_invalid(self):
        for response in (
            '{"target_number":2,"target_number":8}',
            model_reply(target=True),
            model_reply(target=2.5),
            model_reply(abstain=True),
            "先不救人，考虑毒8号",
            "首夜我验8号因为2号发言",
            "[投票]2\n[投票]8",
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.decision.parse(response, "day_vote")

    async def test_witch_committed_save_then_poison_persist_across_nights(self):
        witch = self.players[5]
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.last_killed_id = self.players[4].id
        manager = types.SimpleNamespace(
            rooms={"test": self.room},
            config=self.room.config,
            ai_player_service=self.service,
            message_service=Mock(),
        )
        phase = self.plugin.phases.NightWitchPhase(manager)
        phase._finish_night = AsyncMock()
        action = self.service._witch_action
        action._call_llm = AsyncMock(
            return_value=model_reply(
                "save",
                target=None,
                abstain=False,
                speech="",
                reason="当时决定用解药保留人数",
            )
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._handle_ai_witch(self.room, witch)
        self.assertEqual(
            witch.ai_context.witch_saved_player, self.players[4].display_name
        )
        self.room.current_round = 2
        self.room.witch_state.reset_night()
        self.room.last_killed_id = self.players[8].id
        action._call_llm = AsyncMock(
            return_value=model_reply(
                "poison",
                target=3,
                speech="",
                reason="当晚我选择冒险毒3号，身份仍不确认",
            )
        )
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._handle_ai_witch(self.room, witch)
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        text = self.builder.build_context(witch, self.room)
        self.assertIn("我已救过：5号.名字5", text)
        self.assertIn("我已毒过：3号.名字3", text)
        self.assertIn("解药：已用；毒药：已用", text)
        self.assertEqual([a["round"] for a in witch.ai_context.private_actions], [1, 2])
        self.assertIn(
            "当时决定", witch.ai_context.private_actions[0]["original_reason"]
        )
        other = self.builder.build_context(self.actor, self.room)
        self.assertNotIn("当时决定用解药", other)

    async def test_first_night_choice_cannot_reference_future_evidence(self):
        seer = self.players[2]
        self.room.phase = self.plugin.models.GamePhase.NIGHT_SEER
        seer.ai_context.speeches.append(
            {"round": 2, "player": "8号.名字8", "content": "FUTURE_SPEECH"}
        )
        action = self.service._seer_action
        action._call_llm = AsyncMock(
            side_effect=[
                model_reply("seer_check", evidence_ids=["S1"], speech=""),
                model_reply(
                    "seer_check",
                    speech="",
                    reason="首夜尚无公开发言，按初始偏好选择8号",
                ),
            ]
        )
        self.assertEqual(await action.decide_check(seer, self.room), 8)
        seer.ai_context.add_seer_result(self.players[7].display_name, True)
        self.room.current_round = 2
        text = self.builder.build_context(seer, self.room)
        self.assertIn("首夜尚无公开发言", text)
        self.assertNotIn(
            "FUTURE_SPEECH", seer.ai_context.private_actions[0]["original_reason"]
        )
        self.assertEqual(action._call_llm.await_count, 2)

    async def test_hunter_poison_or_spent_shot_never_calls_model(self):
        hunter = self.players[6]
        self.room.hunter_state.pending_shot_player_id = hunter.id
        self.room.hunter_state.death_type = self.plugin.roles.HunterDeathType.POISON
        action = self.service._hunter_action
        action._call_llm = AsyncMock()
        self.assertIsNone(await action.decide_shoot(hunter, self.room))
        action._call_llm.assert_not_awaited()
        self.room.hunter_state.death_type = self.plugin.roles.HunterDeathType.VOTE
        self.room.hunter_state.has_shot = True
        self.assertIsNone(await action.decide_shoot(hunter, self.room))
        action._call_llm.assert_not_awaited()

    async def test_all_public_expression_is_preserved_and_last_words_are_authorized(
        self,
    ):
        text = "完整原句" * 800 + "不可丢失的句尾。"
        self.room.phase = self.plugin.models.GamePhase.DAY_SPEAKING
        action = self.service._speech_action
        action._call_llm = AsyncMock(
            return_value=model_reply(
                "day_speech", target=None, abstain=False, speech=text
            )
        )
        self.assertEqual(await action.generate_speech(self.actor, self.room), text)
        self.room.phase = self.plugin.models.GamePhase.LAST_WORDS
        self.room.last_words_player_id = self.actor.id
        action._call_llm = AsyncMock(
            return_value=model_reply(
                "last_words", target=None, abstain=False, speech=text
            )
        )
        self.assertEqual(await action.generate_last_words(self.actor, self.room), text)
        self.room.last_words_player_id = self.players[2].id
        action._call_llm.reset_mock()
        await action.generate_last_words(self.actor, self.room)
        action._call_llm.assert_not_awaited()

    async def test_repeated_claim_does_not_increase_confidence_as_new_evidence(self):
        ctx = self.actor.ai_context
        ctx.add_speech("5号.名字5", "8号是狼。")
        ctx.add_speech("2号.名字2", "8号是狼。")
        ctx.player_suspicions["8号.名字8"] = {
            "level": 6,
            "confidence": 0.4,
            "evidence_ids": ["S1"],
            "reason": "尚未证实",
        }
        update = {
            "player_number": 8,
            "suspicion": 6,
            "confidence": 0.9,
            "reason": "另一个人也说一样的话",
            "evidence_ids": ["S2"],
            "counter_evidence_ids": [],
        }
        action = self.service._vote_action
        action._call_llm = AsyncMock(
            side_effect=[model_reply(assessments=[update]), model_reply()]
        )
        self.assertEqual((await action.decide_vote(self.actor, self.room))[1], 8)
        self.assertEqual(action._call_llm.await_count, 1)
        self.assertEqual(ctx.player_suspicions["8号.名字8"]["confidence"], 0.4)
        self.assertIn("belief_rejections", ctx.decision_audit[-1])

    async def test_witch_cannot_use_unavailable_potions_or_parse_negation_as_save(self):
        witch = self.players[5]
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WITCH
        self.room.last_killed_id = self.players[4].id
        self.room.witch_state.antidote_used = True
        action = self.service._witch_action
        action._call_llm = AsyncMock(
            side_effect=[
                "我不救人，先留毒药",
                model_reply("save", target=None, abstain=False, speech=""),
                model_reply("pass", target=None, speech=""),
            ]
        )
        self.assertEqual(
            await action.decide_action(witch, self.room, True, True, "5号.名字5"),
            ("pass", None),
        )
        self.assertEqual(action._call_llm.await_count, 3)

    async def test_actual_wolf_phase_accepts_deliberate_self_attack(self):
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        action = self.service._werewolf_action
        action._call_llm = AsyncMock(
            return_value=model_reply("werewolf_kill", target=2, speech="")
        )
        manager = types.SimpleNamespace(
            rooms={"test": self.room},
            config=self.room.config,
            ai_player_service=self.service,
            message_service=Mock(),
        )
        phase = self.plugin.phases.NightWolfPhase(manager)
        with patch("asyncio.sleep", new=AsyncMock()):
            await phase._handle_ai_werewolf_vote(self.room)
        wolf = self.players[1]
        self.assertEqual(self.room.vote_state.night_votes[wolf.id], wolf.id)
        self.assertEqual(
            wolf.ai_context.private_actions[-1]["target"], wolf.display_name
        )

    async def test_wolf_chat_and_pk_keep_entire_public_field(self):
        text = "不裁剪完整语义。" * 400 + "END_OF_SENTENCE"
        wolf = self.players[1]
        self.room.phase = self.plugin.models.GamePhase.NIGHT_WOLF
        self.service._werewolf_action._call_llm = AsyncMock(
            return_value=model_reply(
                "werewolf_chat", target=None, abstain=False, speech=text
            )
        )
        self.assertEqual(await self.service.decide_werewolf_chat(wolf, self.room), text)
        self.room.phase = self.plugin.models.GamePhase.DAY_PK
        self.service._speech_action._call_llm = AsyncMock(
            return_value=model_reply(
                "pk_speech", target=None, abstain=False, speech=text
            )
        )
        self.assertEqual(
            await self.service.generate_speech(self.actor, self.room, True), text
        )

    async def test_runtime_uses_latest_stances_but_keeps_original_action_reasons(self):
        wolf = self.players[1]
        ctx = wolf.ai_context
        ctx.decision_history = [
            {
                "round": 1,
                "action": "day_speech",
                "reason": "OLD_STANCE",
                "evidence_ids": [],
            },
            {
                "round": 2,
                "action": "day_speech",
                "reason": "LATEST_STANCE",
                "evidence_ids": [],
            },
        ]
        ctx.private_actions = [
            {
                "round": 1,
                "phase": "夜晚-狼人行动",
                "action": "wolf_final_target",
                "target": "5号.名字5",
                "original_reason": "ORIGINAL_SKILL_REASON",
            }
        ]
        self.room.current_round = 3
        text = self.builder.build_context(wolf, self.room)
        self.assertNotIn("OLD_STANCE", text)
        self.assertIn("LATEST_STANCE", text)
        self.assertIn("ORIGINAL_SKILL_REASON", text)
        self.assertIn("OLD_STANCE", ctx.to_prompt_context())
        self.assertEqual(len(ctx.decision_history), 2)

    async def test_runtime_command_handlers_reject_self_vote_and_dead_seer_target(self):
        prefix = self.plugin.models.__package__.rsplit(".", 1)[0]
        try:
            day_module = importlib.import_module(f"{prefix}.handlers.day_commands")
            night_module = importlib.import_module(f"{prefix}.handlers.night_commands")
        except ModuleNotFoundError as error:
            if error.name and error.name.startswith("astrbot"):
                self.skipTest("Command handlers require the installed AstrBot SDK")
            raise
        manager = types.SimpleNamespace(
            get_room=lambda _: self.room,
            message_service=Mock(),
            get_room_by_player=lambda _: (self.room.group_id, self.room),
        )
        handler = day_module.DayCommandHandler(manager)
        handler.get_target_user = lambda _: "1"
        event = types.SimpleNamespace(
            get_group_id=lambda: self.room.group_id,
            get_sender_id=lambda: self.players[0].id,
            plain_result=lambda value: value,
        )
        replies = [reply async for reply in handler.day_vote(event)]
        self.assertTrue(any("不能投票给自己" in reply for reply in replies))
        self.assertEqual(self.room.vote_state.day_votes, {})
        self.room.phase = self.plugin.models.GamePhase.NIGHT_SEER
        self.players[7].is_alive = False
        handler = night_module.NightCommandHandler(manager)
        handler.get_target_user = lambda _: "8"
        event.get_sender_id = lambda: self.players[2].id
        replies = [reply async for reply in handler.seer_check(event)]
        self.assertTrue(any("不能验证已经出局" in reply for reply in replies))
        self.assertFalse(self.room.seer_checked)


if __name__ == "__main__":
    unittest.main()
