"""AI玩家服务 - 模块化重构版

这是原 ai_player_service.py 的模块化重构版本。
将2000+行的单文件拆分为多个职责清晰的模块。
"""
import random
from typing import Optional, List, Tuple, Dict, TYPE_CHECKING
from astrbot.api import logger

from .prompts import PERSONALITY_TEMPLATES, PERSONALITY_NAMES
from .context import ContextBuilder
from .actions import (
    WerewolfAction,
    SeerAction,
    WitchAction,
    HunterAction,
    SpeechAction,
    VoteAction
)

if TYPE_CHECKING:
    from ...models import GameRoom, Player, GamePhase


class AIPlayerService:
    """AI玩家服务 - 处理AI玩家的游戏决策"""

    def __init__(self, context):
        self.context = context
        self._retry_counts: Dict[str, int] = {}
        self._player_personalities: Dict[str, str] = {}

        # 初始化各行动模块
        self._werewolf_action = WerewolfAction(context)
        self._seer_action = SeerAction(context)
        self._witch_action = WitchAction(context)
        self._hunter_action = HunterAction(context)
        self._speech_action = SpeechAction(context)
        self._vote_action = VoteAction(context)

    # ==================== 性格管理 ====================

    def assign_personality(self, player_id: str) -> str:
        """预分配玩家性格并返回中文名称"""
        if player_id not in self._player_personalities:
            personality_key = random.choice(list(PERSONALITY_TEMPLATES.keys()))
            self._player_personalities[player_id] = personality_key
            logger.info(f"[狼人杀AI] 为玩家 {player_id} 分配性格: {personality_key}")
        return PERSONALITY_NAMES.get(self._player_personalities[player_id], "普通")

    # ==================== 狼人行动 ====================

    async def decide_werewolf_kill(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI狼人选择击杀目标"""
        return await self._werewolf_action.decide_kill(player, room)

    async def decide_werewolf_chat(self, player: "Player", room: "GameRoom") -> Optional[str]:
        """AI狼人生成密谋消息"""
        return await self._werewolf_action.decide_chat(player, room)

    # ==================== 预言家行动 ====================

    async def decide_seer_check(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI预言家选择验人目标"""
        return await self._seer_action.decide_check(player, room)

    # ==================== 女巫行动 ====================

    async def decide_witch_action(
        self,
        player: "Player",
        room: "GameRoom",
        can_save: bool,
        can_poison: bool,
        killed_player_name: Optional[str] = None
    ) -> Tuple[str, Optional[int]]:
        """AI女巫决定用药"""
        return await self._witch_action.decide_action(
            player, room, can_save, can_poison, killed_player_name
        )

    # ==================== 猎人行动 ====================

    async def decide_hunter_shoot(self, player: "Player", room: "GameRoom") -> Optional[int]:
        """AI猎人决定开枪目标"""
        return await self._hunter_action.decide_shoot(player, room)

    # ==================== 白天发言 ====================

    async def generate_speech(self, player: "Player", room: "GameRoom", is_pk: bool = False) -> str:
        """AI生成白天发言"""
        return await self._speech_action.generate_speech(player, room, is_pk)

    # ==================== 投票 ====================

    async def decide_vote(
        self,
        player: "Player",
        room: "GameRoom",
        is_pk: bool = False,
        pk_candidates: List[str] = None
    ) -> Tuple[str, Optional[int]]:
        """AI生成投票决策"""
        return await self._vote_action.decide_vote(player, room, is_pk, pk_candidates)

    # ==================== 遗言 ====================

    async def generate_last_words(self, player: "Player", room: "GameRoom") -> str:
        """AI生成遗言"""
        return await self._speech_action.generate_last_words(player, room)

    # ==================== 上下文管理 ====================

    def initialize_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """初始化AI玩家的游戏上下文"""
        from ...models.ai_player import AIPlayerContext

        if not player.is_ai:
            return

        ctx = AIPlayerContext()
        ctx.player_number = player.number
        ctx.role_name = player.role.display_name if player.role else "未知"
        ctx.is_werewolf = player.role and player.role.value == "werewolf"
        ctx.current_round = room.current_round
        ctx.current_phase = self._get_phase_description(room)

        alive_list = [p.display_name for p in room.get_alive_players()]
        ctx.update_alive_players(alive_list, [])

        if ctx.is_werewolf:
            teammates = [
                w.display_name for w in room.get_alive_werewolves()
                if w.id != player.id
            ]
            ctx.werewolf_teammates = teammates

        player.ai_context = ctx
        logger.info(f"[狼人杀AI] 初始化 {player.name} 的上下文: {ctx.role_name}")

    def update_ai_context(self, player: "Player", room: "GameRoom") -> None:
        """更新AI玩家的游戏上下文"""
        if not player.is_ai or not player.ai_context:
            return

        ctx = player.ai_context
        ctx.current_round = room.current_round
        ctx.current_phase = self._get_phase_description(room)

        alive_list = [p.display_name for p in room.get_alive_players()]
        dead_list = [p.display_name for p in room.players.values() if not p.is_alive]
        ctx.update_alive_players(alive_list, dead_list)

        if player.role and player.role.value == "witch":
            ctx.witch_antidote_used = room.witch_state.antidote_used
            ctx.witch_poison_used = room.witch_state.poison_used
            if room.last_killed_id:
                killed = room.get_player(room.last_killed_id)
                ctx.last_killed_player = killed.display_name if killed else None

    def _get_phase_description(self, room: "GameRoom") -> str:
        """获取当前阶段的人类可读描述"""
        from ...models import GamePhase
        phase = room.phase
        round_num = room.current_round

        # 首日发言阶段特殊描述
        if round_num == 1 and phase == GamePhase.DAY_SPEAKING:
            return "第1天白天（首日发言）- 昨晚只分配了身份，今天是第一次发言，没有任何前置信息"

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
