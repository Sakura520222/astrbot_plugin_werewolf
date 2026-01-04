"""发言行动 - 白天发言和遗言"""
import re
import random
from typing import List, TYPE_CHECKING
from astrbot.api import logger

from .base import BaseAction
from ..context import ContextBuilder, SituationAnalyzer, BehaviorAnalyzer
from ..prompts import (
    ANTI_HALLUCINATION_PROTOCOL,
    HUMAN_STYLE_TIPS,
    ROLE_SOUL_SETTINGS,
    PERSONALITY_TEMPLATES,
    ROLE_PROMPTS,
    SPEECH_TIPS,
    PK_TIPS,
    LAST_WORDS_TIPS
)

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class SpeechAction(BaseAction):
    """发言行动"""

    def __init__(self, context):
        super().__init__(context)
        self._player_personalities = {}

    def _get_player_personality(self, player: "Player") -> str:
        """获取或分配玩家性格"""
        if player.id not in self._player_personalities:
            personality_key = random.choice(list(PERSONALITY_TEMPLATES.keys()))
            self._player_personalities[player.id] = personality_key
            logger.info(f"[狼人杀AI] 为 {player.name} 分配性格: {personality_key}")
        return PERSONALITY_TEMPLATES[self._player_personalities[player.id]]

    async def generate_speech(self, player: "Player", room: "GameRoom", is_pk: bool = False) -> str:
        """AI生成白天发言"""
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
        role_name = player.role.display_name if player.role else "玩家"
        soul_setting = ROLE_SOUL_SETTINGS.get(role_key, "")
        personality = self._get_player_personality(player)

        # 增强决策系统 - 利用记忆系统
        memory_guidance = self._get_memory_guidance(player, room)
        if memory_guidance:
            context += "\n" + memory_guidance
        
        # 添加自我认知提醒
        context += f"\n【🆔 自我认知提醒】\n你是{player.number}号玩家{player.display_name}，发言时请先报编号！"

        if is_pk:
            pk_tips = PK_TIPS.get(role_key, PK_TIPS["villager"])
            prompt = ROLE_PROMPTS["pk_speech"].format(
                anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
                soul_setting=soul_setting,
                personality=personality,
                context=context,
                pk_tips=pk_tips,
                human_style=HUMAN_STYLE_TIPS
            )
        else:
            # 动态调整村民提示词
            if role_key == "villager" and player.ai_context and player.ai_context.current_round == 1:
                speech_tips = """【👨‍🌾 村民首日发言】
⚠️ 这是第一天，信息量较少，不要过度推理或编造不存在的信息！

🗣️ 首日发言建议：
1. 如果还没有人发言：简单表态，等待信息
2. 如果已有少量发言：简单评价，不要过度分析
3. 如果有预言家跳出：可以表态支持或怀疑，但要基于实际发言
4. 严禁编造"昨天"、"前一天"等虚假信息

💡 记住：根据已有的发言内容发言，不要分析不存在的事情！"""
            else:
                speech_tips = SPEECH_TIPS.get(role_key, SPEECH_TIPS["villager"])

            prompt = ROLE_PROMPTS["day_speech"].format(
                anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
                soul_setting=soul_setting,
                personality=personality,
                context=context,
                speech_tips=speech_tips,
                human_style=HUMAN_STYLE_TIPS
            )

        response = await self._call_llm(prompt, player)
        if response:
            # 分析并记录发言模式
            if player.ai_context:
                player.ai_context.analyze_speech_pattern(player.display_name, response)
            
            response = re.sub(r'^[\[【]?(发言|说话|speech)[\]】]?[：:]\s*', '', response, flags=re.IGNORECASE)
            return response[:300]

        defaults = [
            "我先听听大家怎么说吧",
            "目前信息太少了，我再观察一下",
            "emmm 我暂时没什么想法",
        ]
        return random.choice(defaults)

    def _get_memory_guidance(self, player: "Player", room: "GameRoom") -> str:
        """基于记忆系统提供决策指导"""
        if not player.ai_context:
            return ""
        
        ctx = player.ai_context
        lines = ["【🧠 记忆系统决策指导】"]
        
        # 基于怀疑度分析
        if ctx.player_suspicions:
            high_suspicion = [(p, info) for p, info in ctx.player_suspicions.items() if info.get("level", 0) >= 7]
            if high_suspicion:
                lines.append("🎯 高怀疑度目标（可能是狼）：")
                for player_name, suspicion in high_suspicion[:3]:
                    reason = suspicion.get("reason", "")
                    lines.append(f"- {player_name}: {reason}")
        
        # 基于阵营推断
        if ctx.player_alliances:
            confirmed_wolves = [(p, info) for p, info in ctx.player_alliances.items() 
                             if info.get("type") == "werewolf" and info.get("confidence", 0) >= 0.8]
            if confirmed_wolves:
                lines.append("🐺 确认的狼人目标：")
                for player_name, alliance in confirmed_wolves:
                    confidence = alliance.get("confidence", 0)
                    lines.append(f"- {player_name} (置信度: {confidence:.1f})")
        
        # 基于发言模式
        if ctx.speech_patterns:
            suspicious_patterns = []
            for player_name, pattern in ctx.speech_patterns.items():
                if player_name == player.display_name:
                    continue
                # 检测可疑的发言模式
                if pattern.get("emotional_state") == "攻击" and pattern.get("keywords", {}).get("预言家", 0) >= 2:
                    suspicious_patterns.append(f"{player_name}: 频繁攻击预言家")
                elif pattern.get("avg_length", 0) < 15 and pattern.get("speech_count", 0) >= 2:
                    suspicious_patterns.append(f"{player_name}: 发言过短，可能划水")
            
            if suspicious_patterns:
                lines.append("🗣️ 可疑发言模式：")
                lines.extend(f"- {pattern}" for pattern in suspicious_patterns[:3])
        
        # 基于投票模式
        if ctx.voting_patterns:
            inconsistent_voters = []
            for player_name, pattern in ctx.voting_patterns.items():
                if player_name == player.display_name:
                    continue
                # 检测投票不一致
                consistency = pattern.get("consistency", 1.0)
                if consistency < 0.5 and pattern.get("vote_count", 0) >= 2:
                    inconsistent_voters.append(f"{player_name}: 投票分散，可能隐藏身份")
            
            if inconsistent_voters:
                lines.append("🗳️ 可疑投票模式：")
                lines.extend(f"- {voter}" for voter in inconsistent_voters[:3])
        
        # 基于关键事件记忆
        if ctx.key_events_memory:
            recent_critical = [event for event in ctx.key_events_memory 
                             if event.get("importance", 0) >= 8 and event.get("round", 0) >= ctx.current_round - 1]
            if recent_critical:
                lines.append("⭐ 最近关键事件：")
                for event in recent_critical[:2]:
                    event_desc = event.get("event", "")
                    lines.append(f"- {event_desc}")
        
        # 角色特定指导
        role_key = ContextBuilder.get_role_key(player)
        if role_key == "werewolf":
            lines.append("🐺 狼人策略提醒：")
            lines.append("- 保护队友，不要暴露狼人身份")
            lines.append("- 引导投票，将目标对准好人")
            if ctx.werewolf_teammates:
                lines.append(f"- 你的队友是：{', '.join(ctx.werewolf_teammates)}")
        elif role_key == "seer" and ctx.seer_results:
            lines.append("🔮 预言家策略提醒：")
            for result in ctx.seer_results:
                target = result.get("target", "")
                is_wolf = result.get("is_werewolf", False)
                status = "狼人" if is_wolf else "好人"
                lines.append(f"- {target}是{status}，应该{'放逐' if is_wolf else '保护'}")
        elif role_key == "witch":
            lines.append("🧪 女巫策略提醒：")
            lines.append(f"- 解药：{'已用' if ctx.witch_antidote_used else '可用'}")
            lines.append(f"- 毒药：{'已用' if ctx.witch_poison_used else '可用'}")
        
        if len(lines) > 1:  # 除了标题行还有内容
            return "\n".join(lines)
        return ""

    async def generate_last_words(self, player: "Player", room: "GameRoom") -> str:
        """AI生成遗言"""
        context = ContextBuilder.build_context(player, room)
        role_key = ContextBuilder.get_role_key(player)
        role_name = player.role.display_name if player.role else "玩家"

        last_words_tips = LAST_WORDS_TIPS.get(role_key, LAST_WORDS_TIPS["villager"])

        # 预言家特殊处理
        if role_key == "seer" and player.ai_context and player.ai_context.seer_results:
            results = [f"{r['target']}是{'狼' if r['is_werewolf'] else '金水'}"
                      for r in player.ai_context.seer_results]
            last_words_tips += f"\n\n🔮 【重要】你的查验记录：{'; '.join(results)}\n务必全部公布出来！"

        prompt = ROLE_PROMPTS["last_words"].format(
            anti_hallucination=ANTI_HALLUCINATION_PROTOCOL,
            context=context,
            role_name=role_name,
            last_words_tips=last_words_tips,
            human_style=HUMAN_STYLE_TIPS
        )

        response = await self._call_llm(prompt, player)
        if response:
            return response[:100]

        return "我没什么好说的了，祝大家好运。"
