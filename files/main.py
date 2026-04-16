import uuid
from models import (
    Action, IntentThread, Message,
    MessageRole, MessageType, Persona
)
from orchestrator import orchestrate
from search import search


# ── Persona (hardcoded for demo — later comes from DB) ───────────────
DEMO_PERSONA = Persona(user_id="u1")   # empty — forces orchestrator to ask


# ── Display helpers ───────────────────────────────────────────────────
SEP   = "─" * 56
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
AMBER = "\033[33m"
BLUE  = "\033[34m"
RED   = "\033[31m"


def print_system(label: str, text: str, color: str = DIM):
    print(f"\n{color}[{label}]{RESET} {DIM}{text}{RESET}")


def print_question(text: str):
    print(f"\n{CYAN}{BOLD}Platform ›{RESET} {text}")


def print_results(candidates):
    print(f"\n{GREEN}{BOLD}Top matches found:{RESET}")
    for i, c in enumerate(candidates[:3], 1):
        bar   = "█" * int(c.score * 10) + "░" * (10 - int(c.score * 10))
        color = GREEN if c.score >= 0.75 else AMBER if c.score >= 0.5 else DIM
        print(f"  {BOLD}{i}. {c.name}{RESET}")
        print(f"     {c.description}")
        print(f"     {color}Match {bar} {int(c.score*100)}%{RESET}")


def print_orchestrator_state(out, signals_known: list[str]):
    print(f"\n{DIM}{SEP}")
    print(f"  Orchestrator › {out.action.value.upper()}")
    print(f"  Reasoning   : {out.reasoning}")
    if signals_known:
        print(f"  Signals     : {', '.join(signals_known)}")
    if out.enriched_intent:
        ei = out.enriched_intent
        hf = ei.hard_filters
        print(f"  Completeness: {int(ei.completeness*100)}%")
        print(f"  Query       : {ei.embedding_query[:80]}...")
    print(f"{SEP}{RESET}")


# ── Main loop ─────────────────────────────────────────────────────────
def run():
    print(f"\n{BOLD}Matchmaker — local prototype{RESET}")
    print(f"{DIM}Scenario: flatmate search. Type your request to begin.{RESET}\n")

    thread = IntentThread(
        thread_id = str(uuid.uuid4())[:8],
        user_id   = "u1",
    )
    persona       = DEMO_PERSONA
    search_result = None

    while True:
        try:
            user_input = input(f"{BOLD}You ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("Bye.")
            break

        # Determine message type
        msg_type = MessageType.ANSWER if thread.messages else MessageType.INTENT
        thread.append(Message(
            role    = MessageRole.USER,
            type    = msg_type,
            content = user_input,
        ))

        # Call orchestrator
        out = orchestrate(thread, persona, search_result)

        # Show internal state (debug layer — remove in prod)
        from orchestrator import _extract_signals
        signals = _extract_signals(thread, persona)
        print_orchestrator_state(out, list(signals.keys()))

        if out.action == Action.ASK:
            thread.append(Message(
                role    = MessageRole.SYSTEM,
                type    = MessageType.QUESTION,
                content = out.question,
            ))
            print_question(out.question)

        elif out.action == Action.SEARCH:
            print_system("search", f"Querying with: {out.enriched_intent.embedding_query[:60]}...", BLUE)
            search_result = search(out.enriched_intent)
            print_system("search", f"Got {len(search_result.candidates)} candidates. Top score: {search_result.top_score}. Distribution: {search_result.score_distribution}", BLUE)

            # Immediately call orchestrator again with results
            out2 = orchestrate(thread, persona, search_result)
            print_orchestrator_state(out2, list(signals.keys()))

            if out2.action == Action.SERVE:
                print_results(search_result.candidates)
                thread.status = "done"

            elif out2.action == Action.ASK:
                thread.append(Message(
                    role    = MessageRole.SYSTEM,
                    type    = MessageType.QUESTION,
                    content = out2.question,
                ))
                print_question(out2.question)

        elif out.action == Action.SERVE:
            if search_result:
                print_results(search_result.candidates)
            thread.status = "done"

        elif out.action == Action.RESPOND:
            print_question(out.question or "Let me know how I can help.")

        if thread.status == "done":
            print(f"\n{DIM}Session complete. Thread {thread.thread_id} closed.{RESET}\n")
            break


if __name__ == "__main__":
    run()
