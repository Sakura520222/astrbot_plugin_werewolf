"""Run actual game phases with mixed players or a complete all-AI room.

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


async def evaluate_game(
    factory, output, seed, max_rounds=None, *, all_ai=False, watchdog_seconds=None
):
    """Drive actual phase/command handlers while preserving full model evidence.

    Args:
        factory: Existing context factory receiving the requested model seed.
        output: Private JSON output file.
        seed: Fixture/model seed.
        max_rounds: Optional day limit; zero runs until victory. Defaults to zero
            for all-AI games and three for mixed games.
        all_ai: Use nine AI players and the actual room-start command.
        watchdog_seconds: Whole-run safety budget, independent of model timeouts.

    Returns:
        Completion information, action counts and observed failures.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    max_rounds = (0 if all_ai else 3) if max_rounds is None else max_rounds
    watchdog_seconds = (
        (14400 if all_ai else 900) if watchdog_seconds is None else watchdog_seconds
    )
    if max_rounds < 0 or watchdog_seconds <= 0:
        raise ValueError(
            "Round limits cannot be negative and watchdogs must be positive"
        )
    with ExitStack() as stack:
        plugin = stack.enter_context(load_plugin())
        context = factory(seed)
        if inspect.isawaitable(context):
            context = await context
        prefix = plugin.models.__package__.rsplit(".", 1)[0]
        handlers = importlib.import_module(f"{prefix}.handlers")
        phases = plugin.phases
        models = plugin.models
        config = models.GameConfig.default()
        config.enable_ai_review = all_ai
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
        tasks = []
        driver = asyncio.current_task()
        started = asyncio.get_running_loop().time()
        journal = stack.enter_context(
            output.with_suffix(".calls.jsonl").open("w", encoding="utf-8")
        )
        model_calls = 0
        review_record = None
        phase_history = []
        failure = None
        completed_naturally = False
        stopped_at_limit = False
        victory_announced = False

        async def capture_group(room, message):
            """Capture a platform message locally.

            Args:
                room: Owning room.
                message: Complete public message.

            Returns:
                True, as a successful isolated send.
            """
            nonlocal victory_announced
            if room.phase == models.GamePhase.FINISHED and str(message).startswith(
                "🎉"
            ):
                victory_announced = True
            current = asyncio.current_task()
            if current is not driver and current not in tasks:
                tasks.append(current)
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
        room = manager.create_room(
            f"headless-{seed}", "observer" if all_ai else "human", "evaluation", None
        )
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
        with patch("random.choices", side_effect=random.Random(seed).choices):
            for number, role in enumerate(roles, 1):
                if all_ai:
                    name = config.ai_nicknames[number - 1]
                    manager.add_ai_player(room, name, models.AIPlayerConfig(name=name))
                else:
                    player = models.Player(
                        id="human" if number == 1 else f"ai-{number}",
                        name="真人" if number == 1 else config.ai_nicknames[number - 1],
                        number=number,
                        role=role,
                        is_ai=number != 1,
                        ai_config=models.AIPlayerConfig(
                            name=config.ai_nicknames[number - 1]
                        ),
                    )
                    room.add_player(player)
                    room.number_to_player[number] = player.id
            if not all_ai:
                for player in room.players.values():
                    if player.is_ai:
                        manager.ai_player_service.initialize_ai_context(player, room)
        day = handlers.DayCommandHandler(manager)
        human = room.get_player("human")
        rng = random.Random(seed)
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
                get_sender_id=lambda: human.id if human else room.creator_id,
                get_messages=lambda: [types.SimpleNamespace(text=text)],
                get_message_str=lambda: text,
                message_str=text,
                get_message_outline=lambda: text,
                stop_event=lambda: None,
                plain_result=lambda value: value,
                unified_msg_origin="evaluation",
                is_admin=lambda: True,
                is_private_chat=lambda: False,
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

        def save_record():
            """Checkpoint the full game without losing records on interruption.

            Returns:
                The same complete document written to the private output path.
            """
            final_winner = (
                plugin.services.VictoryChecker.check(room)[1]
                if victory_announced
                and all(p.role is not None for p in room.players.values())
                else None
            )
            result = {
                "seed": seed,
                "all_ai": all_ai,
                "round": room.current_round,
                "phase": room.phase.value,
                "finished": completed_naturally,
                "stopped_at_round_limit": stopped_at_limit,
                "winner": final_winner,
                "cleanup_complete": manager.get_room(room.group_id) is not room,
                "elapsed_seconds": asyncio.get_running_loop().time() - started,
                "watchdog_seconds": watchdog_seconds,
                "model_calls": model_calls,
                "error": failure,
                "messages": messages,
                "private_messages": private_messages,
                "game_log": room.game_log,
                "phase_history": phase_history,
                "review": review_record,
                "night_votes": dict(room.vote_state.night_votes),
                "day_votes": dict(room.vote_state.day_votes),
                "players": [
                    {
                        "number": p.number,
                        "name": p.name,
                        "role": p.role.value if p.role else None,
                        "alive": p.is_alive,
                        "is_ai": p.is_ai,
                        "personality": p.ai_context.personality_key
                        if p.ai_context
                        else None,
                        "audit": p.ai_context.decision_audit if p.ai_context else [],
                        "decisions": p.ai_context.decision_history
                        if p.ai_context
                        else [],
                        "speeches": p.ai_context.speeches if p.ai_context else [],
                        "votes": p.ai_context.vote_history if p.ai_context else [],
                        "private_actions": p.ai_context.private_actions
                        if p.ai_context
                        else [],
                        "seer_results": p.ai_context.seer_results
                        if p.ai_context
                        else [],
                        "private_events": p.ai_context.private_events
                        if p.ai_context
                        else [],
                        "wolf_chats": p.ai_context.wolf_chat_messages
                        if p.ai_context
                        else [],
                        "own_wolf_chats": p.ai_context.own_wolf_chat_messages
                        if p.ai_context
                        else [],
                    }
                    for p in sorted(
                        room.players.values(), key=lambda player: player.number
                    )
                ],
            }
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return result

        for action_name in (
            "_werewolf_action",
            "_seer_action",
            "_witch_action",
            "_hunter_action",
            "_speech_action",
            "_vote_action",
        ):
            action = getattr(manager.ai_player_service, action_name)
            original_call = action._call_llm

            async def trace_call(
                prompt, player, *args, _call=original_call, _kind=action_name, **kwargs
            ):
                """Journal complete model calls while keeping the real action path.

                Args:
                    prompt: Entire composed prompt.
                    player: Real AI actor in the isolated room.
                    *args: Original action arguments.
                    _call: Captured real model boundary.
                    _kind: Captured action class name.
                    **kwargs: Original retry and timeout arguments.

                Returns:
                    The real, complete model reply.
                """
                nonlocal model_calls
                current = asyncio.current_task()
                if current is not driver and current not in tasks:
                    tasks.append(current)
                model_calls += 1
                call_id = model_calls
                entry = {
                    "call": call_id,
                    "action": _kind,
                    "player": player.display_name,
                    "round": room.current_round,
                    "phase": room.phase.value,
                    "elapsed_seconds": asyncio.get_running_loop().time() - started,
                }
                journal.write(
                    json.dumps(
                        {**entry, "event": "request", "prompt": prompt},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                journal.flush()
                print(
                    f"seed={seed} call={call_id} player={player.display_name} phase={room.phase.value} request",
                    flush=True,
                )
                try:
                    response = await _call(prompt, player, *args, **kwargs)
                except BaseException as error:
                    journal.write(
                        json.dumps(
                            {
                                **entry,
                                "event": "error",
                                "error_type": type(error).__name__,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    journal.flush()
                    raise
                journal.write(
                    json.dumps(
                        {
                            **entry,
                            "event": "response",
                            "response": response,
                            "completed_seconds": asyncio.get_running_loop().time()
                            - started,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                journal.flush()
                print(
                    f"seed={seed} call={call_id} response_characters={len(response or '')}",
                    flush=True,
                )
                return response

            stack.enter_context(patch.object(action, "_call_llm", new=trace_call))

        original_review = manager.ai_reviewer.generate_review

        async def trace_review(review_room, winner):
            """Retain the end-game review and wait for its owning phase task.

            Args:
                review_room: Finished room, still owned by the real manager.
                winner: Authoritative winning faction.

            Returns:
                The entire real review response.
            """
            nonlocal review_record, model_calls
            current = asyncio.current_task()
            if current is not driver and current not in tasks:
                tasks.append(current)
            data = manager.ai_reviewer._format_game_data(review_room, winner)
            system_prompt, prompt = manager.ai_reviewer._build_prompts(
                review_room, data, winner
            )
            review_record = {
                "system_prompt": system_prompt,
                "prompt": prompt,
                "response": None,
                "audit": review_room.review_audit,
            }
            journal.write(
                json.dumps(
                    {"event": "review_request", **review_record}, ensure_ascii=False
                )
                + "\n"
            )
            journal.flush()
            audit_start = len(review_room.review_audit)
            response = await original_review(review_room, winner)
            model_calls += sum(
                "attempt" in item for item in review_room.review_audit[audit_start:]
            )
            review_record["response"] = response
            journal.write(
                json.dumps(
                    {"event": "review_response", "response": response},
                    ensure_ascii=False,
                )
                + "\n"
            )
            journal.flush()
            return response

        stack.enter_context(
            patch.object(manager.ai_reviewer, "generate_review", new=trace_review)
        )
        save_record()
        if all_ai:
            stack.enter_context(patch("random.shuffle", side_effect=rng.shuffle))
            tasks.append(
                asyncio.create_task(
                    command(handlers.RoomCommandHandler(manager).start_game(event()))
                )
            )
        else:
            tasks.append(
                asyncio.create_task(
                    phases.PhaseManager(manager).enter_night_phase(room)
                )
            )
        checkpoint_key = None
        try:
            while True:
                if asyncio.get_running_loop().time() - started > watchdog_seconds:
                    raise TimeoutError("Headless game exceeded its finite watchdog")
                if max_rounds and room.current_round > max_rounds:
                    stopped_at_limit = True
                    break
                for name in (
                    "timer_task",
                    "wolf_ai_vote_task",
                    "wolf_ai_process_task",
                    "wolf_ai_ballot_task",
                    "day_ai_task",
                ):
                    task = getattr(room, name)
                    if task and task is not driver and task not in tasks:
                        tasks.append(task)
                for task in tuple(tasks):
                    if task.done() and not task.cancelled():
                        task.result()
                progress = (
                    room.current_round,
                    room.phase.value,
                    room.vote_state.generation,
                    room.speaking_generation,
                    room.last_words_generation,
                    len(messages),
                    len(room.game_log),
                    len(room.review_audit),
                    sum(
                        len(p.ai_context.decision_audit)
                        for p in room.players.values()
                        if p.ai_context
                    ),
                )
                if progress != checkpoint_key:
                    if checkpoint_key is None or progress[:5] != checkpoint_key[:5]:
                        phase_history.append(
                            {
                                "round": room.current_round,
                                "phase": room.phase.value,
                                "alive": [p.number for p in room.get_alive_players()],
                            }
                        )
                    save_record()
                    checkpoint_key = progress
                if room.phase == models.GamePhase.FINISHED:
                    if not any(not task.done() for task in tasks):
                        if (
                            not victory_announced
                            or not all(
                                p.role is not None for p in room.players.values()
                            )
                            or not plugin.services.VictoryChecker.check(room)[1]
                            or manager.get_room(room.group_id) is room
                        ):
                            raise RuntimeError(
                                "A full game must finish with an announced victory and completed cleanup"
                            )
                        completed_naturally = True
                        break
                    await asyncio.sleep(0.05)
                    continue
                if all_ai:
                    await asyncio.sleep(0.05)
                    continue
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
        except BaseException as error:
            failure = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            result = save_record()
            await manager.cleanup_room(room.group_id)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        actions = [entry for p in result["players"] for entry in p["audit"]]
        return {
            "seed": seed,
            "all_ai": all_ai,
            "finished": result["finished"],
            "winner": result["winner"],
            "round": room.current_round,
            "accepted": sum(
                a["status"] in ("accepted", "explicit_abstain") for a in actions
            ),
            "failed": sum(a["status"] == "failed_fallback" for a in actions),
            "model_calls": result["model_calls"],
            "review_generated": bool(result["review"] and result["review"]["response"]),
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
    with load_plugin() as plugin:
        context = factory(game["seed"])
        if inspect.isawaitable(context):
            context = await context
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
                    "audit": room.review_audit,
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
    parser.add_argument(
        "--max-rounds",
        type=int,
        help="Zero runs until victory; defaults to zero for all-AI or three for mixed games",
    )
    parser.add_argument(
        "--all-ai",
        action="store_true",
        help="Use nine model players with no scripted human",
    )
    parser.add_argument(
        "--watchdog-seconds", type=float, help="Independent whole-run safety budget"
    )
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
                    all_ai=args.all_ai,
                    watchdog_seconds=args.watchdog_seconds,
                )
                print(json.dumps(result), flush=True)
    finally:
        close = getattr(factory, "aclose", None)
        if close:
            await close()


if __name__ == "__main__":
    asyncio.run(main())
