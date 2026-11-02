"""Build graphs once at startup; own and close their resources."""
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Callable, Any

from services.agent.llm import OpenRouterLLM, ScriptedLLM
from services.agent.store import InMemoryStore, PostgresStore
from services.agent.w1.graph import compile_w1
from services.agent.w2.graph import compile_w2
from services.agent.w2.tools import InMemoryRecordsTools, PostgresRecordsTools
from services.records.embed import BgeM3Embedder, HashEmbedder
from services.records.retrieve import InMemoryRetriever, PostgresRetriever

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class AppDeps:
    w1: Any
    w2: Any
    audit_query: Callable[[str], list[dict]]
    close: Callable[[], None] = lambda: None
    # Serialize graph operations, including ownership check + resume. Core runs one worker.
    lock: Any = field(default_factory=RLock)
    w1_llm: Any = None
    store: Any = None
    embedder: Any = None
    upload_ingest: Any = None
    w1_requests: Any = field(default_factory=lambda: W1Requests())


class W1Requests:
    """Atomically reserve identity before starting the graph."""
    def __init__(self, pool=None):
        self.pool, self.rows, self.lock = pool, {}, RLock()

    def claim(self, user, key, thread):
        if self.pool:
            with self.pool.connection() as c:
                row = c.execute("INSERT INTO w1_request (user_id,idempotency_key,thread_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING thread_id", (user,key,thread)).fetchone()
                if row: return thread, True
                return c.execute("SELECT thread_id FROM w1_request WHERE user_id=%s AND idempotency_key=%s", (user,key)).fetchone()['thread_id'], False
        with self.lock:
            if (user,key) in self.rows: return self.rows[user,key]['thread_id'], False
            self.rows[user,key] = dict(thread_id=thread, user_id=user, running=True)
            return thread, True

    def get(self, user, thread):
        if self.pool:
            with self.pool.connection() as c:
                return c.execute("SELECT * FROM w1_request WHERE user_id=%s AND thread_id=%s", (user,thread)).fetchone()
        with self.lock:
            return next((dict(r) for r in self.rows.values() if r['user_id']==user and r['thread_id']==thread), None)

    def finish(self, user, thread):
        if self.pool:
            with self.pool.connection() as c:
                c.execute("UPDATE w1_request SET running=false WHERE user_id=%s AND thread_id=%s", (user,thread))
        else:
            with self.lock:
                for r in self.rows.values():
                    if r['user_id']==user and r['thread_id']==thread: r['running']=False


def build_postgres_deps() -> AppDeps:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver

    stack = ExitStack()
    try:
        url = os.environ["DATABASE_URL"]
        pool = stack.enter_context(ConnectionPool(
            conninfo=url, max_size=10, open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}))
        saver = PostgresSaver(pool)
        saver.setup()
        # Existing tool interfaces take a connection; keep it for the app lifespan.
        store_pool = stack.enter_context(ConnectionPool(
            conninfo=url, max_size=10, open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0}))
        conn = stack.enter_context(store_pool.connection())
        embedder = BgeM3Embedder()
        store = PostgresStore(conn)
        w1 = compile_w1(OpenRouterLLM(workflow="w1"), store, saver)
        w2 = compile_w2(OpenRouterLLM(workflow="w2"),
                        PostgresRecordsTools(conn, PostgresRetriever(conn, embedder)))

        def audit(patient_id):
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                return cur.execute("SELECT * FROM who_saw_record WHERE patient_id = %s ORDER BY at DESC",
                                   (patient_id,)).fetchall()

        def upload_ingest(manifest):
            # Separate connection lets document status reads proceed during parsing/embedding.
            from services.records.ingest import ingest_one
            with store_pool.connection() as worker_conn:
                ingest_one(worker_conn, manifest, embedder)

        return AppDeps(w1, w2, audit, close=stack.close, store=store, embedder=embedder,
                       upload_ingest=upload_ingest, w1_requests=W1Requests(pool))
    except BaseException:
        stack.close()
        raise


def build_test_deps() -> AppDeps:
    from langgraph.checkpoint.memory import InMemorySaver

    llm = ScriptedLLM({"w1.red_flag_net": {"red_flag": False, "flag": ""},
                       "w1.match": {"specialty": "SP07", "alternative": "SP08", "confidence": 0.82,
                                    "clarifying_question": None}})
    store = InMemoryStore.demo()
    tools = InMemoryRecordsTools(InMemoryRetriever([], HashEmbedder()))
    return AppDeps(compile_w1(llm, store, InMemorySaver()), compile_w2(ScriptedLLM({}), tools),
                   lambda patient: [e for e in tools.events + store.events if e["patient_id"] == patient],
                   w1_llm=llm, store=store)
