"""Ask questions about a patient's records from the terminal.

Usage (repo root, DB up, fixtures ingested, .env filled):
    python -m services.agent.w2.cli --as PT-W2-0001                       # Priya, her own records
    python -m services.agent.w2.cli --as PT-W2-0001 --for PT-W2-0002      # Priya as caregiver for her mother
In the real app, --as comes from the login (OIDC) and --for from the patient picker; never from text.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import psycopg

    from services.agent.llm import OpenRouterLLM
    from services.agent.w2.graph import compile_w2
    from services.agent.w2.tools import PostgresRecordsTools
    from services.records.embed import BgeM3Embedder
    from services.records.retrieve import PostgresRetriever

    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="actor", required=True, help="who is asking (patient id)")
    ap.add_argument("--for", dest="subject", help="whose records (default: the asker)")
    a = ap.parse_args()
    subject = a.subject or a.actor
    role = "patient" if subject == a.actor else "caregiver"

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        print("loading the embedding model...")
        graph = compile_w2(OpenRouterLLM(workflow="w2"), PostgresRecordsTools(conn, PostgresRetriever(conn, BgeM3Embedder())))
        print(f"Asking as {a.actor} ({role}) about {subject}'s records. Empty line to quit.\n")
        while True:
            q = input("> ").strip()
            if not q:
                break
            out = graph.invoke({"actor_id": a.actor, "subject_id": subject, "actor_role": role, "question": q})
            print(f"\n{out.get('reply', '')}\n[route: {out.get('route')} · verified: {out.get('verified')} · "
                  f"trace: {out.get('trace_id')}]\n")


if __name__ == "__main__":
    main()
