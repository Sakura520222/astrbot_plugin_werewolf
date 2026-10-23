"""白天投票阶段"""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..models import GamePhase, Role
from ..services import BanService
from .base import BasePhase

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

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
            logger.info(
                f"[狼人杀] 群 {room.group_id} 全AI投票开始，共 {len(ai_players)} 个AI"
            )
            try:
                await asyncio.wait_for(
                    self._handle_ai_votes(room),
                    timeout=120,  # 2分钟超时
                )
                logger.info(
                    f"[狼人杀] 群 {room.group_id} 全AI投票完成，票数: {len(room.vote_state.day_votes)}"
                )
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
            pk_numbers = [
                room.get_player(pid).number
                for pid in room.vote_state.pk_players
                if room.get_player(pid)
            ]
            try:
                await asyncio.wait_for(
                    self._handle_ai_votes(room, is_pk=True, pk_candidates=pk_numbers),
                    timeout=120,  # 2分钟超时
                )
            except asyncio.TimeoutError:
                logger.error(f"[狼人杀] 群 {room.group_id} 全AI PK投票超时")

            # 无论成功与否，都处理投票结果
            await self._process_vote_result(room)
            return

        # 启动定时器（带30秒AI投票）
        await self._start_vote_timer(room, has_ai=len(ai_players) > 0)

    async def _handle_ai_votes(
        self, room: "GameRoom", is_pk: bool = False, pk_candidates: list[int] = None
    ) -> None:
        """处理AI玩家投票"""
        ai_service = self.game_manager.ai_player_service
        ai_players = [player for player in room.get_alive_players() if player.is_ai]
        if not ai_players:
            return

        pk_tag = "PK" if is_pk else ""

        # ===== 第一阶段：所有AI依次发言 =====
        logger.info(
            f"[狼人杀] 群 {room.group_id} AI投票讨论开始，共 {len(ai_players)} 个AI"
        )
        for player in ai_players:
            try:
                # 更新AI上下文
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论）
                if hasattr(room, "vote_discussion") and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(
                                msg["player"], msg["content"]
                            )

                # 生成讨论内容
                discussion, _ = await ai_service.decide_vote(
                    player, room, is_pk, pk_candidates
                )

                # 发表讨论
                if discussion:
                    await self.message_service.send_group_message(
                        room, f"{player.display_name}：{discussion}"
                    )
                    # 同步到房间讨论记录
                    if hasattr(room, "vote_discussion"):
                        room.vote_discussion.append(
                            {"player": player.display_name, "content": discussion[:100]}
                        )
                    # 同步投票讨论到所有AI上下文（使用专门的投票讨论字段）
                    for p in room.players.values():
                        if p.is_ai and p.ai_context:
                            p.ai_context.add_vote_discussion(
                                player.display_name, discussion[:120]
                            )
            except Exception as e:
                logger.error(f"[狼人杀] AI玩家 {player.name} 发言异常: {e}")

        # ===== 第二阶段：所有AI依次投票 =====
        logger.info(
            f"[狼人杀] 群 {room.group_id} AI投票开始，共 {len(ai_players)} 个AI"
        )
        for player in ai_players:
            try:
                # 更新AI上下文，确保能看到所有AI的发言
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论，包括刚才其他AI的发言）
                if hasattr(room, "vote_discussion") and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论（包括新的AI发言）
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(
                                msg["player"], msg["content"]
                            )

                # 生成投票决策（基于最新的讨论信息）
                _, target_number = await ai_service.decide_vote(
                    player, room, is_pk, pk_candidates
                )

                if target_number:
                    target_player = room.get_player_by_number(target_number)
                    if (
                        target_player
                        and target_player.is_alive
                        and target_player.id != player.id
                    ):
                        room.vote_state.day_votes[player.id] = target_player.id

                        # 发送群消息显示投票
                        await self.message_service.send_group_message(
                            room,
                            f"🗳️ {player.display_name} 投票给 {target_player.display_name}",
                        )

                        # 记录日志
                        room.log(
                            f"🗳️ {pk_tag}投票：{player.display_name}（AI）投给 {target_player.display_name}"
                        )
                        logger.info(
                            f"[狼人杀] AI玩家 {player.name} 投票给 {target_player.display_name}"
                        )

                        # 记录到所有AI上下文
                        for p in room.players.values():
                            if p.is_ai and p.ai_context:
                                p.ai_context.add_vote(
                                    player.display_name,
                                    target_player.display_name,
                                    is_pk,
                                )
                    else:
                        # 目标无效，当作弃票
                        room.vote_state.day_votes[player.id] = "ABSTAIN"
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 选择弃票"
                        )
            except Exception as e:
                logger.error(f"[狼人杀] AI玩家 {player.name} 投票处理失败: {e}")
                room.vote_state.day_votes[player.id] = "ABSTAIN"  # 出错时弃票

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
                            pk_candidates = [
                                room.get_player(pid).number
                                for pid in room.vote_state.pk_players
                                if room.get_player(pid)
                            ]

                        # 先让AI发言，再投票
                        await self._handle_ai_discussion_and_votes(
                            room, room.vote_state.is_pk_vote, pk_candidates
                        )

                        # 检查是否所有人都投票了
                        if await self._check_all_voted(room):
                            return

                # 等待剩余时间
                await asyncio.sleep(timeout - ai_action_delay)

                if room.group_id not in self.game_manager.rooms:
                    return
                if room.phase != GamePhase.DAY_VOTE:
                    return

                # 超时处理
                await self.on_timeout(room)

            except asyncio.CancelledError:
                logger.info(f"[狼人杀] 群 {room.group_id} 投票定时器已取消")
            except Exception as e:
                logger.error(f"[狼人杀] 群 {room.group_id} 投票定时器异常: {e}")

        task = asyncio.create_task(vote_timer())
        room.set_timer(task)

    async def _check_all_voted(self, room: "GameRoom") -> bool:
        """检查是否所有人都投票了"""
        voted = len(room.vote_state.day_votes)
        total = room.alive_count
        logger.info(f"[狼人杀] 群 {room.group_id} 投票检查：已投 {voted}/{total}")
        return voted >= total

    async def on_finish(self, event: "AstrMessageEvent") -> None:
        """投票完毕"""
        group_id = event.get_group_id()
        if not group_id:
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            return

        # 取消定时器
        room.cancel_timer()

        # 处理投票结果
        await self._process_vote_result(room)

    async def _process_vote_result(self, room: "GameRoom") -> None:
        """处理投票结果"""
        # 统计投票
        vote_counts = {}
        for voter_id, target_id in room.vote_state.day_votes.items():
            if target_id == "ABSTAIN":
                continue
            if target_id not in vote_counts:
                vote_counts[target_id] = 0
            vote_counts[target_id] += 1

        # 统计弃票数
        abstain_count = sum(
            1
            for target_id in room.vote_state.day_votes.values()
            if target_id == "ABSTAIN"
        )

        # 生成投票结果信息
        voter_map = {}
        for voter_id, target_id in room.vote_state.day_votes.items():
            if target_id not in voter_map:
                voter_map[target_id] = []
            voter_map[target_id].append(voter_id)

        # 找出平票玩家
        if vote_counts:
            max_votes = max(vote_counts.values())
            pk_players = [
                pid for pid, count in vote_counts.items() if count == max_votes
            ]
            room.vote_state.pk_players = pk_players

        # 记录日志
        logger.info(
            f"[狼人杀] 群 {room.group_id} 投票结果统计：有效票 {len(vote_counts)}，弃票 {abstain_count}"
        )

        # 处理平票
        if vote_counts and len(room.vote_state.pk_players) > 1:
            was_pk_vote = room.vote_state.is_pk_vote

            # 重置pk投票状态
            room.vote_state.is_pk_vote = False

            # 使用保存的 was_pk_vote，因为 process_day_vote 可能已经清除了状态
            await self.message_service.announce_vote_result(
                room, vote_counts, voter_map, None, was_pk_vote
            )

            # 同步平票信息到AI上下文
            pk_names = [
                room.get_player(pid).display_name
                for pid in room.vote_state.pk_players
                if room.get_player(pid)
            ]
            for p in room.players.values():
                if p.is_ai and p.ai_context:
                    if was_pk_vote:
                        p.ai_context.add_event("PK投票平票，无人出局")
                    else:
                        p.ai_context.add_event(
                            f"投票平票，{', '.join(pk_names)} 进入PK"
                        )

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

        # 有人被放逐
        exiled_id = room.vote_state.pk_players[0] if vote_counts else None
        if not exiled_id:
            # 同步无人出局到AI上下文
            for p in room.players.values():
                if p.is_ai and p.ai_context:
                    p.ai_context.add_event("投票结果：本轮无人出局")
            await self._enter_night(room)
            return

        exiled_player = room.get_player(exiled_id) if exiled_id else None

        if exiled_player:
            # 发送放逐消息
            await self.message_service.announce_vote_result(
                room, vote_counts, voter_map, exiled_player, room.vote_state.is_pk_vote
            )

            # 记录日志
            room.log(f"📊 投票结果：{exiled_player.display_name} 被放逐")
            logger.info(
                f"[狼人杀] 群 {room.group_id} 投票结果：{exiled_player.display_name} 被放逐"
            )

            # 同步放逐信息到AI上下文
            for p in room.players.values():
                if p.is_ai and p.ai_context:
                    p.ai_context.add_event(
                        f"投票结果：{exiled_player.display_name} 被放逐"
                    )

            # 处理被放逐玩家
            room.vote_state.exiled_player = exiled_player
            exiled_player.is_alive = False

            # 检查游戏是否结束
            if await self.game_manager.check_and_handle_victory(room):
                return

            # 检查角色特殊能力
            if exiled_player.role == Role.HUNTER:
                # 猎人开枪
                await self._wait_for_hunter_shot(room)
                return

            # 检查女巫是否使用了解药或毒药（白天放逐不涉及）

            # 进入遗言阶段
            await self._enter_last_words(room)
        else:
            # 无人被放逐（平票或全弃票）
            await self.message_service.send_group_message(
                room, "📊 投票结果：无人被放逐，直接进入夜晚"
            )
            await self._enter_night(room)

    async def _enter_night(self, room: "GameRoom") -> None:
        """进入夜晚"""
        from .phase_manager import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.enter_night_phase(room)

    async def _enter_last_words(self, room: "GameRoom") -> None:
        """进入遗言阶段"""
        from .phase_manager import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.enter_last_words_phase(room)

    async def _wait_for_hunter_shot(self, room: "GameRoom") -> None:
        """等待猎人开枪"""
        from .phase_manager import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.wait_for_hunter_shot(room, "vote")

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
            # 无人投票，进入下一夜晚前先检查游戏是否结束
            if await self.game_manager.check_and_handle_victory(room):
                return
            await self._enter_night(room)

    async def _handle_ai_discussion_and_votes(
        self, room: "GameRoom", is_pk: bool = False, pk_candidates: list[int] = None
    ) -> None:
        """处理AI发言和投票"""
        ai_service = self.game_manager.ai_player_service
        ai_players = [player for player in room.get_alive_players() if player.is_ai]
        if not ai_players:
            return

        pk_tag = "PK" if is_pk else ""

        # ===== 第一阶段：所有AI依次发言 =====
        logger.info(
            f"[狼人杀] 群 {room.group_id} AI投票讨论开始，共 {len(ai_players)} 个AI"
        )
        for player in ai_players:
            try:
                # 更新AI上下文，包含投票阶段的讨论
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论）
                if hasattr(room, "vote_discussion") and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(
                                msg["player"], msg["content"]
                            )

                # 生成讨论内容
                discussion, _ = await ai_service.decide_vote(
                    player, room, is_pk, pk_candidates
                )

                # 发表讨论
                if discussion:
                    await self.message_service.send_group_message(
                        room, f"{player.display_name}：{discussion}"
                    )
                    logger.info(
                        f"[狼人杀] AI玩家 {player.name} 投票讨论: {discussion[:50]}..."
                    )

                    # 同步到房间讨论记录
                    if hasattr(room, "vote_discussion"):
                        room.vote_discussion.append(
                            {"player": player.display_name, "content": discussion[:100]}
                        )

                    # 同步投票讨论到所有AI上下文（使用专门的投票讨论字段）
                    for p in room.players.values():
                        if p.is_ai and p.ai_context:
                            p.ai_context.add_vote_discussion(
                                player.display_name, discussion[:120]
                            )
            except Exception as e:
                # 单个AI发言失败不影响其他AI
                logger.error(f"[狼人杀] AI玩家 {player.name} 发言异常: {e}")

        # ===== 第二阶段：所有AI依次投票 =====
        logger.info(
            f"[狼人杀] 群 {room.group_id} AI投票开始，共 {len(ai_players)} 个AI"
        )
        for player in ai_players:
            try:
                # 更新AI上下文，确保能看到所有AI的发言
                ai_service.update_ai_context(player, room)

                # 将投票阶段讨论加入上下文（确保AI看到所有讨论，包括刚才其他AI的发言）
                if hasattr(room, "vote_discussion") and room.vote_discussion:
                    for msg in room.vote_discussion:  # 全部讨论（包括新的AI发言）
                        if player.ai_context:
                            # 使用专门的投票讨论字段
                            player.ai_context.add_vote_discussion(
                                msg["player"], msg["content"]
                            )

                # 生成投票决策（基于最新的讨论信息）
                _, target_number = await ai_service.decide_vote(
                    player, room, is_pk, pk_candidates
                )

                if target_number:
                    target_player = room.get_player_by_number(target_number)
                    if (
                        target_player
                        and target_player.is_alive
                        and target_player.id != player.id
                    ):
                        room.vote_state.day_votes[player.id] = target_player.id

                        # 发送群消息显示投票
                        await self.message_service.send_group_message(
                            room,
                            f"🗳️ {player.display_name} 投票给 {target_player.display_name}",
                        )

                        # 记录日志
                        room.log(
                            f"🗳️ {pk_tag}投票：{player.display_name}（AI）投给 {target_player.display_name}"
                        )
                        logger.info(
                            f"[狼人杀] AI玩家 {player.name} 投票给 {target_player.display_name}"
                        )

                        # 记录到所有AI上下文
                        for p in room.players.values():
                            if p.is_ai and p.ai_context:
                                p.ai_context.add_vote(
                                    player.display_name,
                                    target_player.display_name,
                                    is_pk,
                                )
                    else:
                        # 目标无效（已死亡/投自己/不存在），当作弃票
                        room.vote_state.day_votes[player.id] = "ABSTAIN"
                        await self.message_service.send_group_message(
                            room, f"🗳️ {player.display_name} 选择弃票"
                        )
                        room.log(
                            f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票（目标无效）"
                        )
                        logger.info(
                            f"[狼人杀] AI玩家 {player.name} 投票目标无效，转为弃票"
                        )
                else:
                    # AI选择弃票 - 记录为投给"ABSTAIN"表示弃票
                    room.vote_state.day_votes[player.id] = "ABSTAIN"
                    await self.message_service.send_group_message(
                        room, f"🗳️ {player.display_name} 选择弃票"
                    )
                    room.log(f"🗳️ {pk_tag}投票：{player.display_name}（AI）弃票")
                    logger.info(f"[狼人杀] AI玩家 {player.name} 选择弃票")

            except Exception as e:
                # 单个AI投票失败不影响其他AI
                logger.error(f"[狼人杀] AI玩家 {player.name} 投票异常: {e}")
                room.vote_state.day_votes[player.id] = "ABSTAIN"

        # 检查是否所有人都投完了
        if await self._check_all_voted(room):
            return
