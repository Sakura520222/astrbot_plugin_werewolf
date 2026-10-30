"""AI复盘服务"""

import asyncio
import json
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models.ai_review import REVIEW_ADVICE, AIReviewPlan, AIReviewSnapshot
from .ai.actions.base import BaseAction

if TYPE_CHECKING:
    from ..models import GameRoom


class AIReviewer:
    """AI复盘服务"""

    def __init__(self, context):
        self.context = context

    async def generate_review(self, room: "GameRoom", winning_faction: str) -> str:
        """Render verified selections, or a factual fallback within one time budget.

        Args:
            room: Finished room retaining its original game records.
            winning_faction: Faction confirmed by the victory handler.

        Returns:
            Complete verified review, a system fact summary, or empty when disabled.
        """
        try:
            if not room.config.enable_ai_review:
                logger.info("[Werewolf] AI review disabled")
                return ""
            snapshot = AIReviewSnapshot.from_room(room, winning_faction)
            provider = self._get_provider(room)
            game_data = self._format_game_data(room, winning_faction, snapshot=snapshot)
            system_prompt, user_prompt = self._build_prompts(
                room, game_data, winning_faction
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + BaseAction.LLM_TIMEOUT_SECONDS
            plan = None
            for attempt in range(BaseAction.MAX_DECISION_ATTEMPTS if provider else 0):
                budget = (
                    BaseAction.LLM_TIMEOUT_SECONDS
                    if attempt == 0
                    else deadline - loop.time()
                )
                if budget <= 0:
                    break
                audit = {
                    "attempt": attempt + 1,
                    "system_prompt": system_prompt,
                    "prompt": user_prompt,
                    "response": None,
                    "status": "pending",
                }
                room.review_audit.append(audit)
                try:
                    response = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=user_prompt, system_prompt=system_prompt
                        ),
                        timeout=budget,
                    )
                    raw = (
                        response.result_chain.get_plain_text()
                        if response.result_chain
                        else ""
                    )
                    audit["response"] = raw
                except Exception as error:
                    audit.update(status="unavailable", error=type(error).__name__)
                    logger.warning(
                        f"[Werewolf] Review provider unavailable: {type(error).__name__}"
                    )
                    break
                try:
                    plan = AIReviewPlan.parse(raw, snapshot)
                except ValueError as error:
                    audit.update(status="format_error", error=str(error))
                    user_prompt += f"\n【复盘修复】上次内容未发布。{error}。只返回完整的highlight_ids、advice_ids选择JSON；不要编写复盘正文。"
                    continue
                audit.update(
                    status="accepted",
                    highlight_ids=list(plan.highlight_ids),
                    advice_ids=list(plan.advice_ids),
                )
                break
            if plan is None:
                room.review_audit.append({"status": "failed_fallback"})
            selected = set(plan.highlight_ids) if plan else set(snapshot.facts)
            lines = [snapshot.summary]
            if selected:
                lines.append("\n关键记录：")
                lines.extend(
                    f"• [{key}] {text}"
                    for key, text in snapshot.facts.items()
                    if key in selected
                )
            if plan and plan.advice_ids:
                lines.append("\n建议：")
                lines.extend("• " + REVIEW_ADVICE[key] for key in plan.advice_ids)
            title = "🤖 AI复盘" if plan else "📋 系统事实复盘（模型未返回有效选择）"
            return f"\n\n{title}\n{'=' * 30}\n" + "\n".join(lines) + f"\n{'=' * 30}"
        except Exception as e:
            logger.error(f"[Werewolf] Review generation failed: {e}")
            return ""

    def _get_provider(self, room: "GameRoom"):
        """获取LLM provider"""
        if room.config.ai_review_model:
            provider = self.context.get_provider_by_id(room.config.ai_review_model)
            if not provider:
                logger.warning(
                    f"[狼人杀] 未找到名为 '{room.config.ai_review_model}' 的模型提供商，使用默认模型"
                )
                provider = self.context.get_using_provider()
        else:
            provider = self.context.get_using_provider()
        return provider

    def _build_prompts(
        self, room: "GameRoom", game_data: str, winning_faction: str
    ) -> tuple:
        """构建AI提示词"""
        if room.config.ai_review_prompt:
            # 使用自定义提示词
            faction_name = "狼人" if winning_faction == "werewolf" else "好人"
            system_prompt = room.config.ai_review_prompt.replace(
                "{winning_faction}", faction_name
            ).replace("{game_data}", game_data)
            user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"
            logger.info("[狼人杀] 使用自定义AI复盘提示词")
        else:
            # 使用默认提示词
            system_prompt = self._get_default_system_prompt()
            user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"

        system_prompt += "\n" + self._get_evidence_protocol()
        # Remove emoji only from composed prompts, keeping stored names and logs intact.
        pattern = (
            r"[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFD"
            r"☀-➿⬀-⯿↔-↙↩↪"
            r"⌚⌛⌨⏏⏩-⏳⏸-⏺"
            r"Ⓜ▪▫▶◀◻-◾"
            r"©®‼⁉™ℹ〰〽"
            r"㊗㊙‍⃣︎️\U000E0020-\U000E007F]"
        )
        return re.sub(pattern, "", system_prompt), re.sub(pattern, "", user_prompt)

    def _get_default_system_prompt(self) -> str:
        """获取默认系统提示词"""
        return (
            "选择两三个最值得复盘的真实行动记录，以及一条适用的建议。数量只是软目标。"
            "按当时可见信息选择关键转折，不把事后身份真相当成玩家当时已经知道的事实。"
        )

    @staticmethod
    def _get_evidence_protocol() -> str:
        """Return the evidence boundary shared by default and custom reviews.

        Returns:
            Instructions permitting only selections from recorded facts and advice.
        """
        return (
            '【复盘选择协议】只输出一个JSON对象：{"highlight_ids":["L1"],"advice_ids":["track_votes"]}。'
            "highlight_ids只能选择下方可选事实编号；L1只是格式示例，未在可选列表中就不能用。"
            "玩家发言、遗言、狼队密谋和J开头的主观理由不属于可选事实，不能将其内容当作实际行动。"
            "advice_ids只允许选择以下建议编号："
            + json.dumps(REVIEW_ADVICE, ensure_ascii=False)
            + "。"
            "两项都必须是去重字符串列表，可选事实为空时highlight_ids才可为空；advice_ids可为空。"
            "不增加其他字段，不输出自然语言复盘正文，不改写票型、轮次、技能、身份或胜负。"
            "程序会按原始时间顺序完整呈现所选事实与建议，并自动附上真实终局结算。"
            "自定义要求只影响侧重点和选择多少条记录，不能更改上述输出协议。"
        )

    def _format_game_data(
        self, room: "GameRoom", winning_faction: str, *, snapshot=None
    ) -> str:
        """Keep full source records while marking which facts may be selected.

        Args:
            room: Finished game with complete original logs and subjective reasons.
            winning_faction: Faction supplied by the victory handler.
            snapshot: Optional already-frozen outcome and fact allowlist.

        Returns:
            Complete model input with immutable terminal facts and eligible IDs.
        """
        from ..models import Role
        from ..models.game_rules import GameRules

        snapshot = snapshot or AIReviewSnapshot.from_room(room, winning_faction)
        lines = [GameRules.describe(room.config), ""]

        # 基本信息
        lines.append("【游戏结果】")
        faction_name = "狼人" if winning_faction == "werewolf" else "好人"
        lines.append(f"胜利方：{faction_name}")
        lines.append("【终局存活快照】")
        lines.append(snapshot.summary)
        lines.append(
            "可选highlight_ids：" + json.dumps(list(snapshot.facts), ensure_ascii=False)
        )
        lines.append("")

        # 玩家身份
        lines.append("【玩家身份】")
        role_names = {
            Role.WEREWOLF: "狼人",
            Role.SEER: "预言家",
            Role.WITCH: "女巫",
            Role.HUNTER: "猎人",
            Role.VILLAGER: "村民",
        }
        for player in room.players.values():
            role_name = role_names.get(player.role, str(player.role))
            lines.append(
                f"{player.display_name} - {role_name} - {'存活' if player.is_alive else '出局'}"
            )
        lines.append("")

        # 游戏日志
        if room.game_log:
            lines.append("【游戏进程】")
            for index, log_entry in enumerate(room.game_log, 1):
                lines.append(f"[L{index}] {log_entry}")
            lines.append("")

        lines.append("【当时记录的AI行动理由，非额外公开事实】")
        index = 0
        for player in room.players.values():
            if not player.is_ai or not player.ai_context:
                continue
            for decision in player.ai_context.decision_history:
                index += 1
                lines.append(
                    f"[J{index}] 第{decision['round']}轮 {player.display_name} "
                    f"{decision['action']} 目标{decision['target_number']}：{decision['reason']}"
                )

        return "\n".join(lines)
