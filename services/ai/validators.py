"""统一验证器 - 确保AI操作的目标合法"""

import re
from typing import TYPE_CHECKING

from astrbot.api import logger

from ...models.game_rules import GameRules

if TYPE_CHECKING:
    from ...models import GameRoom, Player


def validate_vote_statement(
    speech: str, target_number: int | None, abstain: bool
) -> None:
    """Check clear current promises without deriving a ballot from prose.

    Quoted material is masked only in a local analysis copy. Past, reported and
    hypothetical clauses do not commit the speaker to a vote. Ambiguous wording
    remains unclassified; the structured ballot is never changed.

    Args:
        speech: Complete public expression, retained unchanged by the caller.
        target_number: Structured target, or None for abstention.
        abstain: Whether the structured ballot explicitly abstains.

    Raises:
        ValueError: A definite current promise contradicts the structured ballot.
    """
    closing_quotes = {
        "「": "」",
        "『": "』",
        "“": "”",
        "‘": "’",
        '"': '"',
        "'": "'",
        "`": "`",
    }
    stack = []
    unquoted = []
    for character in speech:
        if stack and character == stack[-1]:
            stack.pop()
            unquoted.append(" ")
        elif character in closing_quotes:
            stack.append(closing_quotes[character])
            unquoted.append(" ")
        else:
            unquoted.append(" " if stack else character)
    text = re.sub(r"(?m)^\s*>[^\n]*", " ", "".join(unquoted))
    current = r"(?:今天|今日|这(?:一)?轮|本轮|这次|现在|此刻|最后|最终)"
    historical = r"(?:昨天|昨日|昨晚|前天|之前|此前|刚才|上(?:一)?轮|上次|原本|曾经)"
    owner = r"(?:我(?:的?(?:这(?:一)?票|票)|把票)?|这(?:一)?票|我的票)"
    prefix = rf"(?P<prefix>(?:(?:{current}|{owner})\s*){{0,3}})"
    verb = rf"(?P<negative>不(?:会|再|要)?|没(?:有)?|绝不|并不)?(?:(?:就|先|会|要|将|决定|确定|还是|改|{current})\s*){{0,3}}"
    ballot = r"(?:(?:投(?:给|向)?|票给)\s*(?P<target>\d+)\s*号|给\s*(?P<given>\d+)\s*号(?P<one_vote>一票)?|(?P<abstain>弃票))"
    statement = re.compile(prefix + verb + ballot)
    inherited_self = True
    past_scope = False
    uncertain_scope = False
    for raw_clause in re.split(r"(?<=[。！？!?；;\n])|[，,]", text):
        clause = raw_clause.strip()
        if not clause:
            continue
        if re.match(r"^(?:但(?:是)?|不过|而是|实际上|最终)", clause):
            uncertain_scope = False
        clause = re.sub(
            r"^(?:但(?:是)?|不过|所以|因此|那么|总之|而是|实际上)\s*", "", clause
        )
        explicit_self = bool(re.match(rf"(?:{current}\s*)?{owner}", clause))
        current_anchor = bool(re.match(rf"(?:{owner}\s*)?{current}", clause))
        if current_anchor:
            past_scope = False
        if re.match(rf"(?:我)?{historical}", clause):
            past_scope = True
        if re.match(
            r"(?:我)?(?:如果|假如|要是|除非|万一|可能|也许|或许|考虑|倾向|还没决定)",
            clause,
        ):
            uncertain_scope = True
        match = statement.match(clause)
        if match and re.match(
            r"\s*(?:(?:或(?:者|是)?|还是)\s*(?:(?:投(?:给)?)?\d+\s*号|弃票)|的话|才怪)",
            clause[match.end() :],
        ):
            uncertain_scope = True
        if explicit_self:
            inherited_self = True
        elif not match:
            inherited_self = bool(re.match(rf"{historical}(?:我)?(?:投|弃票)", clause))
        if (
            match
            and (explicit_self or inherited_self)
            and not (past_scope or uncertain_scope)
            and not re.search(r"[？?]", clause)
        ):
            # "Give seat N a chance" is not an explicit voting commitment.
            if match["given"] and "票" not in match["prefix"] and not match["one_vote"]:
                continue
            number_text = match["target"] or match["given"]
            number = int(number_text) if number_text else None
            negative = bool(match["negative"])
            if match["abstain"]:
                if (not negative and not abstain) or (negative and abstain):
                    raise ValueError("vote_statement: abstention_conflict")
            elif negative:
                if not abstain and number == target_number:
                    raise ValueError("vote_statement: excluded_target")
            elif abstain:
                raise ValueError("vote_statement: target_vs_abstention")
            elif number != target_number:
                raise ValueError("vote_statement: target_mismatch")
        if re.search(r"[。！？!?；;\n]$", raw_clause):
            inherited_self = True
            past_scope = uncertain_scope = False


class TargetValidator:
    """AI目标验证器 - 防止操作死亡玩家"""

    @staticmethod
    def validate_target(
        room: "GameRoom", target_number: int, action_type: str = "操作"
    ) -> int | None:
        """
        验证目标编号是否有效（玩家存在且存活）

        Args:
            room: 游戏房间
            target_number: 本局实际玩家编号
            action_type: 操作类型（用于日志）

        Returns:
            如果目标有效返回目标编号，否则返回 None
        """
        # 基本范围检查
        if type(target_number) is not int or target_number not in room.number_to_player:
            logger.warning(f"[Werewolf AI] Unknown {action_type} seat: {target_number}")
            return None

        # 获取目标玩家
        target_player = room.get_player_by_number(target_number)
        if not target_player:
            logger.warning(f"[AI验证] {action_type}目标 {target_number}号 不存在")
            return None

        # 检查是否存活
        if not target_player.is_alive:
            logger.warning(
                f"[AI验证] {action_type}目标 {target_player.display_name} 已死亡，拒绝操作"
            )
            return None

        logger.debug(
            f"[AI验证] {action_type}目标 {target_player.display_name} 验证通过"
        )
        return target_number

    @staticmethod
    def validate_vote_target(
        room: "GameRoom",
        target_number: int,
        voter: "Player",
        allowed_numbers: list[int] | None = None,
    ) -> int | None:
        """验证投票目标

        Args:
            room: 游戏房间
            target_number: 目标玩家编号
            voter: 投票的玩家
            allowed_numbers: PK投票时允许的候选编号列表，None 表示不限制

        Returns:
            目标有效返回编号，否则返回 None
        """
        # 不能投自己
        if target_number == voter.number:
            logger.warning(f"[AI验证] {voter.display_name} 尝试投自己，拒绝")
            return None

        # PK投票只能投给候选人
        if allowed_numbers is not None and target_number not in allowed_numbers:
            logger.warning(
                f"[AI验证] {voter.display_name} PK投票目标 {target_number}号 不在候选名单，拒绝"
            )
            return None

        return TargetValidator.validate_target(room, target_number, "投票")

    @staticmethod
    def validate_kill_target(
        room: "GameRoom", target_number: int, attacker: "Player"
    ) -> int | None:
        """验证击杀目标（狼人/猎人）"""
        # Hunters cannot shoot themselves; wolves may deliberately self-attack.
        if target_number not in GameRules.legal_targets(
            room, attacker, "kill" if attacker.is_werewolf else "shoot"
        ):
            logger.warning(
                f"[Werewolf AI] {attacker.display_name} supplied an illegal kill/shot target: {target_number}"
            )
            return None

        return TargetValidator.validate_target(room, target_number, "击杀")

    @staticmethod
    def validate_poison_target(
        room: "GameRoom", target_number: int, witch: "Player"
    ) -> int | None:
        """验证毒药目标"""
        # 不能毒自己
        if target_number == witch.number:
            logger.warning("[AI验证] 女巫尝试毒自己，拒绝")
            return None

        return TargetValidator.validate_target(room, target_number, "毒杀")

    @staticmethod
    def validate_check_target(
        room: "GameRoom", target_number: int, seer: "Player"
    ) -> int | None:
        """验证验人目标（预言家）"""
        # 不能验自己
        if target_number == seer.number:
            logger.warning("[AI验证] 预言家尝试验自己，拒绝")
            return None

        return TargetValidator.validate_target(room, target_number, "查验")

    @staticmethod
    def get_valid_targets(
        room: "GameRoom", exclude_player: "Player" = None, include_dead: bool = False
    ) -> list:
        """
        获取所有有效目标列表

        Args:
            room: 游戏房间
            exclude_player: 排除的玩家（通常是操作者自己）
            include_dead: 是否包含死亡玩家

        Returns:
            有效目标玩家编号列表
        """
        targets = []
        for p in room.players.values():
            if exclude_player and p.id == exclude_player.id:
                continue
            if not include_dead and not p.is_alive:
                continue
            targets.append(p.number)
        return sorted(targets)

    @staticmethod
    def get_alive_players_info(room: "GameRoom") -> str:
        """获取存活玩家信息（用于提示词）"""
        alive = room.get_alive_players()
        return ", ".join([f"{p.number}号{p.display_name}" for p in alive])
