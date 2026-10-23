"""Reproducible model evaluations with complete, private JSONL evidence.

Run with a provider factory returning an AstrBot-compatible context:
python tests/evaluate_ai.py --provider-factory my_provider:create --output samples.jsonl
The factory receives a seed. No credentials are accepted, displayed or saved here.
"""

import argparse
import asyncio
import importlib
import inspect
import json
import random
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from test_issue_1_regressions import load_plugin


def create_scenario(
    plugin, context, seed, personality="logical", scenario="claims", role="villager"
):
    """Build an isolated one-human/eight-AI room with controlled evidence.

    Args:
        plugin: Loaded plugin namespace.
        context: Model provider context.
        seed: Deterministic fixture and personality assignment seed.
        personality: Profile assigned to the observed player.
        scenario: Fixed evidence scenario.
        role: Observed player's real role.

    Returns:
        Room, AI service and observed player.
    """
    models = plugin.models
    room = models.GameRoom(f"evaluation-{seed}", "human", models.GameConfig.default())
    room.current_round = 1
    room.phase = models.GamePhase.DAY_SPEAKING
    room.config.enable_ai_review = False
    service = plugin.services.AIPlayerService(context)
    roles = [
        models.Role.VILLAGER,
        models.Role.WEREWOLF,
        models.Role.VILLAGER,
        models.Role.VILLAGER,
        models.Role.SEER,
        models.Role.WITCH,
        models.Role.HUNTER,
        models.Role.WEREWOLF,
        models.Role.WEREWOLF,
    ]
    actor_number = (
        6 if scenario == "witch_memory" else 5 if scenario == "first_night" else 4
    )
    wanted = models.Role(role)
    matching = roles.index(wanted)
    roles[actor_number - 1], roles[matching] = roles[matching], roles[actor_number - 1]
    nickname_rng = random.Random(seed)
    names = list(room.config.ai_nicknames)
    nickname_rng.shuffle(names)
    for number, assigned in enumerate(roles, 1):
        player = models.Player(
            id=f"seat-{number}",
            name=names[number - 1],
            number=number,
            role=assigned,
            is_ai=number != 1,
            ai_config=models.AIPlayerConfig(name=names[number - 1]),
        )
        room.add_player(player)
        room.number_to_player[number] = player.id
    with patch("random.choices", side_effect=random.Random(seed).choices):
        for player in room.players.values():
            if player.is_ai:
                service.initialize_ai_context(player, room)
    actor = room.get_player_by_number(actor_number)
    prefix = plugin.models.__package__.rsplit(".", 1)[0]
    profile = importlib.import_module(f"{prefix}.models.personality").PERSONALITIES[
        personality
    ]
    actor.ai_context.personality = profile.to_prompt()
    actor.ai_context.personality_key = personality
    if scenario == "first_night":
        room.phase = models.GamePhase.NIGHT_SEER
        return room, service, actor
    if actor.role.value == "seer":
        actor.ai_context.add_seer_result(
            room.get_player_by_number(8).display_name, True
        )
    for observer in room.players.values():
        if not observer.ai_context:
            continue
        ctx = observer.ai_context
        if scenario in ("claims", "self_vote", "witch_memory"):
            ctx.add_speech(
                room.get_player_by_number(5).display_name,
                "我是预言家，昨晚查8号是狼人。",
            )
            ctx.add_speech(
                room.get_player_by_number(8).display_name,
                "我不信5号的查杀，为什么要验我？我没有自称预言家。",
            )
        if scenario == "peaceful":
            ctx.add_event("第1夜：平安夜")
        if scenario == "double_death":
            room.current_round = 2
            ctx.current_round = 2
            room.get_player_by_number(2).is_alive = False
            room.get_player_by_number(7).is_alive = False
            ctx.add_event(
                f"第2夜死亡：{room.get_player_by_number(2).display_name}, {room.get_player_by_number(7).display_name}"
            )
        if scenario == "self_vote":
            ctx.add_vote(actor.display_name, room.get_player_by_number(6).display_name)
            room.current_round = 2
        if scenario == "witch_memory" and observer.id == actor.id:
            ctx.witch_saved_player = room.get_player_by_number(5).display_name
            ctx.record_private_action("save", ctx.witch_saved_player)
            ctx.current_round = 2
            ctx.witch_poisoned_player = room.get_player_by_number(3).display_name
            ctx.record_private_action("poison", ctx.witch_poisoned_player)
            room.witch_state.antidote_used = True
            room.witch_state.poison_used = True
            room.current_round = 2
    if scenario == "witch_memory":
        room.get_player_by_number(3).is_alive = False
        room.get_player_by_number(9).is_alive = False
        actor.ai_context.last_killed_player = room.get_player_by_number(9).display_name
        for observer in room.players.values():
            if observer.ai_context:
                observer.ai_context.current_round = 2
                observer.ai_context.add_event(
                    f"第2夜死亡：{room.get_player_by_number(3).display_name}, {room.get_player_by_number(9).display_name}"
                )
    return room, service, actor


async def evaluate(factory, output, seeds, concurrency=4, matrix=True, mixed=True):
    """Evaluate fixed scenarios, all personalities and mixed-game round replays.

    Args:
        factory: Callable receiving a seed and returning a context or awaitable.
        output: JSONL output path; complete prompts and responses are private.
        seeds: Fixture/model seeds supplied to the context factory.
        concurrency: Maximum concurrent independent cases.
        matrix: Include day/vote/last-words samples for every personality.
        mixed: Include two-round, one-human/eight-AI public-phase replays.

    Returns:
        Sample count and observable generation failures. Semantic judgements
        require reading the saved samples; successful parsing is not proof.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    samples = 0
    failures = 0
    with ExitStack() as stack:
        plugin = stack.enter_context(load_plugin())
        stream = stack.enter_context(output.open("w", encoding="utf-8"))
        prefix = plugin.models.__package__.rsplit(".", 1)[0]
        profiles = importlib.import_module(f"{prefix}.models.personality").PERSONALITIES

        async def emit(room, service, actor, task, seed, scenario, profile):
            """Run a real action and persist every complete attempt.

            Args:
                room: Isolated room.
                service: Real AI action service.
                actor: Observed actor.
                task: Stage action to execute.
                seed: Requested evaluation seed.
                scenario: Evidence scenario label.
                profile: Personality key.

            Returns:
                Public speech and optional vote target.
            """
            nonlocal samples, failures
            start = len(actor.ai_context.decision_audit)
            target = None
            if task == "seer_check":
                room.phase = plugin.models.GamePhase.NIGHT_SEER
                target = await service.decide_seer_check(actor, room)
                speech = ""
            elif task == "day_vote":
                room.phase = plugin.models.GamePhase.DAY_VOTE
                speech, target = await service.decide_vote(actor, room)
            elif task == "last_words":
                room.phase = plugin.models.GamePhase.LAST_WORDS
                room.last_words_player_id = actor.id
                speech = await service.generate_last_words(actor, room)
            else:
                room.phase = plugin.models.GamePhase.DAY_SPEAKING
                speech = await service.generate_speech(actor, room)
            attempts = actor.ai_context.decision_audit[start:]
            failed = bool(attempts and attempts[-1]["status"] == "failed_fallback")
            failures += failed
            samples += 1
            stream.write(
                json.dumps(
                    {
                        "seed": seed,
                        "scenario": scenario,
                        "personality": profile,
                        "player": actor.display_name,
                        "role": actor.role.value,
                        "round": room.current_round,
                        "task": task,
                        "speech": speech,
                        "target_number": target,
                        "characters": len(speech),
                        "failed": failed,
                        "audit": attempts,
                        "decisions": actor.ai_context.decision_history,
                        "semantic_review": "required",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()
            print(
                f"sample={samples} failed={failures} {scenario}/{profile}/{task}",
                flush=True,
            )
            return speech, target

        async def run_case(seed, profile, scenario, role, tasks):
            """Execute a stable case under a bounded concurrency limit.

            Args:
                seed: Requested seed.
                profile: Personality key.
                scenario: Fixture identifier.
                role: Real actor role.
                tasks: Ordered actions preserving individual memory.
            """
            async with semaphore:
                context = factory(seed)
                if inspect.isawaitable(context):
                    context = await context
                room, service, actor = create_scenario(
                    plugin, context, seed, profile, scenario, role
                )
                for task in tasks:
                    if task == "last_words":
                        actor.is_alive = False
                        actor.ai_context.add_event(
                            f"投票放逐：{actor.display_name} 被放逐出局（遗言场景指定）"
                        )
                    speech, number = await emit(
                        room, service, actor, task, seed, scenario, profile
                    )
                    for observer in room.players.values():
                        if not observer.ai_context:
                            continue
                        observer.ai_context.current_round = room.current_round
                        if task == "day_speech":
                            observer.ai_context.add_speech(actor.display_name, speech)
                        elif task == "day_vote":
                            observer.ai_context.add_vote_discussion(
                                actor.display_name, speech
                            )
                            target = (
                                room.get_player_by_number(number) if number else None
                            )
                            observer.ai_context.add_vote(
                                actor.display_name,
                                target.display_name if target else "弃票",
                            )

        async def run_mixed(seed):
            """Replay two public rounds using actual generated statements and votes.

            Args:
                seed: Requested scenario/model seed.
            """
            async with semaphore:
                context = factory(seed)
                if inspect.isawaitable(context):
                    context = await context
                room, service, _ = create_scenario(plugin, context, seed)
                room.phase = plugin.models.GamePhase.DAY_SPEAKING
                rng = random.Random(seed)
                for round_number in (1, 2):
                    room.current_round = round_number
                    for actor in room.get_alive_players():
                        if actor.is_ai:
                            profile = actor.ai_context.personality_key
                            speech, _ = await emit(
                                room,
                                service,
                                actor,
                                "day_speech",
                                seed,
                                "mixed_rounds",
                                profile,
                            )
                        else:
                            speech = (
                                "我还没有确认任何人的身份，这轮先看具体的发言和票。"
                            )
                        for observer in room.players.values():
                            if observer.ai_context:
                                observer.ai_context.current_round = round_number
                                observer.ai_context.add_speech(
                                    actor.display_name, speech
                                )
                    ballots = []
                    for actor in room.get_alive_players():
                        if actor.is_ai:
                            speech, target = await emit(
                                room,
                                service,
                                actor,
                                "day_vote",
                                seed,
                                "mixed_rounds",
                                actor.ai_context.personality_key,
                            )
                            for observer in room.players.values():
                                if observer.ai_context:
                                    observer.ai_context.add_vote_discussion(
                                        actor.display_name, speech
                                    )
                        else:
                            target = rng.choice(
                                [
                                    p.number
                                    for p in room.get_alive_players()
                                    if p.id != actor.id
                                ]
                            )
                        ballots.append((actor, target))
                    for actor, number in ballots:
                        target = room.get_player_by_number(number) if number else None
                        for observer in room.players.values():
                            if observer.ai_context:
                                observer.ai_context.add_vote(
                                    actor.display_name,
                                    target.display_name if target else "弃票",
                                )
                # This is a public-phase replay, not an assertion of a full bot game.

        jobs = []
        scenarios = [
            ("claims", "villager"),
            ("peaceful", "villager"),
            ("double_death", "villager"),
            ("witch_memory", "witch"),
            ("self_vote", "villager"),
            ("first_night", "seer"),
        ]
        for seed in seeds:
            for scenario, role in scenarios:
                jobs.append(
                    run_case(
                        seed,
                        "logical",
                        scenario,
                        role,
                        ["seer_check"]
                        if scenario == "first_night"
                        else ["day_speech", "day_vote"],
                    )
                )
            if matrix:
                roles = ["villager", "werewolf", "seer", "witch", "hunter"]
                for index, profile in enumerate(profiles):
                    jobs.append(
                        run_case(
                            seed,
                            profile,
                            "claims",
                            roles[index % len(roles)],
                            ["day_speech", "day_vote", "last_words"],
                        )
                    )
            if mixed:
                jobs.append(run_mixed(seed))
        await asyncio.gather(*jobs)
    return {"samples": samples, "failures": failures, "semantic_review": "required"}


async def main():
    """Resolve an existing provider factory and run the requested evaluation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider-factory",
        required=True,
        help="Importable module:callable receiving the seed",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 43])
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--no-matrix", action="store_true")
    parser.add_argument("--no-mixed", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 8:
        parser.error("concurrency must be between 1 and 8")
    module_name, callable_name = args.provider_factory.split(":", 1)
    factory = getattr(importlib.import_module(module_name), callable_name)
    try:
        result = await evaluate(
            factory,
            args.output,
            args.seeds,
            args.concurrency,
            not args.no_matrix,
            not args.no_mixed,
        )
    finally:
        close = getattr(factory, "aclose", None)
        if close is not None:
            await close()
    print(json.dumps(result))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
