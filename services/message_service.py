"""消息发送服务"""

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain

from ..utils import cmd

if TYPE_CHECKING:
    from ..models import GameRoom, Player


class MessageService:
    """消息发送服务"""

    def __init__(self, context):
        self.context = context

    async def send_group_message(self, room: "GameRoom", text: str) -> bool:
        """发送群消息"""
        if not room.msg_origin:
            return False

        try:
            msg = MessageChain().message(text)
            await self.context.send_message(room.msg_origin, msg)
            return True
        except Exception as e:
            logger.error(f"[狼人杀] 发送群消息失败: {e}")
            return False

    async def send_group_at_message(
        self, room: "GameRoom", player: "Player", text: str
    ) -> bool:
        """发送群消息并@某人"""
        if not room.msg_origin:
            return False

        try:
            msg = MessageChain().at(player.display_name, player.id).message(text)
            await self.context.send_message(room.msg_origin, msg)
            return True
        except Exception as e:
            logger.error(f"[狼人杀] 发送群@消息失败: {e}")
            return False

    async def send_private_message(
        self, room: "GameRoom", player_id: str, text: str
    ) -> bool:
        """发送私聊消息"""
        if not room.bot:
            return False

        try:
            await room.bot.send_private_msg(user_id=int(player_id), message=text)
            return True
        except Exception as e:
            logger.warning(f"[狼人杀] 发送私聊消息给 {player_id} 失败: {e}")
            return False

    async def send_role_card_to_player(
        self,
        room: "GameRoom",
        player_id: str,
        role_name: str,
        player_number: int,
        teammates: list[str] = None,
    ) -> bool:
        """发送角色信息给玩家（纯文本）"""
        from ..roles import RoleFactory

        player = room.get_player(player_id)
        if not player or not player.role:
            return False

        # 使用角色工厂获取完整角色信息
        text = RoleFactory.get_role_info(player.role, player, room)

        return await self.send_private_message(room, player_id, text)

    async def broadcast_to_players(
        self, room: "GameRoom", player_ids: list, text: str
    ) -> int:
        """广播私聊消息给多个玩家"""
        success_count = 0
        for player_id in player_ids:
            if await self.send_private_message(room, player_id, text):
                success_count += 1
        return success_count

    # ========== 预设消息模板 ==========

    async def announce_game_start(self, room: "GameRoom") -> bool:
        """公告游戏开始"""
        text = (
            "🌙 游戏开始！天黑请闭眼...\n\n"
            "角色已分配完毕！\n\n"
            "机器人正在私聊告知各位身份...\n"
            f"如未收到私聊，请使用：{cmd('查角色')}\n\n"
            f"🐺 狼人请私聊使用：{cmd('办掉')} 编号\n"
            f"🔮 预言家请等待狼人行动完成后使用：{cmd('验人')} 编号\n"
            "⏰ 剩余时间：2分钟"
        )
        return await self.send_group_message(room, text)

    async def announce_night_start(self, room: "GameRoom") -> bool:
        """公告夜晚开始"""
        text = (
            "🌙 夜晚降临，天黑请闭眼...\n\n"
            f"🐺 狼人请私聊使用：{cmd('办掉')} 编号\n"
            "🔮 预言家请等待狼人行动完成\n"
            "⏰ 剩余时间：2分钟"
        )
        return await self.send_group_message(room, text)

    async def announce_seer_phase(self, room: "GameRoom") -> bool:
        """公告预言家验人阶段"""
        text = (
            "🔮 狼人行动完成！\n"
            f"预言家请私聊机器人验人：{cmd('验人')} 编号\n"
            "⏰ 剩余时间：2分钟"
        )
        return await self.send_group_message(room, text)

    async def announce_witch_phase(self, room: "GameRoom") -> bool:
        """公告女巫行动阶段"""
        text = "💊 预言家验人完成！\n女巫请私聊机器人行动\n⏰ 剩余时间：2分钟"
        return await self.send_group_message(room, text)

    async def announce_dawn(
        self,
        room: "GameRoom",
        killed_name: str | None = None,
        saved: bool = False,
        poisoned_name: str | None = None,
    ) -> bool:
        """公告天亮"""
        if saved:
            text = (
                f"☀️ 天亮了！\n\n"
                f"昨晚是平安夜，没有人死亡！\n\n"
                f"存活玩家：{room.alive_count}/{room.player_count}\n"
            )
        elif killed_name:
            text = (
                f"☀️ 天亮了！\n\n"
                f"昨晚，玩家 {killed_name} 死了！\n\n"
                f"存活玩家：{room.alive_count}/{room.player_count}\n"
            )
        else:
            text = (
                f"☀️ 天亮了！\n\n"
                f"昨晚是平安夜，没有人死亡！\n\n"
                f"存活玩家：{room.alive_count}/{room.player_count}\n"
            )

        if poisoned_name:
            text += f"\n同时，玩家 {poisoned_name} 死了！\n"

        return await self.send_group_message(room, text)

    async def announce_vote_start(self, room: "GameRoom") -> bool:
        """公告投票开始"""
        text = (
            "📊 发言环节结束！现在进入投票阶段！\n\n"
            "请所有存活玩家使用命令：\n"
            f"{cmd('投票')} 编号\n\n"
            f"当前存活人数：{room.alive_count}\n"
            "⏰ 剩余时间：2分钟"
        )
        return await self.send_group_message(room, text)

    async def announce_pk_start(self, room: "GameRoom", pk_names: list) -> bool:
        """公告PK发言开始"""
        text = (
            "\n📊 投票结果公布！\n\n"
            "⚠️ 出现平票！以下玩家票数相同：\n"
            + "\n".join([f"  • {name}" for name in pk_names])
            + "\n\n进入PK环节！\n平票玩家将依次发言（每人2分钟），然后进行二次投票。\n"
        )
        return await self.send_group_message(room, text)

    async def announce_pk_vote_start(self, room: "GameRoom", pk_names: list) -> bool:
        """公告PK投票开始"""
        text = (
            "📢 PK发言完毕！现在开始二次投票\n\n"
            "⚠️ 只能投给以下平票玩家：\n"
            + "\n".join([f"  • {name}" for name in pk_names])
            + "\n\n⏰ 投票时间：2分钟\n"
            + f"💡 使用 {cmd('投票')} 编号"
        )
        return await self.send_group_message(room, text)

    async def announce_exile(
        self, room: "GameRoom", player_name: str, is_pk: bool = False
    ) -> bool:
        """公告放逐结果"""
        prefix = "\n📊 PK投票结果公布！\n\n" if is_pk else "\n📊 投票结果公布！\n\n"
        text = (
            prefix + f"玩家 {player_name} 被放逐了！\n\n"
            f"存活玩家：{room.alive_count}/{room.player_count}\n"
        )
        return await self.send_group_message(room, text)

    async def announce_vote_result(
        self,
        room: "GameRoom",
        vote_counts: dict[str, int],
        voters_map: dict[str, list[str]],
        exiled_name: str | None = None,
        is_pk: bool = False,
    ) -> bool:
        """公告投票结果（纯文本）"""
        prefix = "📊 PK投票结果公布！\n\n" if is_pk else "📊 投票结果公布！\n\n"
        text = prefix

        # 按票数排序
        sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)

        for target_id, vote_count in sorted_votes:
            target = room.get_player(target_id)
            if target:
                voter_names = voters_map.get(target_id, [])
                voters_str = "、".join(voter_names) if voter_names else "无"
                text += (
                    f"• {target.display_name}: {vote_count}票 (投票者: {voters_str})\n"
                )

        if exiled_name:
            text += f"\n🔨 {exiled_name} 被放逐了！"
        else:
            text += "\n⚖️ 平票，无人被放逐"

        return await self.send_group_message(room, text)

    async def announce_hunter_can_shoot(
        self, room: "GameRoom", hunter_name: str
    ) -> bool:
        """公告猎人可以开枪"""
        text = f"⚠️ {hunter_name} 是猎人，可以选择开枪带走一个人..."
        return await self.send_group_message(room, text)

    async def announce_hunter_shot(self, room: "GameRoom", target_name: str) -> bool:
        """公告猎人开枪结果"""
        text = (
            f"💥 猎人开枪带走了 {target_name}！\n\n剩余存活玩家：{room.alive_count} 人"
        )
        return await self.send_group_message(room, text)

    async def announce_victory(
        self, room: "GameRoom", victory_msg: str, roles_text: str
    ) -> bool:
        """公告胜利"""
        text = f"🎉 {victory_msg}\n游戏结束！\n\n{roles_text}"
        return await self.send_group_message(room, text)

    async def announce_timeout(self, room: "GameRoom", phase_name: str) -> bool:
        """公告超时"""
        text = f"⏰ {phase_name}超时！自动进入下一阶段。"
        return await self.send_group_message(room, text)

    async def announce_vote_reminder(
        self, room: "GameRoom", voted: int, total: int
    ) -> bool:
        """公告投票提醒"""
        text = (
            f"⏰ 投票倒计时：还有30秒！\n\n"
            f"当前投票进度：{voted}/{total}\n"
            f"💡 请尚未投票的玩家抓紧时间：{cmd('投票')} 编号"
        )
        return await self.send_group_message(room, text)
