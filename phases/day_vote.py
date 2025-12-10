"""白天投票阶段"""
import asyncio
import random
from typing import TYPE_CHECKING, Dict, List
from astrbot.api import logger

from .base import BasePhase
from ..models import GamePhase, Role
from ..roles import HunterDeathType
from ..services import BanService

if TYPE_CHECKING:
    from ..models import GameRoom

# AI投票前预留时间（秒）- 在超时前这么多秒强制AI投票
AI_VOTE_BEFORE_TIMEOUT_SECONDS = 30


class DayVotePhase(BasePhase):
    """白天投票阶段"""

    @property
    def name(self) -> str:
        return "投票阶段"

    @property
    def timeout_seconds(self) -> int:
        return self.game_manager.config.timeout_vote

    def _is_current_phase(self, room: "GameRoom") -> bool:
        return room.phase == GamePhase.DAY_VOTE

    async def on_enter(self, room: "GameRoom") -> None:
        """进入投票阶段"""
        room.phase = GamePhase.DAY_VOTE
        room.vote_state.day_votes.clear()
        room.day_ai_voted = False  # AI是否已投票
        room.vote_discussion = []  # 投票阶段讨论记录

        # 发送投票开始消息
        await self.message_service.announce_vote_start(room)

        # 解除全群禁言
        await BanService.set_group_whole_ban(room, False)

        # 检查是否有AI玩家
        ai_players = [p for p in room.get_alive_players() if p.is_ai]
        human_players = [p for p in room.get_alive_players() if not p.is_ai]

        if not human_players:
            # 全是AI，直接投票（带超时保护）
            logger.info(f"[狼人杀] 群 {room.group_id} 全AI投票开始，共 {len(ai_players)} 个AI")
            try:
                await asyncio.wait_for(
                    self._handle_ai_votes(room),
                    timeout=120  # 2分钟超时
                )
                logger.info(f"[狼人杀] 群 {room.group_id} 全AI投票完成，票数: {len(room.vote_state.day_votes)}")
            except asyncio.TimeoutError:
                logger.error(f"[狼人杀] 群 {room.group_id} 全AI投票超时")
            except Exception as e:
                logger.error(f"[狼人杀] 群 {room.group_id} 全AI投票异常: {e}")

            # 无论成功与否，都处理投票结果
            logger.info(f"[狼人杀] 群 {room.group_id} 准备处理全AI投票结果")
            await self._process_vote_result(room)
            logger.info(f"[狼人杀] 群 {room.group_id} 全AI投票结果处理完成")
            return

        # 启动定时器（带30秒AI发言和投票）
        await self._start_vote_timer(room, has_ai=len(ai_players) > 0)

    async def enter_pk_vote(self, room: "GameRoom") -> None:
        """进入PK投票"""
        room.phase = GamePhase.DAY_VOTE
        room.vote_state.is_pk_vote = True
        room.vote_state.day_votes.clear()
        room.day_ai_voted = False
        room.vote_discussion = []  # 投票阶段讨论记录

        # 发送PK投票提示
        pk_names = []
        for pid in room.vote_state.pk_players:
            player = room.get_player(pid)
            if player:
                pk_names.append(player.display_name)

        await self.message_service.announce_pk_vote_start(room, pk_names)

        # 解除全群禁言
        await BanService.set_group_whole_ban(room, False)

        # 检查是否有AI玩家
        ai_players = [p for p in room.get_alive_players() if p.is_ai]
        human_players = [p for p in room.get_alive_players() if not p.is_ai]

        if not human_players:
            # 全是AI，直接投票（带超时保护）
            pk_numbers = [room.get_player(pid).number for pid in room.vote_state.pk_players if room.get_player(pid)]
            try:
                await asyncio.wait_for(
                    self._handle_ai_votes(room, is_pk=True, pk_candidates=pk_numbers),
                    timeout=120  # 2分钟超时
                )
            except asyncio.TimeoutError:
                logger.error(f"[狼人杀] 群 {room.group_id} 全AI PK投票超时")

            # 无论成功与否，都处理投票结果
            await self._process_vote_result(room)
            return

        # 启动定时器（带30秒AI投票）
        await self._start_vote_timer(room, has_ai=len(ai_players) > 0)

    async def _handle_ai_votes(self, room: "GameRoom", is_pk: bool = False, pk_candidates: List[int] = None) -> None:
        """处理AI玩家投票"""
        ai_service = self.game_manager.ai_player_service

        for player in room.get_alive_players():
            if not player.is_ai:
                continue

            pk_tag = "PK" if is_pk else ""

            try:
                # 更新AI上下文
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论）
                if hasattr(room, 'vote_discussion') and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(msg['player'], msg['content'])

                # AI同时生成讨论和投票决策
                discussion, target_number = await ai_service.decide_vote(player, room, is_pk, pk_candidates)

                # 先发讨论
                if discussion:
                    await self.message_service.send_group_message(
                        room, f"{player.display_name}：{discussion}"
                    )
                    # 同步投票讨论到所有AI上下文（使用专门的投票讨论字段）
                    for p in room.players.values():
                        if p.is_ai and p.ai_context:
                            p.ai_context.add_vote_discussion(player.display_name, discussion[:120])

                if target_number:
                    target_player = room.get_player_by_number(target_number)
                    if target_player and target_player.is_alive and target_player.id != player.id:
                        room.vote_state.day_votes[player.id] = target_player.id

                        # 发送群消息显示投票
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 投票给 {target_player.display_name}"
                        )

                        # 记录日志
                        room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）投给 {target_player.display_name}")
                        logger.info(f"[狼人杀] AI玩家 {player.name} 投票给 {target_player.display_name}")

                        # 记录到所有AI上下文
                        for p in room.players.values():
                            if p.is_ai and p.ai_context:
                                p.ai_context.add_vote(player.display_name, target_player.display_name, is_pk)
                    else:
                        # 目标无效，当作弃票
                        room.vote_state.day_votes[player.id] = "ABSTAIN"
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 选择弃票"
                        )
                        room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票")
                else:
                    # AI选择弃票 - 记录为投给"ABSTAIN"表示弃票
                    room.vote_state.day_votes[player.id] = "ABSTAIN"
                    await self.message_service.send_group_message(
                        room, f"🗳️ {player.display_name} 选择弃票"
                    )
                    room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票")
                    logger.info(f"[狼人杀] AI玩家 {player.name} 选择弃票")

            except Exception as e:
                # 单个AI投票失败不影响其他AI，记录弃票
                logger.error(f"[狼人杀] AI玩家 {player.name} 投票异常: {e}")
                room.vote_state.day_votes[player.id] = "ABSTAIN"
                await self.message_service.send_group_message(
                    room, f"🗳️ {player.display_name} 选择弃票"
                )
                room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票（异常）")

    async def _check_all_voted(self, room: "GameRoom") -> bool:
        """检查是否所有人都投票了"""
        if len(room.vote_state.day_votes) >= room.alive_count:
            # 先处理投票结果，最后再取消定时器
            # 这样即使在定时器内部调用，也能完成处理
            await self._process_vote_result(room)
            room.cancel_timer()  # 处理完成后再取消定时器
            return True
        return False

    async def _start_vote_timer(self, room: "GameRoom", has_ai: bool = False) -> None:
        """启动投票定时器（超时前30秒触发AI发言和投票）"""

        async def vote_timer():
            try:
                timeout = self.timeout_seconds

                # 计算AI行动时间点（超时前30秒）
                ai_action_delay = max(timeout - AI_VOTE_BEFORE_TIMEOUT_SECONDS, 10)

                if has_ai and timeout > AI_VOTE_BEFORE_TIMEOUT_SECONDS:
                    # 等待到AI行动时间点
                    await asyncio.sleep(ai_action_delay)

                    if room.group_id not in self.game_manager.rooms:
                        return
                    if room.phase != GamePhase.DAY_VOTE:
                        return

                    # 触发AI发言和投票（如果还没投）
                    if not room.day_ai_voted:
                        room.day_ai_voted = True
                        logger.info(f"[狼人杀] 群 {room.group_id} 触发AI发言和投票")

                        # 获取PK候选人
                        pk_candidates = None
                        if room.vote_state.is_pk_vote:
                            pk_candidates = [room.get_player(pid).number for pid in room.vote_state.pk_players if room.get_player(pid)]

                        # 先让AI发言，再投票
                        await self._handle_ai_discussion_and_votes(room, room.vote_state.is_pk_vote, pk_candidates)

                        # 检查是否所有人都投票了
                        if await self._check_all_voted(room):
                            return

                        # 发送剩余时间提醒
                        voted = len(room.vote_state.day_votes)
                        total = room.alive_count
                        await self.message_service.announce_vote_reminder(room, voted, total)

                    # 等待剩余时间
                    await asyncio.sleep(AI_VOTE_BEFORE_TIMEOUT_SECONDS)
                else:
                    # 没有AI或超时时间太短，直接等待
                    await asyncio.sleep(timeout)

                if room.group_id not in self.game_manager.rooms:
                    return
                if room.phase != GamePhase.DAY_VOTE:
                    return

                await self.on_timeout(room)

            except asyncio.CancelledError:
                logger.info(f"[狼人杀] 群 {room.group_id} 投票定时器已取消")
            except Exception as e:
                logger.error(f"[狼人杀] 投票超时处理失败: {e}")

        task = asyncio.create_task(vote_timer())
        room.set_timer(task)

    async def _handle_ai_discussion_and_votes(self, room: "GameRoom", is_pk: bool = False, pk_candidates: List[int] = None) -> None:
        """处理AI发言和投票"""
        ai_service = self.game_manager.ai_player_service

        for player in room.get_alive_players():
            if not player.is_ai:
                continue

            try:
                # 更新AI上下文，包含投票阶段的讨论
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论）
                if hasattr(room, 'vote_discussion') and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(msg['player'], msg['content'])

                # AI同时生成讨论和投票决策（确保一致性）
                discussion, target_number = await ai_service.decide_vote(player, room, is_pk, pk_candidates)

                # 先发表讨论
                if discussion:
                    await self.message_service.send_group_message(
                        room, f"{player.display_name}：{discussion}"
                    )
                    logger.info(f"[狼人杀] AI玩家 {player.name} 投票讨论: {discussion[:50]}...")
                    # 同步投票讨论到所有AI上下文（使用专门的投票讨论字段）
                    for p in room.players.values():
                        if p.is_ai and p.ai_context:
                            p.ai_context.add_vote_discussion(player.display_name, discussion[:120])

                pk_tag = "PK" if is_pk else ""

                if target_number:
                    target_player = room.get_player_by_number(target_number)
                    if target_player and target_player.is_alive and target_player.id != player.id:
                        room.vote_state.day_votes[player.id] = target_player.id

                        # 发送群消息显示投票
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 投票给 {target_player.display_name}"
                        )

                        # 记录日志
                        room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）投给 {target_player.display_name}")
                        logger.info(f"[狼人杀] AI玩家 {player.name} 投票给 {target_player.display_name}")

                        # 记录到所有AI上下文
                        for p in room.players.values():
                            if p.is_ai and p.ai_context:
                                p.ai_context.add_vote(player.display_name, target_player.display_name, is_pk)

                        # 检查是否所有��都投完了
                        if await self._check_all_voted(room):
                            return
                    else:
                        # 目标无效（已死亡/投自己/不存在），当作弃票
                        room.vote_state.day_votes[player.id] = "ABSTAIN"
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 选择弃票"
                        )
                        room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票（目标无效）")
                        logger.info(f"[狼人杀] AI玩家 {player.name} 投票目标无效，转为弃票")

                        # 检查是否所有人都投完了
                        if await self._check_all_voted(room):
                            return
                else:
                    # AI选择弃票 - 记录为投给"ABSTAIN"表示弃票
                    room.vote_state.day_votes[player.id] = "ABSTAIN"
                    await self.message_service.send_group_message(
                        room, f"🗳️ {player.display_name} 选择弃票"
                    )
                    room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票")
                    logger.info(f"[狼人杀] AI玩家 {player.name} 选择弃票")

                    # 检查是否所有人都投完了
                    if await self._check_all_voted(room):
                        return

            except Exception as e:
                # 单个AI失败不影响其他AI
                logger.error(f"[狼人杀] AI玩家 {player.name} 投票异常: {e}")
                room.vote_state.day_votes[player.id] = "ABSTAIN"

    async def on_timeout(self, room: "GameRoom") -> None:
        """投票超时"""
        voted = len(room.vote_state.day_votes)
        total = room.alive_count

        await self.message_service.send_group_message(
            room, f"⏰ 投票超时！已有 {voted}/{total} 人投票，自动结算。"
        )

        if room.vote_state.day_votes:
            await self._process_vote_result(room)
        else:
            # 无人投票，进入下一夜晚
            room.log("📊 投票超时：无人投票，本轮无人出局")
            await self._enter_night(room)

    async def on_all_voted(self, room: "GameRoom") -> None:
        """所有人投票完成"""
        await self._process_vote_result(room)
        room.cancel_timer()

    async def _process_vote_result(self, room: "GameRoom") -> None:
        """处理投票结果"""
        logger.info(f"[狼人杀] 群 {room.group_id} 开始处理投票结果，当前阶段: {room.phase}")

        # 先保存是否是PK投票（process_day_vote 可能会清除这个状态）
        was_pk_vote = room.vote_state.is_pk_vote

        # 先统计投票，用于生成图片（排除弃票）
        votes = room.vote_state.day_votes
        logger.info(f"[狼人杀] 群 {room.group_id} 投票数据: {len(votes)} 票")
        vote_counts: Dict[str, int] = {}
        voters_map: Dict[str, List[str]] = {}  # target_id -> [voter_names]

        for voter_id, target_id in votes.items():
            if target_id == "ABSTAIN":
                continue  # 跳过弃票
            vote_counts[target_id] = vote_counts.get(target_id, 0) + 1
            voter = room.get_player(voter_id)
            if voter:
                if target_id not in voters_map:
                    voters_map[target_id] = []
                voters_map[target_id].append(voter.display_name)

        # 处理投票结果
        exiled_id, is_tie = await self.game_manager.process_day_vote(room)
        logger.info(f"[狼人杀] 群 {room.group_id} 投票处理结果: exiled_id={exiled_id}, is_tie={is_tie}")

        if is_tie:
            # 平票情况 - 发送投票结果图片（无人被放逐）
            # 使用保存的 was_pk_vote，因为 process_day_vote 可能已经清除了状态
            await self.message_service.announce_vote_result(
                room, vote_counts, voters_map, None, was_pk_vote
            )

            # 同步平票信息到AI上下文
            pk_names = [room.get_player(pid).display_name for pid in room.vote_state.pk_players if room.get_player(pid)]
            for p in room.players.values():
                if p.is_ai and p.ai_context:
                    if was_pk_vote:
                        p.ai_context.add_event(f"PK投票平票，无人出局")
                    else:
                        p.ai_context.add_event(f"投票平票，{', '.join(pk_names)} 进入PK")

            if not was_pk_vote:
                # 第一次平票，进入PK
                from .day_speaking import DaySpeakingPhase
                speaking_phase = DaySpeakingPhase(self.game_manager)
                await speaking_phase.enter_pk_phase(room, room.vote_state.pk_players)
            else:
                # PK后仍平票，无人出局
                room.log("📊 PK投票结果：仍然平票，本轮无人出局")
                await self._enter_night(room)
            return

        if not exiled_id:
            # 同步无人出局到AI上下文
            for p in room.players.values():
                if p.is_ai and p.ai_context:
                    p.ai_context.add_event("投票结果：本轮无人出局")
            await self._enter_night(room)
            return

        # 有人被放逐
        exiled_player = room.get_player(exiled_id)
        if not exiled_player:
            logger.error(f"[狼人杀] 群 {room.group_id} 无法找到被放逐玩家 {exiled_id}，跳过")
            await self._enter_night(room)
            return

        # 记录日志（这里用 was_pk_vote 因为状态可能已被清除）
        room.log(f"📊 {'PK' if was_pk_vote else ''}投票结果：{exiled_player.display_name} 被放逐")

        # 发送投票结果
        await self.message_service.announce_vote_result(
            room, vote_counts, voters_map, exiled_player.display_name, was_pk_vote
        )

        # 公告放逐结果
        await self.message_service.announce_exile(room, exiled_player.display_name, was_pk_vote)

        # 同步放逐结果到AI上下文
        for p in room.players.values():
            if p.is_ai and p.ai_context:
                p.ai_context.add_event(f"投票放逐：{exiled_player.display_name} 被放逐出局")

        # 检查是否是猎人
        if exiled_player.role == Role.HUNTER:
            room.last_killed_id = exiled_id  # 记录被放逐的猎人ID
            room.hunter_state.pending_shot_player_id = exiled_id
            room.hunter_state.death_type = HunterDeathType.VOTE
            await self._wait_for_hunter_shot(room)
            return

        # 检查游戏是否结束
        if await self.game_manager.check_and_handle_victory(room):
            return

        # 进入遗言阶段
        room.last_killed_id = exiled_id
        room.last_words_from_vote = True
        await self._enter_last_words(room)

    async def _wait_for_hunter_shot(self, room: "GameRoom") -> None:
        """等待猎人开枪"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.wait_for_hunter_shot(room, "vote")

    async def _enter_last_words(self, room: "GameRoom") -> None:
        """进入遗言阶段"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.enter_last_words_phase(room)

    async def _enter_night(self, room: "GameRoom") -> None:
        """进入夜晚"""
        from .phase_manager import PhaseManager
        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.enter_night_phase(room)
