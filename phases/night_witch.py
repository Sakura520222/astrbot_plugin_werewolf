"""夜晚-女巫行动阶段"""
import asyncio
import random
from typing import TYPE_CHECKING
from astrbot.api import logger

from .base import BasePhase
from ..models import GamePhase, Role
from ..roles import HunterDeathType
from ..services import BanService
from ..roles import WitchRole

if TYPE_CHECKING:
    from ..models import GameRoom


class NightWitchPhase(BasePhase):
    """女巫行动阶段"""

    @property
    def name(self) -> str:
        return "女巫行动阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_witch

    def _is_current_phase(self, room: "GameRoom") -> bool:
        return room.phase == GamePhase.NIGHT_WITCH

    async def on_enter(self, room: "GameRoom") -> None:
        """进入女巫行动阶段"""
        room.phase = GamePhase.NIGHT_WITCH
        room.witch_state.reset_night()

        witch = room.get_witch()

        # 如果游戏中没有女巫角色，直接跳过
        if not witch:
            logger.info(f"[狼人杀] 群 {room.group_id} 没有女巫角色，跳过女巫阶段")
            await self._finish_night(room)
            return

        # 发送群提示
        await self.message_service.announce_witch_phase(room)

        # 如果女巫是AI且存活或今晚被杀，自动处理
        witch_killed_tonight = (room.last_killed_id == witch.id)
        if witch.is_ai and (witch.is_alive or witch_killed_tonight):
            await self._handle_ai_witch(room, witch)
            return

        # 给女巫发私聊
        await self._notify_witch(room)

        # 计算等待时间
        wait_time = self._calculate_wait_time(room)

        # 启动定时器
        await self.start_timer(room, wait_time)

    async def _handle_ai_witch(self, room: "GameRoom", witch) -> None:
        """处理AI女巫的行动"""
        ai_service = self.game_manager.ai_player_service
        witch_state = room.witch_state

        # 更新AI上下文
        ai_service.update_ai_context(witch, room)

        # 延迟模拟思考
        await asyncio.sleep(random.uniform(3, 6))

        # 判断可用操作
        can_save = not witch_state.antidote_used and room.last_killed_id is not None
        can_poison = not witch_state.poison_used

        # 获取被杀玩家名称
        killed_player_name = None
        if room.last_killed_id:
            killed_player = room.get_player(room.last_killed_id)
            if killed_player:
                killed_player_name = killed_player.display_name

        # AI决策
        action, target_number = await ai_service.decide_witch_action(
            witch, room, can_save, can_poison, killed_player_name
        )

        if action == "save" and can_save:
            witch_state.saved_player_id = room.last_killed_id
            witch_state.antidote_used = True
            witch_state.has_acted = True
            room.log(f"💊 {witch.display_name}（女巫AI）使用解药救了 {killed_player_name}")
            logger.info(f"[狼人杀] AI女巫 {witch.name} 救了 {killed_player_name}")
            # 记录到女巫的AI上下文
            if witch.ai_context:
                witch.ai_context.witch_saved_player = killed_player_name

        elif action == "poison" and can_poison and target_number:
            target_player = room.get_player_by_number(target_number)
            # 确保目标存活且不是自己
            if target_player and target_player.is_alive and target_player.id != witch.id:
                witch_state.poisoned_player_id = target_player.id
                witch_state.poison_used = True
                witch_state.has_acted = True
                room.log(f"💊 {witch.display_name}（女巫AI）使用毒药毒了 {target_player.display_name}")
                logger.info(f"[狼人杀] AI女巫 {witch.name} 毒了 {target_player.display_name}")
                # 记录到女巫的AI上下文
                if witch.ai_context:
                    witch.ai_context.witch_poisoned_player = target_player.display_name

        else:
            witch_state.has_acted = True
            room.log(f"💊 {witch.display_name}（女巫AI）选择不操作")
            logger.info(f"[狼人杀] AI女巫 {witch.name} 不操作")

        await self._finish_night(room)

    def _calculate_wait_time(self, room: "GameRoom") -> float:
        """计算等待时间"""
        witch = room.get_witch()
        if not witch:
            return random.uniform(
                self.game_manager.config.timeout_dead_min,
                self.game_manager.config.timeout_dead_max
            )

        # 女巫存活，或今晚被杀（可以救自己）
        witch_alive = witch.is_alive
        witch_killed_tonight = (room.last_killed_id == witch.id)

        if witch_alive or witch_killed_tonight:
            return self.timeout_seconds
        else:
            return random.uniform(
                self.game_manager.config.timeout_dead_min,
                self.game_manager.config.timeout_dead_max
            )

    async def _notify_witch(self, room: "GameRoom") -> None:
        """通知女巫"""
        witch = room.get_witch()
        if not witch:
            return

        # 女巫存活 或 今晚被杀（可以救自己）时才通知
        witch_killed_tonight = (room.last_killed_id == witch.id)
        if not witch.is_alive and not witch_killed_tonight:
            return

        # 使用角色类生成提示
        witch_role = WitchRole()
        prompt = witch_role.get_action_prompt(room)

        await self.message_service.send_private_message(room, witch.id, prompt)
        logger.info(f"[狼人杀] 已告知女巫 {witch.id} 夜晚信息")

    async def on_timeout(self, room: "GameRoom") -> None:
        """女巫行动超时"""
        room.witch_state.has_acted = True

        # 只有女巫存活时才发送超时提示
        if room.is_witch_alive():
            await self.message_service.announce_timeout(room, "女巫行动")

        await self._finish_night(room)

    async def on_acted(self, room: "GameRoom") -> None:
        """女巫行动完成"""
        room.cancel_timer()
        await self._finish_night(room)

    async def _finish_night(self, room: "GameRoom") -> None:
        """结束夜晚，进入白天"""
        logger.info(f"[狼人杀] 群 {room.group_id} 开始结束夜晚流程")

        # 处理女巫行动结果
        await self.game_manager.process_witch_action(room)
        logger.info(f"[狼人杀] 群 {room.group_id} 女巫行动处理完成")

        # 处理猎人死亡
        await self._handle_hunter_death(room)
        logger.info(f"[狼人杀] 群 {room.group_id} 猎人死亡处理完成，pending_shot={room.hunter_state.pending_shot_player_id}")

        # 发送天亮消息
        await self._announce_dawn(room)
        logger.info(f"[狼人杀] 群 {room.group_id} 天亮消息发送完成")

        # 检查猎人是否需要开枪（猎人开枪优先于胜负判定）
        if room.hunter_state.pending_shot_player_id:
            logger.info(f"[狼人杀] 群 {room.group_id} 等待猎人开枪")
            await self._wait_for_hunter_shot(room)
            return

        # 猎人不需要开枪时，检查游戏是否结束
        logger.info(f"[狼人杀] 群 {room.group_id} 检查胜负")
        if await self.game_manager.check_and_handle_victory(room):
            logger.info(f"[狼人杀] 群 {room.group_id} 游戏结束")
            return

        # 进入白天流程
        logger.info(f"[狼人杀] 群 {room.group_id} 进入白天阶段")
        await self._enter_day_phase(room)

    async def _handle_hunter_death(self, room: "GameRoom") -> None:
        """处理猎人死亡"""
        witch_state = room.witch_state

        # 被毒的猎人不能开枪
        if witch_state.poisoned_player_id:
            poisoned_player = room.get_player(witch_state.poisoned_player_id)
            if poisoned_player and poisoned_player.role == Role.HUNTER:
                room.hunter_state.death_type = HunterDeathType.POISON

        # 被狼杀的猎人可以开枪（未被救的情况）
        if room.last_killed_id and not witch_state.saved_player_id:
            killed_player = room.get_player(room.last_killed_id)
            if killed_player and killed_player.role == Role.HUNTER:
                room.hunter_state.pending_shot_player_id = room.last_killed_id
                room.hunter_state.death_type = HunterDeathType.WOLF

    async def _announce_dawn(self, room: "GameRoom") -> None:
        """公告天亮"""
        killed_name = None
        poisoned_name = None
        saved = bool(room.witch_state.saved_player_id)

        # 调试日志：记录关键状态
        logger.info(f"[天亮判断] last_killed_id={room.last_killed_id}, saved_player_id={room.witch_state.saved_player_id}, saved={saved}")

        if room.last_killed_id and not saved:
            killed_player = room.get_player(room.last_killed_id)
            logger.info(f"[天亮判断] killed_player={'存在' if killed_player else 'None'}, is_alive={killed_player.is_alive if killed_player else 'N/A'}")
            if killed_player:
                killed_name = killed_player.display_name
                logger.info(f"[天亮判断] 确认死亡: {killed_name}")
            else:
                logger.error(f"[BUG] last_killed_id={room.last_killed_id} 但玩家对象不存在！这会导致误判为平安夜")

        if room.witch_state.poisoned_player_id:
            poisoned_player = room.get_player(room.witch_state.poisoned_player_id)
            if poisoned_player:
                poisoned_name = poisoned_player.display_name

        logger.info(f"[天亮判断] 最终结果: killed_name={killed_name}, poisoned_name={poisoned_name}")
        await self.message_service.announce_dawn(room, killed_name, saved, poisoned_name)

        # 记录天亮事件到所有AI玩家上下文（只公布谁死了，不公布死因）
        self._record_dawn_event_to_ai(room, killed_name, poisoned_name)

    def _record_dawn_event_to_ai(self, room: "GameRoom", killed_name, poisoned_name) -> None:
        """记录天亮事件到AI上下文（只记录公开信息）"""
        dead_names = []
        if killed_name:
            dead_names.append(killed_name)
        if poisoned_name:
            dead_names.append(poisoned_name)

        if dead_names:
            event = f"第{room.current_round}夜死亡：{', '.join(dead_names)}"
        else:
            event = f"第{room.current_round}夜：平安夜"

        # 添加到所有AI玩家的上下文
        for player in room.players.values():
            if player.is_ai and player.ai_context:
                player.ai_context.add_event(event)

    async def _wait_for_hunter_shot(self, room: "GameRoom") -> None:
        """等待猎人开枪"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.wait_for_hunter_shot(room, "wolf")

    async def _enter_day_phase(self, room: "GameRoom") -> None:
        """进入白天阶段"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)

        # 第一晚被杀有遗言
        if room.is_first_night and room.last_killed_id:
            await phase_manager.enter_last_words_phase(room)
        else:
            # 禁言死亡玩家
            if room.last_killed_id:
                await BanService.ban_player(room, room.last_killed_id)
            if room.witch_state.poisoned_player_id:
                await BanService.ban_player(room, room.witch_state.poisoned_player_id)

            room.end_first_night()
            await phase_manager.enter_speaking_phase(room)
