"""Constrain reviews to selections from authoritative, frozen game facts."""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import Role
from .game_rules import GameRules

if TYPE_CHECKING:
    from .room import GameRoom


REVIEW_ADVICE = {
    "verify_claims": "把玩家自称、本人真实技能结果和终局公布身份分开核对，按当时可见信息判断。",
    "track_votes": "核对每轮实际票型与改票原因，区分投给具体目标、明确弃票和未投票。",
    "independent_judgement": "重复同一观点不增加证据，给出自己的怀疑对象和具体依据。",
    "check_skills": "行动前核对技能授权和药水库存，被刀或获救都不直接证明阵营。",
    "protect_private": "狼队配合时区分公开伪装与私密经历，避免把密谋误当成全场已知的信息。",
    "concrete_questions": "把追问落到具体原话与票向上，已解决的问题不重复追问。",
}


@dataclass(frozen=True)
class AIReviewSnapshot:
    """An end-game fact source captured before any model request can yield."""

    summary: str
    facts: dict[str, str]

    @classmethod
    def from_room(cls, room: "GameRoom", winning_faction: str) -> "AIReviewSnapshot":
        """Freeze the outcome and committed events without trusting player prose.

        Args:
            room: Finished game with authoritative roles and complete original logs.
            winning_faction: Faction supplied by the game's victory handler.

        Returns:
            A fact summary and selectable original action records with stable L IDs.

        Raises:
            ValueError: Roles are missing or the claimed outcome contradicts rules.
        """
        if not room.players or any(p.role is None for p in room.players.values()):
            raise ValueError("Review requires assigned player roles")
        message, actual_faction = GameRules.outcome(room)
        expected = "villager" if winning_faction == "good" else winning_faction
        if actual_faction is None or actual_faction != expected:
            raise ValueError("Review winner does not match authoritative game outcome")
        alive = room.get_alive_players()
        wolves = [p for p in alive if p.role == Role.WEREWOLF]
        good = [p for p in alive if p.role != Role.WEREWOLF]
        gods = [p for p in good if p.is_god]
        lines = [
            message,
            f"全部存活{len(alive)}人；狼人{len(wolves)}人；好人{len(good)}人（其中神职{len(gods)}人）。",
            "存活狼人：" + ("、".join(p.display_name for p in wolves) or "无"),
            "存活好人：" + ("、".join(p.display_name for p in good) or "无"),
        ]
        facts = {}
        round_number = None
        for index, entry in enumerate(tuple(room.game_log), 1):
            if match := re.fullmatch(r"第(\d+)晚", entry):
                round_number = int(match[1])
            # Only engine-authored action lines are eligible. Speeches, last
            # words, wolf chat and subjective J records never become event facts.
            if entry.startswith(
                ("🐺 ", "🌙 狼人", "🔮 ", "💊 ", "🔫 ", "🗳️ ", "📊 投票放逐：")
            ):
                prefix = (
                    f"第{round_number}轮："
                    if round_number is not None
                    else "轮次未记录："
                )
                facts[f"L{index}"] = prefix + entry
        return cls("\n".join(lines), facts)


@dataclass(frozen=True)
class AIReviewPlan:
    """Model-selected highlights and advice; no generated factual prose is executable."""

    highlight_ids: tuple[str, ...]
    advice_ids: tuple[str, ...]

    @classmethod
    def parse(cls, response: str, snapshot: AIReviewSnapshot) -> "AIReviewPlan":
        """Accept one complete JSON selection without rewriting any facts.

        Args:
            response: Complete uncut model response, retained separately in audit.
            snapshot: Frozen allowlist of authoritative facts for this review.

        Returns:
            Validated selections; all selected records will be rendered completely.

        Raises:
            ValueError: Format, fields, duplicates or selected IDs are invalid.
        """
        text = response.strip()
        if match := re.fullmatch(r"\*\*\s*(\{.*\})\s*\*\*", text, re.DOTALL):
            text = match[1]
        if text.startswith("```"):
            match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if not match:
                raise ValueError("Review JSON code fence is incomplete")
            text = match[1]

        def unique_keys(pairs):
            """Reject conflicting duplicate fields rather than replacing selections."""
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate review field")
                result[key] = value
            return result

        data = json.loads(text, object_pairs_hook=unique_keys)
        if not isinstance(data, dict) or set(data) != {"highlight_ids", "advice_ids"}:
            raise ValueError(
                "Review requires only highlight_ids and advice_ids; no free prose"
            )
        for key, allowed in (
            ("highlight_ids", snapshot.facts),
            ("advice_ids", REVIEW_ADVICE),
        ):
            values = data[key]
            if not isinstance(values, list) or any(
                type(item) is not str for item in values
            ):
                raise ValueError("Review selections must be lists of string IDs")
            if len(values) != len(set(values)) or not set(values) <= allowed.keys():
                raise ValueError(
                    "Review selects duplicate or unavailable fact/advice IDs"
                )
        if snapshot.facts and not data["highlight_ids"]:
            raise ValueError("Review must select at least one available event fact")
        return cls(tuple(data["highlight_ids"]), tuple(data["advice_ids"]))
