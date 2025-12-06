"""统一验证器 - 确保AI操作的目标合法"""
from typing import Optional, TYPE_CHECKING
from astrbot.api import logger

if TYPE_CHECKING:
    from ...models import GameRoom, Player


class TargetValidator:
    """AI目标验证器 - 防止操作死亡玩家"""

    @staticmethod
    def validate_target(
        room: "GameRoom",
        target_number: int,
        action_type: str = "操作"
    ) -> Optional[int]:
        """
        验证目标编号是否有效（玩家存在且存活）

        Args:
            room: 游戏房间
            target_number: 目标玩家编号 (1-9)
            action_type: 操作类型（用于日志）

        Returns:
            如果目标有效返回目标编号，否则返回 None
        """
        # 基本范围检查
        if not (1 <= target_number <= 9):
            logger.warning(f"[AI验证] {action_type}目标 {target_number} 超出范围(1-9)")
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

        logger.debug(f"[AI验证] {action_type}目标 {target_player.display_name} 验证通过")
        return target_number

    @staticmethod
    def validate_vote_target(
        room: "GameRoom",
        target_number: int,
        voter: "Player"
    ) -> Optional[int]:
        """验证投票目标"""
        # 不能投自己
        if target_number == voter.number:
            logger.warning(f"[AI验证] {voter.display_name} 尝试投自己，拒绝")
            return None

        return TargetValidator.validate_target(room, target_number, "投票")

    @staticmethod
    def validate_kill_target(
        room: "GameRoom",
        target_number: int,
        attacker: "Player"
    ) -> Optional[int]:
        """验证击杀目标（狼人/猎人）"""
        # 不能杀自己
        if target_number == attacker.number:
            logger.warning(f"[AI验证] {attacker.display_name} 尝试杀自己，拒绝")
            return None

        return TargetValidator.validate_target(room, target_number, "击杀")

    @staticmethod
    def validate_poison_target(
        room: "GameRoom",
        target_number: int,
        witch: "Player"
    ) -> Optional[int]:
        """验证毒药目标"""
        # 不能毒自己
        if target_number == witch.number:
            logger.warning(f"[AI验证] 女巫尝试毒自己，拒绝")
            return None

        return TargetValidator.validate_target(room, target_number, "毒杀")

    @staticmethod
    def validate_check_target(
        room: "GameRoom",
        target_number: int,
        seer: "Player"
    ) -> Optional[int]:
        """验证验人目标（预言家）"""
        # 不能验自己
        if target_number == seer.number:
            logger.warning(f"[AI验证] 预言家尝试验自己，拒绝")
            return None

        # 预言家可以验死人（但通常没意义）
        if not (1 <= target_number <= 9):
            return None

        target_player = room.get_player_by_number(target_number)
        if not target_player:
            return None

        return target_number

    @staticmethod
    def get_valid_targets(
        room: "GameRoom",
        exclude_player: "Player" = None,
        include_dead: bool = False
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
