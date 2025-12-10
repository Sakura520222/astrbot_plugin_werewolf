"""AI玩家数据模型"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AIPlayerConfig:
    """AI玩家配置"""
    name: str                           # AI玩家名称（如：小咪）
    model_id: str = ""                  # 模型提供商ID（留空使用默认）
    personality: str = ""               # 性格描述（可选）
    max_retries: int = 3                # 最大重试次数
    retry_delay: float = 1.0            # 重试延迟（秒）

    def __post_init__(self):
        """验证配置"""
        if not self.name:
            raise ValueError("AI玩家名称不能为空")


@dataclass
class AIPlayerContext:
    """AI玩家的游戏上下文"""
    # 基本信息
    player_number: int = 0              # 玩家编号
    role_name: str = ""                 # 角色名称
    is_werewolf: bool = False           # 是否是狼人

    # 狼人队友（仅狼人可见）
    werewolf_teammates: List[str] = field(default_factory=list)

    # 验人结果记录（仅预言家可见）
    seer_results: List[dict] = field(default_factory=list)

    # 公开信息
    alive_players: List[str] = field(default_factory=list)  # 存活玩家列表
    dead_players: List[str] = field(default_factory=list)   # 已死亡玩家列表
    current_round: int = 1              # 当前回合
    current_phase: str = ""             # 当前阶段描述（如"第1天白天发言"）

    # 游戏进程记录
    game_events: List[str] = field(default_factory=list)    # 重要事件记录
    speeches: List[dict] = field(default_factory=list)      # 发言记录
    vote_history: List[dict] = field(default_factory=list)  # 投票记录

    # 女巫状态（仅女巫可见）
    witch_antidote_used: bool = False   # 解药是否已用
    witch_poison_used: bool = False     # 毒药是否已用
    last_killed_player: Optional[str] = None  # 今晚被杀的玩家
    witch_saved_player: Optional[str] = None  # 女巫救了谁（用于记忆）
    witch_poisoned_player: Optional[str] = None  # 女巫毒了谁（用于记忆）

    # 猎人状态
    can_shoot: bool = False             # 是否可以开枪

    # 狼人密谋记录（仅狼人可见）
    wolf_chat_messages: List[dict] = field(default_factory=list)  # [{sender, content, round}, ...]

    # 投票期间讨论记录（所有人可见，投票前的重要参考）
    vote_discussions: List[dict] = field(default_factory=list)  # [{player, content, round}, ...]

    def add_wolf_chat(self, sender_name: str, content: str, round_num: int) -> None:
        """添加狼人密谋消息"""
        self.wolf_chat_messages.append({
            "sender": sender_name,
            "content": content,
            "round": round_num
        })

    def add_event(self, event: str) -> None:
        """添加事件记录"""
        self.game_events.append(event)

    def add_speech(self, player_name: str, content: str, is_pk: bool = False) -> None:
        """添加发言记录"""
        self.speeches.append({
            "player": player_name,
            "content": content,
            "is_pk": is_pk,
            "round": self.current_round
        })

    def add_vote(self, voter: str, target: str, is_pk: bool = False) -> None:
        """添加投票记录"""
        self.vote_history.append({
            "voter": voter,
            "target": target,
            "is_pk": is_pk,
            "round": self.current_round
        })

    def add_seer_result(self, target_name: str, is_werewolf: bool) -> None:
        """添加验人结果"""
        self.seer_results.append({
            "target": target_name,
            "is_werewolf": is_werewolf,
            "round": self.current_round
        })

    def add_vote_discussion(self, player_name: str, content: str) -> None:
        """添加投票期间的讨论"""
        self.vote_discussions.append({
            "player": player_name,
            "content": content,
            "round": self.current_round
        })

    def update_alive_players(self, alive_list: List[str], dead_list: List[str]) -> None:
        """更新存活玩家列表"""
        self.alive_players = alive_list
        self.dead_players = dead_list

    def to_prompt_context(self) -> str:
        """将上下文转换为提示词格式"""
        # 在函数内部导入避免循环依赖
        from ..services.ai.prompts import GAME_RULES

        lines = []

        # 📜 游戏规则说明（让所有AI了解基本规则，避免质疑女巫等角色的能力）
        lines.append(GAME_RULES)
        lines.append("")

        # 🌅 首日特殊声明（防止AI产生虚假记忆）
        if self.current_round == 1 and len(self.speeches) == 0:
            lines.append("🌅 【重要】这是游戏的第一天！")
            lines.append("⚠️ 昨晚只分配了身份，没有任何玩家发言，没有任何公开信息。")
            lines.append("⚠️ 严禁编造\"昨天XXX说了\"之类的虚假信息！")
            lines.append("")

        # 🚨 昨晚死亡情况（最重要！放在最前面强调）
        last_night_deaths = [e for e in self.game_events if f"第{self.current_round}夜死亡" in e]
        last_night_peaceful = [e for e in self.game_events if f"第{self.current_round}夜：平安夜" in e]

        if last_night_deaths:
            lines.append("🚨🚨🚨【昨晚死亡公告 - 必须认真阅读！】🚨🚨🚨")
            for death_event in last_night_deaths:
                lines.append(f"☠️ {death_event}")
            lines.append("⚠️ 昨晚有人死了！这不是平安夜！严禁说平安夜！")
            lines.append("")
        elif last_night_peaceful:
            lines.append("🌙【昨晚是平安夜】")
            lines.append("昨晚没有人死亡，女巫可能救了人。")
            lines.append("")

        # 🗳️ 投票放逐结果（重要！突出显示）
        exile_events = [e for e in self.game_events if "投票放逐" in e and "被放逐出局" in e]
        if exile_events:
            lines.append("🗳️🗳️🗳️【投票放逐记录 - 关键信息！】🗳️🗳️🗳️")
            for exile_event in exile_events:
                lines.append(f"⚖️ {exile_event}")
            lines.append("💡 分析：谁投了被放逐者？谁保了他？这能暴露阵营！")
            lines.append("")

        # 当前阶段
        if self.current_phase:
            lines.append(f"【当前阶段】")
            lines.append(f"⏰ {self.current_phase}")
            lines.append("")

        # 基本信息
        lines.append(f"【你的身份】")
        lines.append(f"你是 {self.player_number}号玩家，身份是 {self.role_name}")

        if self.is_werewolf and self.werewolf_teammates:
            lines.append(f"你的狼人队友是：{', '.join(self.werewolf_teammates)}")

        # 狼人密谋记录（仅狼人可见）
        if self.is_werewolf and self.wolf_chat_messages:
            lines.append(f"\n【狼人密谋记录 - 绝密！严禁在白天提及！】")
            lines.append(f"⚠️ 以下是你们狼人队友在夜晚的私密交流，只有狼人能看到，白天绝对不能透露！")
            for msg in self.wolf_chat_messages[-10:]:  # 只显示最近10条
                lines.append(f"[第{msg['round']}晚夜间密谋] {msg['sender']}: {msg['content']}")

        # 验人结果
        if self.seer_results:
            lines.append(f"\n【验人结果】")
            for result in self.seer_results:
                status = "狼人" if result["is_werewolf"] else "好人"
                lines.append(f"第{result['round']}晚：{result['target']} 是 {status}")

        # 存活情况
        lines.append(f"\n【当前存活玩家】")
        lines.append(", ".join(self.alive_players) if self.alive_players else "无")

        if self.dead_players:
            lines.append(f"\n【已死亡玩家】")
            lines.append(", ".join(self.dead_players))

        # 女巫药水状态
        if self.role_name == "女巫":
            lines.append(f"\n【你的女巫技能信息 - 仅你可见】")
            lines.append(f"解药：{'已用' if self.witch_antidote_used else '可用'}")
            lines.append(f"毒药：{'已用' if self.witch_poison_used else '可用'}")
            if self.last_killed_player:
                lines.append(f"今晚被狼人杀死的是：{self.last_killed_player}")
            if self.witch_saved_player:
                lines.append(f"🩹 你救过的人：{self.witch_saved_player}")
            if self.witch_poisoned_player:
                lines.append(f"☠️ 你毒过的人：{self.witch_poisoned_player}")
            lines.append(f"（注：以上是你作为女巫的私密视角，公开说出会暴露身份，除非你决定跳女巫）")

        # 重要事件
        if self.game_events:
            lines.append(f"\n【重要事件】")
            for event in self.game_events[-10:]:  # 只显示最近10条
                lines.append(f"- {event}")

        # 发言记录
        if self.speeches:
            lines.append(f"\n【发言记录】")
            for speech in self.speeches[-15:]:  # 只显示最近15条
                prefix = "[PK]" if speech.get("is_pk") else ""
                lines.append(f"{prefix}{speech['player']}: {speech['content'][:100]}")

        # 投票记录（重要！分析投票可以推断阵营）
        if self.vote_history:
            lines.append(f"\n🗳️【投票记录 - 分析投票方向可推断阵营！】")
            # 按轮次分组显示
            current_round_votes = [v for v in self.vote_history if v.get("round") == self.current_round]
            prev_round_votes = [v for v in self.vote_history if v.get("round") != self.current_round]

            if prev_round_votes:
                lines.append("历史投票：")
                for vote in prev_round_votes[-5:]:
                    prefix = "[PK]" if vote.get("is_pk") else ""
                    lines.append(f"  {prefix}第{vote['round']}轮: {vote['voter']} → {vote['target']}")

            if current_round_votes:
                lines.append("本轮投票：")
                for vote in current_round_votes:
                    prefix = "[PK]" if vote.get("is_pk") else ""
                    lines.append(f"  {prefix}{vote['voter']} → {vote['target']}")

            lines.append("💡 思考：投同一人的可能是同阵营，保人的要警惕！")

        # 投票期间讨论（重要！这是投票前的最新观点）
        if self.vote_discussions:
            current_round_discussions = [d for d in self.vote_discussions if d.get("round") == self.current_round]
            if current_round_discussions:
                lines.append(f"\n💬💬💬【投票期间讨论 - 必读！这是大家投票前的最新观点！】💬💬💬")
                lines.append("⚠️ 以下是在投票阶段，大家针对本次投票发表的看法和讨论：")
                for disc in current_round_discussions:  # 显示全部
                    lines.append(f"  💭 {disc['player']}：{disc['content'][:120]}")
                lines.append("💡 分析：谁在带节奏？谁在保谁？谁在攻击谁？这些讨论会影响投票结果！")

        return "\n".join(lines)
