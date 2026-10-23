"""游戏管理器"""

import random
from typing import TYPE_CHECKING
from uuid import uuid4

from astrbot.api import logger

from ..models import AIPlayerConfig, GameConfig, GamePhase, GameRoom, Player, Role
from ..roles import RoleFactory
from .ai import AIPlayerService
from .ai_reviewer import AIReviewer
from .ban_service import BanService
from .message_service import MessageService
from .victory_checker import VictoryChecker

if TYPE_CHECKING:
    from astrbot.api.star import Context


class GameManager:
    """游戏管理器 - 协调各服务"""

    def __init__(self, context: "Context", config: GameConfig):
        self.context = context
        self.config = config
        self.rooms: dict[str, GameRoom] = {}  # {群ID: 房间}

        # 初始化服务
        self.message_service = MessageService(context)
        self.ai_reviewer = AIReviewer(context)
        self.ai_player_service = AIPlayerService(context)

    # ========== 房间管理 ==========

    def get_room(self, group_id: str) -> GameRoom | None:
        """获取房间"""
        return self.rooms.get(group_id)

    def get_room_by_player(self, player_id: str) -> tuple[str | None, GameRoom | None]:
        """通过玩家ID查找房间"""
        for group_id, room in self.rooms.items():
            if room.is_player_in_room(player_id):
                return group_id, room
        return None, None

    def create_room(self, group_id: str, creator_id: str, msg_origin, bot) -> GameRoom:
        """创建房间"""
        room = GameRoom(
            group_id=group_id,
            creator_id=creator_id,
            config=self.config,
            msg_origin=msg_origin,
            bot=bot,
        )
        self.rooms[group_id] = room
        logger.info(f"[狼人杀] 群 {group_id} 创建房间")
        return room

    def room_exists(self, group_id: str) -> bool:
        """房间是否存在"""
        return group_id in self.rooms

    async def cleanup_room(self, group_id: str) -> None:
        """Stop room activity and release its resources.

        Args:
            group_id: Group ID of the room to clean up.
        """
        if group_id not in self.rooms:
            return

        room = self.rooms[group_id]
        logger.info(f"[Werewolf] Cleaning up room {group_id}, phase: {room.phase}")
        # Stop phase callbacks before any asynchronous cleanup can yield control.
        room.phase = GamePhase.FINISHED
        room.cancel_timer()
        for player in room.players.values():
            if player.is_ai:
                self.ai_player_service.clear_player_data(player.id)

        # 先解除全员禁言（最重要！放在最前面确保执行）
        try:
            await BanService.set_group_whole_ban(room, False)
            logger.info(f"[狼人杀] 群 {group_id} 已解除全员禁言")
        except Exception as e:
            logger.error(f"[狼人杀] 解除全员禁言失败: {e}")

        # 立即解除所有玩家的个人禁言
        try:
            await BanService.unban_all_players(room)
        except Exception as e:
            logger.error(f"[狼人杀] 解除个人禁言失败: {e}")

        # 取消所有临时管理员
        try:
            await BanService.clear_temp_admins(room)
        except Exception as e:
            logger.error(f"[狼人杀] 取消临时管理员失败: {e}")

        # 恢复群昵称
        try:
            await BanService.restore_player_cards(room)
        except Exception as e:
            logger.error(f"[狼人杀] 恢复群昵称失败: {e}")

        # Another cleanup may already have removed the same room.
        if self.rooms.get(group_id) is room:
            del self.rooms[group_id]

        logger.info(f"[狼人杀] 群 {group_id} 房间已清理")

    # ========== 玩家管理 ==========

    def add_player(self, room: GameRoom, player_id: str, player_name: str) -> Player:
        """添加玩家到房间"""
        player = Player(id=player_id, name=player_name)
        room.add_player(player)
        return player

    def add_ai_player(
        self, room: GameRoom, ai_name: str, ai_config: AIPlayerConfig
    ) -> Player:
        """Add an AI player with an opaque ID and the requested display name.

        Args:
            room: Room receiving the player.
            ai_name: Display name, preserved without a prefix.
            ai_config: Model and identity configuration for the AI player.

        Returns:
            The newly added AI player.
        """
        ai_player_id = f"ai_{uuid4().hex}"

        # Assign personality independently of the public display name.
        personality_name = self.ai_player_service.assign_personality(ai_player_id)

        # Keep AI and human display names in the same format.
        player = Player(
            id=ai_player_id,
            name=ai_name,
            is_ai=True,
            ai_config=ai_config,
        )
        room.add_player(player)
        logger.info(
            f"[Werewolf] AI player {ai_name} ({personality_name}) joined room "
            f"{room.group_id}; personality hidden from display name"
        )
        return player

    def get_ai_players(self, room: GameRoom) -> list:
        """获取房间内所有AI玩家"""
        return [p for p in room.players.values() if p.is_ai]

    def get_human_players(self, room: GameRoom) -> list:
        """获取房间内所有人类玩家"""
        return [p for p in room.players.values() if not p.is_ai]

    # ========== 游戏流程 ==========

    async def start_game(self, room: GameRoom) -> None:
        """开始游戏"""
        try:
            players_list = list(room.players.values())

            # 随机打乱玩家顺序（确保编号随机分配）
            random.shuffle(players_list)

            # Assign random seat numbers and keep existing list displays in seat order.
            room.number_to_player.clear()
            for index, player in enumerate(players_list, start=1):
                player.assign_number(index)
                room.number_to_player[index] = player.id
            room.players = {player.id: player for player in players_list}

            # 分配角色
            roles_pool = self.config.get_roles_pool()
            random.shuffle(roles_pool)
            for player, role in zip(players_list, roles_pool):
                player.assign_role(role)

            # 初始化游戏状态
            room.phase = GamePhase.NIGHT_WOLF
            room.current_round = 1

            # 为AI玩家初始化上下文
            for player in players_list:
                if player.is_ai:
                    try:
                        self.ai_player_service.initialize_ai_context(player, room)
                    except Exception as e:
                        logger.error(f"[狼人杀] 初始化AI玩家上下文失败: {e}")

            # 记录日志
            room.log_round_start()

            # 修改群昵称为编号（仅人类玩家）
            await BanService.set_player_numbers(room)

            # 开启全员禁言
            await BanService.set_group_whole_ban(room, True)

            # 私聊告知角色（仅人类玩家）
            await self._send_roles_to_players(room)

            logger.info(f"[狼人杀] 群 {room.group_id} 游戏开始")
        except Exception as e:
            logger.error(f"[狼人杀] 开始游戏失败: {e}")
            # 清理房间，避免处于不一致状态
            await self.cleanup_room(room.group_id)

    async def _send_roles_to_players(self, room: GameRoom) -> None:
        """私聊告知所有玩家角色（发送角色卡片图片）"""
        for player in room.players.values():
            # 跳过AI玩家（AI不需要接收私聊）
            if player.is_ai:
                logger.info(
                    f"[狼人杀] AI玩家 {player.name} 身份：{player.role.display_name if player.role else '未知'}"
                )
                continue

            if player.role:
                role_name = player.role.value

                # 如果是狼人，获取队友信息
                teammates = None
                if player.role == Role.WEREWOLF:
                    teammates = []
                    for w in room.get_werewolves():
                        if w.id != player.id:
                            teammates.append(w.display_name)

                # 尝试发送角色卡片图片
                success = await self.message_service.send_role_card_to_player(
                    room, player.id, role_name, player.number, teammates
                )

                # 如果图片发送失败，降级为文本
                if not success:
                    role_info = RoleFactory.get_role_info(player.role, player, room)
                    await self.message_service.send_private_message(
                        room, player.id, role_info
                    )

                logger.info(f"[狼人杀] 已私聊告知玩家 {player.id} 的身份：{role_name}")

    # ========== 胜负判定 ==========

    async def check_and_handle_victory(self, room: GameRoom) -> bool:
        """Check victory without letting an old review affect a replacement room.

        Args:
            room: Room whose game outcome is being evaluated.

        Returns:
            Whether this game has finished or is no longer the active room.
        """
        if (
            self.rooms.get(room.group_id) is not room
            or room.phase == GamePhase.FINISHED
        ):
            return True
        try:
            victory_msg, winning_faction = VictoryChecker.check(room)

            if not victory_msg:
                return False

            room.phase = GamePhase.FINISHED
            logger.info(
                f"[狼人杀] 群 {room.group_id} 游戏结束，胜利阵营: {winning_faction}"
            )

            # 获取角色公布文本
            roles_text = VictoryChecker.get_all_players_roles(room)

            # 发送胜利消息
            try:
                await self.message_service.announce_victory(
                    room, victory_msg, roles_text
                )
            except Exception as e:
                logger.error(f"[狼人杀] 发送胜利消息失败: {e}")

            if self.rooms.get(room.group_id) is not room:
                return True
            # 生成AI复盘（失败不影响游戏结束）
            try:
                if winning_faction:
                    ai_review = await self.ai_reviewer.generate_review(
                        room, winning_faction
                    )
                    if ai_review and self.rooms.get(room.group_id) is room:
                        await self.message_service.send_group_message(room, ai_review)
            except Exception as e:
                logger.error(f"[狼人杀] AI复盘生成失败: {e}")

            if self.rooms.get(room.group_id) is not room:
                return True
            # 清理房间（确保一定会执行）
            logger.info(f"[狼人杀] 群 {room.group_id} 开始清理房间")
            await self.cleanup_room(room.group_id)
            logger.info(f"[狼人杀] 群 {room.group_id} 清理房间完成")

            return True
        except Exception as e:
            logger.error(f"[狼人杀] 检查胜负处理失败: {e}")
            if self.rooms.get(room.group_id) is not room:
                return True
            # 确保房间被清理，避免处于不一致状态
            logger.info(f"[狼人杀] 群 {room.group_id} 异常清理房间")
            await self.cleanup_room(room.group_id)
            logger.info(f"[狼人杀] 群 {room.group_id} 异常清理房间完成")
            return True

    # ========== 夜晚流程 ==========

    async def process_night_kill(self, room: GameRoom) -> str | None:
        """处理狼人投票结果，返回被杀玩家ID"""
        try:
            votes = room.vote_state.night_votes

            if not votes:
                return None

            # 统计票数
            vote_counts: dict[str, int] = {}
            for target_id in votes.values():
                vote_counts[target_id] = vote_counts.get(target_id, 0) + 1

            # 获取票数最多的目标
            max_votes = max(vote_counts.values())
            targets = [pid for pid, count in vote_counts.items() if count == max_votes]

            # 平票随机选择
            killed_id = random.choice(targets)

            # 清空投票
            room.vote_state.clear_night_votes()

            # 记录被杀玩家（不立即移除，等女巫行动后确定）
            room.last_killed_id = killed_id

            # 记录日志
            killed_player = room.get_player(killed_id)
            if killed_player:
                room.log(f"🌙 狼人最终决定刀 {killed_player.display_name}")

            return killed_id
        except Exception as e:
            logger.error(f"[狼人杀] 处理狼人投票结果失败: {e}")
            return None

    async def process_witch_action(self, room: GameRoom) -> None:
        """处理女巫行动结果"""
        try:
            witch_state = room.witch_state

            # 如果女巫救人
            if witch_state.saved_player_id:
                room.last_killed_id = None

            # 如果女巫没救人，被杀者确定死亡
            elif room.last_killed_id:
                room.kill_player(room.last_killed_id)

            # 如果女巫毒人
            if witch_state.poisoned_player_id:
                room.kill_player(witch_state.poisoned_player_id)
                try:
                    await BanService.ban_player(room, witch_state.poisoned_player_id)
                except Exception as e:
                    logger.error(f"[狼人杀] 女巫毒人后禁言失败: {e}")
        except Exception as e:
            logger.error(f"[狼人杀] 处理女巫行动结果失败: {e}")

    # ========== 白天流程 ==========

    async def process_day_vote(self, room: GameRoom) -> tuple[str | None, bool]:
        """
        处理白天投票结果

        返回: (被放逐玩家ID, 是否平票)
        """
        try:
            votes = room.vote_state.day_votes

            if not votes:
                return None, False

            # 统计票数（排除弃票）
            vote_counts: dict[str, int] = {}
            for target_id in votes.values():
                if target_id == "ABSTAIN":
                    continue  # 跳过弃票
                vote_counts[target_id] = vote_counts.get(target_id, 0) + 1

            # 如果全部弃票，无人出局
            if not vote_counts:
                return None, False

            # 获取票数最多的目标
            max_votes = max(vote_counts.values())
            targets = [pid for pid, count in vote_counts.items() if count == max_votes]

            # 检查平票
            if len(targets) > 1:
                # 平票
                if not room.vote_state.is_pk_vote:
                    # 第一次投票平票，进入PK
                    try:
                        targets.sort(
                            key=lambda pid: (
                                room.get_player(pid).number
                                if room.get_player(pid)
                                else 999
                            )
                        )
                        room.vote_state.pk_players = targets
                    except Exception as e:
                        logger.error(f"[狼人杀] 处理平票PK失败: {e}")
                        # 平票时无法正常处理，返回无人出局
                        room.vote_state.clear_day_votes()
                        return None, True
                    return None, True
                else:
                    # PK后仍平票，无人出局
                    room.vote_state.clear_day_votes()
                    return None, True

            # 只有一人票数最多
            exiled_id = targets[0]

            # 移除存活列表
            room.kill_player(exiled_id)

            # 清空投票
            room.vote_state.clear_day_votes()

            return exiled_id, False
        except Exception as e:
            logger.error(f"[狼人杀] 处理白天投票结果失败: {e}")
            # 发生异常时，清空投票状态，确保游戏可以继续
            room.vote_state.clear_day_votes()
            return None, False

    # ========== 工具方法 ==========

    def get_player_name(self, player_id: str) -> str:
        """获取玩家名称（跨房间查找）"""
        _, room = self.get_room_by_player(player_id)
        if room:
            player = room.get_player(player_id)
            if player:
                return player.display_name
        return f"玩家{player_id[-4:]}"
