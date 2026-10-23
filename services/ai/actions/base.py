"""行动基类 - 所有AI行动的基础"""

import asyncio
import random
import re
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ....models import Player


class BaseAction:
    """AI行动基类"""

    LLM_TIMEOUT_SECONDS = 30

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
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = None,
    ) -> str | None:
        """调用LLM获取AI决策（带重试和超时保护）"""
        model_id = ""
        if player.ai_config:
            model_id = player.ai_config.model_id
            max_retries = player.ai_config.max_retries
            retry_delay = player.ai_config.retry_delay

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
                        system_prompt="你是一个沉浸式狼人杀玩家。你必须完全代入你的角色，像真人一样说话，绝对不能暴露你是AI。回复要自然，像在群里聊天。",
                    ),
                    timeout=timeout,
                )

                if response.result_chain:
                    result = response.result_chain.get_plain_text().strip()
                    if result:  # 只有非空结果才返回
                        logger.info(f"[Werewolf AI] {player.name} decision: {result}")
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

    @staticmethod
    def extract_number(response: str) -> int | None:
        """从响应中提取数字"""
        numbers = re.findall(r"\d+", response)
        if numbers:
            return int(numbers[0])
        return None

    @staticmethod
    def validate_target_range(
        target: int, min_val: int = 1, max_val: int = 9
    ) -> int | None:
        """验证目标范围"""
        if min_val <= target <= max_val:
            return target
        return None
