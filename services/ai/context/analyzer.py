"""Evidence-based observations, without keyword-derived identity labels."""

import re
from typing import TYPE_CHECKING

from ..prompts import DUEL_CONTEXT_TEMPLATE, SITUATION_TEMPLATE, TACTICAL_DIRECTIVES

if TYPE_CHECKING:
    from ....models import GameRoom, Player


class SituationAnalyzer:
    """Offer public progress and role-authorized tactical options."""

    @staticmethod
    def get_situation_awareness(room: "GameRoom") -> str:
        """Render public progress only.

        Args:
            room: Current room.

        Returns:
            Public counts without hidden role distribution.
        """
        return SITUATION_TEMPLATE.format(
            round_number=room.current_round,
            alive_count=room.alive_count,
            total_players=room.player_count,
        )

    @staticmethod
    def get_tactical_directive(player: "Player", room: "GameRoom") -> str:
        """Offer a wolf option from the wolf's known teammates.

        Args:
            player: Acting player.
            room: Current room.

        Returns:
            A consideration rather than a compulsory tactic.
        """
        if not player.is_werewolf:
            return ""
        wolves = len(room.get_alive_werewolves())
        if wolves == 1:
            return TACTICAL_DIRECTIVES["wolf_disadvantage"]
        return TACTICAL_DIRECTIVES["normal"]

    @staticmethod
    def get_duel_context(player: "Player", room: "GameRoom") -> str:
        """Recognize only explicit first-person role claims with source evidence.

        Args:
            player: Observer whose public evidence is used.
            room: Current room.

        Returns:
            A sourced description, or empty text when fewer than two claimants exist.
        """
        if not player.ai_context:
            return ""
        claimants = {}
        pattern = r"(?:^|[。！？；\n])\s*(?:我是(?:真)?预言家|我(?:昨晚|第[一二三四五六七八九十\d]+[晚夜])?(?:查验|验)(?:了)?\s*\d+号)"
        for key, record in player.ai_context.evidence_records().items():
            if record["kind"] not in ("S", "D"):
                continue
            if re.search(pattern, record["text"]):
                claimants.setdefault(record["actor"], []).append(key)
        if len(claimants) < 2:
            return ""
        names = list(claimants)
        source = "；".join(
            f"{name}的原话见{','.join(ids)}" for name, ids in claimants.items()
        )
        return (
            DUEL_CONTEXT_TEMPLATE["observer"].format(
                player_a=names[0], player_b=names[1]
            )
            + " 来源："
            + source
        )


class BehaviorAnalyzer:
    """Expose observable actions without classifying motives or identities."""

    @staticmethod
    def analyze_player_behaviors(room: "GameRoom") -> dict[str, list[str]]:
        """Summarize visible activity without interpreting brevity or consensus.

        Args:
            room: Current room.

        Returns:
            Observable counts per living player.
        """
        observer = next(
            (p for p in room.players.values() if p.is_ai and p.ai_context), None
        )
        if observer is None:
            return {}
        ctx = observer.ai_context
        return {
            p.display_name: [
                f"公开发言{sum(s['player'] == p.display_name for s in ctx.speeches)}次",
                f"已提交投票{sum(v['voter'] == p.display_name for v in ctx.vote_history)}次",
            ]
            for p in room.get_alive_players()
        }

    @staticmethod
    def get_behavior_analysis_prompt(player: "Player", room: "GameRoom") -> str:
        """Reference actual evidence instead of assigning speculative labels.

        Args:
            player: Observer.
            room: Current room.

        Returns:
            Source references for public player statements and ballots.
        """
        if not player.ai_context:
            return ""
        lines = []
        for key, record in player.ai_context.evidence_records().items():
            if record["kind"] in ("S", "D", "V"):
                lines.append(
                    f"{record['actor']}：{key}（{'已提交投票' if record['kind'] == 'V' else '原话声明'}）"
                )
        return "\n".join(lines)
