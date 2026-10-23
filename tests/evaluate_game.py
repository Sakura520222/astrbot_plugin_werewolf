"""Run the real game phases with one scripted human and eight model players.

Requires the installed AstrBot SDK. All platform operations are replaced with
local captures. No messages, bans, cards or administrator changes reach a bot.
"""

import argparse
import asyncio
import importlib
import inspect
import json
import random
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_issue_1_regressions import load_plugin


async def evaluate_game(factory, output, seed, max_rounds=3):
    """Drive actual phase/command handlers while preserving full model evidence.

    Args:
        factory: Existing context factory receiving the requested model seed.
        output: Private JSON output file.
        seed: Fixture/model seed.
        max_rounds: Stop at the next night after this number of completed days.

    Returns:
        Completion information, action counts and observed failures.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    context = factory(seed)
    if inspect.isawaitable(context):
        context = await context
    with ExitStack() as stack:
        plugin = stack.enter_context(load_plugin())
        prefix = plugin.models.__package__.rsplit(".", 1)[0]
        handlers = importlib.import_module(f"{prefix}.handlers")
        phases = plugin.phases
        models = plugin.models
        config = models.GameConfig.default()
        config.enable_ai_review = False
        config.timeout_vote = 3600
        config.timeout_speaking = 3600
        config.timeout_wolf = 3600
        config.timeout_seer = 3600
        config.timeout_witch = 3600
        config.timeout_hunter = 3600
        manager = plugin.services.GameManager(context, config)
        # Patch actual platform boundaries, retaining the real phase logic.
        ban = plugin.services.BanService
        for name in dir(ban):
            if not name.startswith("_") and inspect.iscoroutinefunction(
                getattr(ban, name)
            ):
                stack.enter_context(patch.object(ban, name, new=AsyncMock()))
        # The phase timer treats zero as its default timeout, so use an epsilon.
        stack.enter_context(patch("random.uniform", return_value=0.001))
        messages = []
        private_messages = []

        async def capture_group(room, message):
            """Capture a platform message locally.

            Args:
                room: Owning room.
                message: Complete public message.

            Returns:
                True, as a successful isolated send.
            """
            messages.append(
                {
                    "round": room.current_round,
                    "phase": room.phase.value,
                    "message": str(message),
                }
            )
            print(
                f"seed={seed} round={room.current_round} phase={room.phase.value} messages={len(messages)}",
                flush=True,
            )
            return True

        async def capture_private(room, player_id, message):
            """Capture private notifications without sending them.

            Args:
                room: Owning room.
                player_id: Synthetic recipient.
                message: Complete private notification.

            Returns:
                True, as a successful isolated send.
            """
            private_messages.append(
                {
                    "round": room.current_round,
                    "player": player_id,
                    "message": str(message),
                }
            )
            return True

        manager.message_service.send_group_message = capture_group
        manager.message_service.send_group_at_message = AsyncMock(return_value=True)
        manager.message_service.send_private_message = capture_private
        manager.message_service.send_role_card_to_player = AsyncMock(return_value=True)
        room = manager.create_room(f"headless-{seed}", "human", "evaluation", None)
        # Keep the seating deterministic while exercising real skills and deaths.
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
        for number, role in enumerate(roles, 1):
            player = models.Player(
                id="human" if number == 1 else f"ai-{number}",
                name="真人" if number == 1 else config.ai_nicknames[number - 1],
                number=number,
                role=role,
                is_ai=number != 1,
                ai_config=models.AIPlayerConfig(name=config.ai_nicknames[number - 1]),
            )
            room.add_player(player)
            room.number_to_player[number] = player.id
        with patch("random.choices", side_effect=random.Random(seed).choices):
            for player in room.players.values():
                if player.is_ai:
                    manager.ai_player_service.initialize_ai_context(player, room)
        day = handlers.DayCommandHandler(manager)
        human = room.get_player("human")
        rng = random.Random(seed)
        tasks = []
        submitted = set()

        def event(text=""):
            """Create a synthetic command event for the scripted human.

            Args:
                text: Scripted public content or command argument.

            Returns:
                Minimal event implementing the handlers' public input methods.
            """
            return types.SimpleNamespace(
                get_group_id=lambda: room.group_id,
                get_sender_id=lambda: human.id,
                get_messages=lambda: [types.SimpleNamespace(text=text)],
                get_message_str=lambda: text,
                message_str=text,
                get_message_outline=lambda: text,
                stop_event=lambda: None,
                plain_result=lambda value: value,
                unified_msg_origin="evaluation",
                is_admin=lambda: True,
            )

        async def command(generator):
            """Consume the real command handler's reply and continuation.

            Args:
                generator: Handler's asynchronous results.
            """
            async for reply in generator:
                messages.append(
                    {
                        "round": room.current_round,
                        "phase": room.phase.value,
                        "command_reply": str(reply),
                    }
                )

        tasks.append(
            asyncio.create_task(phases.PhaseManager(manager).enter_night_phase(room))
        )
        started = asyncio.get_running_loop().time()
        stopped_at_limit = False
        try:
            while room.phase != models.GamePhase.FINISHED:
                if asyncio.get_running_loop().time() - started > 900:
                    raise TimeoutError("Headless game exceeded its finite watchdog")
                if room.current_round > max_rounds:
                    stopped_at_limit = True
                    break
                for task in tasks:
                    if task.done() and not task.cancelled():
                        task.result()
                if (
                    room.phase
                    in (models.GamePhase.DAY_SPEAKING, models.GamePhase.DAY_PK)
                    and room.speaking_state.current_speaker_id == human.id
                ):
                    key = ("speech", room.speaking_generation)
                    if key not in submitted:
                        submitted.add(key)
                        text = "我还没有确认任何人的身份。请把实际证据和自己的推测分开，这轮我按发言和票型判断。"
                        await day.capture_speech(event(text))
                        tasks.append(
                            asyncio.create_task(command(day.finish_speaking(event())))
                        )
                elif (
                    room.phase == models.GamePhase.DAY_VOTE
                    and not room.vote_state.settled
                ):
                    key = ("vote", room.vote_state.generation)
                    if key not in submitted:
                        submitted.add(key)
                        targets = [
                            p
                            for p in room.get_alive_players()
                            if p.id != human.id
                            and (
                                not room.vote_state.is_pk_vote
                                or p.id in room.vote_state.pk_players
                            )
                        ]
                        if human.is_alive and targets:
                            target = rng.choice(targets)
                            tasks.append(
                                asyncio.create_task(
                                    command(day.day_vote(event(str(target.number))))
                                )
                            )
                        tasks.append(asyncio.create_task(command(day.ai_vote(event()))))
                elif (
                    room.phase == models.GamePhase.LAST_WORDS
                    and room.last_words_player_id == human.id
                ):
                    key = ("last_words", room.last_words_generation)
                    if key not in submitted:
                        submitted.add(key)
                        await day.capture_speech(
                            event("我没有额外的身份信息，继续核对已有发言和票。")
                        )
                        tasks.append(
                            asyncio.create_task(command(day.finish_last_words(event())))
                        )
                await asyncio.sleep(0.05)
        finally:
            final_winner = plugin.services.VictoryChecker.check(room)[1]
            result = {
                "seed": seed,
                "round": room.current_round,
                "finished": room.phase == models.GamePhase.FINISHED,
                "stopped_at_round_limit": stopped_at_limit,
                "winner": final_winner,
                "messages": messages,
                "private_messages": private_messages,
                "game_log": room.game_log,
                "players": [
                    {
                        "number": p.number,
                        "name": p.name,
                        "role": p.role.value,
                        "alive": p.is_alive,
                        "personality": p.ai_context.personality_key
                        if p.ai_context
                        else None,
                        "audit": p.ai_context.decision_audit if p.ai_context else [],
                        "decisions": p.ai_context.decision_history
                        if p.ai_context
                        else [],
                    }
                    for p in room.players.values()
                ],
            }
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await manager.cleanup_room(room.group_id)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        actions = [entry for p in result["players"] for entry in p["audit"]]
        return {
            "seed": seed,
            "finished": result["finished"],
            "winner": final_winner,
            "round": room.current_round,
            "accepted": sum(
                a["status"] in ("accepted", "explicit_abstain") for a in actions
            ),
            "failed": sum(a["status"] == "failed_fallback" for a in actions),
        }


async def review_game_record(factory, game_path, output):
    """Review a completed captured game through the real reviewer.

    Args:
        factory: Existing context factory.
        game_path: Completed headless game's JSON record.
        output: Private review output and complete prompt record.

    Returns:
        Review length; the complete content remains available for semantic review.
    """
    game = json.loads(Path(game_path).read_text(encoding="utf-8"))
    if not game.get("finished") or not game.get("winner"):
        raise ValueError("Review requires a completed game with an actual winner")
    context = factory(game["seed"])
    if inspect.isawaitable(context):
        context = await context
    with load_plugin() as plugin:
        models = plugin.models
        room = models.GameRoom(
            "completed-evaluation", "human", models.GameConfig.default()
        )
        room.phase = models.GamePhase.FINISHED
        room.current_round = game["round"]
        room.game_log = list(game["game_log"])
        for entry in game["players"]:
            player = models.Player(
                id=f"seat-{entry['number']}",
                name=entry["name"],
                number=entry["number"],
                role=models.Role(entry["role"]),
                is_ai=entry["personality"] is not None,
                is_alive=entry["alive"],
            )
            if player.is_ai:
                player.ai_context = models.AIPlayerContext(
                    decision_history=entry["decisions"]
                )
            room.add_player(player)
        reviewer = plugin.services.AIReviewer(context)
        data = reviewer._format_game_data(room, game["winner"])
        system_prompt, prompt = reviewer._build_prompts(room, data, game["winner"])
        review = await reviewer.generate_review(room, game["winner"])
        Path(output).write_text(
            json.dumps(
                {
                    "seed": game["seed"],
                    "winner": game["winner"],
                    "system_prompt": system_prompt,
                    "prompt": prompt,
                    "review": review,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not review:
            raise RuntimeError("Review generation failed")
        return len(review)


async def main():
    """Run isolated full-phase simulations through an existing model factory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-factory", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 43])
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument(
        "--review-only",
        type=Path,
        help="Review a completed captured game instead of replaying it",
    )
    args = parser.parse_args()
    module, name = args.provider_factory.split(":", 1)
    factory = getattr(importlib.import_module(module), name)
    try:
        if args.review_only:
            length = await review_game_record(
                factory,
                args.review_only,
                args.output_dir / f"{args.review_only.stem}-review.json",
            )
            print(json.dumps({"review_characters": length}), flush=True)
        else:
            for seed in args.seeds:
                result = await evaluate_game(
                    factory,
                    args.output_dir / f"game-{seed}.json",
                    seed,
                    args.max_rounds,
                )
                print(json.dumps(result), flush=True)
    finally:
        close = getattr(factory, "aclose", None)
        if close:
            await close()


if __name__ == "__main__":
    asyncio.run(main())
