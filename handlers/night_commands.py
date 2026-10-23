"""夜晚命令处理"""

import re
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

from ..models import GamePhase, Role
from ..utils import cmd
from .base import BaseCommandHandler

if TYPE_CHECKING:
    pass


class NightCommandHandler(BaseCommandHandler):
    """夜晚命令处理器"""

    async def werewolf_kill(
        self, event: AstrMessageEvent, *, abstain: bool = False
    ) -> AsyncGenerator:
        """Submit a private wolf ballot, including a no-target ballot.

        Args:
            event: Private command from the wolf.
            abstain: Whether to submit an abstention instead of a kill target.

        Yields:
            The command result.
        """
        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return
        player_id = event.get_sender_id()
        group_id, room = self.game_manager.get_room_by_player(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        if room.phase != GamePhase.NIGHT_WOLF:
            yield event.plain_result("⚠️ 现在不是狼人行动阶段！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.WEREWOLF:
            yield event.plain_result("❌ 你不是狼人！")
            return

        if not player.is_alive:
            yield event.plain_result("❌ 你已经出局了！")
            return

        target_str = "弃票" if abstain else self.get_target_user(event)
        abstain = target_str == "弃票"
        target_id = "ABSTAIN" if abstain else room.parse_target(target_str or "")
        if not abstain:
            if not target_str:
                yield event.plain_result(
                    f"❌ 请指定目标！\n使用：{cmd('办掉')} 编号，或 {cmd('弃票')}"
                )
                return
            if not target_id:
                yield event.plain_result(
                    f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-{room.config.total_players}）"
                )
                return
            if not room.is_player_alive(target_id):
                yield event.plain_result("❌ 目标玩家已经出局！")
                return

        changed = room.vote_state.night_votes.get(player_id) != target_id
        room.vote_state.night_votes[player_id] = target_id
        target_player = room.get_player(target_id)
        if changed:
            ballot = (
                "选择弃票，不提交刀人目标"
                if abstain
                else f"选择刀 {target_player.display_name}"
            )
            room.log(f"🐺 {player.display_name}（狼人）{ballot}")
            for teammate in room.get_alive_werewolves():
                if teammate.id != player_id and teammate.is_ai and teammate.ai_context:
                    teammate.ai_context.current_round = room.current_round
                    teammate.ai_context.add_private_event(
                        f"狼队友 {player.display_name} {ballot}"
                    )

        alive_wolves = room.get_alive_werewolves()
        human_wolves = [w for w in alive_wolves if not w.is_ai]
        ai_wolves = [w for w in alive_wolves if w.is_ai]
        human_voted_count = sum(
            1 for w in human_wolves if w.id in room.vote_state.night_votes
        )

        round_num = room.current_round
        yield event.plain_result(
            f"✅ {'已弃票，不提交刀人目标' if abstain else '你选择了办掉目标'}！"
            f"当前 {human_voted_count}/{len(human_wolves)} 名人类狼人已投票（含弃票）"
        )
        if (
            self.game_manager.get_room(group_id) is not room
            or room.phase != GamePhase.NIGHT_WOLF
            or room.current_round != round_num
        ):
            return

        # 如果有AI狼人，每次人类狼人投票都触发AI重新决策
        if ai_wolves and changed:
            from ..phases import NightWolfPhase

            wolf_phase = NightWolfPhase(self.game_manager)
            await wolf_phase.trigger_ai_wolf_vote(room, reconsider=True)
            if (
                self.game_manager.get_room(group_id) is not room
                or room.phase != GamePhase.NIGHT_WOLF
                or room.current_round != round_num
            ):
                return

        # 检查是否所有狼人都投票了（包括AI）
        all_voted = len(room.vote_state.night_votes) >= len(alive_wolves)
        if all_voted:
            from ..phases import NightWolfPhase

            wolf_phase = NightWolfPhase(self.game_manager)
            await wolf_phase.on_all_voted(room)

    async def werewolf_chat(self, event: AstrMessageEvent) -> AsyncGenerator:
        """狼人密谋"""
        player_id = event.get_sender_id()

        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return

        _, room = self.game_manager.get_room_by_player(player_id)
        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.WEREWOLF:
            yield event.plain_result("❌ 你不是狼人！")
            return

        if not player.is_alive:
            yield event.plain_result("❌ 你已经出局了！")
            return

        if room.phase != GamePhase.NIGHT_WOLF:
            yield event.plain_result("⚠️ 只能在夜晚狼人行动阶段与队友交流！")
            return

        # 提取消息内容
        message_text = re.sub(
            r"^/?\s*(狼人杀\s*)?密谋\s*", "", event.message_str
        ).strip()
        if not message_text:
            yield event.plain_result(
                f"❌ 请输入要发送的消息！\n用法：{cmd('密谋')} 消息内容"
            )
            return

        # 找到其他狼人
        teammates = [w for w in room.get_alive_werewolves() if w.id != player_id]
        if not teammates:
            yield event.plain_result("❌ 没有其他存活的狼人队友！")
            return

        # 发送消息给队友
        msg = f"🐺 队友 {player.display_name} 说：\n{message_text}"
        success_count = 0
        for teammate in teammates:
            if teammate.is_ai:
                # AI队友：加入上下文
                if teammate.ai_context:
                    teammate.ai_context.add_wolf_chat(
                        player.display_name, message_text, room.current_round
                    )
                success_count += 1
            else:
                # 人类队友：发送私聊
                if await self.message_service.send_private_message(
                    room, teammate.id, msg
                ):
                    success_count += 1

        # 更新最后密谋时间
        import time

        room.wolf_last_chat_time = time.time()

        room.log(f"💬 {player.display_name}（狼人）密谋：{message_text}")
        yield event.plain_result(f"✅ 消息已发送给 {success_count} 名队友！")

    async def seer_check(self, event: AstrMessageEvent) -> AsyncGenerator:
        """预言家验人"""
        player_id = event.get_sender_id()
        group_id, room = self.game_manager.get_room_by_player(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        if room.phase != GamePhase.NIGHT_SEER:
            yield event.plain_result("⚠️ 现在不是预言家验人阶段！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.SEER:
            yield event.plain_result("❌ 你不是预言家！")
            return

        if room.seer_checked:
            yield event.plain_result("❌ 你今晚已经验过人了！")
            return

        # 获取目标
        target_str = self.get_target_user(event)
        if not target_str:
            yield event.plain_result(
                f"❌ 请指定验证目标！\n使用：{cmd('验人')} 编号\n示例：{cmd('验人')} 3"
            )
            return

        target_id = room.parse_target(target_str)
        if not target_id:
            yield event.plain_result(
                f"❌ 无效的目标：{target_str}\n请使用本局玩家编号（1-{room.config.total_players}）"
            )
            return

        if target_id == player_id:
            yield event.plain_result("❌ 不能验证自己！")
            return

        if not room.is_player_alive(target_id):
            yield event.plain_result("❌ 不能验证已经出局的玩家！")
            return

        # 获取验人结果
        target_player = room.get_player(target_id)
        is_werewolf = target_player and target_player.role == Role.WEREWOLF

        if is_werewolf:
            result_msg = (
                f"🔮 验人结果：\n\n玩家 {target_player.display_name} 是 🐺 狼人！"
            )
            room.log(
                f"🔮 {player.display_name}（预言家）验 {target_player.display_name}：狼人"
            )
        else:
            result_msg = (
                f"🔮 验人结果：\n\n玩家 {target_player.display_name} 是 ✅ 好人！"
            )
            room.log(
                f"🔮 {player.display_name}（预言家）验 {target_player.display_name}：好人"
            )

        yield event.plain_result(result_msg)

        # 进入女巫阶段
        from ..phases import NightSeerPhase

        seer_phase = NightSeerPhase(self.game_manager)
        await seer_phase.on_checked(room)

    async def witch_save(self, event: AstrMessageEvent) -> AsyncGenerator:
        """女巫救人"""
        player_id = event.get_sender_id()
        group_id, room = self.game_manager.get_room_by_player(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        if room.phase != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.WITCH:
            yield event.plain_result("❌ 你不是女巫！")
            return

        witch_state = room.witch_state
        if witch_state.has_acted:
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        if witch_state.antidote_used:
            yield event.plain_result("❌ 解药已经用过了！")
            return

        if not room.last_killed_id:
            yield event.plain_result("❌ 今晚没有人被杀，无法使用解药！")
            return

        # 使用解药
        witch_state.saved_player_id = room.last_killed_id
        witch_state.antidote_used = True
        witch_state.has_acted = True

        saved_player = room.get_player(room.last_killed_id)
        room.log(
            f"💊 {player.display_name}（女巫）使用解药救了 {saved_player.display_name}"
        )

        yield event.plain_result(f"✅ 你使用解药救了 {saved_player.display_name}！")

        from ..phases import NightWitchPhase

        witch_phase = NightWitchPhase(self.game_manager)
        await witch_phase.on_acted(room)

    async def witch_poison(self, event: AstrMessageEvent) -> AsyncGenerator:
        """女巫毒人"""
        player_id = event.get_sender_id()
        group_id, room = self.game_manager.get_room_by_player(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        if room.phase != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.WITCH:
            yield event.plain_result("❌ 你不是女巫！")
            return

        witch_state = room.witch_state
        if witch_state.has_acted:
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        if witch_state.poison_used:
            yield event.plain_result("❌ 毒药已经用过了！")
            return

        # 获取目标
        target_str = self.get_target_user(event)
        if not target_str:
            yield event.plain_result(
                f"❌ 请指定毒人目标！\n使用：{cmd('毒人')} 编号\n示例：{cmd('毒人')} 5"
            )
            return

        target_id = room.parse_target(target_str)
        if not target_id:
            yield event.plain_result(
                f"❌ 无效的目标：{target_str}\n请使用本局玩家编号（1-{room.config.total_players}）"
            )
            return

        if not room.is_player_alive(target_id):
            yield event.plain_result("❌ 目标玩家已经出局！")
            return

        if target_id == player_id:
            yield event.plain_result("❌ 不能毒自己！")
            return

        # 使用毒药
        witch_state.poisoned_player_id = target_id
        witch_state.poison_used = True
        witch_state.has_acted = True

        target_player = room.get_player(target_id)
        room.log(
            f"💊 {player.display_name}（女巫）使用毒药毒了 {target_player.display_name}"
        )

        yield event.plain_result(f"✅ 你使用毒药毒了 {target_player.display_name}！")

        from ..phases import NightWitchPhase

        witch_phase = NightWitchPhase(self.game_manager)
        await witch_phase.on_acted(room)

    async def witch_pass(self, event: AstrMessageEvent) -> AsyncGenerator:
        """女巫不操作"""
        player_id = event.get_sender_id()
        group_id, room = self.game_manager.get_room_by_player(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        if room.phase != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.WITCH:
            yield event.plain_result("❌ 你不是女巫！")
            return

        witch_state = room.witch_state
        if witch_state.has_acted:
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        witch_state.has_acted = True
        room.log(f"💊 {player.display_name}（女巫）选择不操作")

        yield event.plain_result("✅ 你选择不操作！")

        from ..phases import NightWitchPhase

        witch_phase = NightWitchPhase(self.game_manager)
        await witch_phase.on_acted(room)

    async def hunter_shoot(self, event: AstrMessageEvent) -> AsyncGenerator:
        """猎人开枪"""
        player_id = event.get_sender_id()

        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return

        _, room = self.game_manager.get_room_by_player(player_id)
        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        player = room.get_player(player_id)
        if not player or player.role != Role.HUNTER:
            yield event.plain_result("❌ 你不是猎人！")
            return

        if room.hunter_state.pending_shot_player_id != player_id:
            yield event.plain_result("❌ 当前不能开枪！")
            return

        from ..roles import HunterDeathType

        if room.hunter_state.death_type == HunterDeathType.POISON:
            yield event.plain_result("❌ 你被女巫毒死，不能开枪！")
            return

        # 获取目标
        target_str = self.get_target_user(event)
        if not target_str:
            yield event.plain_result(
                f"❌ 请指定目标！\n使用：{cmd('开枪')} 编号\n示例：{cmd('开枪')} 1"
            )
            return

        target_id = room.parse_target(target_str)
        if not target_id:
            yield event.plain_result(
                f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）"
            )
            return

        if not room.is_player_alive(target_id):
            target_player = room.get_player(target_id)
            name = target_player.display_name if target_player else target_id
            yield event.plain_result(f"❌ {name} 已经出局！")
            return

        if target_id == player_id:
            yield event.plain_result("❌ 不能开枪带走自己！")
            return

        target_player = room.get_player(target_id)
        yield event.plain_result(f"💥 你开枪带走了 {target_player.display_name}！")

        from ..phases import PhaseManager

        phase_manager = PhaseManager(self.game_manager)
        await phase_manager.on_hunter_shot(room, target_id)
