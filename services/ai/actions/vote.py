"""投票行动 - 白天投票"""

import re
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..context import BehaviorAnalyzer, ContextBuilder, SituationAnalyzer
from ..prompts import (
    ANTI_HALLUCINATION_PROTOCOL,
    HUMAN_STYLE_TIPS,
    ROLE_PROMPTS,
    ROLE_SOUL_SETTINGS,
    VOTE_TIPS,
)
from ..validators import TargetValidator
from .base import BaseAction

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class VoteAction(BaseAction):
    """投票行动"""

    async def decide_vote(
        self,
        player: "Player",
        room: "GameRoom",
        is_pk: bool = False,
        pk_candidates: list[str] = None,
    ) -> tuple[str, int | None]:
        """AI生成投票决策"""
        context = ContextBuilder.build_context(player, room)
        context += "\n" + SituationAnalyzer.get_situation_awareness(room)

        # 检查特殊事件
        special_event_tip = ContextBuilder.get_special_event_tip(player, room)
        if special_event_tip:
            context += "\n" + special_event_tip

        # 添加战术指令
        tactical_directive = SituationAnalyzer.get_tactical_directive(player, room)
        if tactical_directive:
            context += "\n" + tactical_directive

        # 添加玩家行为分析
        behavior_analysis = BehaviorAnalyzer.get_behavior_analysis_prompt(player, room)
        if behavior_analysis:
            context += "\n" + behavior_analysis

        role_key = ContextBuilder.get_role_key(player)
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")
        vote_tips = VOTE_TIPS.get(role_key, VOTE_TIPS["villager"])

        # 增强决策系统 - 利用记忆系统
        memory_guidance = self._get_vote_memory_guidance(player, room)
        if memory_guidance:
            context += "\n" + memory_guidance

        # 添加自我认知提醒
        context += f"\n【🆔 自我认知提醒】\n你是{player.number}号玩家{player.display_name}，投票时不能投给自己！"

        prompt = ROLE_PROMPTS["day_vote"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            soul_setting=soul_setting,
            context=context,
            vote_tips=vote_tips,
            human_style=HUMAN_STYLE_TIPS,
        )

        response = await self._call_llm(prompt, player)

        speech = ""
        vote_target = None

        if response:
            # 解析发言
            speech_match = re.search(
                r"\[发言\]\s*(.+?)(?=\[投票\]|$)", response, re.DOTALL
            )
            if speech_match:
                speech = speech_match.group(1).strip()[:100]  # 允许更长的发言

            # 解析投票
            vote_match = re.search(r"\[投票\]\s*(\d+|弃票)", response)
            if vote_match:
                vote_str = vote_match.group(1)
                if vote_str != "弃票":
                    try:
                        raw_target = int(vote_str)
                        # 使用验证器确保目标有效
                        vote_target = TargetValidator.validate_vote_target(
                            room, raw_target, player
                        )
                        if vote_target is None:
                            logger.warning(
                                f"[狼人杀AI] {player.name} 投票目标 {raw_target} 无效（死亡或不存在）"
                            )
                    except ValueError:
                        pass

            # 如果没有找到格式化内容，尝试直接提取
            if not speech and vote_target is None:
                numbers = re.findall(r"\d+", response)
                if numbers:
                    raw_target = int(numbers[0])
                    # 确保不投自己
                    if raw_target != player.number:
                        vote_target = TargetValidator.validate_vote_target(
                            room, raw_target, player
                        )
                        if vote_target is None:
                            logger.warning(
                                f"[狼人杀AI] {player.name} 投票目标 {raw_target} 无效（死亡或不存在）"
                            )
                    else:
                        logger.warning(f"[狼人杀AI] {player.name} 尝试投自己，自动拒绝")
                speech = response[:100] if len(response) <= 100 else ""

        # 如果投票目标仍然无效，提供默认行为
        if vote_target is None:
            # 获取所有有效的投票目标（排除自己和死亡玩家）
            valid_targets = TargetValidator.get_valid_targets(
                room, exclude_player=player, include_dead=False
            )
            if valid_targets:
                # 选择第一个有效目标作为默认投票
                vote_target = valid_targets[0]
                logger.info(
                    f"[狼人杀AI] {player.name} 投票目标无效，使用默认目标 {vote_target}号"
                )
                if not speech:
                    speech = f"投票目标无效，我选择投{vote_target}号。"
            else:
                # 如果没有有效目标，选择弃票
                logger.info(f"[狼人杀AI] {player.name} 没有有效投票目标，选择弃票")
                if not speech:
                    speech = "没有有效的投票目标，我选择弃票。"

        # 记录投票模式
        if vote_target is not None and player.ai_context:
            target_player = room.get_player(vote_target)
            if target_player:
                player.ai_context.analyze_voting_pattern(
                    player.display_name, target_player.display_name, is_pk
                )

        return (speech, vote_target)

    def _get_vote_memory_guidance(self, player: "Player", room: "GameRoom") -> str:
        """基于记忆系统提供投票决策指导"""
        if not player.ai_context:
            return ""

        ctx = player.ai_context
        lines = ["【🧠 记忆系统投票指导】"]

        # 基于怀疑度分析 - 投票高怀疑度目标
        if ctx.player_suspicions:
            high_suspicion = [
                (p, info)
                for p, info in ctx.player_suspicions.items()
                if info.get("level", 0) >= 7
            ]
            if high_suspicion:
                lines.append("🎯 建议投票目标（高怀疑度）：")
                for player_name, suspicion in high_suspicion[:3]:
                    reason = suspicion.get("reason", "")
                    level = suspicion.get("level", 0)
                    lines.append(f"- {player_name} ({level}/10): {reason}")

        # 基于阵营推断 - 投票确认的狼人
        if ctx.player_alliances:
            confirmed_wolves = [
                (p, info)
                for p, info in ctx.player_alliances.items()
                if info.get("type") == "werewolf" and info.get("confidence", 0) >= 0.8
            ]
            if confirmed_wolves:
                lines.append("🐺 确认的狼人（优先投票）：")
                for player_name, alliance in confirmed_wolves:
                    confidence = alliance.get("confidence", 0)
                    lines.append(f"- {player_name} (置信度: {confidence:.1f})")

        # 基于投票历史 - 分析投票模式
        if ctx.vote_history:
            # 分析谁经常投好人
            vote_analysis = self._analyze_voting_patterns(ctx, room)
            if vote_analysis:
                lines.extend(vote_analysis)

        # 基于发言模式 - 分析可疑发言
        if ctx.speech_patterns:
            suspicious_speakers = []
            for player_name, pattern in ctx.speech_patterns.items():
                if player_name == player.display_name:
                    continue

                # 检测可疑的发言模式
                suspicion_score = 0
                reasons = []

                if pattern.get("emotional_state") == "攻击":
                    suspicion_score += 2
                    reasons.append("情绪激动")

                if pattern.get("keywords", {}).get("预言家", 0) >= 2:
                    suspicion_score += 3
                    reasons.append("频繁攻击预言家")

                if (
                    pattern.get("avg_length", 0) < 15
                    and pattern.get("speech_count", 0) >= 2
                ):
                    suspicion_score += 1
                    reasons.append("发言过短")

                if suspicion_score >= 3:
                    suspicious_speakers.append(
                        (player_name, suspicion_score, ", ".join(reasons))
                    )

            if suspicious_speakers:
                lines.append("🗣️ 可疑发言模式分析：")
                suspicious_speakers.sort(key=lambda x: x[1], reverse=True)
                for name, score, reasons in suspicious_speakers[:3]:
                    lines.append(f"- {name} (可疑度{score}): {reasons}")

        # 角色特定投票指导
        role_key = ContextBuilder.get_role_key(player)
        if role_key == "werewolf":
            lines.append("🐺 狼人投票策略：")
            lines.append("- 避免投队友，分散投票看起来更自然")
            lines.append("- 可以跟票，但不要总是跟同一群人")
            if ctx.werewolf_teammates:
                teammates_str = ", ".join(ctx.werewolf_teammates)
                lines.append(f"- 绝对不能投的队友：{teammates_str}")
        elif role_key == "seer" and ctx.seer_results:
            lines.append("🔮 预言家投票策略：")
            for result in ctx.seer_results:
                target = result.get("target", "")
                is_wolf = result.get("is_werewolf", False)
                if is_wolf:
                    lines.append(f"- 优先投票：{target} (已验出是狼人)")
        elif role_key == "witch":
            lines.append("🧪 女巫投票策略：")
            lines.append("- 结合昨晚信息判断")
            if ctx.last_killed_player and not ctx.witch_antidote_used:
                lines.append(
                    f"- 注意：昨晚{ctx.last_killed_player}被刀，你没救，他可能是狼自刀"
                )

        if len(lines) > 1:  # 除了标题行还有内容
            return "\n".join(lines)
        return ""

    def _analyze_voting_patterns(self, ctx, room: "GameRoom") -> list[str]:
        """分析投票模式"""
        lines = []

        # 分析谁经常投相同目标
        voter_targets = {}
        for vote in ctx.vote_history:
            voter = vote.get("voter", "")
            target = vote.get("target", "")
            if voter and target:
                if voter not in voter_targets:
                    voter_targets[voter] = {}
                voter_targets[voter][target] = voter_targets[voter].get(target, 0) + 1

        # 找出投票模式一致的玩家
        consistent_voters = []
        for voter, targets in voter_targets.items():
            if len(targets) <= 2 and len(targets) > 0:  # 只投1-2个不同目标
                max_votes = max(targets.values())
                if max_votes >= 2:  # 至少投过同一人2次
                    consistent_voters.append((voter, targets, max_votes))

        if consistent_voters:
            lines.append("🗳️ 投票模式分析：")
            for voter, targets, max_votes in consistent_voters[:3]:
                primary_target = max(targets.items(), key=lambda x: x[1])[0]
                lines.append(f"- {voter}: 经常投{primary_target} ({max_votes}次)")

        return lines
