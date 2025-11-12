"""
狼人杀游戏插件
游戏规则：9人局（3狼人 + 3神 + 3平民）
神职：预言家 + 女巫 + 猎人
流程：创建房间 → 分配角色 → 夜晚（狼人办掉→预言家验人→女巫行动） → 白天投票 → 判断胜负
"""
import random
import asyncio
from typing import Dict, Set, List, Optional
from enum import Enum

from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.message.components import At
from astrbot.core.message.message_event_result import MessageChain


# 游戏常量
LOG_SEPARATOR = "=" * 30  # 游戏日志分隔线


class GameConfig:
    """游戏配置常量"""
    TOTAL_PLAYERS = 9          # 总玩家数
    WEREWOLF_COUNT = 3         # 狼人数量
    SEER_COUNT = 1             # 预言家数量
    WITCH_COUNT = 1            # 女巫数量
    HUNTER_COUNT = 1           # 猎人数量
    VILLAGER_COUNT = 3         # 平民数量
    BAN_DURATION_DAYS = 30     # 禁言时长（天）

    @classmethod
    def get_roles_pool(cls) -> List[str]:
        """获取角色池"""
        return (
            ["werewolf"] * cls.WEREWOLF_COUNT +
            ["seer"] * cls.SEER_COUNT +
            ["witch"] * cls.WITCH_COUNT +
            ["hunter"] * cls.HUNTER_COUNT +
            ["villager"] * cls.VILLAGER_COUNT
        )


class GamePhase(Enum):
    """游戏阶段"""
    WAITING = "等待中"
    NIGHT_WOLF = "夜晚-狼人行动"
    NIGHT_SEER = "夜晚-预言家验人"
    NIGHT_WITCH = "夜晚-女巫行动"
    LAST_WORDS = "遗言阶段"
    DAY_SPEAKING = "白天发言"
    DAY_VOTE = "白天投票"
    DAY_PK = "PK发言"  # 平票时PK发言
    FINISHED = "已结束"


@register("astrbot_plugin_werewolf", "miao", "狼人杀游戏（3狼3神3平民+AI复盘）", "v1.0.0")
class WerewolfPlugin(Star):
    def __init__(self, context: Context, config: dict = None, *args, **kwargs):
        super().__init__(context)
        self.context = context

        # 读取配置
        self.config = config or {}
        self.enable_ai_review = self.config.get("enable_ai_review", True)
        self.ai_review_model = self.config.get("ai_review_model", "")
        self.ai_review_prompt = self.config.get("ai_review_prompt", "")

        # 游戏人数配置
        GameConfig.TOTAL_PLAYERS = self.config.get("total_players", 9)
        GameConfig.WEREWOLF_COUNT = self.config.get("werewolf_count", 3)
        GameConfig.SEER_COUNT = self.config.get("seer_count", 1)
        GameConfig.WITCH_COUNT = self.config.get("witch_count", 1)
        GameConfig.HUNTER_COUNT = self.config.get("hunter_count", 1)
        GameConfig.VILLAGER_COUNT = self.config.get("villager_count", 3)
        GameConfig.BAN_DURATION_DAYS = self.config.get("ban_duration_days", 30)

        # 验证配置
        role_sum = (GameConfig.WEREWOLF_COUNT + GameConfig.SEER_COUNT +
                   GameConfig.WITCH_COUNT + GameConfig.HUNTER_COUNT +
                   GameConfig.VILLAGER_COUNT)
        if role_sum != GameConfig.TOTAL_PLAYERS:
            logger.warning(
                f"[狼人杀] 角色配置不匹配！角色总数({role_sum}) ≠ 总玩家数({GameConfig.TOTAL_PLAYERS})，"
                f"使用默认配置：9人局（3狼3神3平民）"
            )
            # 恢复默认配置
            GameConfig.TOTAL_PLAYERS = 9
            GameConfig.WEREWOLF_COUNT = 3
            GameConfig.SEER_COUNT = 1
            GameConfig.WITCH_COUNT = 1
            GameConfig.HUNTER_COUNT = 1
            GameConfig.VILLAGER_COUNT = 3

        # 超时配置（秒）
        self.timeout_wolf = self.config.get("timeout_wolf", 120)
        self.timeout_seer = self.config.get("timeout_seer", 120)
        self.timeout_witch = self.config.get("timeout_witch", 120)
        self.timeout_hunter = self.config.get("timeout_hunter", 120)
        self.timeout_speaking = self.config.get("timeout_speaking", 120)
        self.timeout_vote = self.config.get("timeout_vote", 120)
        self.timeout_dead_min = self.config.get("timeout_dead_min", 10)
        self.timeout_dead_max = self.config.get("timeout_dead_max", 15)

        # 游戏房间：{群号: 房间数据}
        self.game_rooms: Dict[str, Dict] = {}

        ai_status = "已关闭" if not self.enable_ai_review else (
            f"{self.ai_review_model if self.ai_review_model else '默认模型'}"
            f"{' (自定义提示词)' if self.ai_review_prompt else ''}"
        )
        logger.info(
            f"[狼人杀] 插件已加载 | "
            f"游戏配置：{GameConfig.TOTAL_PLAYERS}人局"
            f"({GameConfig.WEREWOLF_COUNT}狼{GameConfig.SEER_COUNT+GameConfig.WITCH_COUNT+GameConfig.HUNTER_COUNT}神{GameConfig.VILLAGER_COUNT}民) | "
            f"AI复盘：{ai_status}"
        )

    @filter.command("创建房间")
    async def create_room(self, event: AstrMessageEvent):
        """创建游戏房间"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在群聊中使用此命令！")
            return

        if group_id in self.game_rooms:
            yield event.plain_result("❌ 当前群已存在游戏房间！请先结束现有游戏。")
            return

        # 初始化房间
        self.game_rooms[group_id] = {
            "players": set(),           # 玩家集合
            "player_names": {},         # {玩家ID: 昵称}
            "roles": {},                # {玩家ID: "werewolf"/"seer"/"witch"/"hunter"/"villager"}
            "alive": set(),             # 存活玩家集合
            "phase": GamePhase.WAITING, # 当前阶段
            "creator": event.get_sender_id(),  # 房主
            "night_votes": {},          # 夜晚投票：{狼人ID: 目标ID}
            "day_votes": {},            # 白天投票：{玩家ID: 目标ID}
            "night_result": None,       # 夜晚结果消息（待发布）
            "msg_origin": event.unified_msg_origin,  # 群聊消息源（用于主动发送）
            "seer_checked": False,      # 预言家是否已验人
            "banned_players": set(),    # 被禁言的玩家集合
            "bot": event.bot,           # Bot实例（用于禁言操作）
            "timer_task": None,         # 定时器任务
            "speaking_order": [],       # 发言顺序列表
            "current_speaker_index": 0, # 当前发言者索引
            "current_speaker": None,    # 当前发言者ID
            "temp_admins": set(),       # 临时管理员集合（发言时设置）
            "last_killed": None,        # 上一晚被杀的玩家ID（用于遗言）
            "witch_poison_used": False, # 女巫毒药是否已使用
            "witch_antidote_used": False, # 女巫解药是否已使用
            "witch_saved": None,        # 女巫本晚救的玩家ID
            "witch_poisoned": None,     # 女巫本晚毒的玩家ID
            "witch_acted": False,       # 女巫是否已行动
            "is_first_night": True,     # 是否第一晚（只有第一晚有遗言）
            "last_words_from_vote": False, # 遗言是否来自投票放逐
            "pk_players": [],           # 平票PK的玩家列表
            "is_pk_vote": False,        # 是否是PK投票（二次投票）
            "player_numbers": {},       # 玩家编号：{玩家ID: 编号(1-9)}
            "number_to_player": {},     # 编号到玩家的映射：{编号: 玩家ID}
            "original_group_cards": {}, # 原始群昵称：{玩家ID: 原始昵称}
            "hunter_shot": False,       # 猎人是否已开枪
            "pending_hunter_shot": None,# 待开枪的猎人ID
            "hunter_death_type": None,  # 猎人死亡方式：'wolf'(狼杀)/'vote'(投票)/'poison'(毒杀)
            "game_log": [],             # 游戏日志：记录关键事件用于AI复盘
            "current_round": 0,         # 当前回合数
            "current_speech": [],       # 当前发言者的发言内容（临时存储）
        }

        # 构建角色配置描述
        god_count = GameConfig.SEER_COUNT + GameConfig.WITCH_COUNT + GameConfig.HUNTER_COUNT
        god_roles = []
        if GameConfig.SEER_COUNT > 0:
            god_roles.append(f"预言家×{GameConfig.SEER_COUNT}" if GameConfig.SEER_COUNT > 1 else "预言家")
        if GameConfig.WITCH_COUNT > 0:
            god_roles.append(f"女巫×{GameConfig.WITCH_COUNT}" if GameConfig.WITCH_COUNT > 1 else "女巫")
        if GameConfig.HUNTER_COUNT > 0:
            god_roles.append(f"猎人×{GameConfig.HUNTER_COUNT}" if GameConfig.HUNTER_COUNT > 1 else "猎人")

        yield event.plain_result(
            f"✅ 狼人杀房间创建成功！\n\n"
            f"📋 游戏规则：\n"
            f"• {GameConfig.TOTAL_PLAYERS}人局（{GameConfig.WEREWOLF_COUNT}狼人 + {god_count}神 + {GameConfig.VILLAGER_COUNT}平民）\n"
            f"• 神职：{' + '.join(god_roles)}\n"
            f"• 夜晚：狼人办掉 → 预言家验人 → 女巫行动\n"
            f"• 白天：遗言 → 发言 → 投票放逐\n"
            f"• 遗言规则：第一晚被狼杀有遗言，投票放逐有遗言，被毒无遗言\n"
            f"• 猎人：被狼杀或投票放逐可开枪，被毒不能开枪\n"
            f"• 游戏结束后生成AI复盘报告\n\n"
            f"💡 使用 /加入房间 来参与游戏\n"
            f"👥 {GameConfig.TOTAL_PLAYERS}人齐全后，房主使用 /开始游戏"
        )

    @filter.command("加入房间")
    async def join_room(self, event: AstrMessageEvent):
        """加入游戏"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("⚠️ 请在群聊中使用此命令！")
            return

        if group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群未创建房间！请使用 /创建房间")
            return

        room = self.game_rooms[group_id]
        if room["phase"] != GamePhase.WAITING:
            yield event.plain_result("❌ 游戏已开始，无法加入！")
            return

        player_id = event.get_sender_id()
        if player_id in room["players"]:
            yield event.plain_result("⚠️ 你已经在游戏中了！")
            return

        if len(room["players"]) >= GameConfig.TOTAL_PLAYERS:
            yield event.plain_result(f"❌ 房间已满（{GameConfig.TOTAL_PLAYERS}/{GameConfig.TOTAL_PLAYERS}）！")
            return

        # 加入游戏
        room["players"].add(player_id)

        # 获取玩家昵称
        try:
            player_name = None

            # 方法1：尝试从event.unified_msg_origin获取
            if hasattr(event, 'unified_msg_origin') and event.unified_msg_origin:
                msg_origin = event.unified_msg_origin
                if hasattr(msg_origin, 'sender') and msg_origin.sender:
                    sender = msg_origin.sender
                    # 优先群昵称，其次昵称
                    player_name = getattr(sender, 'card', None) or getattr(sender, 'nickname', None)

            # 方法2：尝试从event.sender获取
            if not player_name and hasattr(event, 'sender'):
                sender = event.sender
                if isinstance(sender, dict):
                    player_name = sender.get('card') or sender.get('nickname') or sender.get('name')
                else:
                    player_name = getattr(sender, 'card', None) or getattr(sender, 'nickname', None)

            # 方法3：尝试使用message_obj
            if not player_name and hasattr(event, 'message_obj'):
                msg_obj = event.message_obj
                if hasattr(msg_obj, 'sender'):
                    sender = msg_obj.sender
                    player_name = getattr(sender, 'card', None) or getattr(sender, 'nickname', None)

            # 如果上面都失败了，使用QQ号后4位
            if not player_name:
                player_name = f"玩家{player_id[-4:]}"
        except Exception as e:
            logger.warning(f"[狼人杀] 获取玩家昵称失败: {e}")
            player_name = f"玩家{player_id[-4:]}"

        room["player_names"][player_id] = player_name

        yield event.plain_result(
            f"✅ 成功加入游戏！\n\n"
            f"当前人数：{len(room['players'])}/{GameConfig.TOTAL_PLAYERS}"
        )

    @filter.command("开始游戏")
    async def start_game(self, event: AstrMessageEvent):
        """开始游戏（房主专用）"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有创建的房间！")
            return

        room = self.game_rooms[group_id]

        # 验证房主权限
        if event.get_sender_id() != room["creator"]:
            yield event.plain_result("⚠️ 只有房主才能开始游戏！")
            return

        # 验证人数
        if len(room["players"]) != GameConfig.TOTAL_PLAYERS:
            yield event.plain_result(f"❌ 人数不足！当前 {len(room['players'])}/{GameConfig.TOTAL_PLAYERS} 人")
            return

        if room["phase"] != GamePhase.WAITING:
            yield event.plain_result("❌ 游戏已经开始！")
            return

        # 分配角色（完全随机）
        players_list = list(room["players"])

        # 分配编号（1-9）
        for index, player_id in enumerate(players_list, start=1):
            room["player_numbers"][player_id] = index
            room["number_to_player"][index] = player_id

        # 创建并打乱角色列表
        roles_pool = GameConfig.get_roles_pool()
        random.shuffle(roles_pool)

        # 分配角色
        for player_id, role in zip(players_list, roles_pool):
            room["roles"][player_id] = role

        # 初始化存活状态和验人记录
        room["alive"] = set(players_list)
        room["seer_checked"] = False  # 预言家是否已验人
        room["phase"] = GamePhase.NIGHT_WOLF
        room["current_round"] = 1  # 第一晚

        # 记录日志
        room["game_log"].append(LOG_SEPARATOR)
        room["game_log"].append("第1晚")
        room["game_log"].append(LOG_SEPARATOR)

        # 公告游戏开始
        yield event.plain_result(
            "🌙 游戏开始！天黑请闭眼...\n\n"
            "角色已分配完毕！\n\n"
            "机器人正在私聊告知各位身份...\n"
            "如未收到私聊，请使用：/查角色\n\n"
            "🐺 狼人请私聊使用：/办掉 编号\n"
            "🔮 预言家请等待狼人行动完成后使用：/验人 编号\n"
            "⏰ 剩余时间：2分钟"
        )

        # 修改玩家群昵称为编号
        await self._set_group_cards_to_numbers(group_id, room)

        # 开启全员禁言
        await self._set_group_whole_ban(group_id, room, True)

        # 启动狼人办掉定时器
        room["timer_task"] = asyncio.create_task(self._wolf_kill_timeout(group_id))

        # 主动私聊告知所有玩家身份
        await self._send_roles_to_players(group_id, room)

        # 记录狼人用于调试
        werewolves = [pid for pid, role in room["roles"].items() if role == "werewolf"]
        logger.info(f"[狼人杀] 群 {group_id} - 狼人: {werewolves}")

    @filter.command("查角色")
    async def check_role(self, event: AstrMessageEvent):
        """查看自己的角色（私聊）"""
        player_id = event.get_sender_id()

        # 必须是私聊
        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return

        # 查找玩家所在的游戏房间
        _, player_room = self._get_player_room(player_id)

        if not player_room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 获取角色
        role = player_room["roles"].get(player_id)
        if not role:
            yield event.plain_result("❌ 游戏尚未开始，角色还未分配！")
            return

        # 返回角色信息
        if role == "werewolf":
            # 找到其他狼人
            werewolves = [pid for pid, r in player_room["roles"].items() if r == "werewolf"]
            teammates = [pid for pid in werewolves if pid != player_id]

            # 狼人队友信息
            teammate_info = ""
            if teammates:
                teammate_names = ", ".join([self._format_player_name(pid, player_room) for pid in teammates])
                teammate_info = f"\n\n🤝 你的队友：{teammate_names}"

            # 列出所有其他玩家（除了狼人自己）
            other_players = [pid for pid in player_room["players"] if pid not in werewolves]
            players_list = "\n".join([f"  • {self._format_player_name(pid, player_room)}" for pid in other_players])

            role_text = (
                f"🐺 狼人\n\n"
                f"你的目标：消灭所有平民！{teammate_info}\n\n"
                f"📋 可选目标列表：\n{players_list}\n\n"
                f"💡 夜晚私聊使用命令：\n"
                f"  /办掉 编号 - 投票办掉目标\n"
                f"  /密谋 消息 - 与队友交流\n"
                f"示例：/办掉 {list(room['player_numbers'].values())[0] if room.get('player_numbers') else '1'}"
            )
        elif role == "seer":
            # 列出所有其他玩家（预言家可以验所有人）
            other_players = [pid for pid in player_room["players"] if pid != player_id]
            players_list = "\n".join([f"  • {self._format_player_name(pid, player_room)}" for pid in other_players])

            role_text = (
                f"🔮 预言家\n\n"
                f"你的目标：找出狼人，帮助平民获胜！\n\n"
                f"📋 可验证玩家列表：\n{players_list}\n\n"
                f"💡 夜晚私聊使用命令：\n"
                f"/验人 编号\n"
                f"示例：/验人 {room['player_numbers'][other_players[0]] if other_players else '3'}\n\n"
                f"⚠️ 注意：每晚只能验证一个人！"
            )
        elif role == "witch":
            # 列出所有其他玩家
            other_players = [pid for pid in player_room["players"] if pid != player_id]
            players_list = "\n".join([f"  • {self._format_player_name(pid, player_room)}" for pid in other_players])

            role_text = (
                f"💊 女巫\n\n"
                f"你的目标：帮助平民获胜！\n\n"
                f"你拥有两种药：\n"
                f"💉 解药：可以救活当晚被杀的人（只能用一次）\n"
                f"💊 毒药：可以毒杀任何人（只能用一次）\n\n"
                f"⚠️ 注意：\n"
                f"• 同一晚不能同时使用两种药\n"
                f"• 解药只能救当晚被杀的人\n"
                f"• 每晚女巫行动时会告知谁被杀\n\n"
                f"💡 夜晚私聊使用命令：\n"
                f"  /救人 - 救活被杀的人\n"
                f"  /毒人 编号 - 毒杀某人\n"
                f"  /不操作 - 不使用任何药"
            )
        elif role == "hunter":
            # 列出所有其他玩家
            other_players = [pid for pid in player_room["players"] if pid != player_id]
            players_list = "\n".join([f"  • {self._format_player_name(pid, player_room)}" for pid in other_players])

            role_text = (
                f"🔫 猎人\n\n"
                f"你的目标：帮助好人获胜！\n\n"
                f"你的技能：\n"
                f"• 被狼人办掉时可以开枪带走一人\n"
                f"• 被投票放逐时可以开枪带走一人\n"
                f"• 被女巫毒死时不能开枪（死的太突然）\n\n"
                f"📋 可选目标列表：\n{players_list}\n\n"
                f"💡 当你死亡时（非毒死），私聊使用命令：\n"
                f"  /开枪 编号 - 带走一个人\n"
                f"示例：/开枪 1"
            )
        else:
            role_text = "👤 平民\n\n你的目标：找出并放逐所有狼人！\n白天投票时使用 /投票 编号 放逐可疑玩家。"

        yield event.plain_result(f"🎭 你的角色是：\n\n{role_text}")

    @filter.command("游戏状态")
    async def show_status(self, event: AstrMessageEvent):
        """查看游戏状态"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]
        alive_count = len(room["alive"])
        total_count = len(room["players"])

        status_text = (
            f"📊 游戏状态\n\n"
            f"阶段：{room['phase'].value}\n"
            f"存活人数：{alive_count}/{total_count}\n"
        )

        yield event.plain_result(status_text)

    @filter.command("结束游戏")
    async def end_game(self, event: AstrMessageEvent):
        """强制结束游戏（房主专用）"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]
        if event.get_sender_id() != room["creator"]:
            yield event.plain_result("⚠️ 只有房主才能结束游戏！")
            return

        # 清理房间
        await self._cleanup_room(group_id)

        yield event.plain_result("✅ 游戏已强制结束！")

    @filter.command("办掉")
    async def werewolf_kill(self, event: AstrMessageEvent):
        """狼人夜晚办掉目标（支持私聊）"""
        player_id = event.get_sender_id()

        # 查找玩家所在的游戏房间
        group_id, room = self._get_player_room(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证阶段
        if room["phase"] != GamePhase.NIGHT_WOLF:
            yield event.plain_result("⚠️ 现在不是狼人行动阶段！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "werewolf":
            yield event.plain_result("❌ 你不是狼人！")
            return

        # 验证存活
        if player_id not in room["alive"]:
            yield event.plain_result("❌ 你已经出局了！")
            return

        # 获取目标（支持@、编号、QQ号）
        target_str = self._get_target_user(event)
        if not target_str:
            yield event.plain_result("❌ 请指定目标！\n使用：/办掉 编号\n示例：/办掉 1")
            return

        # 解析目标（编号或QQ号）
        target_id = self._parse_target(target_str, room)
        if not target_id:
            yield event.plain_result(f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）")
            return

        # 验证目标存活
        if target_id not in room["alive"]:
            yield event.plain_result("❌ 目标玩家已经出局！")
            return

        # 记录投票（允许选择任何存活玩家，包括队友和自己）
        room["night_votes"][player_id] = target_id

        # 记录日志
        voter_name = self._format_player_name(player_id, room)
        target_name = self._format_player_name(target_id, room)
        room["game_log"].append(f"🐺 {voter_name}（狼人）选择刀 {target_name}")

        yield event.plain_result(f"✅ 你选择了办掉目标！当前 {len(room['night_votes'])}/{len([p for p, r in room['roles'].items() if r == 'werewolf' and p in room['alive']])} 人已投票")

        # 检查是否所有狼人都投票了
        werewolves = [pid for pid, role in room["roles"].items() if role == "werewolf" and pid in room["alive"]]
        if len(room["night_votes"]) >= len(werewolves):
            # 取消狼人定时器
            self._cancel_timer(room)

            # 处理夜晚办掉，将结果存储到房间
            await self._process_night_kill(group_id)

            # 检查游戏是否结束（_process_night_kill可能会清理房间）
            if group_id not in self.game_rooms:
                yield event.plain_result("✅ 所有狼人已投票完成！游戏结束。")
                return  # 游戏已结束，退出

            # 进入预言家验人阶段（不管预言家是否存活都进入，避免泄露身份）
            room["phase"] = GamePhase.NIGHT_SEER
            room["seer_checked"] = False

            # 在群里发送预言家验人提示
            if room.get("msg_origin"):
                seer_msg = MessageChain().message("🔮 狼人行动完成！\n预言家请私聊机器人验人：/验人 编号\n⏰ 剩余时间：2分钟")
                await self.context.send_message(room["msg_origin"], seer_msg)

            # 启动预言家定时器（如果预言家已死，等待随机时间后自动进入下一阶段）
            import random
            seer_alive = any(r == "seer" and pid in room["alive"] for pid, r in room["roles"].items())
            if seer_alive:
                # 预言家存活，正常倒计时
                wait_time = self.timeout_seer
            else:
                # 预言家已死，随机等待
                wait_time = random.uniform(self.timeout_dead_min, self.timeout_dead_max)

            room["timer_task"] = asyncio.create_task(self._seer_check_timeout(group_id, wait_time))

            yield event.plain_result("✅ 所有狼人已投票完成！现在进入预言家验人阶段。")

    @filter.command("密谋")
    async def werewolf_chat(self, event: AstrMessageEvent):
        """狼人队友之间交流（私聊）"""
        player_id = event.get_sender_id()

        # 必须是私聊
        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return

        # 查找玩家所在的游戏房间
        room = None
        group_id = None
        for gid, r in self.game_rooms.items():
            if player_id in r["players"]:
                room = r
                group_id = gid
                break

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "werewolf":
            yield event.plain_result("❌ 你不是狼人！")
            return

        # 验证存活
        if player_id not in room["alive"]:
            yield event.plain_result("❌ 你已经出局了！")
            return

        # 验证阶段（只能在夜晚狼人行动阶段交流）
        if room["phase"] != GamePhase.NIGHT_WOLF:
            yield event.plain_result("⚠️ 只能在夜晚狼人行动阶段与队友交流！")
            return

        # 获取消息内容（去掉命令部分）
        # 支持多种格式：/密谋、/狼人杀 密谋
        import re
        message_text = re.sub(r'^/?\s*(狼人杀\s*)?密谋\s*', '', event.message_str).strip()
        if not message_text:
            yield event.plain_result("❌ 请输入要发送的消息！\n用法：/密谋 消息内容")
            return

        # 找到其他存活的狼人队友
        werewolves = [pid for pid, role in room["roles"].items() if role == "werewolf" and pid in room["alive"] and pid != player_id]

        if not werewolves:
            yield event.plain_result("❌ 没有其他存活的狼人队友！")
            return

        # 发送消息给所有队友
        sender_name = self._format_player_name(player_id, room)
        teammate_msg = f"🐺 队友 {sender_name} 说：\n{message_text}"

        success_count = 0
        for teammate_id in werewolves:
            try:
                await room["bot"].send_private_msg(user_id=int(teammate_id), message=teammate_msg)
                success_count += 1
            except Exception as e:
                logger.error(f"[狼人杀] 发送消息给狼人 {teammate_id} 失败: {e}")

        # 记录日志
        room["game_log"].append(f"💬 {sender_name}（狼人）密谋：{message_text}")

        yield event.plain_result(f"✅ 消息已发送给 {success_count} 名队友！")

    @filter.command("验人")
    async def seer_check(self, event: AstrMessageEvent):
        """预言家夜晚验人（支持私聊）"""
        player_id = event.get_sender_id()

        # 查找玩家所在的游戏房间
        group_id, room = self._get_player_room(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证阶段
        if room["phase"] != GamePhase.NIGHT_SEER:
            yield event.plain_result("⚠️ 现在不是预言家验人阶段！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "seer":
            yield event.plain_result("❌ 你不是预言家！")
            return

        # 检查是否已经验过人
        if room.get("seer_checked"):
            yield event.plain_result("❌ 你今晚已经验过人了！")
            return

        # 获取目标（支持@、编号、QQ号）
        target_str = self._get_target_user(event)
        if not target_str:
            yield event.plain_result("❌ 请指定验证目标！\n使用：/验人 编号\n示例：/验人 3")
            return

        # 解析目标（编号或QQ号）
        target_id = self._parse_target(target_str, room)
        if not target_id:
            yield event.plain_result(f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）")
            return

        # 不能验自己
        if target_id == player_id:
            yield event.plain_result("❌ 不能验证自己！")
            return

        # 获取目标身份
        target_role = room["roles"].get(target_id)
        is_werewolf = (target_role == "werewolf")

        # 标记已验人
        room["seer_checked"] = True

        # 取消预言家定时器
        self._cancel_timer(room)

        # 返回验人结果
        target_name = self._format_player_name(target_id, room)
        seer_name = self._format_player_name(player_id, room)
        if is_werewolf:
            result_msg = f"🔮 验人结果：\n\n玩家 {target_name} 是 🐺 狼人！"
            # 记录日志
            room["game_log"].append(f"🔮 {seer_name}（预言家）验 {target_name}：狼人")
        else:
            result_msg = f"🔮 验人结果：\n\n玩家 {target_name} 是 ✅ 好人！"
            # 记录日志
            room["game_log"].append(f"🔮 {seer_name}（预言家）验 {target_name}：好人")

        yield event.plain_result(result_msg)

        # 验人完成后进入女巫阶段
        # 找到女巫（不管是否存活都要通知）
        witch_id = None
        for pid, r in room["roles"].items():
            if r == "witch":
                witch_id = pid
                break

        if witch_id:
            # 进入女巫行动阶段
            room["phase"] = GamePhase.NIGHT_WITCH
            room["witch_acted"] = False
            room["witch_saved"] = None
            room["witch_poisoned"] = None

            # 在群里发送女巫行动提示（不透露女巫是否存活）
            if room.get("msg_origin"):
                witch_msg = MessageChain().message("💊 预言家验人完成！\n女巫请私聊机器人行动\n⏰ 剩余时间：2分钟")
                await self.context.send_message(room["msg_origin"], witch_msg)

            # 给女巫发私聊，告知谁被杀（即使女巫已死也发送，让她知道自己被杀可以救自己）
            await self._notify_witch(group_id, witch_id, room)

            # 启动女巫定时器
            # 如果女巫被杀了，给足够时间让她救自己
            # 如果女巫没被杀但已死（前几晚死的），用随机短时间
            import random
            witch_alive = witch_id in room["alive"]
            witch_is_killed_tonight = (room.get("last_killed") == witch_id)

            if witch_alive or witch_is_killed_tonight:
                # 女巫存活，或者女巫今晚被杀（可以救自己）
                wait_time = self.timeout_witch
            else:
                # 女巫早已死亡（前几晚死的），随机等待
                wait_time = random.uniform(self.timeout_dead_min, self.timeout_dead_max)

            room["timer_task"] = asyncio.create_task(self._witch_timeout(group_id, wait_time))

            yield event.plain_result("✅ 预言家验人完成！现在进入女巫行动阶段。")
        else:
            # 不应该发生（游戏配置错误）
            logger.error(f"[狼人杀] 游戏配置错误：找不到女巫角色")
            yield event.plain_result("❌ 游戏配置错误！")

    @filter.command("救人")
    async def witch_save(self, event: AstrMessageEvent):
        """女巫使用解药救人（私聊）"""
        player_id = event.get_sender_id()

        # 查找玩家所在的游戏房间
        group_id, room = self._get_player_room(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证阶段
        if room["phase"] != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "witch":
            yield event.plain_result("❌ 你不是女巫！")
            return

        # 检查女巫是否被杀（如果被杀了，只能救自己）
        witch_killed = (player_id == room.get("last_killed"))

        # 检查是否已经行动
        if room.get("witch_acted"):
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        # 检查解药是否已使用
        if room.get("witch_antidote_used"):
            yield event.plain_result("❌ 解药已经用过了！")
            return

        # 检查是否有被杀的人
        if not room.get("last_killed"):
            yield event.plain_result("❌ 今晚没有人被杀，无法使用解药！")
            return

        # 如果女巫被杀了，检查她是否在救自己
        if witch_killed and room.get("last_killed") != player_id:
            yield event.plain_result("❌ 你已经出局了！只有被杀的人才能在死后救自己！")
            return

        # 使用解药救人
        room["witch_saved"] = room["last_killed"]
        room["witch_antidote_used"] = True
        room["witch_acted"] = True

        # 取消定时器
        self._cancel_timer(room)

        saved_name = self._format_player_name(room["last_killed"], room)
        witch_name = self._format_player_name(player_id, room)

        # 记录日志
        room["game_log"].append(f"💊 {witch_name}（女巫）使用解药救了 {saved_name}")

        yield event.plain_result(f"✅ 你使用解药救了 {saved_name}！")

        # 女巫行动完成，准备天亮
        await self._witch_finish(group_id)

    @filter.command("毒人")
    async def witch_poison(self, event: AstrMessageEvent):
        """女巫使用毒药毒人（私聊）"""
        player_id = event.get_sender_id()

        # 查找玩家所在的游戏房间
        group_id, room = self._get_player_room(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证阶段
        if room["phase"] != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "witch":
            yield event.plain_result("❌ 你不是女巫！")
            return

        # 检查是否已经行动
        if room.get("witch_acted"):
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        # 检查毒药是否已使用
        if room.get("witch_poison_used"):
            yield event.plain_result("❌ 毒药已经用过了！")
            return

        # 获取目标（支持@、编号、QQ号）
        target_str = self._get_target_user(event)
        if not target_str:
            yield event.plain_result("❌ 请指定毒人目标！\n使用：/毒人 编号\n示例：/毒人 5")
            return

        # 解析目标（编号或QQ号）
        target_id = self._parse_target(target_str, room)
        if not target_id:
            yield event.plain_result(f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）")
            return

        # 验证目标存活
        if target_id not in room["alive"]:
            yield event.plain_result("❌ 目标玩家已经出局！")
            return

        # 不能毒自己
        if target_id == player_id:
            yield event.plain_result("❌ 不能毒自己！")
            return

        # 使用毒药毒人
        room["witch_poisoned"] = target_id
        room["witch_poison_used"] = True
        room["witch_acted"] = True

        # 取消定时器
        self._cancel_timer(room)

        poisoned_name = self._format_player_name(target_id, room)
        witch_name = self._format_player_name(player_id, room)

        # 记录日志
        room["game_log"].append(f"💊 {witch_name}（女巫）使用毒药毒了 {poisoned_name}")

        yield event.plain_result(f"✅ 你使用毒药毒了 {poisoned_name}！")

        # 女巫行动完成，准备天亮
        await self._witch_finish(group_id)

    @filter.command("不操作")
    async def witch_pass(self, event: AstrMessageEvent):
        """女巫选择不操作（私聊）"""
        player_id = event.get_sender_id()

        # 查找玩家所在的游戏房间
        group_id, room = self._get_player_room(player_id)

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证阶段
        if room["phase"] != GamePhase.NIGHT_WITCH:
            yield event.plain_result("⚠️ 现在不是女巫行动阶段！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "witch":
            yield event.plain_result("❌ 你不是女巫！")
            return

        # 检查是否已经行动
        if room.get("witch_acted"):
            yield event.plain_result("❌ 你今晚已经行动过了！")
            return

        # 标记已行动
        room["witch_acted"] = True

        # 取消定时器
        self._cancel_timer(room)

        # 记录日志
        witch_name = self._format_player_name(player_id, room)
        room["game_log"].append(f"💊 {witch_name}（女巫）选择不操作")

        yield event.plain_result("✅ 你选择不操作！")

        # 女巫行动完成，准备天亮
        await self._witch_finish(group_id)


    @filter.command("遗言完毕")
    async def finish_last_words(self, event: AstrMessageEvent):
        """被杀玩家遗言完毕"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]
        player_id = event.get_sender_id()

        # 验证阶段
        if room["phase"] != GamePhase.LAST_WORDS:
            yield event.plain_result("⚠️ 现在不是遗言阶段！")
            return

        # 验证是否是被杀的玩家
        if room.get("last_killed") != player_id:
            yield event.plain_result("⚠️ 只有被杀的玩家才能使用此命令！")
            return

        # 取消定时器
        self._cancel_timer(room)

        # 记录遗言内容到游戏日志
        player_name = self._format_player_name(player_id, room)
        if room["current_speech"]:
            # 合并多条发言
            full_speech = " ".join(room["current_speech"])
            # 限制长度，避免过长
            if len(full_speech) > 200:
                full_speech = full_speech[:200] + "..."

            room["game_log"].append(f"💀遗言：{player_name} - {full_speech}")
            logger.info(f"[狼人杀] 记录遗言: {player_name}: {full_speech[:50]}")
        else:
            # 如果没有捕获到遗言内容
            room["game_log"].append(f"💀遗言：{player_name} - [未捕获到文字内容]")

        # 清空当前发言缓存
        room["current_speech"] = []

        # 取消临时管理员
        await self._remove_temp_admin(group_id, player_id, room)

        # 禁言被杀玩家（确保遗言者无法再说话）
        await self._ban_player(group_id, player_id, room)

        # 确保全员禁言状态（遗言阶段已经开启，这里再次确认）
        await self._set_group_whole_ban(group_id, room, True)

        yield event.plain_result("✅ 遗言完毕！")

        # 检查遗言是否来自投票放逐
        if room.get("last_words_from_vote"):
            # 来自投票放逐，进入夜晚
            room["phase"] = GamePhase.NIGHT_WOLF
            room["seer_checked"] = False  # 重置预言家验人标记
            room["is_first_night"] = False  # 第一晚结束
            room["last_words_from_vote"] = False  # 重置标记
            room["current_round"] += 1  # 回合数+1

            # 记录日志
            room["game_log"].append(LOG_SEPARATOR)
            room["game_log"].append(f"第{room['current_round']}晚")
            room["game_log"].append(LOG_SEPARATOR)
            # 启动狼人定时器
            room["timer_task"] = asyncio.create_task(self._wolf_kill_timeout(group_id))

            # 发送夜晚消息
            if room.get("msg_origin"):
                night_msg = MessageChain().message(
                    "🌙 夜晚降临，天黑请闭眼...\n\n"
                    "🐺 狼人请私聊使用：/办掉 编号\n"
                    "🔮 预言家请等待狼人行动完成\n"
                    "⏰ 剩余时间：2分钟"
                )
                await self.context.send_message(room["msg_origin"], night_msg)
        else:
            # 来自夜晚被杀，进入发言阶段
            # 清空遗言相关状态
            room["last_killed"] = None
            # 第一晚结束
            room["is_first_night"] = False

            room["phase"] = GamePhase.DAY_SPEAKING
            await self._start_speaking_phase(group_id)

    @filter.command("发言完毕")
    async def finish_speaking(self, event: AstrMessageEvent):
        """当前发言者/PK发言者发言完毕"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]
        player_id = event.get_sender_id()

        # 验证阶段（支持发言阶段和PK阶段）
        if room["phase"] not in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK]:
            yield event.plain_result("⚠️ 现在不是发言阶段！")
            return

        # 验证是否是当前发言者
        if room.get("current_speaker") != player_id:
            yield event.plain_result("⚠️ 现在不是你的发言时间！")
            return

        # 取消定时器
        self._cancel_timer(room)

        # 记录发言内容到游戏日志
        player_name = self._format_player_name(player_id, room)
        if room["current_speech"]:
            # 合并多条发言
            full_speech = " ".join(room["current_speech"])
            # 限制长度，避免过长
            if len(full_speech) > 200:
                full_speech = full_speech[:200] + "..."

            phase_tag = "💬PK发言" if room["phase"] == GamePhase.DAY_PK else "💬发言"
            room["game_log"].append(f"{phase_tag}：{player_name} - {full_speech}")
            logger.info(f"[狼人杀] 记录发言: {player_name}: {full_speech[:50]}")
        else:
            # 如果没有捕获到发言内容，也记录一下（可能是纯表情等）
            phase_tag = "💬PK发言" if room["phase"] == GamePhase.DAY_PK else "💬发言"
            room["game_log"].append(f"{phase_tag}：{player_name} - [未捕获到文字内容]")

        # 清空当前发言缓存
        room["current_speech"] = []

        # 取消当前发言者的临时管理员
        await self._remove_temp_admin(group_id, player_id, room)

        yield event.plain_result("✅ 发言完毕！")

        # 根据阶段决定下一步
        if room["phase"] == GamePhase.DAY_PK:
            # PK发言，切换到下一个PK发言者
            room["current_speaker_index"] += 1
            await self._next_pk_speaker(group_id)
        else:
            # 正常发言，切换到下一个发言者
            room["current_speaker_index"] += 1
            await self._next_speaker(group_id)

    @filter.command("开始投票")
    async def start_vote(self, event: AstrMessageEvent):
        """跳过发言直接进入投票阶段（房主专用）"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]

        # 验证房主权限
        if event.get_sender_id() != room["creator"]:
            yield event.plain_result("⚠️ 只有房主才能跳过发言环节！")
            return

        # 验证阶段（支持普通发言和PK发言）
        if room["phase"] not in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK]:
            yield event.plain_result("⚠️ 现在不是发言阶段！")
            return

        # 取消定时器
        self._cancel_timer(room)

        # 取消当前发言者的临时管理员
        if room.get("current_speaker"):
            await self._remove_temp_admin(group_id, room["current_speaker"], room)

        yield event.plain_result("✅ 房主跳过发言环节，直接进入投票！")

        # 根据阶段决定投票类型
        if room["phase"] == GamePhase.DAY_PK:
            # PK发言阶段 → PK投票（只能投平票玩家）
            await self._start_pk_vote(group_id)
        else:
            # 普通发言阶段 → 普通投票
            await self._auto_start_vote(group_id)

    @filter.command("投票")
    async def day_vote(self, event: AstrMessageEvent):
        """白天投票放逐"""
        group_id = event.get_group_id()
        if not group_id or group_id not in self.game_rooms:
            yield event.plain_result("❌ 当前群没有进行中的游戏！")
            return

        room = self.game_rooms[group_id]
        player_id = event.get_sender_id()

        # 验证阶段
        if room["phase"] != GamePhase.DAY_VOTE:
            yield event.plain_result("⚠️ 现在不是投票阶段！使用 /开始投票 进入投票")
            return

        # 验证玩家在游戏中且存活
        if player_id not in room["players"]:
            yield event.plain_result("❌ 你不在游戏中！")
            return

        if player_id not in room["alive"]:
            yield event.plain_result("❌ 你已经出局了！")
            return

        # 获取目标（支持@、编号、QQ号）
        target_str = self._get_target_user(event)
        if not target_str:
            yield event.plain_result("❌ 请指定投票目标！\n使用：/投票 编号\n示例：/投票 2")
            return

        # 解析目标（编号或QQ号）
        target_id = self._parse_target(target_str, room)
        if not target_id:
            yield event.plain_result(f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）")
            return

        # 验证目标存活
        if target_id not in room["alive"]:
            yield event.plain_result("❌ 目标玩家已经出局！")
            return

        # 如果是PK投票，验证目标必须在PK玩家列表中
        if room.get("is_pk_vote"):
            if target_id not in room.get("pk_players", []):
                pk_names = [self._format_player_name(pid, room) for pid in room["pk_players"]]
                yield event.plain_result(
                    f"❌ PK投票只能投给平票玩家！\n\n"
                    f"可投票对象：\n" + "\n".join([f"  • {name}" for name in pk_names])
                )
                return

        # 记录投票
        room["day_votes"][player_id] = target_id

        # 记录日志
        voter_name = self._format_player_name(player_id, room)
        target_name = self._format_player_name(target_id, room)
        if room.get("is_pk_vote"):
            room["game_log"].append(f"🗳️ PK投票：{voter_name} 投给 {target_name}")
        else:
            room["game_log"].append(f"🗳️ {voter_name} 投票给 {target_name}")

        yield event.plain_result(f"✅ 投票成功！当前已投票 {len(room['day_votes'])}/{len(room['alive'])} 人")

        # 检查是否所有人都投票了
        if len(room["day_votes"]) >= len(room["alive"]):
            # 取消投票定时器
            self._cancel_timer(room)

            result = await self._process_day_vote(group_id)
            if result:
                yield event.plain_result(result)

    @filter.command("开枪")
    async def hunter_shoot(self, event: AstrMessageEvent):
        """猎人开枪（私聊）"""
        player_id = event.get_sender_id()

        # 必须是私聊
        if not event.is_private_chat():
            yield event.plain_result("⚠️ 请私聊机器人使用此命令！")
            return

        # 查找玩家所在的游戏房间
        room = None
        group_id = None
        for gid, r in self.game_rooms.items():
            if player_id in r["players"]:
                room = r
                group_id = gid
                break

        if not room:
            yield event.plain_result("❌ 你没有参与任何游戏！")
            return

        # 验证身份
        if room["roles"].get(player_id) != "hunter":
            yield event.plain_result("❌ 你不是猎人！")
            return

        # 验证是否在待开枪状态
        if room.get("pending_hunter_shot") != player_id:
            yield event.plain_result("❌ 当前不能开枪！")
            return

        # 验证死亡方式（被毒不能开枪）
        if room.get("hunter_death_type") == "poison":
            yield event.plain_result("❌ 你被女巫毒死，不能开枪！")
            return

        # 获取目标（支持@、编号、QQ号）
        target_str = self._get_target_user(event)
        if not target_str:
            yield event.plain_result("❌ 请指定目标！\n使用：/开枪 编号\n示例：/开枪 1")
            return

        # 解析目标（编号或QQ号）
        target_id = self._parse_target(target_str, room)
        if not target_id:
            yield event.plain_result(f"❌ 无效的目标：{target_str}\n请使用玩家编号（1-9）")
            return

        # 验证目标
        if target_id not in room["alive"]:
            yield event.plain_result(f"❌ {self._format_player_name(target_id, room)} 已经出局！")
            return

        if target_id == player_id:
            yield event.plain_result("❌ 不能开枪带走自己！")
            return

        # 执行开枪
        room["alive"].discard(target_id)
        room["hunter_shot"] = True
        room["pending_hunter_shot"] = None

        target_name = self._format_player_name(target_id, room)
        hunter_name = self._format_player_name(player_id, room)

        # 记录日志
        room["game_log"].append(f"🔫 {hunter_name}（猎人）开枪带走 {target_name}")

        yield event.plain_result(f"💥 你开枪带走了 {target_name}！")

        # 禁言被带走的玩家
        await self._ban_player(group_id, target_id, room)

        # 通知群聊
        if room.get("msg_origin"):
            shot_msg = MessageChain().message(
                f"💥 猎人开枪带走了 {target_name}！\n\n"
                f"剩余存活玩家：{len(room['alive'])} 人"
            )
            await self.context.send_message(room["msg_origin"], shot_msg)

        # 取消定时器
        self._cancel_timer(room)

        # 检查游戏是否结束
        victory_msg, winning_faction = self._check_victory_condition(room)
        if victory_msg:
            result_text = f"🎉 {victory_msg}\n游戏结束！\n\n"
            result_text += self._get_all_players_roles(room)
            room["phase"] = GamePhase.FINISHED

            # 发送结果
            if room.get("msg_origin"):
                result_msg = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_msg)

                # 生成AI复盘（异步，不阻塞）
                try:
                    ai_review = await self._generate_ai_review(room, winning_faction)
                    if ai_review:
                        review_msg = MessageChain().message(ai_review)
                        await self.context.send_message(room["msg_origin"], review_msg)
                except Exception as e:
                    logger.error(f"[狼人杀] AI复盘发送失败: {e}")

            # 清理房间
            await self._cleanup_room(group_id)
            return

        # 游戏继续，根据猎人死亡方式决定下一阶段
        hunter_id = player_id
        death_type = room.get("hunter_death_type")

        if death_type == "vote":
            # 猎人被投票放逐，进入遗言阶段
            room["phase"] = GamePhase.LAST_WORDS
            room["last_killed"] = hunter_id
            room["last_words_from_vote"] = True
            await self._start_last_words(group_id)
        elif death_type == "wolf":
            # 猎人被狼杀，根据是否第一晚决定
            if room.get("is_first_night") and (room.get("last_killed") or room.get("witch_poisoned")):
                # 第一晚有遗言
                room["phase"] = GamePhase.LAST_WORDS
                if room.get("last_killed"):
                    await self._start_last_words(group_id)
                elif room.get("witch_poisoned"):
                    room["last_killed"] = room["witch_poisoned"]
                    await self._start_last_words(group_id)
            else:
                # 其他夜晚没有遗言，直接进入发言阶段
                if room.get("last_killed"):
                    await self._ban_player(group_id, room["last_killed"], room)

                room["phase"] = GamePhase.DAY_SPEAKING
                await self._start_speaking_phase(group_id)

    @filter.command("狼人杀帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        # 动态生成游戏配置描述
        god_count = GameConfig.SEER_COUNT + GameConfig.WITCH_COUNT + GameConfig.HUNTER_COUNT

        help_text = (
            "📖 狼人杀游戏 - 命令列表\n\n"
            "基础命令：\n"
            "  /创建房间 - 创建游戏房间\n"
            "  /加入房间 - 加入房间\n"
            "  /开始游戏 - 开始游戏（房主）\n"
            "  /查角色 - 查看角色（私聊）\n"
            "  /游戏状态 - 查看游戏状态\n"
            "  /结束游戏 - 结束游戏（房主）\n\n"
            f"游戏命令（使用编号1-{GameConfig.TOTAL_PLAYERS}）：\n"
            "  /办掉 编号 - 狼人夜晚办掉（如：/办掉 1）\n"
            "  /密谋 消息 - 狼人与队友交流\n"
            "  /验人 编号 - 预言家查验（如：/验人 3）\n"
            "  /毒人 编号 - 女巫使用毒药（如：/毒人 5）\n"
            "  /救人 - 女巫使用解药\n"
            "  /不操作 - 女巫不使用道具\n"
            "  /开枪 编号 - 猎人开枪带走（如：/开枪 2）\n"
            "  /发言完毕 - 发言说完\n"
            "  /遗言完毕 - 遗言说完\n"
            "  /投票 编号 - 白天投票放逐（如：/投票 2）\n"
            "  /开始投票 - 跳过发言直接投票（房主）\n\n"
            "游戏规则：\n"
            f"• {GameConfig.TOTAL_PLAYERS}人局：{GameConfig.WEREWOLF_COUNT}狼人 + {god_count}神 + {GameConfig.VILLAGER_COUNT}平民\n"
            f"• 使用编号（1-{GameConfig.TOTAL_PLAYERS}号）代替QQ号\n"
            "• 遗言规则：第一晚被狼杀有遗言，投票放逐有遗言，被毒无遗言\n"
            "• 猎人：被狼杀或投票放逐可开枪，被毒不能开枪\n"
            f"• 游戏结束后{'生成AI复盘报告' if self.enable_ai_review else '不生成AI复盘'}\n"
            "• 狼人胜利：好人 ≤ 狼人 或 神职全灭\n"
            "• 好人胜利：狼人全部出局"
        )
        yield event.plain_result(help_text)

    # ========== 辅助函数 ==========

    def _get_player_room(self, player_id: str) -> tuple:
        """根据玩家ID查找所在房间

        返回：(group_id, room) 或 (None, None)
        """
        for group_id, room in self.game_rooms.items():
            if player_id in room["players"]:
                return group_id, room
        return None, None

    def _format_player_name(self, player_id: str, room: Dict) -> str:
        """格式化玩家显示名称：编号.昵称"""
        name = room["player_names"].get(player_id, "未知")
        number = room["player_numbers"].get(player_id, "?")
        return f"{number}号.{name}"

    def _parse_target(self, target_str: str, room: Dict) -> str:
        """解析目标玩家（支持编号或QQ号）
        返回玩家ID，如果解析失败返回None
        """
        # 尝试作为编号解析（1-9的数字）
        try:
            number = int(target_str)
            if number in room["number_to_player"]:
                return room["number_to_player"][number]
        except ValueError:
            pass

        # 尝试作为QQ号解析
        if target_str in room["players"]:
            return target_str

        return None

    async def _set_group_cards_to_numbers(self, group_id: str, room: Dict):
        """将玩家群昵称改为编号"""
        for player_id, number in room["player_numbers"].items():
            try:
                # 获取当前群昵称（保存以便恢复）
                if player_id not in room["original_group_cards"]:
                    # 使用player_names作为原始昵称
                    room["original_group_cards"][player_id] = room["player_names"].get(player_id, "")

                # 设置新昵称为"编号号"
                new_card = f"{number}号"
                await room["bot"].set_group_card(group_id=int(group_id), user_id=int(player_id), card=new_card)
                logger.info(f"[狼人杀] 已将玩家 {player_id} 群昵称改为 {new_card}")
            except Exception as e:
                logger.error(f"[狼人杀] 修改玩家 {player_id} 群昵称失败: {e}")

    async def _restore_group_cards(self, group_id: str, room: Dict):
        """恢复玩家原始群昵称"""
        for player_id, original_card in room.get("original_group_cards", {}).items():
            try:
                await room["bot"].set_group_card(group_id=int(group_id), user_id=int(player_id), card=original_card)
                logger.info(f"[狼人杀] 已恢复玩家 {player_id} 群昵称为 {original_card}")
            except Exception as e:
                logger.error(f"[狼人杀] 恢复玩家 {player_id} 群昵称失败: {e}")

    async def _cleanup_room(self, group_id: str):
        """清理游戏房间"""
        if group_id in self.game_rooms:
            room = self.game_rooms[group_id]
            # 恢复群昵称
            await self._restore_group_cards(group_id, room)
            # 取消定时器
            self._cancel_timer(room)
            # 解除所有禁言
            await self._unban_all_players(group_id, room)
            # 解除全员禁言
            await self._set_group_whole_ban(group_id, room, False)
            # 取消所有临时管理员
            await self._clear_temp_admins(group_id, room)
            # 删除房间
            del self.game_rooms[group_id]
            logger.info(f"[狼人杀] 群 {group_id} 房间已清理")

    def _get_all_players_roles(self, room: Dict) -> str:
        """获取所有玩家的身份列表"""
        result = "📜 身份公布：\n\n"

        # 按角色分组
        werewolves = []
        seers = []
        witches = []
        hunters = []
        villagers = []

        for player_id in room["players"]:
            role = room["roles"].get(player_id)
            player_name = self._format_player_name(player_id, room)

            if role == "werewolf":
                werewolves.append(player_name)
            elif role == "seer":
                seers.append(player_name)
            elif role == "witch":
                witches.append(player_name)
            elif role == "hunter":
                hunters.append(player_name)
            elif role == "villager":
                villagers.append(player_name)

        # 格式化输出
        if werewolves:
            result += "🐺 狼人：\n"
            for name in werewolves:
                result += f"  • {name}\n"
            result += "\n"

        if seers:
            result += "🔮 预言家：\n"
            for name in seers:
                result += f"  • {name}\n"
            result += "\n"

        if witches:
            result += "💊 女巫：\n"
            for name in witches:
                result += f"  • {name}\n"
            result += "\n"

        if hunters:
            result += "🔫 猎人：\n"
            for name in hunters:
                result += f"  • {name}\n"
            result += "\n"

        if villagers:
            result += "👤 平民：\n"
            for name in villagers:
                result += f"  • {name}\n"

        return result

    async def _ban_player(self, group_id: str, player_id: str, room: Dict):
        """禁言玩家"""
        try:
            await room["bot"].set_group_ban(
                group_id=int(group_id),
                user_id=int(player_id),
                duration=86400 * GameConfig.BAN_DURATION_DAYS  # 游戏结束后会解除
            )
            room["banned_players"].add(player_id)
            logger.info(f"[狼人杀] 已禁言玩家 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 禁言玩家 {player_id} 失败: {e}")

    async def _unban_all_players(self, group_id: str, room: Dict):
        """解除所有被禁言玩家"""
        for player_id in room["banned_players"]:
            try:
                await room["bot"].set_group_ban(
                    group_id=int(group_id),
                    user_id=int(player_id),
                    duration=0  # 0表示解除禁言
                )
                logger.info(f"[狼人杀] 已解除禁言 {player_id}")
            except Exception as e:
                logger.error(f"[狼人杀] 解除禁言 {player_id} 失败: {e}")
        room["banned_players"].clear()

    async def _set_group_whole_ban(self, group_id: str, room: Dict, enable: bool):
        """设置全员禁言"""
        try:
            await room["bot"].set_group_whole_ban(
                group_id=int(group_id),
                enable=enable
            )
            logger.info(f"[狼人杀] 全员禁言状态: {enable}")
        except Exception as e:
            logger.error(f"[狼人杀] 设置全员禁言失败: {e}")

    async def _set_temp_admin(self, group_id: str, player_id: str, room: Dict):
        """设置临时管理员（用于发言）"""
        try:
            await room["bot"].set_group_admin(
                group_id=int(group_id),
                user_id=int(player_id),
                enable=True
            )
            room["temp_admins"].add(player_id)
            logger.info(f"[狼人杀] 已设置临时管理员 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 设置临时管理员 {player_id} 失败: {e}")

    async def _remove_temp_admin(self, group_id: str, player_id: str, room: Dict):
        """取消临时管理员"""
        try:
            await room["bot"].set_group_admin(
                group_id=int(group_id),
                user_id=int(player_id),
                enable=False
            )
            room["temp_admins"].discard(player_id)
            logger.info(f"[狼人杀] 已取消临时管理员 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 取消临时管理员 {player_id} 失败: {e}")

    async def _clear_temp_admins(self, group_id: str, room: Dict):
        """清除所有临时管理员"""
        for player_id in list(room["temp_admins"]):
            await self._remove_temp_admin(group_id, player_id, room)
        room["temp_admins"].clear()

    async def _send_roles_to_players(self, group_id: str, room: Dict):
        """主动私聊告知所有玩家的身份"""
        for player_id in room["players"]:
            try:
                role = room["roles"].get(player_id)
                if not role:
                    continue

                # 生成角色信息
                if role == "werewolf":
                    # 找到其他狼人
                    werewolves = [pid for pid, r in room["roles"].items() if r == "werewolf"]
                    teammates = [pid for pid in werewolves if pid != player_id]

                    # 狼人队友信息
                    teammate_info = ""
                    if teammates:
                        teammate_names = ", ".join([self._format_player_name(pid, room) for pid in teammates])
                        teammate_info = f"\n\n🤝 你的队友：{teammate_names}"

                    # 列出所有其他玩家（除了狼人自己）
                    other_players = [pid for pid in room["players"] if pid not in werewolves]
                    players_list = "\n".join([f"  • {self._format_player_name(pid, room)}" for pid in other_players])

                    role_text = (
                        f"🎭 游戏开始！你的角色是：\n\n"
                        f"🐺 狼人\n\n"
                        f"你的目标：消灭所有平民！{teammate_info}\n\n"
                        f"📋 可选目标列表：\n{players_list}\n\n"
                        f"💡 夜晚私聊使用命令：\n"
                        f"  /办掉 编号 - 投票办掉目标\n"
                        f"  /密谋 消息 - 与队友交流\n"
                        f"示例：/办掉 {list(room['player_numbers'].values())[0] if room.get('player_numbers') else '1'}"
                    )
                elif role == "seer":
                    # 列出所有其他玩家（预言家可以验所有人）
                    other_players = [pid for pid in room["players"] if pid != player_id]
                    players_list = "\n".join([f"  • {self._format_player_name(pid, room)}" for pid in other_players])

                    role_text = (
                        f"🎭 游戏开始！你的角色是：\n\n"
                        f"🔮 预言家\n\n"
                        f"你的目标：找出狼人，帮助平民获胜！\n\n"
                        f"📋 可验证玩家列表：\n{players_list}\n\n"
                        f"💡 夜晚私聊使用命令：\n"
                        f"/验人 编号\n"
                        f"示例：/验人 {room['player_numbers'][other_players[0]] if other_players else '3'}\n\n"
                        f"⚠️ 注意：每晚只能验证一个人！"
                    )
                elif role == "witch":
                    # 列出所有其他玩家
                    other_players = [pid for pid in room["players"] if pid != player_id]
                    players_list = "\n".join([f"  • {self._format_player_name(pid, room)}" for pid in other_players])

                    role_text = (
                        f"🎭 游戏开始！你的角色是：\n\n"
                        f"💊 女巫\n\n"
                        f"你的目标：帮助平民获胜！\n\n"
                        f"你拥有两种药：\n"
                        f"💉 解药：可以救活当晚被杀的人（只能用一次）\n"
                        f"💊 毒药：可以毒杀任何人（只能用一次）\n\n"
                        f"⚠️ 注意：\n"
                        f"• 同一晚不能同时使用两种药\n"
                        f"• 解药只能救当晚被杀的人\n"
                        f"• 每晚女巫行动时会告知谁被杀\n\n"
                        f"💡 夜晚私聊使用命令：\n"
                        f"  /救人 - 救活被杀的人\n"
                        f"  /毒人 编号 - 毒杀某人\n"
                        f"  /不操作 - 不使用任何药"
                    )
                elif role == "hunter":
                    # 列出所有其他玩家
                    other_players = [pid for pid in room["players"] if pid != player_id]
                    players_list = "\n".join([f"  • {self._format_player_name(pid, room)}" for pid in other_players])

                    role_text = (
                        f"🎭 游戏开始！你的角色是：\n\n"
                        f"🔫 猎人\n\n"
                        f"你的目标：帮助好人获胜！\n\n"
                        f"你的技能：\n"
                        f"• 被狼人办掉时可以开枪带走一人\n"
                        f"• 被投票放逐时可以开枪带走一人\n"
                        f"• 被女巫毒死时不能开枪（死的太突然）\n\n"
                        f"📋 可选目标列表：\n{players_list}\n\n"
                        f"💡 当你死亡时（非毒死），私聊使用命令：\n"
                        f"  /开枪 编号 - 带走一个人\n"
                        f"示例：/开枪 1"
                    )
                else:  # villager
                    role_text = (
                        f"🎭 游戏开始！你的角色是：\n\n"
                        f"👤 平民\n\n"
                        f"你的目标：找出并放逐所有狼人！\n"
                        f"白天投票时使用 /投票 编号 放逐可疑玩家。"
                    )

                # 尝试发送私聊消息
                await room["bot"].send_private_msg(
                    user_id=int(player_id),
                    message=role_text
                )
                logger.info(f"[狼人杀] 已私聊告知玩家 {player_id} 的身份：{role}")

            except Exception as e:
                logger.warning(f"[狼人杀] 私聊告知玩家 {player_id} 失败: {e}")
                # 失败不影响游戏继续，玩家可以手动查看角色

    async def _start_last_words(self, group_id: str):
        """开始遗言阶段"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 检查是否有被杀的玩家
        if not room.get("last_killed"):
            # 没有被杀的玩家，直接进入发言阶段
            room["phase"] = GamePhase.DAY_SPEAKING
            await self._start_speaking_phase(group_id)
            return

        killed_player = room["last_killed"]

        # 清空发言缓存，准备记录遗言
        room["current_speech"] = []

        # 开启全群禁言
        await self._set_group_whole_ban(group_id, room, True)

        # 设置被杀玩家为临时管理员（可以在全群禁言状态下说话）
        await self._set_temp_admin(group_id, killed_player, room)

        # 发送遗言提示消息
        if room.get("msg_origin"):
            killed_name = self._format_player_name(killed_player, room)
            msg = MessageChain().at(killed_name, killed_player).message(
                f" 现在请你留遗言\n\n"
                f"⏰ 遗言时间：2分钟\n"
                f"💡 遗言完毕后请使用：/遗言完毕"
            )
            await self.context.send_message(room["msg_origin"], msg)

        # 启动遗言定时器
        room["timer_task"] = asyncio.create_task(self._last_words_timeout(group_id))

    async def _last_words_timeout(self, group_id: str):
        """遗言超时处理"""
        try:
            await asyncio.sleep(self.timeout_speaking)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是遗言阶段
            if room["phase"] != GamePhase.LAST_WORDS:
                return

            logger.info(f"[狼人杀] 群 {group_id} 遗言阶段超时")

            # 取消被杀者的临时管理员
            if room.get("last_killed"):
                await self._remove_temp_admin(group_id, room["last_killed"], room)
                # 禁言被杀玩家
                await self._ban_player(group_id, room["last_killed"], room)

            # 确保全员禁言状态
            await self._set_group_whole_ban(group_id, room, True)

            # 发送超时提醒
            if room.get("msg_origin"):
                timeout_msg = MessageChain().message("⏰ 遗言超时！自动进入下一阶段。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 检查遗言是否来自投票放逐
            if room.get("last_words_from_vote"):
                # 来自投票放逐，进入夜晚
                room["phase"] = GamePhase.NIGHT_WOLF
                room["seer_checked"] = False
                room["is_first_night"] = False  # 第一晚结束
                room["last_words_from_vote"] = False

                # 开启全员禁言
                await self._set_group_whole_ban(group_id, room, True)
                # 启动狼人定时器
                room["timer_task"] = asyncio.create_task(self._wolf_kill_timeout(group_id))

                # 发送夜晚消息
                if room.get("msg_origin"):
                    night_msg = MessageChain().message(
                        "🌙 夜晚降临，天黑请闭眼...\n\n"
                        "🐺 狼人请私聊使用：/狼人杀 办掉 编号\n"
                        "🔮 预言家请等待狼人行动完成\n"
                        "⏰ 剩余时间：2分钟"
                    )
                    await self.context.send_message(room["msg_origin"], night_msg)
            else:
                # 来自夜晚被杀，进入发言阶段
                # 清空遗言相关状态
                room["last_killed"] = None
                # 第一晚结束
                room["is_first_night"] = False

                room["phase"] = GamePhase.DAY_SPEAKING
                await self._start_speaking_phase(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 遗言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 遗言超时处理失败: {e}")

    async def _start_speaking_phase(self, group_id: str):
        """开始发言阶段"""
        room = self.game_rooms[group_id]

        # 设置发言顺序（按编号1-9排序）
        alive_players = list(room["alive"])
        # 按玩家编号排序
        alive_players.sort(key=lambda pid: room["player_numbers"].get(pid, 999))
        room["speaking_order"] = alive_players
        room["current_speaker_index"] = 0

        # 确保全群禁言开启
        await self._set_group_whole_ban(group_id, room, True)

        # 开始第一个人发言
        await self._next_speaker(group_id)

    async def _next_speaker(self, group_id: str):
        """切换到下一个发言者"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 检查是否所有人都发言完毕
        if room["current_speaker_index"] >= len(room["speaking_order"]):
            # 所有人发言完毕，进入投票阶段
            await self._auto_start_vote(group_id)
            return

        # 获取当前发言者
        current_speaker = room["speaking_order"][room["current_speaker_index"]]
        room["current_speaker"] = current_speaker

        # 清空上一个发言者的发言缓存
        room["current_speech"] = []

        # 设置为临时管理员
        await self._set_temp_admin(group_id, current_speaker, room)

        # 发送提示消息
        if room.get("msg_origin"):
            speaker_name = self._format_player_name(current_speaker, room)
            msg = MessageChain().at(speaker_name, current_speaker).message(
                f" 现在轮到你发言\n\n"
                f"⏰ 发言时间：2分钟\n"
                f"💡 发言完毕后请使用：/发言完毕\n\n"
                f"进度：{room['current_speaker_index'] + 1}/{len(room['speaking_order'])}"
            )
            await self.context.send_message(room["msg_origin"], msg)

        # 启动发言定时器
        room["timer_task"] = asyncio.create_task(self._speaking_timeout(group_id))

    async def _next_pk_speaker(self, group_id: str):
        """切换到下一个PK发言者"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 检查是否所有PK玩家都发言完毕
        if room["current_speaker_index"] >= len(room["pk_players"]):
            # 所有PK玩家发言完毕，进入二次投票
            await self._start_pk_vote(group_id)
            return

        # 获取当前PK发言者
        current_speaker = room["pk_players"][room["current_speaker_index"]]
        room["current_speaker"] = current_speaker

        # 清空上一个发言者的发言缓存
        room["current_speech"] = []

        # 设置为临时管理员
        await self._set_temp_admin(group_id, current_speaker, room)

        # 发送提示消息
        if room.get("msg_origin"):
            speaker_name = self._format_player_name(current_speaker, room)
            msg = MessageChain().at(speaker_name, current_speaker).message(
                f" PK发言：现在轮到你发言\n\n"
                f"⏰ 发言时间：2分钟\n"
                f"💡 发言完毕后请使用：/发言完毕\n\n"
                f"进度：{room['current_speaker_index'] + 1}/{len(room['pk_players'])}"
            )
            await self.context.send_message(room["msg_origin"], msg)

        # 启动PK发言定时器
        room["timer_task"] = asyncio.create_task(self._pk_speaking_timeout(group_id))

    async def _pk_speaking_timeout(self, group_id: str):
        """PK发言超时处理"""
        try:
            await asyncio.sleep(self.timeout_speaking)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是PK阶段
            if room["phase"] != GamePhase.DAY_PK:
                return

            logger.info(f"[狼人杀] 群 {group_id} PK发言超时")

            # 取消当前发言者的管理员
            if room.get("current_speaker"):
                await self._remove_temp_admin(group_id, room["current_speaker"], room)

            # 发送超时提醒
            if room.get("msg_origin"):
                speaker_name = self._format_player_name(room["current_speaker"], room)
                timeout_msg = MessageChain().message(f"⏰ {speaker_name} PK发言超时！自动进入下一位。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 切换到下一个PK发言者
            room["current_speaker_index"] += 1
            await self._next_pk_speaker(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} PK发言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] PK发言超时处理失败: {e}")

    async def _start_pk_vote(self, group_id: str):
        """启动PK二次投票"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 进入投票阶段
        room["phase"] = GamePhase.DAY_VOTE
        room["is_pk_vote"] = True  # 标记为PK投票
        room["day_votes"] = {}

        # 发送投票提示
        if room.get("msg_origin"):
            pk_names = [self._format_player_name(pid, room) for pid in room["pk_players"]]
            msg = MessageChain().message(
                "📢 PK发言完毕！现在开始二次投票\n\n"
                "⚠️ 只能投给以下平票玩家：\n"
                + "\n".join([f"  • {name}" for name in pk_names])
                + "\n\n⏰ 投票时间：2分钟\n"
                + "💡 使用 /投票 编号"
            )
            await self.context.send_message(room["msg_origin"], msg)

        # 解除全群禁言（允许投票）
        await self._set_group_whole_ban(group_id, room, False)

        # 启动投票定时器
        room["timer_task"] = asyncio.create_task(self._day_vote_timeout(group_id))

    async def _speaking_timeout(self, group_id: str):
        """发言超时处理"""
        try:
            await asyncio.sleep(self.timeout_speaking)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是发言阶段
            if room["phase"] != GamePhase.DAY_SPEAKING:
                return

            logger.info(f"[狼人杀] 群 {group_id} 发言超时")

            # 取消当前发言者的管理员
            if room.get("current_speaker"):
                await self._remove_temp_admin(group_id, room["current_speaker"], room)

            # 发送超时提醒
            if room.get("msg_origin"):
                speaker_name = self._format_player_name(room["current_speaker"], room)
                timeout_msg = MessageChain().message(f"⏰ {speaker_name} 发言超时！自动进入下一位。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 切换到下一个发言者
            room["current_speaker_index"] += 1
            await self._next_speaker(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 发言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 发言超时处理失败: {e}")

    async def _auto_start_vote(self, group_id: str):
        """自动开始投票阶段"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 进入投票阶段
        room["phase"] = GamePhase.DAY_VOTE
        room["day_votes"] = {}

        # 发送投票开始消息
        if room.get("msg_origin"):
            vote_msg = MessageChain().message(
                "📊 发言环节结束！现在进入投票阶段！\n\n"
                "请所有存活玩家使用命令：\n"
                "/投票 编号\n\n"
                f"当前存活人数：{len(room['alive'])}\n"
                "⏰ 剩余时间：2分钟"
            )
            await self.context.send_message(room["msg_origin"], vote_msg)

        # 解除全群禁言
        await self._set_group_whole_ban(group_id, room, False)

        # 启动投票定时器
        room["timer_task"] = asyncio.create_task(self._day_vote_timeout(group_id))

    def _get_at_user(self, event: AstrMessageEvent) -> str:
        """获取消息中@的第一个用户ID"""
        for seg in event.get_messages():
            if isinstance(seg, At):
                return str(seg.qq)
        return ""

    def _get_target_user(self, event: AstrMessageEvent) -> str:
        """获取目标用户ID（支持@、编号和QQ号）"""
        # 方式1：尝试从@中提取
        target = self._get_at_user(event)
        if target:
            return target

        # 方式2：从消息文本中提取数字（编号1-9或QQ号）
        import re
        for seg in event.get_messages():
            if hasattr(seg, 'text'):
                # 查找消息中的数字（支持1-9的编号或长QQ号）
                match = re.search(r'\b(\d+)\b', seg.text)
                if match:
                    return match.group(1)

        return ""

    async def _process_night_kill(self, group_id: str):
        """处理夜晚办掉结果（存储到房间，不直接发送）"""
        room = self.game_rooms[group_id]

        # 统计票数
        vote_counts = {}
        for voter, target in room["night_votes"].items():
            vote_counts[target] = vote_counts.get(target, 0) + 1

        # 获取票数最多的目标
        if not vote_counts:
            return

        max_votes = max(vote_counts.values())
        targets = [pid for pid, count in vote_counts.items() if count == max_votes]

        # 如果有平票，随机选择一个
        killed_player = random.choice(targets)

        # 清空投票记录
        room["night_votes"] = {}

        # 记录被杀的玩家（注意：不立即移除 alive，等女巫行动后再确定生死）
        room["last_killed"] = killed_player

        # 记录日志
        killed_name = self._format_player_name(killed_player, room)
        room["game_log"].append(f"🌙 狼人最终决定刀 {killed_name}")

        # 禁言被杀玩家（暂时不禁言，等遗言完毕后再禁言）
        # await self._ban_player(group_id, killed_player, room)

        # 进入预言家验人阶段
        room["phase"] = GamePhase.NIGHT_SEER

        # 注意：全员禁言在女巫行动完成后才解除，确保夜晚行动全程处于禁言状态

        # 构造结果消息并存储（用于女巫查看和最后天亮）
        killed_name = self._format_player_name(killed_player, room)
        result_text = (
            f"☀️ 天亮了！\n\n"
            f"昨晚，玩家 {killed_name} 死了！\n\n"
            f"存活玩家：{len(room['alive'])}/{len(room['players'])}\n\n"
        )

        # 检查胜利条件
        victory_msg, winning_faction = self._check_victory_condition(room)
        if victory_msg:
            result_text += f"🎉 {victory_msg}\n游戏结束！\n\n"
            # 公布所有玩家身份
            result_text += self._get_all_players_roles(room)
            room["phase"] = GamePhase.FINISHED

            # 立即发送游戏结束消息（不能只存储，因为后续会清理房间）
            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

                # 生成AI复盘
                try:
                    ai_review = await self._generate_ai_review(room, winning_faction)
                    if ai_review:
                        review_msg = MessageChain().message(ai_review)
                        await self.context.send_message(room["msg_origin"], review_msg)
                except Exception as e:
                    logger.error(f"[狼人杀] AI复盘发送失败: {e}")

            # 清理房间
            await self._cleanup_room(group_id)
        else:
            # 存储结果到房间（不包含遗言提示，由后续逻辑决定）
            room["night_result"] = result_text

    async def _process_day_vote(self, group_id: str) -> str:
        """处理白天投票结果"""
        room = self.game_rooms[group_id]

        # 统计票数
        vote_counts = {}
        for voter, target in room["day_votes"].items():
            vote_counts[target] = vote_counts.get(target, 0) + 1

        # 获取票数最多的目标
        if not vote_counts:
            return ""

        max_votes = max(vote_counts.values())
        targets = [pid for pid, count in vote_counts.items() if count == max_votes]

        # 检查是否平票
        if len(targets) > 1 and not room.get("is_pk_vote"):
            # 第一次投票平票，进入PK环节
            # 按编号排序PK玩家
            targets.sort(key=lambda pid: room["player_numbers"].get(pid, 999))
            room["pk_players"] = targets
            room["phase"] = GamePhase.DAY_PK
            room["day_votes"] = {}  # 清空投票
            room["current_speaker_index"] = 0

            # 构造PK提示
            pk_names = [self._format_player_name(pid, room) for pid in targets]
            result_text = (
                f"\n📊 投票结果公布！\n\n"
                f"⚠️ 出现平票！以下玩家票数相同：\n"
                + "\n".join([f"  • {name}" for name in pk_names])
                + f"\n\n进入PK环节！\n平票玩家将依次发言（每人2分钟），然后进行二次投票。\n"
            )

            # 发送PK提示消息
            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

            # 开启全群禁言
            await self._set_group_whole_ban(group_id, room, True)

            # 启动第一个PK发言者
            await self._next_pk_speaker(group_id)

            # 返回None，避免调用者重复发送消息
            return None

        # 如果是二次投票仍然平票，本轮无人出局
        if len(targets) > 1 and room.get("is_pk_vote"):
            # PK投票后仍然平票，无人出局
            room["is_pk_vote"] = False
            room["pk_players"] = []
            room["day_votes"] = {}

            # 记录日志
            room["game_log"].append("📊 PK投票结果：仍然平票，本轮无人出局")

            # 进入下一个夜晚
            room["phase"] = GamePhase.NIGHT_WOLF
            room["seer_checked"] = False
            room["is_first_night"] = False
            room["current_round"] += 1  # 回合数+1

            # 记录日志
            room["game_log"].append(LOG_SEPARATOR)
            room["game_log"].append(f"第{room['current_round']}晚")
            room["game_log"].append(LOG_SEPARATOR)

            # 先开启全员禁言
            await self._set_group_whole_ban(group_id, room, True)

            # 再发送消息
            result_text = (
                "\n📊 PK投票结果：仍然平票！\n\n"
                "本轮无人出局，直接进入夜晚！\n\n"
                "🌙 夜晚降临，天黑请闭眼...\n\n"
                "🐺 狼人请私聊使用：/狼人杀 办掉 编号\n"
                "🔮 预言家请等待狼人行动完成\n"
                "⏰ 剩余时间：2分钟"
            )

            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

            # 启动狼人定时器
            room["timer_task"] = asyncio.create_task(self._wolf_kill_timeout(group_id))

            return None  # 消息已发送，返回None

        # 只有一个人得票最多
        if len(targets) == 1:
            exiled_player = targets[0]
            if room.get("is_pk_vote"):
                result_text_prefix = "\n📊 PK投票结果公布！\n\n"
            else:
                result_text_prefix = "\n📊 投票结果公布！\n\n"
        else:
            # 第一次投票平票但不应该走到这里（应该已经在上面进入PK了）
            # 这是一个异常情况，记录日志
            logger.error(f"[狼人杀] 异常：非PK投票出现平票，targets={targets}")
            return ""

        # 重置PK标记
        room["is_pk_vote"] = False
        room["pk_players"] = []

        # 移除存活列表
        room["alive"].discard(exiled_player)
        room["day_votes"] = {}

        # 记录被放逐的玩家（用于遗言）
        room["last_killed"] = exiled_player

        exiled_name = self._format_player_name(exiled_player, room)

        # 记录日志
        if room.get("is_pk_vote"):
            room["game_log"].append(f"📊 PK投票结果：{exiled_name} 被放逐")
        else:
            room["game_log"].append(f"📊 投票结果：{exiled_name} 被放逐")

        result_text = (
            result_text_prefix
            + f"玩家 {exiled_name} 被放逐了！\n\n"
            + f"存活玩家：{len(room['alive'])}/{len(room['players'])}\n\n"
        )

        # 检查被放逐的是否是猎人
        if room["roles"].get(exiled_player) == "hunter":
            # 猎人被放逐，可以开枪
            room["pending_hunter_shot"] = exiled_player
            room["hunter_death_type"] = "vote"

            # 发送投票结果消息
            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

            # 通知猎人开枪
            try:
                msg = (
                    f"💀 你被投票放逐了！\n\n"
                    f"🔫 你可以选择开枪带走一个人！\n\n"
                    f"请私聊使用命令：\n"
                    f"  /开枪 编号\n"
                    f"示例：/开枪 1\n\n"
                    f"⏰ 限时2分钟"
                )
                await room["bot"].send_private_msg(user_id=int(exiled_player), message=msg)

                # 通知群里猎人可以开枪
                group_msg = f"⚠️ {exiled_name} 是猎人，可以选择开枪带走一个人..."
                await self.context.send_message(room["msg_origin"], MessageChain().message(group_msg))

                # 启动猎人开枪定时器（2分钟）
                room["timer_task"] = asyncio.create_task(self._hunter_shot_timeout_for_vote(group_id, self.timeout_hunter))
                return None  # 等待猎人开枪
            except Exception as e:
                logger.error(f"[狼人杀] 通知猎人 {exiled_player} 开枪失败: {e}")

        # 检查胜利条件
        victory_msg, winning_faction = self._check_victory_condition(room)
        if victory_msg:
            result_text += f"🎉 {victory_msg}\n游戏结束！\n\n"
            # 公布所有玩家身份
            result_text += self._get_all_players_roles(room)
            room["phase"] = GamePhase.FINISHED

            # 发送结果消息
            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

                # 生成AI复盘
                try:
                    ai_review = await self._generate_ai_review(room, winning_faction)
                    if ai_review:
                        review_msg = MessageChain().message(ai_review)
                        await self.context.send_message(room["msg_origin"], review_msg)
                except Exception as e:
                    logger.error(f"[狼人杀] AI复盘发送失败: {e}")

            # 清理房间
            await self._cleanup_room(group_id)
            return None
        else:
            # 被放逐的人留遗言
            # 进入遗言阶段
            room["phase"] = GamePhase.LAST_WORDS
            room["last_words_from_vote"] = True  # 标记遗言来自投票放逐

            # 发送投票结果消息
            if room.get("msg_origin"):
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

            # 启动遗言流程
            await self._start_last_words(group_id)

            # 返回None，避免调用者重复发送消息
            return None

    def _check_victory_condition(self, room: Dict) -> tuple:
        """检查胜利条件，返回(胜利消息, 胜利阵营)"""
        # 统计存活的狼人和好人数量
        alive_werewolves = sum(1 for pid in room["alive"] if room["roles"][pid] == "werewolf")
        alive_goods = len(room["alive"]) - alive_werewolves

        # 检查神职（预言家、女巫、猎人）是否都死了
        alive_gods = [pid for pid in room["alive"] if room["roles"][pid] in ["seer", "witch", "hunter"]]

        if alive_werewolves == 0:
            return ("好人胜利！所有狼人已被放逐！", "villager")
        elif alive_goods <= alive_werewolves:
            return ("狼人胜利！好人数量不足！", "werewolf")
        elif len(alive_gods) == 0 and alive_werewolves > 0:
            return ("狼人胜利！所有神职人员已出局！", "werewolf")
        else:
            return ("", None)


    def _get_role_name(self, role: str) -> str:
        """获取角色中文名"""
        role_names = {
            "werewolf": "狼人 🐺",
            "seer": "预言家 🔮",
            "witch": "女巫 💊",
            "villager": "平民 👤"
        }
        return role_names.get(role, "未知")

    # ========== 定时器相关函数 ==========

    def _cancel_timer(self, room: Dict):
        """取消当前定时器"""
        if room.get("timer_task") and not room["timer_task"].done():
            room["timer_task"].cancel()
            room["timer_task"] = None

    async def _notify_witch(self, group_id: str, witch_id: str, room: Dict):
        """给女巫发私聊告知谁被杀"""
        try:
            if not room.get("last_killed"):
                msg = (
                    "💊 女巫行动阶段\n\n"
                    "今晚没有人被杀！\n\n"
                    f"💊 毒药状态：{'已使用' if room.get('witch_poison_used') else '可用'}\n"
                    f"💉 解药状态：{'已使用' if room.get('witch_antidote_used') else '可用'}\n\n"
                    "命令：\n"
                    "  /毒人 编号 - 使用毒药\n"
                    "  /不操作 - 不使用道具"
                )
            else:
                killed_name = self._format_player_name(room["last_killed"], room)
                msg = (
                    "💊 女巫行动阶段\n\n"
                    f"今晚被杀的是：{killed_name}\n\n"
                    f"💊 毒药状态：{'已使用' if room.get('witch_poison_used') else '可用'}\n"
                    f"💉 解药状态：{'已使用' if room.get('witch_antidote_used') else '可用'}\n\n"
                    "命令：\n"
                    "  /救人 - 使用解药救此人\n"
                    "  /毒人 编号 - 使用毒药\n"
                    "  /不操作 - 不使用道具"
                )

            await room["bot"].send_private_msg(
                user_id=int(witch_id),
                message=msg
            )
            logger.info(f"[狼人杀] 已告知女巫 {witch_id} 夜晚信息")

        except Exception as e:
            logger.error(f"[狼人杀] 告知女巫 {witch_id} 失败: {e}")

    async def _witch_finish(self, group_id: str):
        """女巫行动完成，准备天亮"""
        if group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]

        # 处理女巫的行动结果
        # 1. 如果女巫救人，清空被杀记录（被救者本来就还在 alive 中）
        if room.get("witch_saved"):
            room["last_killed"] = None  # 清空被杀记录
        # 2. 如果女巫没救人，被狼杀的人确定死亡
        elif room.get("last_killed"):
            room["alive"].discard(room["last_killed"])  # 确定死亡，移除 alive

        # 3. 如果女巫毒人，则被毒的人死亡
        if room.get("witch_poisoned"):
            room["alive"].discard(room["witch_poisoned"])
            # 被毒的人也要禁言
            await self._ban_player(group_id, room["witch_poisoned"], room)

            # 检查被毒的是否是猎人（被毒不能开枪）
            if room["roles"].get(room["witch_poisoned"]) == "hunter":
                room["hunter_death_type"] = "poison"

        # 检查被狼杀的是否是猎人（未被救的情况下）
        if room.get("last_killed") and not room.get("witch_saved"):
            if room["roles"].get(room["last_killed"]) == "hunter":
                room["pending_hunter_shot"] = room["last_killed"]
                room["hunter_death_type"] = "wolf"

        # 3. 构造天亮消息
        if room.get("night_result") and room.get("msg_origin"):
            # 修改原有的天亮消息，加入女巫毒人信息
            if room.get("witch_saved"):
                # 有人被救
                result_text = (
                    f"☀️ 天亮了！\n\n"
                    f"昨晚是平安夜，没有人死亡！\n\n"
                    f"存活玩家：{len(room['alive'])}/{len(room['players'])}\n\n"
                )
            else:
                # 使用原有的被杀消息
                result_text = room["night_result"]

                # 如果是第一晚且有人死亡，添加遗言提示
                if room.get("is_first_night") and room.get("last_killed"):
                    killed_name = self._format_player_name(room["last_killed"], room)
                    result_text += f"💬 请 {killed_name} 留遗言...\n"

            # 添加毒人信息
            if room.get("witch_poisoned"):
                poisoned_name = self._format_player_name(room["witch_poisoned"], room)
                result_text += f"\n同时，玩家 {poisoned_name} 死了！\n"
                # 注意：被毒者没有遗言

            # 重新检查胜利条件
            victory_msg, winning_faction = self._check_victory_condition(room)
            if victory_msg:
                result_text += f"\n🎉 {victory_msg}\n游戏结束！\n\n"
                result_text += self._get_all_players_roles(room)
                room["phase"] = GamePhase.FINISHED

                # 发送结果
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

                # 清理房间
                await self._cleanup_room(group_id)
            else:
                # 游戏继续
                # 发送天亮消息
                result_message = MessageChain().message(result_text)
                await self.context.send_message(room["msg_origin"], result_message)

                # 检查是否有猎人待开枪（被狼杀）
                if room.get("pending_hunter_shot") and room.get("hunter_death_type") == "wolf":
                    hunter_id = room["pending_hunter_shot"]
                    hunter_name = self._format_player_name(hunter_id, room)
                    try:
                        msg = (
                            f"💀 你被狼人办掉了！\n\n"
                            f"🔫 你可以选择开枪带走一个人！\n\n"
                            f"请私聊使用命令：\n"
                            f"  /开枪 编号\n"
                            f"示例：/开枪 1\n\n"
                            f"⏰ 限时2分钟"
                        )
                        await room["bot"].send_private_msg(user_id=int(hunter_id), message=msg)

                        # 通知群里猎人可以开枪
                        group_msg = f"⚠️ {hunter_name} 可以选择开枪带走一个人..."
                        await self.context.send_message(room["msg_origin"], MessageChain().message(group_msg))

                        # 启动猎人开枪定时器（2分钟）
                        room["timer_task"] = asyncio.create_task(self._hunter_shot_timeout(group_id, self.timeout_hunter))
                        return  # 等待猎人开枪，暂不继续游戏流程
                    except Exception as e:
                        logger.error(f"[狼人杀] 通知猎人 {hunter_id} 开枪失败: {e}")

                # 检查是否第一晚且有人被狼杀（被毒者没有遗言）
                if room.get("is_first_night") and room.get("last_killed"):
                    # 第一晚被狼杀有遗言
                    room["phase"] = GamePhase.LAST_WORDS
                    await self._start_last_words(group_id)
                else:
                    # 其他夜晚没有遗言，或被毒者，直接进入发言阶段
                    # 禁言死亡的玩家
                    if room.get("last_killed"):
                        await self._ban_player(group_id, room["last_killed"], room)
                    if room.get("witch_poisoned"):
                        await self._ban_player(group_id, room["witch_poisoned"], room)

                    # 如果是第一晚且没死人（跳过遗言），标记第一晚结束
                    if room.get("is_first_night"):
                        room["is_first_night"] = False
                        room["last_killed"] = None  # 清空遗留的 last_killed
                        room["witch_poisoned"] = None  # 清空遗留的 witch_poisoned

                    room["phase"] = GamePhase.DAY_SPEAKING
                    await self._start_speaking_phase(group_id)

            room["night_result"] = None

    async def _witch_timeout(self, group_id: str, wait_time: float = 120):
        """女巫超时处理"""
        try:
            await asyncio.sleep(wait_time)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是女巫行动
            if room["phase"] != GamePhase.NIGHT_WITCH:
                return

            logger.info(f"[狼人杀] 群 {group_id} 女巫行动阶段超时")

            # 标记女巫已行动（视为不操作）
            room["witch_acted"] = True

            # 检查女巫是否存活，只有存活时才发送超时提示
            witch_id = None
            for pid, r in room["roles"].items():
                if r == "witch":
                    witch_id = pid
                    break

            witch_alive = witch_id and witch_id in room["alive"]
            if witch_alive and room.get("msg_origin"):
                # 女巫存活但超时未操作
                timeout_msg = MessageChain().message("⏰ 女巫行动超时！视为不操作。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 女巫行动完成，准备天亮
            await self._witch_finish(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 女巫定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 女巫超时处理失败: {e}")

    async def _hunter_shot_timeout(self, group_id: str, wait_time: float = 120):
        """猎人开枪超时处理"""
        try:
            await asyncio.sleep(wait_time)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查是否还有猎人待开枪
            if not room.get("pending_hunter_shot"):
                return

            logger.info(f"[狼人杀] 群 {group_id} 猎人开枪超时")

            # 清除待开枪状态
            hunter_id = room["pending_hunter_shot"]
            hunter_name = self._format_player_name(hunter_id, room)
            room["pending_hunter_shot"] = None
            room["hunter_shot"] = True  # 标记为已处理

            # 记录日志
            room["game_log"].append(f"🔫 {hunter_name}（猎人）超时未开枪")

            # 通知群聊
            if room.get("msg_origin"):
                timeout_msg = MessageChain().message(f"⏰ {hunter_name} 开枪超时！放弃开枪机会。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 继续游戏流程
            if room.get("is_first_night") and room.get("last_killed"):
                # 第一晚被狼杀有遗言
                room["phase"] = GamePhase.LAST_WORDS
                await self._start_last_words(group_id)
            else:
                # 其他夜晚没有遗言，或被毒者，直接进入发言阶段
                # 禁言死亡的玩家
                if room.get("last_killed"):
                    await self._ban_player(group_id, room["last_killed"], room)
                if room.get("witch_poisoned"):
                    await self._ban_player(group_id, room["witch_poisoned"], room)

                room["phase"] = GamePhase.DAY_SPEAKING
                await self._start_speaking_phase(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 猎人开枪定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 猎人开枪超时处理失败: {e}")

    async def _hunter_shot_timeout_for_vote(self, group_id: str, wait_time: float = 120):
        """投票后猎人开枪超时处理"""
        try:
            await asyncio.sleep(wait_time)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查是否还有猎人待开枪
            if not room.get("pending_hunter_shot"):
                return

            logger.info(f"[狼人杀] 群 {group_id} 投票后猎人开枪超时")

            # 清除待开枪状态
            hunter_id = room["pending_hunter_shot"]
            hunter_name = self._format_player_name(hunter_id, room)
            room["pending_hunter_shot"] = None
            room["hunter_shot"] = True

            # 记录日志
            room["game_log"].append(f"🔫 {hunter_name}（猎人）超时未开枪")

            # 通知群聊
            if room.get("msg_origin"):
                timeout_msg = MessageChain().message(f"⏰ {hunter_name} 开枪超时！放弃开枪机会。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 检查胜利条件
            victory_msg, winning_faction = self._check_victory_condition(room)
            if victory_msg:
                result_text = f"🎉 {victory_msg}\n游戏结束！\n\n"
                result_text += self._get_all_players_roles(room)
                room["phase"] = GamePhase.FINISHED

                await self.context.send_message(room["msg_origin"], MessageChain().message(result_text))
                await self._cleanup_room(group_id)
                return

            # 游戏继续，进入遗言阶段（被放逐的人）
            room["phase"] = GamePhase.LAST_WORDS
            room["last_words_from_vote"] = True
            await self._start_last_words(group_id)

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 投票后猎人开枪定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 投票后猎人开枪超时处理失败: {e}")

    async def _wolf_kill_timeout(self, group_id: str):
        """狼人办掉超时处理"""
        try:
            await asyncio.sleep(self.timeout_wolf)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是狼人行动
            if room["phase"] != GamePhase.NIGHT_WOLF:
                return

            logger.info(f"[狼人杀] 群 {group_id} 狼人办掉阶段超时")

            # 发送超时提醒
            if room.get("msg_origin"):
                timeout_msg = MessageChain().message(f"⏰ 狼人行动超时！自动进入下一阶段。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 处理投票结果（即使没有全部投票）
            if room["night_votes"]:
                # 有投票，处理办掉
                await self._process_night_kill(group_id)

                # 检查游戏是否结束（_process_night_kill可能会清理房间）
                if group_id not in self.game_rooms:
                    return  # 游戏已结束，退出

                # 游戏未结束，进入预言家验人阶段
                room["phase"] = GamePhase.NIGHT_SEER
                room["seer_checked"] = False

                # 发送预言家验人提示
                if room.get("msg_origin"):
                    seer_msg = MessageChain().message("🔮 狼人行动完成！\n预言家请私聊机器人验人：/验人 编号\n⏰ 剩余时间：2分钟")
                    await self.context.send_message(room["msg_origin"], seer_msg)

                # 启动预言家定时器（如果预言家已死，等待随机时间后自动进入下一阶段）
                import random
                seer_alive = any(r == "seer" and pid in room["alive"] for pid, r in room["roles"].items())
                if seer_alive:
                    wait_time = self.timeout_seer
                else:
                    wait_time = random.uniform(self.timeout_dead_min, self.timeout_dead_max)

                room["timer_task"] = asyncio.create_task(self._seer_check_timeout(group_id, wait_time))
            else:
                # 没有任何投票，跳过狼人行动，直接进入预言家阶段
                # 记录日志
                room["game_log"].append("🐺 狼人超时：未投票，今晚无人被刀")

                room["phase"] = GamePhase.NIGHT_SEER
                room["seer_checked"] = False

                # 发送预言家验人提示
                if room.get("msg_origin"):
                    seer_msg = MessageChain().message("🔮 狼人未行动！\n预言家请私聊机器人验人：/验人 编号\n⏰ 剩余时间：2分钟")
                    await self.context.send_message(room["msg_origin"], seer_msg)

                # 启动预言家定时器
                import random
                seer_alive = any(r == "seer" and pid in room["alive"] for pid, r in room["roles"].items())
                if seer_alive:
                    wait_time = self.timeout_seer
                else:
                    wait_time = random.uniform(self.timeout_dead_min, self.timeout_dead_max)

                room["timer_task"] = asyncio.create_task(self._seer_check_timeout(group_id, wait_time))

        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 狼人办掉定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 狼人办掉超时处理失败: {e}")

    async def _seer_check_timeout(self, group_id: str, wait_time: float = 120):
        """预言家验人超时处理"""
        try:
            await asyncio.sleep(wait_time)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是预言家验人
            if room["phase"] != GamePhase.NIGHT_SEER:
                return

            logger.info(f"[狼人杀] 群 {group_id} 预言家验人阶段超时")

            # 标记预言家已验人（视为未验人，超时）
            room["seer_checked"] = True

            # 检查预言家是否存活，只有存活时才发送超时提示
            seer_alive = any(r == "seer" and pid in room["alive"] for pid, r in room["roles"].items())
            if seer_alive and room.get("msg_origin"):
                # 预言家存活但超时未验人
                timeout_msg = MessageChain().message("⏰ 预言家验人超时！")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 进入女巫阶段
            witch_id = None
            for pid, r in room["roles"].items():
                if r == "witch":
                    witch_id = pid
                    break

            if witch_id:
                # 进入女巫行动阶段
                room["phase"] = GamePhase.NIGHT_WITCH
                room["witch_acted"] = False
                room["witch_saved"] = None
                room["witch_poisoned"] = None

                # 在群里发送女巫行动提示
                if room.get("msg_origin"):
                    witch_msg = MessageChain().message("💊 预言家验人完成！\n女巫请私聊机器人行动\n⏰ 剩余时间：2分钟")
                    await self.context.send_message(room["msg_origin"], witch_msg)

                # 给女巫发私聊
                await self._notify_witch(group_id, witch_id, room)

                # 启动女巫定时器
                # 如果女巫被杀了，给足够时间让她救自己
                # 如果女巫没被杀但已死（前几晚死的），用随机短时间
                import random
                witch_alive = witch_id in room["alive"]
                witch_is_killed_tonight = (room.get("last_killed") == witch_id)

                if witch_alive or witch_is_killed_tonight:
                    # 女巫存活，或者女巫今晚被杀（可以救自己）
                    wait_time = self.timeout_witch
                else:
                    # 女巫早已死亡（前几晚死的），随机等待
                    wait_time = random.uniform(self.timeout_dead_min, self.timeout_dead_max)

                room["timer_task"] = asyncio.create_task(self._witch_timeout(group_id, wait_time))
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 预言家验人定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 预言家验人超时处理失败: {e}")

    async def _day_vote_timeout(self, group_id: str):
        """白天投票超时处理（带30秒提醒）"""
        try:
            # 如果总时间超过30秒，先等待到剩余30秒时提醒
            if self.timeout_vote > 30:
                await asyncio.sleep(self.timeout_vote - 30)

                if group_id not in self.game_rooms:
                    return

                room = self.game_rooms[group_id]

                # 检查阶段是否还是投票阶段
                if room["phase"] != GamePhase.DAY_VOTE:
                    return

                # 发送30秒提醒
                voted_count = len(room["day_votes"])
                alive_count = len(room["alive"])

                if room.get("msg_origin"):
                    reminder_msg = MessageChain().message(
                        f"⏰ 投票倒计时：还有30秒！\n\n"
                        f"当前投票进度：{voted_count}/{alive_count}\n"
                        f"💡 请尚未投票的玩家抓紧时间：/投票 编号"
                    )
                    await self.context.send_message(room["msg_origin"], reminder_msg)

                # 继续等待剩余30秒
                await asyncio.sleep(30)
            else:
                # 总时间不足30秒，直接等待全部时间
                await asyncio.sleep(self.timeout_vote)

            if group_id not in self.game_rooms:
                return

            room = self.game_rooms[group_id]

            # 检查阶段是否还是投票阶段
            if room["phase"] != GamePhase.DAY_VOTE:
                return

            logger.info(f"[狼人杀] 群 {group_id} 白天投票阶段超时")

            # 统计投票情况
            voted_count = len(room["day_votes"])
            alive_count = len(room["alive"])

            # 发送超时提醒
            if room.get("msg_origin"):
                timeout_msg = MessageChain().message(f"⏰ 投票超时！已有 {voted_count}/{alive_count} 人投票，自动结算。")
                await self.context.send_message(room["msg_origin"], timeout_msg)

            # 处理投票结果
            if room["day_votes"]:
                # 有投票，处理放逐
                result = await self._process_day_vote(group_id)
                if result and room.get("msg_origin"):
                    result_message = MessageChain().message(result)
                    await self.context.send_message(room["msg_origin"], result_message)
            else:
                # 没有任何投票，本轮无人出局
                # 记录日志
                room["game_log"].append("📊 投票超时：无人投票，本轮无人出局")

                # 进入下一个夜晚
                room["phase"] = GamePhase.NIGHT_WOLF
                room["seer_checked"] = False
                room["is_first_night"] = False  # 第一晚结束
                room["current_round"] += 1  # 回合数+1

                # 记录日志
                room["game_log"].append(LOG_SEPARATOR)
                room["game_log"].append(f"第{room['current_round']}晚")
                room["game_log"].append(LOG_SEPARATOR)

                # 先开启全员禁言
                await self._set_group_whole_ban(group_id, room, True)

                # 再发送消息
                if room.get("msg_origin"):
                    no_vote_msg = MessageChain().message(
                        "📊 投票结果：无人投票\n\n"
                        "本轮无人出局！\n\n"
                        "🌙 夜晚降临，天黑请闭眼...\n\n"
                        "🐺 狼人请私聊使用：/狼人杀 办掉 编号\n"
                        "🔮 预言家请等待狼人行动完成\n"
                        "⏰ 剩余时间：2分钟"
                    )
                    await self.context.send_message(room["msg_origin"], no_vote_msg)

                # 启动狼人定时器
                room["timer_task"] = asyncio.create_task(self._wolf_kill_timeout(group_id))
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {group_id} 白天投票定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 白天投票超时处理失败: {e}")

    async def _generate_ai_review(self, room: Dict, winning_faction: str) -> str:
        """生成AI复盘报告"""
        try:
            # 检查是否启用AI复盘
            if not self.enable_ai_review:
                logger.info("[狼人杀] AI复盘已关闭，跳过生成")
                return ""

            # 获取LLM provider
            if self.ai_review_model:
                # 如果配置了自定义模型，使用指定的 provider
                provider = self.context.get_provider_by_id(self.ai_review_model)
                if not provider:
                    logger.warning(f"[狼人杀] 未找到名为 '{self.ai_review_model}' 的模型提供商，使用默认模型")
                    provider = self.context.get_using_provider()
            else:
                # 如果未配置，使用默认 provider
                provider = self.context.get_using_provider()

            if not provider:
                logger.warning("[狼人杀] 无法获取LLM provider，跳过AI复盘")
                return ""

            # 整理游戏数据
            game_data = self._format_game_data_for_ai(room, winning_faction)

            # 构造prompt
            if self.ai_review_prompt:
                # 使用自定义提示词
                faction_name = "狼人" if winning_faction == "werewolf" else "好人"
                system_prompt = self.ai_review_prompt.replace("{winning_faction}", faction_name).replace("{game_data}", game_data)
                user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"
                logger.info("[狼人杀] 使用自定义AI复盘提示词")
            else:
                # 使用默认提示词
                system_prompt = (
                    "你是一个资深的狼人杀游戏分析专家。请根据提供的游戏数据，生成一份专业的复盘报告。\n"
                    "要求：\n"
                    "1. 分析关键决策点和转折点\n"
                    "2. 评价各阵营的策略和失误\n"
                    "3. 指出精彩的操作和值得学习的地方\n"
                    "4. 游戏日志中包含了狼人夜晚的密谋内容（标记为「💬 XXX（狼人）密谋：...」），如果有精彩、搞笑或关键的狼人聊天，可以适当引用原文，增加复盘的趣味性和真实感\n"
                    "5. 评选出本局MVP（表现最好的玩家）和本局超级划水玩家（存在感最低/失误最多的玩家）\n"
                    "6. 语言风格轻松幽默，但分析要专业深入\n"
                    "7. 控制在1000字以内\n"
                    "8. 使用emoji让内容更生动\n\n"
                    "输出格式参考：\n"
                    "[复盘分析内容]\n"
                    "[如有精彩的狼人聊天，可在此引用，格式：💬 「XXX：原话内容」]\n\n"
                    "🏆 本局MVP：[玩家昵称] - [简短理由]\n"
                    "💤 本局超级划水：[玩家昵称] - [简短理由]"
                )
                user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"

            # 调用AI
            response = await provider.text_chat(
                prompt=user_prompt,
                system_prompt=system_prompt
            )

            if response.result_chain:
                review_text = response.result_chain.get_plain_text()
                return f"\n\n🤖 AI复盘\n{'='*30}\n{review_text}\n{'='*30}"
            else:
                return ""

        except Exception as e:
            logger.error(f"[狼人杀] AI复盘生成失败: {e}")
            return ""

    def _format_game_data_for_ai(self, room: Dict, winning_faction: str) -> str:
        """整理游戏数据为AI可读格式"""
        lines = []

        # 基本信息
        lines.append(f"【游戏结果】")
        faction_name = "狼人" if winning_faction == "werewolf" else "好人"
        lines.append(f"胜利方：{faction_name}")
        lines.append("")

        # 玩家身份
        lines.append(f"【玩家身份】")
        role_names = {
            "werewolf": "狼人",
            "seer": "预言家",
            "witch": "女巫",
            "hunter": "猎人",
            "villager": "村民"
        }
        for player_id, role in room["roles"].items():
            player_name = self._format_player_name(player_id, room)
            role_name = role_names.get(role, role)
            lines.append(f"{player_name} - {role_name}")
        lines.append("")

        # 游戏日志
        if room.get("game_log"):
            lines.append(f"【游戏进程】")
            for log_entry in room["game_log"]:
                lines.append(log_entry)
            lines.append("")

        return "\n".join(lines)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def capture_speech(self, event: AstrMessageEvent):
        """捕获发言阶段和遗言阶段的玩家发言"""
        group_id = event.get_group_id()

        # 检查是否有进行中的游戏
        if not group_id or group_id not in self.game_rooms:
            return

        room = self.game_rooms[group_id]
        player_id = event.get_sender_id()

        # 检查是否在发言阶段（白天发言、PK发言或遗言）
        if room["phase"] not in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK, GamePhase.LAST_WORDS]:
            return

        # 遗言阶段：检查是否是被杀的玩家
        if room["phase"] == GamePhase.LAST_WORDS:
            if room.get("last_killed") != player_id:
                return
        # 发言阶段：检查是否是当前发言者
        else:
            if room.get("current_speaker") != player_id:
                return

        # 获取消息内容
        message_text = event.get_message_outline()

        # 排除命令消息
        if message_text.startswith("/"):
            return

        # 记录发言内容
        if message_text.strip():
            room["current_speech"].append(message_text)
            logger.debug(f"[狼人杀] 捕获发言: {self._format_player_name(player_id, room)}: {message_text[:50]}")

    async def terminate(self):
        """插件终止时"""
        logger.info("狼人杀插件已终止")
