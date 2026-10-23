"""阶段管理器"""

import asyncio
import random
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..roles import HunterDeathType, HunterRole
from ..services import BanService

if TYPE_CHECKING:
    from ..models import GameRoom
    from ..services import GameManager


class PhaseManager:
    """阶段管理器 - 协调阶段切换"""

    def __init__(self, game_manager: "GameManager"):
        self.game_manager = game_manager
        self.message_service = game_manager.message_service

    # ========== 夜晚阶段 ==========

    async def enter_night_phase(self, room: "GameRoom") -> None:
        """进入夜晚阶段"""
        room.start_new_night()
        room.log_round_start()

        # 开启全员禁言
        await BanService.set_group_whole_ban(room, True)

        # 发送夜晚消息
        await self.message_service.announce_night_start(room)

        # 进入狼人阶段
        from .night_wolf import NightWolfPhase

        wolf_phase = NightWolfPhase(self.game_manager)
        await wolf_phase.on_enter(room)

    async def enter_seer_phase(self, room: "GameRoom") -> None:
        """进入预言家验人阶段"""
        from .night_seer import NightSeerPhase

        seer_phase = NightSeerPhase(self.game_manager)
        await seer_phase.on_enter(room)

    async def enter_witch_phase(self, room: "GameRoom") -> None:
        """进入女巫行动阶段"""
        from .night_witch import NightWitchPhase

        witch_phase = NightWitchPhase(self.game_manager)
        await witch_phase.on_enter(room)

    # ========== 白天阶段 ==========

    async def enter_speaking_phase(self, room: "GameRoom") -> None:
        """进入发言阶段"""
        from .day_speaking import DaySpeakingPhase

        speaking_phase = DaySpeakingPhase(self.game_manager)
        await speaking_phase.on_enter(room)

    async def enter_vote_phase(self, room: "GameRoom") -> None:
        """进入投票阶段"""
        from .day_vote import DayVotePhase

        vote_phase = DayVotePhase(self.game_manager)
        await vote_phase.on_enter(room)

    async def enter_pk_vote_phase(self, room: "GameRoom") -> None:
        """进入PK投票阶段"""
        from .day_vote import DayVotePhase

        vote_phase = DayVotePhase(self.game_manager)
        await vote_phase.enter_pk_vote(room)

    async def enter_last_words_phase(self, room: "GameRoom") -> None:
        """进入遗言阶段"""
        from .last_words import LastWordsPhase

        last_words_phase = LastWordsPhase(self.game_manager)
        await last_words_phase.on_enter(room)

    # ========== 猎人开枪 ==========

    async def wait_for_hunter_shot(self, room: "GameRoom", death_type: str) -> None:
        """等待猎人开枪"""
        hunter_id = room.hunter_state.pending_shot_player_id
        if not hunter_id:
            return

        hunter = room.get_player(hunter_id)
        if not hunter:
            return

        # 通知群
        await self.message_service.announce_hunter_can_shoot(room, hunter.display_name)

        # 如果是AI猎人，自动开枪
        if hunter.is_ai:
            await self._handle_ai_hunter_shot(room, hunter, death_type)
            return

        # 发送开枪提示给猎人
        hunter_role = HunterRole()
        prompt = hunter_role.get_death_prompt(death_type)
        await self.message_service.send_private_message(room, hunter_id, prompt)

        # 启动猎人开枪定时器
        timeout = self.game_manager.config.timeout_hunter

        async def hunter_timer():
            try:
                await asyncio.sleep(timeout)

                if room.group_id not in self.game_manager.rooms:
                    return
                if not room.hunter_state.pending_shot_player_id:
                    return

                logger.info(f"[狼人杀] 群 {room.group_id} 猎人开枪超时")

                # 清除状态
                hunter_id = room.hunter_state.pending_shot_player_id
                hunter_name = (
                    room.get_player(hunter_id).display_name
                    if room.get_player(hunter_id)
                    else "猎人"
                )
                room.hunter_state.pending_shot_player_id = None
                room.hunter_state.has_shot = True

                room.log(f"🔫 {hunter_name}（猎人）超时未开枪")
                await self.message_service.send_group_message(
                    room, f"⏰ {hunter_name} 开枪超时！放弃开枪机会。"
                )

                # 继续游戏流程
                await self._after_hunter_timeout(room, death_type)

            except asyncio.CancelledError:
                logger.info(f"[狼人杀] 群 {room.group_id} 猎人开枪定时器已取消")
            except Exception as e:
                logger.error(f"[狼人杀] 猎人开枪超时处理失败: {e}")

        task = asyncio.create_task(hunter_timer())
        room.set_timer(task)

    async def _handle_ai_hunter_shot(
        self, room: "GameRoom", hunter, death_type: str
    ) -> None:
        """处理AI猎人开枪"""
        ai_service = self.game_manager.ai_player_service

        # 延迟模拟思考
        await asyncio.sleep(random.uniform(2, 4))

        # AI决策开枪目标
        target_number = await ai_service.decide_hunter_shoot(hunter, room)

        if target_number:
            target_player = room.get_player_by_number(target_number)
            if (
                target_player
                and target_player.is_alive
                and target_player.id != hunter.id
            ):
                # 执行开枪
                room.kill_player(target_player.id)
                room.hunter_state.has_shot = True
                room.hunter_state.pending_shot_player_id = None

                # 记录日志
                room.log(
                    f"🔫 {hunter.display_name}（猎人AI）开枪带走 {target_player.display_name}"
                )
                logger.info(
                    f"[狼人杀] AI猎人 {hunter.name} 开枪带走 {target_player.display_name}"
                )

                # 禁言被带走的玩家（跳过AI）
                if not target_player.is_ai:
                    await BanService.ban_player(room, target_player.id)

                # 通知群
                await self.message_service.announce_hunter_shot(
                    room, target_player.display_name
                )

                # 记录到AI上下文
                for p in room.players.values():
                    if p.is_ai and p.ai_context:
                        p.ai_context.add_event(
                            f"猎人 {hunter.display_name} 开枪带走 {target_player.display_name}"
                        )

                # 检查游戏是否结束
                if await self.game_manager.check_and_handle_victory(room):
                    return

                # 继续游戏流程
                await self._after_hunter_shot(room, death_type)
                return

        # 不开枪
        room.hunter_state.has_shot = True
        room.hunter_state.pending_shot_player_id = None
        room.log(f"🔫 {hunter.display_name}（猎人AI）选择不开枪")
        logger.info(f"[狼人杀] AI猎人 {hunter.name} 不开枪")

        await self.message_service.send_group_message(
            room, f"🔫 {hunter.display_name} 选择不开枪！"
        )

        await self._after_hunter_shot(room, death_type)

    async def on_hunter_shot(self, room: "GameRoom", target_id: str) -> None:
        """猎人开枪"""
        room.cancel_timer()

        hunter_id = room.hunter_state.pending_shot_player_id
        if not hunter_id:
            return

        hunter = room.get_player(hunter_id)
        target = room.get_player(target_id)
        if not hunter or not target:
            return

        # 执行开枪
        room.kill_player(target_id)
        room.hunter_state.has_shot = True
        room.hunter_state.pending_shot_player_id = None

        # 记录日志
        room.log(f"🔫 {hunter.display_name}（猎人）开枪带走 {target.display_name}")

        # 禁言被带走的玩家
        await BanService.ban_player(room, target_id)

        # 通知群
        await self.message_service.announce_hunter_shot(room, target.display_name)

        # 记录到AI上下文
        for p in room.players.values():
            if p.is_ai and p.ai_context:
                p.ai_context.add_event(
                    f"猎人 {hunter.display_name} 开枪带走 {target.display_name}"
                )

        # 检查游戏是否结束
        if await self.game_manager.check_and_handle_victory(room):
            return

        # 继续游戏流程
        death_type = room.hunter_state.death_type
        await self._after_hunter_shot(room, death_type)

    async def _after_hunter_timeout(self, room: "GameRoom", death_type: str) -> None:
        """猎人超时后的流程"""
        await self._after_hunter_shot(room, death_type)

    async def _after_hunter_shot(self, room: "GameRoom", death_type) -> None:
        """猎人开枪后的流程"""
        # 检查游戏是否结束
        if await self.game_manager.check_and_handle_victory(room):
            return

        # 处理death_type可能是字符串或枚举的情况
        is_vote_death = (
            death_type == HunterDeathType.VOTE
            or str(death_type) == "vote"
            or death_type == "vote"
        )
        is_wolf_death = (
            death_type == HunterDeathType.WOLF
            or str(death_type) == "wolf"
            or death_type == "wolf"
        )

        if is_vote_death:
            # 猎人被投票放逐，进入遗言
            hunter_id = room.hunter_state.pending_shot_player_id
            if not hunter_id and room.last_killed_id:
                hunter_id = room.last_killed_id
            room.last_words_from_vote = True
            await self.enter_last_words_phase(room)
        elif is_wolf_death:
            # 猎人被狼杀
            if room.is_first_night and room.last_killed_id:
                await self.enter_last_words_phase(room)
            else:
                if room.last_killed_id:
                    await BanService.ban_player(room, room.last_killed_id)
                await self.enter_speaking_phase(room)
