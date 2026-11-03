"""Build graphs once at startup; own and close their resources."""
import json
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
from services.agent.w3.graph import compile_w3
from services.agent.w3.stock import ClinicCsvStockAdapter
from services.agent.w3.store import InMemoryW3Store, PostgresW3Store
from services.records.embed import BgeM3Embedder, HashEmbedder
from services.records.retrieve import InMemoryRetriever, PostgresRetriever
from services.common.tracing import InMemoryExporter, PostgresExporter, Tracer

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
    upload_ingest: Any = None  # Explicit in-process callback for injected test deps only.
    w1_requests: Any = field(default_factory=lambda: W1Requests())
    tracer: Tracer | None = None
    w3: Any = None
    w3_store: Any = None
    w3_requests: Any = None


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


class W3Requests:
    """Persist replay-safe doctor actions using the W3 request table."""
    def __init__(self, pool=None):
        self.pool, self.rows, self.lock = pool, {}, RLock()

    def claim(self, doctor_id, key, fingerprint, draft_id):
        if self.pool:
            with self.pool.connection() as conn:
                row = conn.execute(
                    """INSERT INTO w3_request (doctor_id, idempotency_key, fingerprint, draft_id)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                       RETURNING doctor_id, idempotency_key, fingerprint, draft_id, status, response""",
                    (doctor_id, key, fingerprint, draft_id),
                ).fetchone()
                if row:
                    return dict(row), True
                existing = conn.execute(
                    """SELECT doctor_id, idempotency_key, fingerprint, draft_id, status, response
                         FROM w3_request WHERE doctor_id = %s AND idempotency_key = %s""",
                    (doctor_id, key),
                ).fetchone()
                return dict(existing), False
        with self.lock:
            identity = (doctor_id, key)
            existing = self.rows.get(identity)
            if existing is not None:
                return dict(existing), False
            row = {"doctor_id": doctor_id, "idempotency_key": key, "fingerprint": fingerprint,
                   "draft_id": str(draft_id), "status": "running", "response": None}
            self.rows[identity] = row
            return dict(row), True

    def complete(self, doctor_id, key, response):
        if self.pool:
            with self.pool.connection() as conn:
                conn.execute(
                    """UPDATE w3_request SET status = 'complete', response = %s::jsonb
                         WHERE doctor_id = %s AND idempotency_key = %s""",
                    (json.dumps(response, ensure_ascii=False), doctor_id, key),
                )
            return
        with self.lock:
            row = self.rows[(doctor_id, key)]
            row.update(status="complete", response=response)


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
        tracer = Tracer(PostgresExporter(pool))
        # Existing tool interfaces take a connection; keep it for the app lifespan.
        store_pool = stack.enter_context(ConnectionPool(
            conninfo=url, max_size=10, open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0}))
        conn = stack.enter_context(store_pool.connection())
        embedder = BgeM3Embedder()
        store = PostgresStore(conn, tracer)
        w1 = compile_w1(OpenRouterLLM(workflow="w1", tracer=tracer), store, saver, tracer)
        w2 = compile_w2(OpenRouterLLM(workflow="w2", tracer=tracer),
                        PostgresRecordsTools(conn, PostgresRetriever(conn, embedder), tracer),
                        tracer=tracer)
        w3_store = PostgresW3Store(conn)
        stock_dir = Path(os.environ.get(
            "W3_STOCK_DIR", ROOT / "data" / "synthetic" / "out" / "w3" / "stock"
        ))
        w3_stock = ClinicCsvStockAdapter(stock_dir)
        w3 = compile_w3(OpenRouterLLM(workflow="w3", tracer=tracer),
                        w3_store, w3_stock, saver, tracer)

        def audit(patient_id):
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                return cur.execute("SELECT * FROM who_saw_record WHERE patient_id = %s ORDER BY at DESC",
                                   (patient_id,)).fetchall()

        return AppDeps(w1, w2, audit, close=stack.close, store=store, embedder=embedder,
                       w1_requests=W1Requests(pool), tracer=tracer,
                       w3=w3, w3_store=w3_store, w3_requests=W3Requests(pool))
    except BaseException:
        stack.close()
        raise


def build_test_deps() -> AppDeps:
    from langgraph.checkpoint.memory import InMemorySaver

    tracer = Tracer(InMemoryExporter())
    llm = ScriptedLLM({"w1.red_flag_net": {"red_flag": False, "flag": ""},
                       "w1.match": {"specialty": "SP07", "alternative": "SP08", "confidence": 0.82,
                                    "clarifying_question": None}})
    store = InMemoryStore.demo()
    store.tracer = tracer
    tools = InMemoryRecordsTools(InMemoryRetriever([], HashEmbedder()), tracer=tracer)
    llm.tracer = tracer
    class TestStock:
        def molecule_for(self, name):
            return name.lower() if name else None

        def check(self, _clinic_id, _medicine):
            return {"medicine_id": None, "status": "in_stock", "as_of": "2026-11-02",
                    "alternatives": []}

    w3_store = InMemoryW3Store({
        "ENC-W3-TEST": {"encounter_id": "ENC-W3-TEST", "patient_id": "PT-W3-TEST",
                        "doctor_id": "DR-W3-TEST", "clinic_id": "TEST-CLINIC"},
    })
    w3_llm = ScriptedLLM({"w3.draft_soap": {
        "S": "Follow-up visit.", "O": "", "A": "", "P": "Continue metformin 500 mg daily.",
        "medicines": [{"name": "metformin", "molecule": "metformin", "dose": "500 mg",
                       "frequency": "daily", "quote": "metformin 500 mg daily"}],
    }}, tracer=tracer)
    return AppDeps(compile_w1(llm, store, InMemorySaver(), tracer),
                   compile_w2(ScriptedLLM({}, tracer=tracer), tools, tracer=tracer),
                   lambda patient: [e for e in tools.events + store.events if e["patient_id"] == patient],
                   w1_llm=llm, store=store, tracer=tracer,
                   w3=compile_w3(w3_llm, w3_store, TestStock(), InMemorySaver(), tracer),
                   w3_store=w3_store, w3_requests=W3Requests())
