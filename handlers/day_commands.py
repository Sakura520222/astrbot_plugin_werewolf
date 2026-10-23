"""白天命令处理"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..models import GamePhase
from ..utils import cmd, get_command_prefix
from .base import BaseCommandHandler

if TYPE_CHECKING:
    pass


class DayCommandHandler(BaseCommandHandler):
    """白天命令处理器"""

    async def finish_last_words(self, event: AstrMessageEvent) -> AsyncGenerator:
        """遗言完毕"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令！")
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        player_id = event.get_sender_id()

        if room.phase != GamePhase.LAST_WORDS:
            yield event.plain_result("⚠️ 现在不是遗言阶段！")
            return

        if room.last_words_player_id != player_id:
            yield event.plain_result("⚠️ 只有被杀的玩家才能使用此命令！")
            return

        generation = room.last_words_generation
        logger.info(
            f"[狼人杀] 群 {group_id} 玩家 {room.get_player(player_id).display_name} 遗言完毕"
        )
        yield event.plain_result("✅ 遗言完毕！")

        from ..phases import LastWordsPhase

        last_words_phase = LastWordsPhase(self.game_manager)
        await last_words_phase.on_finish(room, generation=generation)

    async def finish_speaking(self, event: AstrMessageEvent) -> AsyncGenerator:
        """发言完毕"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令！")
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        player_id = event.get_sender_id()

        if room.phase not in (GamePhase.DAY_SPEAKING, GamePhase.DAY_PK):
            yield event.plain_result("⚠️ 现在不是发言阶段！")
            return

        if room.speaking_state.current_speaker_id != player_id:
            yield event.plain_result("⚠️ 现在不是你的发言时间！")
            return

        generation = room.speaking_generation
        speaker_id = room.speaking_state.current_speaker_id
        logger.info(
            f"[狼人杀] 群 {group_id} 玩家 {room.get_player(player_id).display_name} 发言完毕"
        )
        yield event.plain_result("✅ 发言完毕！")

        from ..phases import DaySpeakingPhase

        if room.speaking_state.current_speaker_id != speaker_id:
            return
        speaking_phase = DaySpeakingPhase(self.game_manager)
        await speaking_phase.on_finish_speaking(room, generation=generation)

    async def start_vote(self, event: AstrMessageEvent) -> AsyncGenerator:
        """跳过发言进入投票"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令！")
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        if event.get_sender_id() != room.creator_id:
            yield event.plain_result("⚠️ 只有房主才能跳过发言环节！")
            return

        if room.phase not in (GamePhase.DAY_SPEAKING, GamePhase.DAY_PK):
            yield event.plain_result("⚠️ 现在不是发言阶段！")
            return

        creator = room.get_player(event.get_sender_id())
        logger.info(
            f"[狼人杀] 群 {group_id} 房主 {creator.display_name} 跳过发言环节，直接进入投票"
        )

        # Invalidate any in-flight speech before yielding to platform operations.
        previous_phase = room.phase
        generation = room.speaking_generation + 1
        room.speaking_generation = generation
        room.cancel_timer()

        # 取消当前发言者管理员
        from ..services import BanService

        current_speaker_id = room.speaking_state.current_speaker_id
        room.speaking_state.current_speaker_id = None
        if current_speaker_id:
            await BanService.remove_temp_admin(room, current_speaker_id)

        yield event.plain_result("✅ 房主跳过发言环节，直接进入投票！")

        from ..phases import PhaseManager

        phase_manager = PhaseManager(self.game_manager)

        if (
            self.game_manager.get_room(group_id) is not room
            or room.phase != previous_phase
            or room.speaking_generation != generation
        ):
            return
        if previous_phase == GamePhase.DAY_PK:
            await phase_manager.enter_pk_vote_phase(room)
        else:
            await phase_manager.enter_vote_phase(room)

    async def day_vote(self, event: AstrMessageEvent) -> AsyncGenerator:
        """投票放逐"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令！")
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        player_id = event.get_sender_id()

        if room.phase != GamePhase.DAY_VOTE or room.vote_state.settled:
            yield event.plain_result(
                f"⚠️ 现在不是投票阶段！使用 {cmd('开始投票')} 进入投票"
            )
            return

        if not room.is_player_in_room(player_id):
            yield event.plain_result("❌ 你不在游戏中！")
            return

        if not room.is_player_alive(player_id):
            yield event.plain_result("❌ 你已经出局了！")
            return

        # 获取目标
        target_str = self.get_target_user(event)
        if not target_str:
            yield event.plain_result(
                f"❌ 请指定投票目标！\n使用：{cmd('投票')} 编号\n示例：{cmd('投票')} 2"
            )
            return

        target_id = room.parse_target(target_str)
        if not target_id:
            yield event.plain_result(
                f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）"
            )
            return

        if not room.is_player_alive(target_id):
            yield event.plain_result("❌ 目标玩家已经出局！")
            return

        # PK投票限制
        if room.vote_state.is_pk_vote:
            if target_id not in room.vote_state.pk_players:
                pk_names = []
                for pid in room.vote_state.pk_players:
                    p = room.get_player(pid)
                    if p:
                        pk_names.append(p.display_name)
                yield event.plain_result(
                    "❌ PK投票只能投给平票玩家！\n\n"
                    "可投票对象：\n" + "\n".join([f"  • {name}" for name in pk_names])
                )
                return

        # Capture ownership before yielding the command reply.
        generation = room.vote_state.generation
        room.vote_state.day_votes[player_id] = target_id

        # 记录日志
        voter = room.get_player(player_id)
        target = room.get_player(target_id)
        is_pk = room.vote_state.is_pk_vote
        if is_pk:
            room.log(f"🗳️ PK投票：{voter.display_name} 投给 {target.display_name}")
        else:
            room.log(f"🗳️ {voter.display_name} 投票给 {target.display_name}")

        # 同步投票到所有AI玩家上下文
        for p in room.players.values():
            if p.is_ai and p.ai_context:
                p.ai_context.current_round = room.current_round
                p.ai_context.add_vote(voter.display_name, target.display_name, is_pk)
                p.ai_context.analyze_voting_pattern(
                    voter.display_name, target.display_name, is_pk
                )

        yield event.plain_result(
            f"✅ 投票成功！当前已投票 {len(room.vote_state.day_votes)}/{room.alive_count} 人"
        )

        # 检查是否所有人都投票了
        if len(room.vote_state.day_votes) >= room.alive_count:
            from ..phases import DayVotePhase

            vote_phase = DayVotePhase(self.game_manager)
            await vote_phase.on_all_voted(room, generation=generation)

    async def ai_vote(self, event: AstrMessageEvent) -> AsyncGenerator:
        """触发AI发言和投票"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 请在群聊中使用此命令！")
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        if room.phase != GamePhase.DAY_VOTE or room.vote_state.settled:
            yield event.plain_result(
                f"⚠️ 现在不是投票阶段！使用 {cmd('开始投票')} 进入投票"
            )
            return

        if room.creator_id != event.get_sender_id():
            yield event.plain_result("⚠️ 只有房主才能使用此命令！")
            return

        if not any(p.is_ai for p in room.get_alive_players()):
            yield event.plain_result("❌ 当前游戏中没有AI玩家！")
            return

        from ..phases import DayVotePhase

        generation = room.vote_state.generation
        await DayVotePhase(self.game_manager).trigger_ai_votes(
            room, generation=generation, manual=True
        )
        if (
            self.game_manager.get_room(group_id) is room
            and room.phase == GamePhase.DAY_VOTE
            and room.vote_state.generation == generation
            and not room.vote_state.settled
        ):
            yield event.plain_result(
                f"✅ AI发言和投票已完成！当前已投票 {len(room.vote_state.day_votes)}/{room.alive_count} 人"
            )

    async def capture_speech(self, event: AstrMessageEvent) -> None:
        """捕获发言内容（非命令消息）"""
        group_id = event.get_group_id()
        if not group_id:
            return

        room = self.game_manager.get_room(group_id)
        if not room:
            return

        player_id = event.get_sender_id()
        message_text = event.get_message_outline()

        # 排除命令
        if message_text.startswith(get_command_prefix()):
            return

        if not message_text.strip():
            return

        # 投票阶段：监听所有人的讨论
        if room.phase == GamePhase.DAY_VOTE:
            player = room.get_player(player_id)
            if player and player.is_alive:
                # 记录到投票讨论（单一事件源，完整保留不截断）
                if not hasattr(room, "vote_discussion"):
                    room.vote_discussion = []
                room.vote_discussion.append(
                    {
                        "player": player.display_name,
                        "content": message_text,
                    }
                )
                # 同步投票讨论到所有AI上下文（实时广播一次，不重复回放）
                for p in room.players.values():
                    if p.is_ai and p.ai_context:
                        p.ai_context.add_vote_discussion(
                            player.display_name, message_text
                        )
            return

        # 发言阶段和遗言阶段：只记录当前发言者
        if room.phase not in (
            GamePhase.DAY_SPEAKING,
            GamePhase.DAY_PK,
            GamePhase.LAST_WORDS,
        ):
            return

        # 检查是否是当前发言者/遗言者
        if room.phase == GamePhase.LAST_WORDS:
            if room.last_words_player_id != player_id:
                return
        else:
            if room.speaking_state.current_speaker_id != player_id:
                return

        # 记录发言
        room.speaking_state.current_speech.append(message_text)
