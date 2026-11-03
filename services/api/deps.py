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
    replay_requests: Any = field(default_factory=lambda: ReplayRequests())
    rate_limit_pool: Any = None
    tracer: Tracer | None = None
    w3: Any = None
    w3_store: Any = None
    w3_requests: Any = None
    stock: Any = None


class W1Requests:
    """Atomically reserve identity before starting the graph."""
    def __init__(self, pool=None):
        self.pool, self.rows, self.lock = pool, {}, RLock()

    def claim(self, user, key, thread, fingerprint):
        if self.pool:
            with self.pool.connection() as c:
                row = c.execute(
                    """INSERT INTO w1_request (user_id, idempotency_key, thread_id, fingerprint)
                       VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                       RETURNING thread_id""",
                    (user, key, thread, fingerprint),
                ).fetchone()
                if row:
                    return thread, True, True
                existing = c.execute(
                    """SELECT thread_id, fingerprint FROM w1_request
                        WHERE user_id = %s AND idempotency_key = %s""",
                    (user, key),
                ).fetchone()
                if existing["fingerprint"] is None:
                    updated = c.execute(
                        """UPDATE w1_request SET fingerprint = %s
                            WHERE user_id = %s AND idempotency_key = %s AND fingerprint IS NULL
                            RETURNING fingerprint""",
                        (fingerprint, user, key),
                    ).fetchone()
                    if updated:
                        return existing["thread_id"], False, True
                    existing = c.execute(
                        """SELECT thread_id, fingerprint FROM w1_request
                            WHERE user_id = %s AND idempotency_key = %s""",
                        (user, key),
                    ).fetchone()
                return existing["thread_id"], False, existing["fingerprint"] == fingerprint
        with self.lock:
            existing = self.rows.get((user, key))
            if existing is not None:
                return (existing["thread_id"], False,
                        existing["fingerprint"] == fingerprint)
            self.rows[user, key] = dict(thread_id=thread, user_id=user,
                                        fingerprint=fingerprint, running=True)
            return thread, True, True

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


class ReplayRequests:
    """Share W1-resume and upload replay claims across API processes."""
    def __init__(self, pool=None):
        self.pool, self.rows, self.lock = pool, {}, RLock()

    def get(self, action, owner, key):
        if self.pool:
            with self.pool.connection() as conn:
                row = conn.execute(
                    """SELECT identity, fingerprint, status, response
                         FROM api_replay_request
                        WHERE action = %s AND owner_id = %s AND idempotency_key = %s""",
                    (action, owner, key),
                ).fetchone()
                return dict(row) if row else None
        with self.lock:
            row = self.rows.get((action, owner, key))
            return dict(row) if row else None

    def claim(self, action, owner, key, identity, fingerprint):
        if self.pool:
            with self.pool.connection() as conn:
                row = conn.execute(
                    """INSERT INTO api_replay_request
                         (action, owner_id, idempotency_key, identity, fingerprint, status, lease_until)
                       VALUES (%s, %s, %s, %s, %s, 'running', now() + interval '15 minutes')
                       ON CONFLICT (action, owner_id, idempotency_key) DO UPDATE
                         SET identity = EXCLUDED.identity, fingerprint = EXCLUDED.fingerprint,
                             status = 'running', response = NULL,
                             lease_until = now() + interval '15 minutes', updated_at = now()
                       WHERE api_replay_request.status = 'running'
                         AND api_replay_request.lease_until < now()
                         AND api_replay_request.identity = EXCLUDED.identity
                         AND api_replay_request.fingerprint = EXCLUDED.fingerprint
                       RETURNING identity, fingerprint, status, response""",
                    (action, owner, key, identity, fingerprint),
                ).fetchone()
                if row:
                    return dict(row), True
                existing = conn.execute(
                    """SELECT identity, fingerprint, status, response
                         FROM api_replay_request
                        WHERE action = %s AND owner_id = %s AND idempotency_key = %s""",
                    (action, owner, key),
                ).fetchone()
                return dict(existing), False
        with self.lock:
            identity_key = (action, owner, key)
            existing = self.rows.get(identity_key)
            if existing is not None:
                return dict(existing), False
            row = {"identity": identity, "fingerprint": fingerprint,
                   "status": "running", "response": None}
            self.rows[identity_key] = row
            return dict(row), True

    def complete(self, action, owner, key, response):
        if self.pool:
            with self.pool.connection() as conn:
                updated = conn.execute(
                    """UPDATE api_replay_request
                          SET status = 'complete', response = %s::jsonb,
                              lease_until = NULL, updated_at = now()
                        WHERE action = %s AND owner_id = %s AND idempotency_key = %s
                          AND status = 'running'
                     RETURNING idempotency_key""",
                    (json.dumps(response, ensure_ascii=False), action, owner, key),
                ).fetchone()
                if updated is None:
                    raise RuntimeError("Replay request claim was lost before completion")
            return
        with self.lock:
            row = self.rows[(action, owner, key)]
            row.update(status="complete", response=response)

    def release(self, action, owner, key):
        if self.pool:
            with self.pool.connection() as conn:
                conn.execute(
                    """DELETE FROM api_replay_request
                        WHERE action = %s AND owner_id = %s AND idempotency_key = %s
                          AND status = 'running'""",
                    (action, owner, key),
                )
            return
        with self.lock:
            self.rows.pop((action, owner, key), None)


def build_postgres_deps() -> AppDeps:
    from dotenv import load_dotenv
    if os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
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
                       w1_requests=W1Requests(pool), replay_requests=ReplayRequests(pool),
                       rate_limit_pool=pool, tracer=tracer,
                       w3=w3, w3_store=w3_store, w3_requests=W3Requests(pool), stock=w3_stock)
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
    stock = TestStock()
    w3_llm = ScriptedLLM({"w3.draft_soap": {
        "S": "Follow-up visit.", "O": "", "A": "", "P": "Continue metformin 500 mg daily.",
        "medicines": [{"name": "metformin", "molecule": "metformin", "dose": "500 mg",
                       "frequency": "daily", "quote": "metformin 500 mg daily"}],
    }}, tracer=tracer)
    return AppDeps(compile_w1(llm, store, InMemorySaver(), tracer),
                   compile_w2(ScriptedLLM({}, tracer=tracer), tools, tracer=tracer),
                   lambda patient: [e for e in tools.events + store.events if e["patient_id"] == patient],
                   w1_llm=llm, store=store, tracer=tracer,
                   w3=compile_w3(w3_llm, w3_store, stock, InMemorySaver(), tracer),
                   w3_store=w3_store, w3_requests=W3Requests(), stock=stock)
