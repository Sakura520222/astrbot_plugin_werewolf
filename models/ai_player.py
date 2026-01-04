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
    
    # 增强记忆系统
    player_suspicions: dict = field(default_factory=dict)  # 玩家怀疑度记录 {player_name: suspicion_level}
    player_alliances: dict = field(default_factory=dict)   # 玩家阵营推断 {player_name: alliance_type}
    key_events_memory: List[dict] = field(default_factory=list)  # 关键事件记忆 [{event, importance, round}, ...]
    speech_patterns: dict = field(default_factory=dict)   # 玩家发言模式分析 {player_name: pattern_analysis}
    voting_patterns: dict = field(default_factory=dict)   # 玩家投票模式分析 {player_name: voting_analysis}
    round_summaries: List[str] = field(default_factory=list)  # 每轮总结
    personal_notes: List[str] = field(default_factory=list)  # 个人笔记和推理

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

    # ==================== 增强记忆系统方法 ====================
    
    def update_suspicion_level(self, player_name: str, level: int, reason: str = "") -> None:
        """更新玩家怀疑度 (0-10, 0=绝对好人, 10=绝对狼人)"""
        self.player_suspicions[player_name] = {
            "level": level,
            "reason": reason,
            "round": self.current_round,
            "history": self.player_suspicions.get(player_name, {}).get("history", [])
        }
        
        # 保存历史记录
        if "history" not in self.player_suspicions[player_name]:
            self.player_suspicions[player_name]["history"] = []
        self.player_suspicions[player_name]["history"].append({
            "level": level,
            "reason": reason,
            "round": self.current_round
        })
        
        # 限制历史记录长度
        if len(self.player_suspicions[player_name]["history"]) > 5:
            self.player_suspicions[player_name]["history"] = self.player_suspicions[player_name]["history"][-5:]

    def update_alliance_inference(self, player_name: str, alliance_type: str, confidence: float, reason: str = "") -> None:
        """更新玩家阵营推断 (werewolf/good/unknown, 置信度0-1)"""
        self.player_alliances[player_name] = {
            "type": alliance_type,
            "confidence": confidence,
            "reason": reason,
            "round": self.current_round,
            "history": self.player_alliances.get(player_name, {}).get("history", [])
        }
        
        # 保存历史记录
        if "history" not in self.player_alliances[player_name]:
            self.player_alliances[player_name]["history"] = []
        self.player_alliances[player_name]["history"].append({
            "type": alliance_type,
            "confidence": confidence,
            "reason": reason,
            "round": self.current_round
        })
        
        # 限制历史记录长度
        if len(self.player_alliances[player_name]["history"]) > 5:
            self.player_alliances[player_name]["history"] = self.player_alliances[player_name]["history"][-5:]

    def add_key_event_memory(self, event: str, importance: int, details: dict = None) -> None:
        """添加关键事件记忆 (重要性1-10)"""
        memory_entry = {
            "event": event,
            "importance": importance,
            "round": self.current_round,
            "details": details or {},
            "timestamp": self.current_phase
        }
        
        # 检查是否已存在类似事件
        for existing in self.key_events_memory:
            if existing["event"] == event and existing["round"] == self.current_round:
                # 更新重要性
                existing["importance"] = max(existing["importance"], importance)
                if details:
                    existing["details"].update(details)
                return
        
        self.key_events_memory.append(memory_entry)
        
        # 按重要性排序并限制数量
        self.key_events_memory.sort(key=lambda x: x["importance"], reverse=True)
        if len(self.key_events_memory) > 20:
            self.key_events_memory = self.key_events_memory[:20]

    def analyze_speech_pattern(self, player_name: str, speech_content: str) -> None:
        """分析玩家发言模式"""
        if player_name not in self.speech_patterns:
            self.speech_patterns[player_name] = {
                "speech_count": 0,
                "avg_length": 0,
                "keywords": {},
                "emotional_state": "neutral",
                "consistency_score": 0.5,
                "recent_speeches": []
            }
        
        pattern = self.speech_patterns[player_name]
        pattern["speech_count"] += 1
        pattern["recent_speeches"].append({
            "content": speech_content,
            "round": self.current_round,
            "length": len(speech_content)
        })
        
        # 限制最近发言记录数量
        if len(pattern["recent_speeches"]) > 10:
            pattern["recent_speeches"] = pattern["recent_speeches"][-10:]
        
        # 更新平均长度
        total_length = sum(s["length"] for s in pattern["recent_speeches"])
        pattern["avg_length"] = total_length / len(pattern["recent_speeches"])
        
        # 简单关键词分析
        keywords = ["狼", "杀", "投票", "预言家", "女巫", "猎人", "好人", "坏人", "怀疑", "相信"]
        for keyword in keywords:
            if keyword in speech_content:
                pattern["keywords"][keyword] = pattern["keywords"].get(keyword, 0) + 1
        
        # 简单情绪分析
        emotional_words = {
            "激动": ["！", "？？", "什么鬼", "搞笑"],
            "冷静": ["分析", "逻辑", "因为", "所以"],
            "困惑": ["？", "不懂", "为什么", "奇怪"],
            "攻击": ["假", "骗子", "悍跳", "装"]
        }
        
        for emotion, words in emotional_words.items():
            if any(word in speech_content for word in words):
                pattern["emotional_state"] = emotion
                break

    def analyze_voting_pattern(self, player_name: str, vote_target: str, is_pk: bool = False) -> None:
        """分析玩家投票模式"""
        if player_name not in self.voting_patterns:
            self.voting_patterns[player_name] = {
                "vote_count": 0,
                "targets": {},
                "pk_behavior": "avoid",
                "consistency": 0.5,
                "recent_votes": []
            }
        
        pattern = self.voting_patterns[player_name]
        pattern["vote_count"] += 1
        pattern["targets"][vote_target] = pattern["targets"].get(vote_target, 0) + 1
        pattern["recent_votes"].append({
            "target": vote_target,
            "round": self.current_round,
            "is_pk": is_pk
        })
        
        # 限制最近投票记录数量
        if len(pattern["recent_votes"]) > 10:
            pattern["recent_votes"] = pattern["recent_votes"][-10:]
        
        # 分析PK行为
        if is_pk:
            pattern["pk_behavior"] = "participate"
        
        # 计算一致性（基于投票目标的多样性）
        unique_targets = len(set(v["target"] for v in pattern["recent_votes"]))
        pattern["consistency"] = 1.0 - (unique_targets - 1) / max(len(pattern["recent_votes"]) - 1, 1)

    def add_round_summary(self, summary: str) -> None:
        """添加轮次总结"""
        self.round_summaries.append(f"第{self.current_round}轮: {summary}")
        # 限制总结数量
        if len(self.round_summaries) > 10:
            self.round_summaries = self.round_summaries[-10:]

    def add_personal_note(self, note: str) -> None:
        """添加个人笔记和推理"""
        self.personal_notes.append(f"[第{self.current_round}轮] {note}")
        # 限制笔记数量
        if len(self.personal_notes) > 30:
            self.personal_notes = self.personal_notes[-30:]

    def get_memory_summary(self) -> str:
        """获取记忆摘要"""
        lines = []
        
        # 怀疑度摘要
        if self.player_suspicions:
            lines.append("【🧠 玩家怀疑度分析】")
            sorted_suspicions = sorted(
                self.player_suspicions.items(), 
                key=lambda x: x[1].get("level", 0), 
                reverse=True
            )
            for player, suspicion in sorted_suspicions[:5]:  # 只显示前5个
                level = suspicion.get("level", 0)
                reason = suspicion.get("reason", "")
                lines.append(f"- {player}: {level}/10 ({reason})")
            lines.append("")
        
        # 阵营推断摘要
        if self.player_alliances:
            lines.append("【👥 阵营推断】")
            for player, alliance in self.player_alliances.items():
                alliance_type = alliance.get("type", "unknown")
                confidence = alliance.get("confidence", 0)
                lines.append(f"- {player}: {alliance_type} (置信度: {confidence:.1f})")
            lines.append("")
        
        # 关键事件摘要
        if self.key_events_memory:
            lines.append("【⭐ 关键事件记忆】")
            for event in self.key_events_memory[:5]:  # 只显示前5个
                importance = event.get("importance", 0)
                event_desc = event.get("event", "")
                lines.append(f"- [{importance}/10] {event_desc}")
            lines.append("")
        
        # 发言模式摘要
        if self.speech_patterns:
            lines.append("【🗣️ 发言模式分析】")
            for player, pattern in list(self.speech_patterns.items())[:3]:  # 只显示前3个
                avg_length = pattern.get("avg_length", 0)
                emotion = pattern.get("emotional_state", "neutral")
                lines.append(f"- {player}: 平均长度{avg_length:.0f}字, 情绪{emotion}")
            lines.append("")
        
        # 投票模式摘要
        if self.voting_patterns:
            lines.append("【🗳️ 投票模式分析】")
            for player, pattern in list(self.voting_patterns.items())[:3]:  # 只显示前3个
                consistency = pattern.get("consistency", 0)
                pk_behavior = pattern.get("pk_behavior", "avoid")
                lines.append(f"- {player}: 一致性{consistency:.1f}, PK行为{pk_behavior}")
            lines.append("")
        
        return "\n".join(lines)

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

        # 🧠 增强记忆系统 - 记忆摘要
        memory_summary = self.get_memory_summary()
        if memory_summary:
            lines.append(f"\n🧠【你的记忆分析 - AI增强记忆系统】")
            lines.append(memory_summary)

        # 轮次总结
        if self.round_summaries:
            lines.append(f"\n【📝 游戏轮次总结】")
            for summary in self.round_summaries[-3:]:  # 只显示最近3轮
                lines.append(f"- {summary}")

        # 个人笔记
        if self.personal_notes:
            lines.append(f"\n【📔 你的个人笔记和推理】")
            for note in self.personal_notes[-5:]:  # 只显示最近5条
                lines.append(f"- {note}")

        return "\n".join(lines)
