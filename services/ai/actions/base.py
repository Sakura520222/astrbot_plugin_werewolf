"""行动基类 - 所有AI行动的基础"""

import asyncio
import math
import random
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

from ....models.ai_decision import AIDecision
from ....models.game_rules import GameRules
from ..context import ContextBuilder
from ..prompts import (
    ANTI_HALLUCINATION_PROTOCOL,
    HUMAN_STYLE_TIPS,
    ROLE_PROMPTS,
    ROLE_SOUL_SETTINGS,
)
from ..prompts.base import SYSTEM_PROMPT
from ..prompts.templates import DECISION_PROTOCOL

if TYPE_CHECKING:
    from ....models import Player


class BaseAction:
    """AI行动基类"""

    LLM_TIMEOUT_SECONDS = 180
    MAX_DECISION_ATTEMPTS = 3

    def __init__(self, context, personality_cache: dict[str, str] | None = None):
        self.context = context
        self._player_personalities = (
            personality_cache if personality_cache is not None else {}
        )

    def _get_player_personality(self, player: "Player") -> str:
        """Reuse the player's personality across speech, votes, and last words.

        Args:
            player: Player whose context or shared cache supplies the personality.

        Returns:
            The personality prompt, assigned once if necessary.
        """
        from ..prompts import PERSONALITY_TEMPLATES

        if player.ai_context and player.ai_context.personality:
            return player.ai_context.personality
        if player.id not in self._player_personalities:
            self._player_personalities[player.id] = random.choice(
                list(PERSONALITY_TEMPLATES)
            )
        personality = PERSONALITY_TEMPLATES[self._player_personalities[player.id]]
        if player.ai_context:
            player.ai_context.personality = personality
        return personality

    def _get_provider(self, model_id: str = ""):
        """获取LLM provider"""
        if model_id:
            provider = self.context.get_provider_by_id(model_id)
            if not provider:
                logger.warning(f"[狼人杀AI] 未找到模型 '{model_id}'，使用默认模型")
                provider = self.context.get_using_provider()
        else:
            provider = self.context.get_using_provider()
        return provider

    async def _call_llm(
        self,
        prompt: str,
        player: "Player",
        max_retries: int | None = None,
        retry_delay: float = 1.0,
        timeout: float = None,
    ) -> str | None:
        """调用LLM获取AI决策（带重试和超时保护）"""
        model_id = ""
        if player.ai_config:
            model_id = player.ai_config.model_id
            if max_retries is None:
                max_retries = player.ai_config.max_retries
            retry_delay = player.ai_config.retry_delay

        if max_retries is None:
            max_retries = 3
        if timeout is None:
            timeout = self.LLM_TIMEOUT_SECONDS

        provider = self._get_provider(model_id)
        if not provider:
            logger.error("[狼人杀AI] 无法获取LLM provider")
            return None

        # Sanitize only the composed prompt, never player names or stored records.
        prompt = re.sub(
            r"[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFD"
            r"☀-➿⬀-⯿↔-↙↩↪"
            r"⌚⌛⌨⏏⏩-⏳⏸-⏺"
            r"Ⓜ▪▫▶◀◻-◾"
            r"©®‼⁉™ℹ〰〽"
            r"㊗㊙‍⃣︎️\U000E0020-\U000E007F]",
            "",
            prompt,
        )

        for attempt in range(max_retries):
            try:
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=prompt,
                        system_prompt=SYSTEM_PROMPT,
                    ),
                    timeout=timeout,
                )

                if response.result_chain:
                    result = response.result_chain.get_plain_text().strip()
                    if result:  # 只有非空结果才返回
                        logger.debug(
                            f"[Werewolf AI] Received {len(result)} characters for {player.name}"
                        )
                        return result
                    else:
                        logger.warning(
                            f"[狼人杀AI] {player.name} 第{attempt + 1}次调用返回空内容"
                        )
                else:
                    logger.warning(
                        f"[狼人杀AI] {player.name} 第{attempt + 1}次调用返回空响应"
                    )

            except asyncio.TimeoutError:
                logger.warning(
                    f"[狼人杀AI] {player.name} 第{attempt + 1}次调用超时（{timeout}秒）"
                )
            except Exception as e:
                logger.warning(
                    f"[狼人杀AI] {player.name} 第{attempt + 1}次调用失败: {e}"
                )

            # 统一在循环末尾等待重试
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        logger.error(f"[狼人杀AI] {player.name} 所有重试均失败")
        return None

    async def _request_decision(
        self,
        player,
        room,
        task: str,
        *,
        guidance: str = "",
        candidates: list[int] | None = None,
        available_actions: list[str] | None = None,
    ) -> AIDecision | None:
        """Make one bounded structured decision and retain every complete attempt.

        Args:
            player: Acting player.
            room: Room owning the action.
            task: Stage protocol identifier.
            guidance: Additional stage-specific facts and strategy options.
            candidates: Optional runoff target restriction.
            available_actions: Permitted witch actions, including pass.

        Returns:
            Validated decision, or None after observable failure/stale work.
        """
        ctx = ContextBuilder.refresh_context(player, room)
        expected_phases = {
            "werewolf_kill": "夜晚-狼人行动",
            "werewolf_chat": "夜晚-狼人行动",
            "seer_check": "夜晚-预言家验人",
            "witch_action": "夜晚-女巫行动",
            "day_speech": "白天发言",
            "pk_speech": "PK发言",
            "day_vote": "白天投票",
            "last_words": "遗言阶段",
        }
        if task in expected_phases and room.phase.value != expected_phases[task]:
            ctx.decision_audit.append(
                {
                    "round": room.current_round,
                    "task": task,
                    "status": "unauthorized_phase",
                }
            )
            return None
        if task == "last_words" and room.last_words_player_id != player.id:
            ctx.decision_audit.append(
                {
                    "round": room.current_round,
                    "task": task,
                    "status": "unauthorized_last_words",
                }
            )
            return None
        self._get_player_personality(player)
        context = ContextBuilder.build_context(player, room)
        action_types = {
            "day_vote": "vote",
            "werewolf_kill": "kill",
            "seer_check": "check",
            "witch_action": "poison",
            "hunter_shoot": "shoot",
        }
        action_type = action_types.get(task, "vote")
        targets = GameRules.legal_targets(room, player, action_type, candidates)
        role = ContextBuilder.get_role_key(player)
        prompt = "\n\n".join(
            (
                context,
                ANTI_HALLUCINATION_PROTOCOL,
                ROLE_SOUL_SETTINGS.get(role, ""),
                "【当前阶段任务】" + ROLE_PROMPTS[task],
                guidance,
                ContextBuilder.get_special_event_tip(player, room),
                HUMAN_STYLE_TIPS,
                f"本次合法目标编号：{targets}。只能使用列出的编号。",
                f"action必须是{available_actions if task == 'witch_action' else [task]}。",
                DECISION_PROTOCOL,
            )
        )
        if task == "witch_action":
            prompt += "\n救人使用action=save、target_number=null、abstain=false；不操作使用action=pass、target_number=null、abstain=true；毒人使用action=poison和合法目标。"
        snapshot = (
            room.current_round,
            room.phase,
            room.vote_state.generation,
            room.speaking_generation,
            room.last_words_generation,
        )
        max_attempts = min(
            max(
                player.ai_config.max_retries
                if player.ai_config
                else self.MAX_DECISION_ATTEMPTS,
                1,
            ),
            self.MAX_DECISION_ATTEMPTS,
        )
        for attempt in range(1, max_attempts + 1):
            response = await self._call_llm(prompt, player, max_retries=1)
            audit = {
                "round": snapshot[0],
                "phase": snapshot[1].value,
                "task": task,
                "attempt": attempt,
                "prompt": prompt,
                "response": response,
                "status": "pending",
            }
            ctx.decision_audit.append(audit)
            if snapshot != (
                room.current_round,
                room.phase,
                room.vote_state.generation,
                room.speaking_generation,
                room.last_words_generation,
            ):
                audit["status"] = "stale"
                return None
            try:
                if not response:
                    raise ValueError("Model unavailable or empty response")
                decision = AIDecision.parse(response, task)
                self._validate_decision(
                    decision, player, room, task, candidates, available_actions
                )
            except ValueError as error:
                audit.update(
                    status="format_error" if response else "unavailable",
                    error=str(error),
                )
                logger.warning(
                    f"[Werewolf AI] {player.display_name} {task} attempt {attempt}: {error}"
                )
                if attempt < max_attempts:
                    prompt += f"\n【格式修复】上一次没有执行任何行动。错误：{error}。请重新输出完整JSON，不是修改游戏事实；公开speech与目标要一致。"
                continue
            audit["status"] = "explicit_abstain" if decision.abstain else "accepted"
            audit["target_number"] = decision.target_number
            if decision.belief_rejections:
                audit["belief_rejections"] = list(decision.belief_rejections)
            logger.info(
                f"[Werewolf AI] {player.display_name} {task}: status={audit['status']} "
                f"target={decision.target_number} attempts={attempt}"
            )
            record = {
                "round": room.current_round,
                "phase": room.phase.value,
                "action": decision.action,
                "target_number": decision.target_number,
                "abstain": decision.abstain,
                "intent": decision.intent,
                "reason": decision.reason,
                "evidence_ids": list(decision.evidence_ids),
                "change_reason": decision.change_reason,
                "belief_rejections": list(decision.belief_rejections),
            }
            ctx.decision_history.append(record)
            for assessment in decision.assessments:
                target = room.get_player_by_number(assessment["player_number"])
                previous = ctx.player_suspicions.get(target.display_name, {})
                history = list(previous.get("history", []))
                if previous:
                    history.append(
                        {
                            key: value
                            for key, value in previous.items()
                            if key != "history"
                        }
                    )
                ctx.player_suspicions[target.display_name] = {
                    **assessment,
                    "level": assessment["suspicion"],
                    "round": room.current_round,
                    "history": history,
                }
            if task in ("day_speech", "pk_speech", "day_vote") and (
                decision.target_number is not None or decision.abstain
            ):
                ctx.vote_intention = {
                    "round": room.current_round,
                    "target_number": decision.target_number,
                    "abstain": decision.abstain,
                    "speech": decision.speech,
                    "change_reason": decision.change_reason,
                }
            return decision
        ctx.decision_audit.append(
            {
                "round": room.current_round,
                "task": task,
                "status": "failed_fallback",
                "attempts": max_attempts,
            }
        )
        logger.error(
            f"[Werewolf AI] {player.display_name} {task} exhausted {max_attempts} attempts; failure fallback"
        )
        return None

    @staticmethod
    def _validate_decision(decision, player, room, task, candidates, available_actions):
        """Validate source references, legal actions and explicit public promises.

        Args:
            decision: Parsed model output.
            player: Acting player.
            room: Authoritative room after the model call.
            task: Expected stage.
            candidates: Runoff restriction if any.
            available_actions: Allowed witch actions.

        Raises:
            ValueError: Illegal action, unsupported evidence or inconsistent intent.
        """
        ctx = player.ai_context
        permitted = available_actions if task == "witch_action" else [task]
        if decision.action not in (permitted or []):
            raise ValueError("action is not available in this stage")
        if task == "witch_action":
            state = room.witch_state
            if state.has_acted and decision.action != "pass":
                raise ValueError("Witch already acted this night")
            if decision.action == "save" and (
                state.antidote_used or not room.last_killed_id
            ):
                raise ValueError("Antidote or attacked target is no longer available")
            if decision.action == "poison" and state.poison_used:
                raise ValueError("Poison is no longer available")
        if task == "hunter_shoot" and (
            room.hunter_state.pending_shot_player_id != player.id
            or not room.hunter_state.can_shoot()
        ):
            raise ValueError("Hunter no longer has an authorized shot")
        kinds = {
            "day_vote": "vote",
            "werewolf_kill": "kill",
            "seer_check": "check",
            "hunter_shoot": "shoot",
            "witch_action": "poison",
        }
        target = decision.target_number
        if target is not None and target not in GameRules.legal_targets(
            room, player, kinds.get(task, "vote"), candidates
        ):
            raise ValueError("Target is not a legal living player or runoff candidate")
        requires_target = (
            task in ("day_vote", "werewolf_kill", "seer_check", "hunter_shoot")
            or decision.action == "poison"
        )
        if requires_target and not decision.abstain and target is None:
            raise ValueError("A target or explicit abstention is required")
        if decision.action in ("save", "pass") and target is not None:
            raise ValueError("Save/pass must not specify a poison target")
        if decision.action == "save" and decision.abstain:
            raise ValueError("Saving is not abstention")
        if decision.action == "pass" and not decision.abstain:
            raise ValueError("Pass requires explicit abstention")
        if decision.action == "poison" and decision.abstain:
            raise ValueError("Poisoning is not abstention")
        known_ids = ctx.visible_evidence_ids
        if not set(decision.evidence_ids) <= known_ids:
            raise ValueError(
                "Evidence references were not present in this player's view"
            )
        if task == "day_vote":
            declarations = re.findall(
                r"(?:我(?:这轮|最后)?(?:决定|改|会|要)?(?:投|票给)|这(?:一)?票(?:投|给))\s*(\d+)\s*号",
                decision.speech,
            )
            if any(int(number) != target for number in declarations):
                raise ValueError("Public voting statement conflicts with target_number")
            previous = ctx.vote_intention
            if (
                previous.get("round") == room.current_round
                and (previous.get("target_number"), previous.get("abstain"))
                != (target, decision.abstain)
                and not decision.change_reason.strip()
            ):
                raise ValueError(
                    "Changing a public vote intention requires change_reason"
                )
        seen_players = set()
        valid_assessments = []
        for assessment in decision.assessments:
            try:
                BaseAction._validate_assessment(
                    assessment, decision, player, room, seen_players
                )
            except ValueError as error:
                decision.belief_rejections.append(str(error))
            else:
                valid_assessments.append(assessment)
        decision.assessments = valid_assessments

    @staticmethod
    def _validate_assessment(assessment, decision, player, room, seen_players):
        """Validate an optional belief update independently of a legal action.

        Args:
            assessment: Proposed individual judgement update.
            decision: Owning action and explicit change rationale.
            player: Acting player.
            room: Current room.
            seen_players: Subjects already encountered in this response.

        Raises:
            ValueError: The update is unsupported; the previous belief is retained.
        """
        ctx = player.ai_context
        known_ids = ctx.visible_evidence_ids
        number = assessment.get("player_number")
        subject = room.get_player_by_number(number) if type(number) is int else None
        if not subject or subject.id == player.id or number in seen_players:
            raise ValueError("Assessment needs a unique other player number")
        seen_players.add(number)
        suspicion, confidence = (
            assessment.get("suspicion"),
            assessment.get("confidence"),
        )
        if (
            type(suspicion) not in (int, float)
            or not 0 <= suspicion <= 10
            or not math.isfinite(suspicion)
            or type(confidence) not in (int, float)
            or not 0 <= confidence <= 1
            or not math.isfinite(confidence)
        ):
            raise ValueError("Assessment suspicion/confidence is out of range")
        if (
            not isinstance(assessment.get("reason"), str)
            or not assessment["reason"].strip()
        ):
            raise ValueError("Assessment needs an explicit rationale")
        for name in ("evidence_ids", "counter_evidence_ids"):
            values = assessment.get(name, [])
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or not set(values) <= known_ids
            ):
                raise ValueError("Assessment references unknown or private evidence")
        if not assessment.get("evidence_ids"):
            raise ValueError("Assessment update requires observed evidence")
        previous = ctx.player_suspicions.get(subject.display_name)
        records = ctx.evidence_records()
        old_evidence = previous.get("evidence_ids", []) if previous else []
        old_content = {
            re.sub(r"\s+", "", records[key]["text"])
            for key in old_evidence
            if key in records
        }
        new_content = {
            re.sub(r"\s+", "", records[key]["text"])
            for key in assessment["evidence_ids"]
        }
        if (
            previous
            and (
                previous.get("level") != suspicion
                or previous.get("confidence") != confidence
            )
            and not new_content - old_content
            and not decision.change_reason.strip()
        ):
            raise ValueError(
                "Changing suspicion or confidence needs independent new evidence or change_reason"
            )
