"""夜晚-狼人行动阶段"""
import asyncio
import random
import time
from typing import TYPE_CHECKING
from astrbot.api import logger

from .base import BasePhase
from ..models import GamePhase, Role

if TYPE_CHECKING:
    from ..models import GameRoom

# AI投票前预留时间（秒）- 在超时前这么多秒强制AI投票
AI_VOTE_BEFORE_TIMEOUT_SECONDS = 30


class NightWolfPhase(BasePhase):
    """狼人行动阶段"""

    @property
    def name(self) -> str:
        return "狼人行动阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_wolf

    def _is_current_phase(self, room: "GameRoom") -> bool:
        return room.phase == GamePhase.NIGHT_WOLF

    async def on_enter(self, room: "GameRoom") -> None:
        """进入狼人行动阶段"""
        room.phase = GamePhase.NIGHT_WOLF
        room.seer_checked = False
        room.vote_state.clear_night_votes()

        # 重置狼人状态
        room.wolf_last_chat_time = None
        room.wolf_ai_voted = False  # AI是否已投票
        room.wolf_ai_chatted = False  # AI是否已密谋（防止重复）

        # 取消可能存在的旧任务
        self._cancel_ai_tasks(room)

        # 检查是否有人类狼人
        alive_wolves = room.get_alive_werewolves()
        human_wolves = [w for w in alive_wolves if not w.is_ai]
        ai_wolves = [w for w in alive_wolves if w.is_ai]

        if human_wolves:
            # 有人类狼人：启动主定时器
            await self.start_timer(room)

            # 如果有AI狼人，立即让AI发起密谋（人类需要看到AI的消息来做决策）
            if ai_wolves:
                # 启动AI投票定时器（超时前30秒触发）
                ai_vote_delay = max(self.timeout_seconds - AI_VOTE_BEFORE_TIMEOUT_SECONDS, 10)
                room.wolf_ai_vote_task = asyncio.create_task(
                    self._ai_vote_timer(room, ai_vote_delay)
                )
                # 立即让AI狼人发起密谋（不阻塞，后台执行）
                asyncio.create_task(self._initial_ai_wolf_chat(room))
        elif ai_wolves:
            # 全是AI狼人：创建后台任务处理（不使用wait_for，避免超时继续在其他阶段触发）
            room.wolf_ai_process_task = asyncio.create_task(
                self._process_all_ai_wolves_with_timeout(room)
            )

    async def _process_all_ai_wolves_with_timeout(self, room: "GameRoom") -> None:
        """带超时保护的全AI狼人处理（作为独立任务运行）"""
        try:
            # 设置60秒超时
            start_time = asyncio.get_event_loop().time()
            timeout = 60

            # 处理密谋
            if room.phase != GamePhase.NIGHT_WOLF:
                logger.info(f"[狼人杀] 群 {room.group_id} 全AI狼人处理：阶段已变更，跳过密谋")
                return
            await self._handle_ai_werewolf_chat(room)

            # 检查超时和阶段
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise asyncio.TimeoutError()
            if room.phase != GamePhase.NIGHT_WOLF:
                logger.info(f"[狼人杀] 群 {room.group_id} 全AI狼人处理：阶段已变更，跳过投票")
                return

            # 处理投票
            await self._handle_ai_werewolf_vote(room)

            # 再次检查阶段
            if room.phase != GamePhase.NIGHT_WOLF:
                logger.info(f"[狼人杀] 群 {room.group_id} 全AI狼人处理：阶段已变更，跳过结算")
                return

            # 检查投票完成
            await self._check_all_voted(room)

        except asyncio.TimeoutError:
            # 超时：检查是否还在狼人阶段
            if room.phase != GamePhase.NIGHT_WOLF:
                logger.info(f"[狼人杀] 群 {room.group_id} 全AI狼人超时但阶段已变更，忽略")
                return
            logger.error(f"[狼人杀] 群 {room.group_id} 全AI狼人处理超时，使用兜底策略")
            await self._fallback_wolf_vote(room)
            await self._finish_and_next(room)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {room.group_id} 全AI狼人处理任务被取消")

        except Exception as e:
            logger.error(f"[狼人杀] 群 {room.group_id} 全AI狼人处理异常: {e}")
            # 异常时也使用兜底策略
            if room.phase == GamePhase.NIGHT_WOLF:
                await self._fallback_wolf_vote(room)
                await self._finish_and_next(room)

    async def _process_all_ai_wolves(self, room: "GameRoom") -> None:
        """处理全AI狼人的密谋和投票（内部方法）"""
        await self._handle_ai_werewolf_chat(room)
        await self._handle_ai_werewolf_vote(room)
        await self._check_all_voted(room)

    async def _fallback_wolf_vote(self, room: "GameRoom") -> None:
        """兜底策略：随机选择一个非狼人目标"""
        from ..models import Role
        alive_wolves = room.get_alive_werewolves()
        candidates = [p for p in room.get_alive_players() if p.role != Role.WEREWOLF]

        if candidates and alive_wolves:
            target = random.choice(candidates)
            # 所有狼人都投同一个目标
            for wolf in alive_wolves:
                room.vote_state.night_votes[wolf.id] = target.id
            room.log(f"🐺 狼人AI兜底：选择刀 {target.display_name}")
            logger.info(f"[狼人杀] 狼人AI兜底投票: {target.display_name}")

    async def _ai_vote_timer(self, room: "GameRoom", delay: float) -> None:
        """AI投票定时器：在指定延迟后触发AI投票"""
        try:
            await asyncio.sleep(delay)

            # 检查是否还在狼人阶段且AI未投票
            if room.phase != GamePhase.NIGHT_WOLF:
                return
            if room.wolf_ai_voted:
                return

            logger.info(f"[狼人杀] 群 {room.group_id} AI投票定时器触发")
            await self._trigger_ai_vote_and_finish(room)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {room.group_id} AI投票定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] AI投票定时器失败: {e}")

    async def _initial_ai_wolf_chat(self, room: "GameRoom") -> None:
        """进入狼人阶段时，AI狼人主动发起密谋（给人类队友看）"""
        try:
            # 等待一小段时间，让阶段切换消息先发出
            await asyncio.sleep(2)

            # 检查是否还在狼人阶段
            if room.phase != GamePhase.NIGHT_WOLF:
                return

            # 防止重复密谋
            if room.wolf_ai_chatted:
                return
            room.wolf_ai_chatted = True

            logger.info(f"[狼人杀] 群 {room.group_id} AI狼人开始主动密谋")
            await self._handle_ai_werewolf_chat(room)
            logger.info(f"[狼人杀] 群 {room.group_id} AI狼人主动密谋完成")

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {room.group_id} AI狼人主动密谋被取消")
        except Exception as e:
            logger.error(f"[狼人杀] AI狼人主动密谋失败: {e}")

    async def _handle_ai_werewolf_chat(self, room: "GameRoom") -> None:
        """AI狼人密谋：生成消息并发给队友"""
        ai_service = self.game_manager.ai_player_service
        alive_wolves = room.get_alive_werewolves()

        for wolf in alive_wolves:
            if not wolf.is_ai:
                continue

            # 更新AI上下文
            ai_service.update_ai_context(wolf, room)

            # 延迟模拟思考
            await asyncio.sleep(random.uniform(1, 3))

            # 生成密谋消息
            chat_message = await ai_service.decide_werewolf_chat(wolf, room)
            if not chat_message:
                continue

            # 记录日志
            room.log(f"💬 {wolf.display_name}（狼人AI）密谋：{chat_message}")
            logger.info(f"[狼人杀] AI狼人 {wolf.name} 密谋：{chat_message}")

            # 发送给其他狼人队友
            teammates = [w for w in alive_wolves if w.id != wolf.id]
            for teammate in teammates:
                if teammate.is_ai:
                    # AI队友：加入上下文
                    if teammate.ai_context:
                        teammate.ai_context.add_wolf_chat(
                            wolf.display_name,
                            chat_message,
                            room.current_round
                        )
                else:
                    # 人类队友：发送私聊
                    msg = f"🐺 队友 {wolf.display_name} 说：\n{chat_message}"
                    await self.message_service.send_private_message(room, teammate.id, msg)

    async def _handle_ai_werewolf_vote(self, room: "GameRoom") -> None:
        """AI狼人投票：基于密谋信息决策击杀目标"""
        ai_service = self.game_manager.ai_player_service

        for wolf in room.get_alive_werewolves():
            if not wolf.is_ai:
                continue

            # 再次更新上下文（包含刚收到的密谋消息）
            ai_service.update_ai_context(wolf, room)

            # 延迟模拟思考
            await asyncio.sleep(random.uniform(2, 4))

            # AI决策击杀目标
            target_number = await ai_service.decide_werewolf_kill(wolf, room)
            if target_number:
                target_player = room.get_player_by_number(target_number)
                if target_player and target_player.is_alive:
                    room.vote_state.night_votes[wolf.id] = target_player.id
                    room.log(f"🐺 {wolf.display_name}（狼人AI）选择刀 {target_player.display_name}")
                    logger.info(f"[狼人杀] AI狼人 {wolf.name} 选择击杀 {target_player.display_name}")

                    # 同步刀人选择到其他狼人AI上下文
                    for teammate in room.get_alive_werewolves():
                        if teammate.id != wolf.id and teammate.is_ai and teammate.ai_context:
                            teammate.ai_context.add_event(f"狼队友 {wolf.display_name} 选择刀 {target_player.display_name}")

    async def _check_all_voted(self, room: "GameRoom") -> bool:
        """检查是否所有狼人都已投票"""
        alive_wolves = room.get_alive_werewolves()
        voted_count = len(room.vote_state.night_votes)

        if voted_count >= len(alive_wolves):
            room.cancel_timer()
            await self._finish_and_next(room)
            return True
        return False

    async def on_timeout(self, room: "GameRoom") -> None:
        """狼人行动超时"""
        self._cancel_ai_vote_timer(room)
        await self.message_service.announce_timeout(room, "狼人行动")

        # 检查是否有人类狼人投过票，如果有则触发AI狼人投票
        alive_wolves = room.get_alive_werewolves()
        human_wolves = [w for w in alive_wolves if not w.is_ai]
        ai_wolves = [w for w in alive_wolves if w.is_ai]

        # 只要有任何一个人类狼人投过票，就触发AI狼人投票
        human_voted = any(w.id in room.vote_state.night_votes for w in human_wolves)

        if human_voted and ai_wolves and not room.wolf_ai_voted:
            logger.info(f"[狼人杀] 群 {room.group_id} 人类狼人已投票，触发AI狼人投票")
            room.wolf_ai_voted = True
            await self._handle_ai_werewolf_vote(room)

        if room.vote_state.night_votes:
            # 有投票，处理
            await self._finish_and_next(room)
        else:
            # 无投票，记录日志，直接进入预言家阶段
            room.log("🐺 狼人超时：未投票，今晚无人被刀")
            await self._enter_seer_phase(room)

    async def on_human_wolves_voted(self, room: "GameRoom") -> None:
        """所有人类狼人投票完成，开始处理AI狼人"""
        alive_wolves = room.get_alive_werewolves()
        ai_wolves = [w for w in alive_wolves if w.is_ai]

        if not ai_wolves:
            # 没有AI狼人，直接结束
            self._cancel_ai_vote_timer(room)
            room.cancel_timer()
            await self._finish_and_next(room)
            return

        # 防止重复处理
        if room.wolf_ai_chatted:
            logger.info(f"[狼人杀] 群 {room.group_id} AI狼人已处理过，跳过")
            return
        room.wolf_ai_chatted = True

        # AI发出密谋
        await self._handle_ai_werewolf_chat(room)
        logger.info(f"[狼人杀] 群 {room.group_id} AI狼人密谋完成")

        # 人类都投完了，AI也直接投票并结束
        await self._trigger_ai_vote_and_finish(room)

    def _cancel_ai_tasks(self, room: "GameRoom") -> None:
        """取消所有AI相关的后台任务"""
        # 取消AI投票定时器
        if hasattr(room, 'wolf_ai_vote_task') and room.wolf_ai_vote_task:
            if not room.wolf_ai_vote_task.done():
                room.wolf_ai_vote_task.cancel()
            room.wolf_ai_vote_task = None

        # 取消全AI处理任务
        if hasattr(room, 'wolf_ai_process_task') and room.wolf_ai_process_task:
            if not room.wolf_ai_process_task.done():
                room.wolf_ai_process_task.cancel()
            room.wolf_ai_process_task = None

    def _cancel_ai_vote_timer(self, room: "GameRoom") -> None:
        """取消AI投票定时器（向后兼容）"""
        self._cancel_ai_tasks(room)

    async def _trigger_ai_vote_and_finish(self, room: "GameRoom") -> None:
        """触发AI投票并结束狼人阶段"""
        # 检查是否还在狼人阶段
        if room.phase != GamePhase.NIGHT_WOLF:
            return

        # 标记AI已投票
        room.wolf_ai_voted = True

        # AI投票
        await self._handle_ai_werewolf_vote(room)

        # 结束狼人阶段
        self._cancel_ai_vote_timer(room)
        room.cancel_timer()
        await self._finish_and_next(room)

    async def trigger_ai_wolf_vote(self, room: "GameRoom") -> None:
        """触发AI狼人投票（可多次调用，AI会根据最新信息重新决策）"""
        if room.phase != GamePhase.NIGHT_WOLF:
            return

        # AI投票（每次人类投票后都重新决策）
        await self._handle_ai_werewolf_vote(room)
        logger.info(f"[狼人杀] 群 {room.group_id} AI狼人已投票/重新决策")

    async def on_all_voted(self, room: "GameRoom") -> None:
        """所有狼人投票完成（包括AI）"""
        self._cancel_ai_vote_timer(room)
        room.cancel_timer()
        await self._finish_and_next(room)

    async def _finish_and_next(self, room: "GameRoom") -> None:
        """完成狼人阶段，进入下一阶段"""
        # 只取消AI投票定时器，不取消process_task（可能是当前任务自己）
        if hasattr(room, 'wolf_ai_vote_task') and room.wolf_ai_vote_task:
            if not room.wolf_ai_vote_task.done():
                room.wolf_ai_vote_task.cancel()
            room.wolf_ai_vote_task = None

        # 处理投票结果
        await self.game_manager.process_night_kill(room)

        # 检查游戏是否结束
        if await self.game_manager.check_and_handle_victory(room):
            return

        # 进入预言家阶段
        await self._enter_seer_phase(room)

    async def _enter_seer_phase(self, room: "GameRoom") -> None:
        """进入预言家验人阶段"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.enter_seer_phase(room)
