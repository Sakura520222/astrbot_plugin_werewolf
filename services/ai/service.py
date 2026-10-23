"""AI玩家服务 - 模块化重构版

这是原 ai_player_service.py 的模块化重构版本。
将2000+行的单文件拆分为多个职责清晰的模块。
"""

import random
from typing import TYPE_CHECKING

from astrbot.api import logger

from ...models.personality import load_personalities
from .actions import (
    HunterAction,
    SeerAction,
    SpeechAction,
    VoteAction,
    WerewolfAction,
    WitchAction,
)
from .context import ContextBuilder

if TYPE_CHECKING:
    from ...models import GameRoom, Player


class AIPlayerService:
    """AI玩家服务 - 处理AI玩家的游戏决策"""

    def __init__(self, context):
        self.context = context
        self._retry_counts: dict[str, int] = {}
        self._player_personalities: dict[str, str] = {}
        self._player_profiles = {}

        # 初始化各行动模块
        self._werewolf_action = WerewolfAction(context, self._player_personalities)
        self._seer_action = SeerAction(context, self._player_personalities)
        self._witch_action = WitchAction(context, self._player_personalities)
        self._hunter_action = HunterAction(context, self._player_personalities)
        self._speech_action = SpeechAction(context, self._player_personalities)
        self._vote_action = VoteAction(context, self._player_personalities)

    # ==================== 性格管理 ====================

    def assign_personality(self, player_id: str, room: "GameRoom" = None) -> str:
        """Assign a weighted style, preferring unused profiles within this room.

        Args:
            player_id: Opaque player identifier.
            room: Room whose configuration and existing assignments are used.

        Returns:
            The private personality name, never appended to a public nickname.
        """
        if player_id not in self._player_personalities:
            profiles = load_personalities(
                room.config.ai_personality_overrides if room else ""
            )
            enabled = room.config.ai_personality_pool if room else []
            pool = [
                p
                for key, p in profiles.items()
                if p.weight > 0 and (not enabled or key in enabled)
            ]
            if not pool:
                pool = list(load_personalities().values())
            used = (
                {
                    self._player_personalities[p.id]
                    for p in room.players.values()
                    if p.id in self._player_personalities
                }
                if room
                else set(self._player_personalities.values())
            )
            choices = [p for p in pool if p.key not in used] or pool
            profile = random.choices(choices, weights=[p.weight for p in choices], k=1)[
                0
            ]
            personality_key = profile.key
            self._player_personalities[player_id] = personality_key
            self._player_profiles[player_id] = profile
            logger.info(
                f"[Werewolf AI] Assigned personality {personality_key} to {player_id}"
            )
        return self._player_profiles[player_id].name

    # ==================== 狼人行动 ====================

    async def decide_werewolf_kill(
        self, player: "Player", room: "GameRoom"
    ) -> int | None:
        """AI狼人选择击杀目标"""
        return await self._werewolf_action.decide_kill(player, room)

    async def decide_werewolf_chat(
        self, player: "Player", room: "GameRoom"
    ) -> str | None:
        """AI狼人生成密谋消息"""
        return await self._werewolf_action.decide_chat(player, room)

    # ==================== 预言家行动 ====================

    async def decide_seer_check(self, player: "Player", room: "GameRoom") -> int | None:
        """AI预言家选择验人目标"""
        return await self._seer_action.decide_check(player, room)

    # ==================== 女巫行动 ====================

    async def decide_witch_action(
        self,
        player: "Player",
        room: "GameRoom",
        can_save: bool,
        can_poison: bool,
        killed_player_name: str | None = None,
    ) -> tuple[str, int | None]:
        """AI女巫决定用药"""
        return await self._witch_action.decide_action(
            player, room, can_save, can_poison, killed_player_name
        )

    # ==================== 猎人行动 ====================

    async def decide_hunter_shoot(
        self, player: "Player", room: "GameRoom"
    ) -> int | None:
        """AI猎人决定开枪目标"""
        return await self._hunter_action.decide_shoot(player, room)

    # ==================== 白天发言 ====================

    async def generate_speech(
        self, player: "Player", room: "GameRoom", is_pk: bool = False
    ) -> str:
        """AI生成白天发言"""
        return await self._speech_action.generate_speech(player, room, is_pk)

    # ==================== 投票 ====================

    async def decide_vote(
        self,
        player: "Player",
        room: "GameRoom",
        is_pk: bool = False,
        pk_candidates: list[int] | None = None,
    ) -> tuple[str, int | None]:
        """AI生成投票决策"""
        return await self._vote_action.decide_vote(player, room, is_pk, pk_candidates)

    # ==================== 遗言 ====================

    async def generate_last_words(self, player: "Player", room: "GameRoom") -> str:
        """AI生成遗言"""
        return await self._speech_action.generate_last_words(player, room)

    # ==================== 上下文管理 ====================

    def initialize_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """Assign identity and style without inventing memories.

        Args:
            player: AI player being initialized.
            room: Its game room.
        """
        if not player.is_ai:
            return
        from ...models.ai_player import AIPlayerContext

        self.assign_personality(player.id, room)
        player.ai_context = AIPlayerContext()
        ctx = ContextBuilder.refresh_context(player, room)
        ctx.personality_key = self._player_personalities[player.id]
        custom = player.ai_config.personality if player.ai_config else ""
        ctx.personality = custom.strip() or self._player_profiles[player.id].to_prompt()

    def update_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """Refresh state without repeatedly duplicating events and conclusions.

        Args:
            player: AI player whose original records must be preserved.
            room: Its authoritative room.
        """
        if player.is_ai and player.ai_context:
            ContextBuilder.refresh_context(player, room)

    def _get_phase_description(self, room: "GameRoom") -> str:
        """获取当前阶段的人类可读描述"""
        from ...models import GamePhase

        phase = room.phase
        round_num = room.current_round

        # 首日发言阶段特殊描述（首夜已有夜间行动，只是还没有公开信息）
        if round_num == 1 and phase == GamePhase.DAY_SPEAKING:
            return (
                "第1天白天（首日发言）- 昨夜已完成夜间行动"
                "（各角色掌握各自的夜间信息），今天是第一次公开发言"
            )

        phase_map = {
            GamePhase.NIGHT_WOLF: f"第{round_num}天夜晚 - 狼人行动阶段",
            GamePhase.NIGHT_SEER: f"第{round_num}天夜晚 - 预言家验人阶段",
            GamePhase.NIGHT_WITCH: f"第{round_num}天夜晚 - 女巫行动阶段",
            GamePhase.DAY_SPEAKING: f"第{round_num}天白天 - 发言阶段",
            GamePhase.DAY_VOTE: f"第{round_num}天白天 - 投票阶段",
            GamePhase.DAY_PK: f"第{round_num}天白天 - PK发言阶段",
            GamePhase.LAST_WORDS: f"第{round_num}天 - 遗言阶段",
        }

        return phase_map.get(phase, f"第{round_num}天")

    def clear_player_data(self, player_id: str) -> None:
        """清理玩家数据"""
        self._retry_counts.pop(player_id, None)
        self._player_personalities.pop(player_id, None)
        self._player_profiles.pop(player_id, None)
