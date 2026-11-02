"""Talk to W1 in the terminal, against the real database and a Postgres checkpointer.

Usage (repo root, Docker db running, .env filled in):
    python -m services.agent.w1.cli
    python -m services.agent.w1.cli --resume <thread_id>     # pick up a paused run, even after a restart

The interrupt payload printed here is exactly what the patient app will render.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.types import Command

    from services.agent.llm import OpenRouterLLM
    from services.agent.store import PostgresStore
    from services.agent.w1.graph import compile_w1

    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", help="thread id of a paused run")
    ap.add_argument("--patient", default="PT-DEMO-0001")
    a = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (see .env.example)")

    with psycopg.connect(url, autocommit=True) as conn, PostgresSaver.from_conn_string(url) as saver:
        saver.setup()                                  # creates the checkpoint tables once
        store = PostgresStore(conn)
        store.ensure_demo_patient(a.patient)
        llm = OpenRouterLLM()
        graph = compile_w1(llm, store, saver)

        thread = a.resume or uuid.uuid4().hex
        cfg = {"configurable": {"thread_id": thread}}
        if a.resume:
            pending = graph.get_state(cfg)
            if not pending.next:
                raise SystemExit(f"thread {thread} is not paused")
            payload = pending.tasks[0].interrupts[0].value
            out = None
        else:
            text = input("Describe the symptoms: ").strip()
            age = input("Age (blank if unknown): ").strip()
            out = graph.invoke({"text": text, "age": int(age) if age else None, "patient_id": a.patient}, cfg)
            payload = (out.get("__interrupt__") or [None])[0]
            payload = payload.value if payload else None

        while payload:
            print("\n" + json.dumps(payload, indent=2, ensure_ascii=False))
            if payload["type"] == "clarify":
                answer = input(f"\n{payload['question']}\n> ")
            else:
                for i, o in enumerate(payload["options"], 1):
                    print(f"  {i}. {o['doctor_name']} · {o['clinic']} · {o['starts_at']} · {','.join(o['languages'])}")
                pick = input(f"Pick 1-{len(payload['options'])} (or q to stop and resume later with --resume {thread}): ")
                if pick.strip().lower() == "q":
                    print(f"Paused. Resume with: python -m services.agent.w1.cli --resume {thread}")
                    return
                answer = payload["options"][int(pick) - 1]["slot_id"]
            out = graph.invoke(Command(resume=answer), cfg)
            nxt = out.get("__interrupt__") or []
            payload = nxt[0].value if nxt else None

        print("\n" + (out or {}).get("reply", ""))
        print(f"\ntrace: {thread} · flags: {(out or {}).get('red_flags')} · booking: {(out or {}).get('booking_id')}")
        print(f"model calls: {llm.usage.calls} · tokens in/out: {llm.usage.prompt_tokens}/{llm.usage.completion_tokens}")


if __name__ == "__main__":
    main()
