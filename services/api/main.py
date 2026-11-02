"""Synchronous HTTP adapter for W1 and W2. Run one worker for the Core demo."""
import logging
import hashlib
import json
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from services.api import frontdesk as fd
from services.common.timefmt import ist
from services.api.auth import current_user
from services.api.deps import build_postgres_deps
from services.api.ratelimit import RateLimiter
from services.api.ops import build_ops_summary, build_trace_tree

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "data" / "uploads"

logger = logging.getLogger("sahyog.api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = True


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Triage(StrictRequest):
    text: str = Field(min_length=1, max_length=20000)
    age: int | None = Field(default=None, ge=0, le=130)


class Resume(StrictRequest):
    value: str = Field(min_length=1, max_length=20000)


class Ask(StrictRequest):
    question: str = Field(min_length=1, max_length=20000)
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)


def w1_response(thread, out):
    interrupts = out.get("__interrupt__") or []
    return dict(thread_id=thread, status="needs_input" if interrupts else "done",
                interrupt=interrupts[0].value if interrupts else None, reply=out.get("reply", ""),
                red_flags=out.get("red_flags", []), booking_id=out.get("booking_id"),
                specialty=out.get("specialty"), route=out.get("route"))


def create_app(deps_factory=build_postgres_deps):
    @asynccontextmanager
    async def lifespan(app):
        app.state.deps = deps_factory()
        try:
            yield
        finally:
            app.state.deps.close()

    app = FastAPI(lifespan=lifespan)
    limiter = RateLimiter()
    replays = {}
    upload_replays = {}

    def write_key(request):
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 128:
            raise HTTPException(400, "Idempotency-Key must be 8-128 characters")
        return key

    def limit(request, user, kind):
        retry = limiter.check(user, request.client.host if request.client else "unknown", kind)
        if retry:
            deps = request.app.state.deps
            with deps.lock:
                deps.store.audit("rate_limited", "api", request.state.trace_id, user,
                                 kind, request.state.trace_id, {"bucket": kind})
            raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(retry)})

    def trace_span(deps, kind, name, workflow, trace_id):
        tracer = getattr(deps, "tracer", None)
        return (tracer.span(kind, name, workflow=workflow, trace_id=trace_id)
                if tracer else nullcontext())

    @app.middleware("http")
    async def request_log(request, call_next):
        request.state.trace_id = uuid4().hex
        started = time.perf_counter()
        status = 500
        deps = getattr(request.app.state, "deps", None)
        tracer = getattr(deps, "tracer", None)
        try:
            with trace_span(deps, "http", f"{request.method} {request.url.path}", "",
                            request.state.trace_id) as http_span:
                try:
                    response = await call_next(request)
                except Exception:
                    response = JSONResponse({"detail": "Internal server error"}, status_code=500)
                status = response.status_code
                if status >= 500:
                    http_span.set_status("error") if tracer else None
                elif getattr(request.state, "degraded", False):
                    http_span.set_status("degraded") if tracer else None
                if request.url.path.startswith(("/w1/", "/w2/")):
                    response.headers["X-Trace-Id"] = request.state.trace_id
                    if getattr(request.state, "thread_id", None):
                        response.headers["X-Thread-Id"] = request.state.thread_id
                    if getattr(request.state, "degraded", False):
                        response.headers["X-Degraded"] = "true"
                return response
        finally:
            route = request.scope.get("route")
            user = getattr(request.state, "user", None)
            logger.info(json.dumps(dict(
                ts=datetime.now(timezone.utc).isoformat(), method=request.method,
                path=getattr(route, "path", "unmatched"), status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                user=hashlib.sha256(user.encode()).hexdigest()[:12] if user else None,
                trace_id=request.state.trace_id,
                thread_id=getattr(request.state, "thread_id", None),
                route=getattr(request.state, "model_route", None),
                degraded=getattr(request.state, "degraded", False))))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ops")
    def ops_page(request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "ops":
            raise HTTPException(403, "Role not permitted")
        return FileResponse(Path(__file__).parent / "static" / "ops.html")

    @app.get("/ops/summary")
    def ops_summary(request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "ops":
            raise HTTPException(403, "Role not permitted")
        deps = request.app.state.deps
        with deps.lock:
            return build_ops_summary(deps)

    @app.get("/ops/traces/{trace_id}")
    def ops_trace(trace_id: str, request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "ops":
            raise HTTPException(403, "Role not permitted")
        deps = request.app.state.deps
        tree = build_trace_tree(deps, trace_id)
        if tree is None:
            raise HTTPException(404, "Trace not found")
        return tree

    def current_w1(deps, user, thread, request=None):
        row = deps.w1_requests.get(user, thread)
        if not row:
            raise HTTPException(404, "Thread not found")
        if row["running"]:
            return JSONResponse(dict(thread_id=thread, status="running"), status_code=202)
        with deps.lock:
            state = deps.w1.get_state({"configurable": {"thread_id": thread}})
            out = dict(state.values)
            if request is not None:
                request.state.degraded = bool(out.get("degraded", False))
            out["__interrupt__"] = [i for task in state.tasks for i in task.interrupts]
            return w1_response(thread, out)

    @app.get("/w1/{thread_id}")
    def w1_status(thread_id: str, request: Request, user: str = Depends(current_user)):
        request.state.thread_id = thread_id
        if request.app.state.deps.tracer:
            request.app.state.deps.tracer.set_trace_id(thread_id)
        return current_w1(request.app.state.deps, user, thread_id, request)

    @app.post("/w1/triage")
    def triage(body: Triage, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        limit(request, user, "read")
        deps = request.app.state.deps
        thread, claimed = deps.w1_requests.claim(user, key, uuid4().hex)
        request.state.trace_id = thread
        request.state.thread_id = thread
        if deps.tracer:
            deps.tracer.set_trace_id(thread)
        if not claimed:
            return current_w1(deps, user, thread, request)
        with trace_span(deps, "node", "triage_turn", "w1", thread) as span:
            with deps.lock:
                out = deps.w1.invoke(dict(text=body.text, age=body.age, patient_id=user,
                                          trace_id=thread, idempotency_key=key),
                                     {"configurable": {"thread_id": thread}})
                deps.w1_requests.finish(user, thread)
            if out.get("degraded"):
                span.set_status("degraded") if deps.tracer else None
        request.state.model_route = out.get("route")
        request.state.degraded = bool(out.get("degraded", False))
        return w1_response(thread, out)

    @app.post("/w1/{thread_id}/resume")
    def resume(thread_id: str, body: Resume, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        cfg = {"configurable": {"thread_id": thread_id}}
        with deps.lock:
            state = deps.w1.get_state(cfg)
            if not state.values or state.values.get("patient_id") != user:
                raise HTTPException(404, "Thread not found")
            request.state.degraded = bool(state.values.get("degraded", False))
            request.state.trace_id = thread_id
            request.state.thread_id = thread_id
            if deps.tracer:
                deps.tracer.set_trace_id(thread_id)
            if (user, key) in replays:
                result = replays[user, key]
                if result["thread_id"] != thread_id:
                    raise HTTPException(409, "Idempotency key belongs to another action")
                return result
            if state.values.get("booking_id") and state.values.get("idempotency_key") == key:
                return w1_response(thread_id, state.values)
            if not any(t.interrupts for t in state.tasks):
                raise HTTPException(409, "Thread is not paused")
            booking = any(i.value.get("type") == "pick_slot"
                          for task in state.tasks for i in task.interrupts)
            limit(request, user, "book" if booking else "read")
            if booking and body.value not in {c["slot_id"] for c in state.values.get("candidates", [])}:
                raise HTTPException(400, "Choose one of the offered slots")
            with trace_span(deps, "node", "triage_turn", "w1", thread_id) as span:
                out = deps.w1.invoke(Command(resume=body.value, update={"idempotency_key": key}), cfg)
                if out.get("degraded"):
                    span.set_status("degraded") if deps.tracer else None
            replays[user, key] = w1_response(thread_id, out)
            request.state.model_route = out.get("route")
            request.state.degraded = bool(out.get("degraded", False))
        return w1_response(thread_id, out)

    @app.post("/w2/ask")
    def ask(body: Ask, request: Request, user: str = Depends(current_user)):
        limit(request, user, "read")
        subject = body.subject_id or user
        deps = request.app.state.deps
        with trace_span(deps, "node", "record_answer", "w2", request.state.trace_id) as span:
            with deps.lock:
                out = deps.w2.invoke(dict(actor_id=user, subject_id=subject,
                                         actor_role="patient" if subject == user else "caregiver",
                                         question=body.question, trace_id=request.state.trace_id))
            if out.get("degraded"):
                span.set_status("degraded") if deps.tracer else None
        request.state.trace_id = out.get("trace_id") or request.state.trace_id
        request.state.model_route = out.get("route")
        request.state.degraded = bool(out.get("degraded", False))
        return {key: out.get(key, False if key == "verified" else "")
                for key in ("route", "reply", "verified", "trace_id")}

    @app.get("/records/access-log")
    def access_log(request: Request, user: str = Depends(current_user)):
        limit(request, user, "read")
        deps = request.app.state.deps
        with deps.lock:
            return [dict(row, **({"at_display": ist(row["at"])} if row.get("at") else {})) for row in deps.audit_query(user)]

    @app.get("/frontdesk")
    def frontdesk_page(request: Request, user: str = Depends(current_user)):
        deps = request.app.state.deps
        with deps.lock:
            fd.audit(deps, user, "read", "frontdesk", "page")
        return FileResponse(Path(__file__).parent / "static" / "frontdesk.html")

    @app.get("/frontdesk/escalations")
    def escalation_list(request: Request, status: str = "open", user: str = Depends(current_user)):
        if status not in ("open", "handled"): raise HTTPException(400, "Invalid status")
        deps = request.app.state.deps
        with deps.lock:
            result = fd.escalations(deps, status)
            fd.audit(deps, user, "read", "escalation", "queue")
            for row in result:
                if row.get("at"): row["at_display"] = ist(row["at"])
            return result

    @app.post("/frontdesk/escalations/{eid}/handled")
    def mark_handled(eid: int, request: Request, user: str = Depends(current_user)):
        write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.handled(deps, eid, user)

    @app.get("/frontdesk/bookings")
    def booking_list(request: Request, booked_by: str = "assistant", user: str = Depends(current_user)):
        if booked_by not in ("assistant", "front_desk", "patient"): raise HTTPException(400, "Invalid booked_by")
        deps = request.app.state.deps
        with deps.lock:
            result = fd.bookings(deps, booked_by)
            fd.audit(deps, user, "read", "appointment", "bookings")
            return result

    class Move(StrictRequest):
        slot_id: str = Field(min_length=1, max_length=200)

    @app.post("/frontdesk/bookings/{aid}/cancel")
    def cancel(aid: str, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.override(deps, user, aid, None, key)

    @app.post("/frontdesk/bookings/{aid}/move")
    def move(aid: str, body: Move, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.override(deps, user, aid, body.slot_id, key)

    def ingest_upload(deps, manifest):
        """Core uses an in-process background task. Pro uses a queue with a job id."""
        from services.records.ingest import ingest_one
        if deps.upload_ingest is not None:
            deps.upload_ingest(manifest)
        else:
            with deps.lock:
                ingest_one(deps.store.conn, manifest, deps.embedder)

    @app.post("/records/upload", status_code=202)
    async def upload(request: Request, background: BackgroundTasks, file: UploadFile = File(...), user: str = Depends(current_user)):
        key = write_key(request)
        data = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 10 * 1024 * 1024: raise HTTPException(413, "PDF exceeds 10 MB")
        if file.content_type != "application/pdf" or not data.startswith(b"%PDF-"):
            raise HTTPException(415, "A PDF report is required")
        deps = request.app.state.deps
        if not hasattr(deps.store, "conn") or deps.embedder is None:
            raise HTTPException(503, "Document ingestion unavailable")
        fingerprint = hashlib.sha256(data).hexdigest()
        previous = upload_replays.get((user, key))
        if previous:
            if previous[0] != fingerprint: raise HTTPException(409, "Idempotency key belongs to another upload")
            return previous[1]
        from services.records.ingest import register
        doc = uuid4().hex
        root = UPLOAD_ROOT
        path = root / user / (doc + ".pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest = dict(doc_id=doc, patient_id=user, type="other", file=path.name,
                        path=str(path), uri=path.as_uri(), title="Uploaded report", date=None)
        try:
            with deps.lock: register(deps.store.conn, manifest)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        background.add_task(ingest_upload, deps, manifest)
        result = dict(doc_id=doc, status="queued")
        upload_replays[user, key] = (fingerprint, result)
        return result

    @app.get("/records/documents")
    def documents(request: Request, user: str = Depends(current_user)):
        deps = request.app.state.deps
        with deps.lock:
            if not hasattr(deps.store, "conn"): return []
            result = fd.rows(deps.store.conn, "SELECT doc_id,title,ingest_status,error,uploaded_at FROM document WHERE patient_id=%s ORDER BY uploaded_at DESC", (user,))
            deps.store.audit("read", "document", "list", user, "documents", request.state.trace_id)
            return [dict(r, uploaded_at_display=ist(r["uploaded_at"])) for r in result]

    @app.get("/")
    def index(request: Request):
        view = request.query_params.get("view")
        page = "frontdesk.html" if view == "frontdesk" else "ops.html" if view == "ops" else "index.html"
        return FileResponse(Path(__file__).parent / "static" / page)

    return app


app = create_app()
