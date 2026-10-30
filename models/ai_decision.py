"""Strict model output parsing; prose is never searched for arbitrary numbers."""

import json
import re
from dataclasses import dataclass, field


@dataclass
class AIDecision:
    """Separate private intent and judgement from public expression."""

    action: str
    target_number: int | None = None
    abstain: bool = False
    speech: str = ""
    intent: str = ""
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    change_reason: str = ""
    assessments: list[dict] = field(default_factory=list)
    legacy: bool = False
    belief_rejections: list[str] = field(default_factory=list)
    request_evidence_ids: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, response: str, task: str) -> "AIDecision":
        """Parse JSON or an unambiguous legacy command.

        Args:
            response: Complete model response, never truncated.
            task: Expected stage protocol.

        Returns:
            A structured decision ready for room-specific validation.

        Raises:
            ValueError: Missing, ambiguous or contradictory output fields.
        """
        text = response.strip()
        bold_object = re.fullmatch(r"\*\*\s*(\{.*\})\s*\*\*", text, re.DOTALL)
        if bold_object:
            text = bold_object[1]
        if text.startswith("```"):
            match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if not match:
                raise ValueError("JSON code fence is incomplete")
            text = match[1].strip()
        if text.startswith("{"):

            def unique_keys(pairs):
                """Reject duplicate keys rather than silently replacing a ballot."""
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError(f"Duplicate JSON field: {key}")
                    result[key] = value
                return result

            data = json.loads(text, object_pairs_hook=unique_keys)
            if not isinstance(data, dict):
                raise ValueError("Expected one JSON object")
            requested = data.get("request_evidence_ids", [])
            if not isinstance(requested, list) or any(
                not isinstance(item, str) for item in requested
            ):
                raise ValueError("request_evidence_ids must be a list of source IDs")
            if requested:
                # A read request never carries an executable action or belief update.
                return cls(task, request_evidence_ids=list(dict.fromkeys(requested)))
            for name in ("intent", "reason", "speech", "change_reason"):
                if name in data and not isinstance(data[name], str):
                    raise ValueError(f"{name} must be text")
            if not data.get("intent", "").strip() or not data.get("reason", "").strip():
                raise ValueError("intent and reason are required")
            target = data.get("target_number")
            abstain = data.get("abstain")
            if target is not None and type(target) is not int:
                raise ValueError("target_number must be an integer or null")
            if type(abstain) is not bool:
                raise ValueError("abstain must be a boolean")
            if abstain and target is not None:
                raise ValueError("Abstention cannot also specify a target")
            evidence = data.get("evidence_ids", [])
            assessments = data.get("assessments", [])
            if not isinstance(evidence, list) or any(
                not isinstance(item, str) for item in evidence
            ):
                raise ValueError("evidence_ids must be a list of source IDs")
            if not isinstance(assessments, list) or any(
                not isinstance(item, dict) for item in assessments
            ):
                raise ValueError("assessments must be a list of judgements")
            action = data.get("action", task)
            if not isinstance(action, str):
                raise ValueError("action must be text")
            speech = data.get("speech", "")
            if (
                task
                in (
                    "day_speech",
                    "pk_speech",
                    "last_words",
                    "werewolf_chat",
                    "day_vote",
                )
                and not speech.strip()
            ):
                raise ValueError("This stage requires public expression in speech")
            return cls(
                action,
                target,
                abstain,
                speech,
                data["intent"],
                data["reason"],
                evidence,
                data.get("change_reason", ""),
                assessments,
            )
        if task == "day_vote":
            match = re.fullmatch(
                r"(?:\[发言\](.*?)\s*)?\[投票\]\s*(\d+|弃票)\s*", text, re.DOTALL
            )
            if match:
                speech, ballot = match[1] or "", match[2]
            elif re.fullmatch(r"\d+|弃票", text):
                speech, ballot = "", text
            else:
                raise ValueError("Missing an unambiguous explicit ballot")
            return cls(
                task,
                None if ballot == "弃票" else int(ballot),
                ballot == "弃票",
                speech,
                legacy=True,
            )
        if task in ("werewolf_kill", "seer_check", "hunter_shoot"):
            if re.fullmatch(r"\d+", text):
                return cls(task, int(text), legacy=True)
            if task == "hunter_shoot" and text == "不开枪":
                return cls(task, abstain=True, legacy=True)
        if task == "witch_action":
            if text in ("救人", "不操作"):
                return cls(
                    "save" if text == "救人" else "pass",
                    abstain=text == "不操作",
                    legacy=True,
                )
            match = re.fullmatch(r"毒人\s*(\d+)\s*号?", text)
            if match:
                return cls("poison", int(match[1]), legacy=True)
        raise ValueError(
            "Expected the stage's JSON protocol or an exact legacy command"
        )
