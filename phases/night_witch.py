"""夜晚-女巫行动阶段"""

import asyncio
import random
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase
from ..roles import HunterDeathType, WitchRole
from ..services import BanService
from .base import BasePhase

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

    def _is_current_phase(
        self, room: "GameRoom", round_number: int | None = None
    ) -> bool:
        """Check room identity, phase, and the originating night when supplied."""
        return (
            self.game_manager.rooms.get(room.group_id) is room
            and room.phase == GamePhase.NIGHT_WITCH
            and (round_number is None or room.current_round == round_number)
        )

    async def on_enter(self, room: "GameRoom") -> None:
        """进入女巫行动阶段"""
        room.cancel_timer()
        room.phase = GamePhase.NIGHT_WITCH
        round_number = room.current_round
        room.witch_state.reset_night()

        witch = room.get_witch()

        # 如果游戏中没有女巫角色，直接跳过
        if not witch:
            logger.info(f"[狼人杀] 群 {room.group_id} 没有女巫角色，跳过女巫阶段")
            await self._finish_night(room)
            return

        # 发送群提示
        await self.message_service.announce_witch_phase(room)
        if not self._is_current_phase(room, round_number):
            return

        # 如果女巫是AI且存活或今晚被杀，自动处理
        witch_killed_tonight = room.last_killed_id == witch.id
        if witch.is_ai and (witch.is_alive or witch_killed_tonight):
            await self._handle_ai_witch(room, witch)
            return

        # 给女巫发私聊
        await self._notify_witch(room)
        if not self._is_current_phase(room, round_number):
            return

        # 计算等待时间
        wait_time = self._calculate_wait_time(room)

        # 启动定时器
        await self.start_timer(room, wait_time)

    async def _handle_ai_witch(self, room: "GameRoom", witch) -> None:
        """处理AI女巫的行动"""
        ai_service = self.game_manager.ai_player_service
        witch_state = room.witch_state
        round_number = room.current_round

        # 更新AI上下文
        ai_service.update_ai_context(witch, room)

        # 延迟模拟思考
        await asyncio.sleep(random.uniform(3, 6))
        if (
            not self._is_current_phase(room, round_number)
            or room.night_settling_round == round_number
        ):
            return

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
        if (
            not self._is_current_phase(room, round_number)
            or room.night_settling_round == round_number
            or witch_state.has_acted
        ):
            return

        if action == "save" and can_save:
            witch_state.saved_player_id = room.last_killed_id
            witch_state.antidote_used = True
            witch_state.has_acted = True
            room.log(
                f"💊 {witch.display_name}（女巫AI）使用解药救了 {killed_player_name}"
            )
            logger.info(f"[狼人杀] AI女巫 {witch.name} 救了 {killed_player_name}")
            # 记录到女巫的AI上下文
            if witch.ai_context:
                witch.ai_context.witch_saved_player = killed_player_name
                witch.ai_context.witch_antidote_used = True
                witch.ai_context.record_private_action("save", killed_player_name)

        elif action == "poison" and can_poison and target_number:
            target_player = room.get_player_by_number(target_number)
            # 确保目标存活且不是自己
            if (
                target_player
                and target_player.is_alive
                and target_player.id != witch.id
            ):
                witch_state.poisoned_player_id = target_player.id
                witch_state.poison_used = True
                witch_state.has_acted = True
                room.log(
                    f"💊 {witch.display_name}（女巫AI）使用毒药毒了 {target_player.display_name}"
                )
                logger.info(
                    f"[狼人杀] AI女巫 {witch.name} 毒了 {target_player.display_name}"
                )
                # 记录到女巫的AI上下文
                if witch.ai_context:
                    witch.ai_context.witch_poisoned_player = target_player.display_name
                    witch.ai_context.witch_poison_used = True
                    witch.ai_context.record_private_action(
                        "poison", target_player.display_name
                    )

        else:
            witch_state.has_acted = True
            if witch.ai_context:
                witch.ai_context.record_private_action("pass", None)
            room.log(f"💊 {witch.display_name}（女巫AI）选择不操作")
            logger.info(f"[狼人杀] AI女巫 {witch.name} 不操作")

        await self._finish_night(room)

    def _calculate_wait_time(self, room: "GameRoom") -> float:
        """计算等待时间"""
        witch = room.get_witch()
        if not witch:
            return random.uniform(
                self.game_manager.config.timeout_dead_min,
                self.game_manager.config.timeout_dead_max,
            )

        # 女巫存活，或今晚被杀（可以救自己）
        witch_alive = witch.is_alive
        witch_killed_tonight = room.last_killed_id == witch.id

        if witch_alive or witch_killed_tonight:
            return self.timeout_seconds
        else:
            return random.uniform(
                self.game_manager.config.timeout_dead_min,
                self.game_manager.config.timeout_dead_max,
            )

    async def _notify_witch(self, room: "GameRoom") -> None:
        """通知女巫"""
        witch = room.get_witch()
        if not witch:
            return

        # 女巫存活 或 今晚被杀（可以救自己）时才通知
        witch_killed_tonight = room.last_killed_id == witch.id
        if not witch.is_alive and not witch_killed_tonight:
            return

        # 使用角色类生成提示
        witch_role = WitchRole()
        prompt = witch_role.get_action_prompt(room)

        if not self._is_current_phase(room):
            return
        await self.message_service.send_private_message(room, witch.id, prompt)
        logger.info(f"[狼人杀] 已告知女巫 {witch.id} 夜晚信息")

    async def on_timeout(self, room: "GameRoom") -> None:
        """Complete an unclaimed witch phase after its timer expires."""
        if not self._is_current_phase(room):
            return
        room.witch_state.has_acted = True
        await self._finish_night(room, timed_out=True)

    async def on_acted(self, room: "GameRoom") -> None:
        """Settle the witch's submitted action once."""
        await self._finish_night(room)

    async def _finish_night(self, room: "GameRoom", *, timed_out: bool = False) -> None:
        """Claim night settlement before applying deaths or publishing dawn.

        Args:
            room: Active room whose night is being settled.
            timed_out: Whether a living witch's action timed out.
        """
        round_number = room.current_round
        if (
            not self._is_current_phase(room, round_number)
            or room.night_settling_round == round_number
        ):
            return
        room.night_settling_round = round_number
        room.cancel_timer()
        if timed_out and room.is_witch_alive():
            await self.message_service.announce_timeout(room, "女巫行动")
            if not self._is_current_phase(room, round_number):
                return
        await self.game_manager.process_witch_action(room)
        if not self._is_current_phase(room, round_number):
            return
        await self._handle_hunter_death(room)
        if not self._is_current_phase(room, round_number):
            return
        await self._announce_dawn(room)
        if not self._is_current_phase(room, round_number):
            return
        if room.hunter_state.pending_shot_player_id:
            await self._wait_for_hunter_shot(room)
            return
        if await self.game_manager.check_and_handle_victory(room):
            return
        if not self._is_current_phase(room, round_number):
            return
        await self._enter_day_phase(room)

    async def _handle_hunter_death(self, room: "GameRoom") -> None:
        """Let poison override a simultaneous wolf attack on the hunter."""
        hunter = room.get_hunter()
        if not hunter or hunter.is_alive or room.hunter_state.has_shot:
            return
        if room.witch_state.poisoned_player_id == hunter.id:
            room.hunter_state.pending_shot_player_id = None
            room.hunter_state.death_type = HunterDeathType.POISON
        elif room.last_killed_id == hunter.id:
            room.hunter_state.pending_shot_player_id = hunter.id
            room.hunter_state.death_type = HunterDeathType.WOLF

    async def _announce_dawn(self, room: "GameRoom") -> None:
        """Publish each death once, without disclosing its cause."""
        round_number = room.current_round
        killed_player = room.get_player(room.last_killed_id)
        poisoned_player = room.get_player(room.witch_state.poisoned_player_id)
        killed_name = (
            killed_player.display_name
            if killed_player and not killed_player.is_alive
            else None
        )
        poisoned_name = (
            poisoned_player.display_name
            if poisoned_player
            and not poisoned_player.is_alive
            and poisoned_player.id != room.last_killed_id
            else None
        )
        await self.message_service.announce_dawn(
            room, killed_name, bool(room.witch_state.saved_player_id), poisoned_name
        )
        if not self._is_current_phase(room, round_number):
            return
        self._record_dawn_event_to_ai(room, killed_name, poisoned_name)

    def _record_dawn_event_to_ai(
        self, room: "GameRoom", killed_name, poisoned_name
    ) -> None:
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
                player.ai_context.current_round = room.current_round
                player.ai_context.add_event(event)

    async def _wait_for_hunter_shot(self, room: "GameRoom") -> None:
        """等待猎人开枪"""
        from .phase_manager import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.wait_for_hunter_shot(room, "wolf")

    async def _enter_day_phase(self, room: "GameRoom") -> None:
        """Allow first-night last words only for an unpoisoned wolf victim."""
        from .phase_manager import PhaseManager

        round_number = room.current_round
        phase_manager = PhaseManager(self.game_manager)
        last_words_id = (
            room.last_killed_id
            if room.is_first_night
            and room.last_killed_id != room.witch_state.poisoned_player_id
            else None
        )
        for player_id in dict.fromkeys(
            (room.last_killed_id, room.witch_state.poisoned_player_id)
        ):
            if player_id and player_id != last_words_id:
                await BanService.ban_player(room, player_id)
                if not self._is_current_phase(room, round_number):
                    return
        if last_words_id:
            room.last_words_from_vote = False
            room.last_words_player_id = last_words_id
            await phase_manager.enter_last_words_phase(room)
        else:
            room.end_first_night()
            await phase_manager.enter_speaking_phase(room)
