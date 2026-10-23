"""统一验证器 - 确保AI操作的目标合法"""

from typing import TYPE_CHECKING

from astrbot.api import logger

from ...models.game_rules import GameRules

if TYPE_CHECKING:
    from ...models import GameRoom, Player


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
