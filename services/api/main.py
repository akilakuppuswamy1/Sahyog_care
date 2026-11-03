"""Synchronous HTTP adapter for W1 and W2. Run one worker for the Core demo."""
import logging
import hashlib
import json
import os
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from services.api import frontdesk as fd
from services.common.timefmt import ist
from services.api.auth import current_user
from services.api.deps import build_postgres_deps
from services.api.ratelimit import RateLimiter
from services.api.ops import build_ops_summary, build_trace_tree
from services.agent.w3.stock import NotConfigured
from services.common.deid import warm_analyzer
from services.mcp.server import create_mcp_http_app

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


class CaregiverConsent(StrictRequest):
    caregiver_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    scope: list[Literal["records", "appointments", "signed_notes"]] = Field(min_length=1, max_length=3)
    purpose: str = Field(min_length=1, max_length=256)
    expires_at: datetime


class ScribeDraft(StrictRequest):
    encounter_id: str = Field(min_length=1, max_length=128)
    transcript: str = Field(min_length=1, max_length=20000)


class ScribeReview(StrictRequest):
    action: Literal["approve", "edit", "reject"]
    edits: list[dict] = Field(default_factory=list)


def w1_response(thread, out):
    interrupts = out.get("__interrupt__") or []
    return dict(thread_id=thread, status="needs_input" if interrupts else "done",
                interrupt=interrupts[0].value if interrupts else None, reply=out.get("reply", ""),
                red_flags=out.get("red_flags", []), booking_id=out.get("booking_id"),
                specialty=out.get("specialty"), route=out.get("route"))


def create_app(deps_factory=build_postgres_deps):
    transport_app = {}

    @asynccontextmanager
    async def lifespan(app):
        warm_analyzer()
        app.state.deps = deps_factory()
        mcp_app = create_mcp_http_app(lambda: app.state.deps, limiter)
        transport_app["mcp"] = mcp_app
        try:
            async with mcp_app.app.router.lifespan_context(mcp_app.app):
                yield
        finally:
            transport_app.pop("mcp", None)
            app.state.deps.close()

    app = FastAPI(lifespan=lifespan)
    limiter = RateLimiter()
    app.state.rate_limiter = limiter

    async def dispatch_mcp(scope, receive, send):
        mcp_app = transport_app.get("mcp")
        if mcp_app is None:
            await JSONResponse({"detail": "MCP server is not running"}, status_code=503)(
                scope, receive, send
            )
            return
        await mcp_app(scope, receive, send)

    app.mount("/mcp", dispatch_mcp)

    def write_key(request):
        key = request.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 128:
            raise HTTPException(400, "Idempotency-Key must be 8-128 characters")
        return key

    def limit(request, user, kind):
        deps = request.app.state.deps
        retry = limiter.check(
            user, request.client.host if request.client else "unknown", kind,
            pool=getattr(deps, "rate_limit_pool", None),
        )
        if retry:
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
        trace_path = request.url.path
        if trace_path.startswith("/records/documents/"):
            trace_path = "/records/documents/{doc_id}"
        elif trace_path.startswith("/records/consents/"):
            trace_path = "/records/consents/{caregiver_id}"
        try:
            with trace_span(deps, "http", f"{request.method} {trace_path}", "",
                            request.state.trace_id) as http_span:
                try:
                    response = await call_next(request)
                except Exception as exc:
                    diagnostic = getattr(exc, "diag", None)
                    logger.error(
                        "Request failed: trace_id=%s path=%s error_type=%s db_column=%s db_table=%s db_error=%s",
                        request.state.trace_id, request.url.path, type(exc).__name__,
                        getattr(diagnostic, "column_name", None),
                        getattr(diagnostic, "table_name", None),
                        getattr(diagnostic, "message_primary", None),
                    )
                    response = JSONResponse({"detail": "Internal server error"}, status_code=500)
                status = response.status_code
                replica_id = os.environ.get("SCALE_REPLICA_ID")
                if replica_id:
                    response.headers["X-Replica-ID"] = replica_id
                if status >= 500:
                    http_span.set_status("error") if tracer else None
                elif getattr(request.state, "degraded", False):
                    http_span.set_status("degraded") if tracer else None
                if request.url.path.startswith(("/w1/", "/w2/", "/scribe/api/")):
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

    def static_html(filename):
        return FileResponse(
            Path(__file__).parent / "static" / filename,
            media_type="text/html; charset=utf-8",
        )

    @app.get("/static/sahyog.css")
    def shared_css():
        return FileResponse(Path(__file__).parent / "static" / "sahyog.css", media_type="text/css; charset=utf-8")

    @app.get("/ops")
    def ops_page():
        return static_html("ops.html")

    @app.get("/scribe")
    def scribe_page():
        return static_html("scribe.html")

    def scribe_idempotency(request, user, payload, draft_id):
        key = write_key(request)
        fingerprint = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        deps = request.app.state.deps
        record, claimed = deps.w3_requests.claim(user, key, fingerprint, draft_id)
        if record["fingerprint"] != fingerprint:
            raise HTTPException(409, "Idempotency key belongs to another action")
        if not claimed:
            if record["status"] == "complete":
                return key, record, record["response"]
            raise HTTPException(409, "An action with this Idempotency-Key is still in progress")
        return key, record, None

    def scribe_draft(deps, draft_id, doctor_id):
        draft = deps.w3_store.get_draft(draft_id, doctor_id)
        if draft is None:
            raise HTTPException(404, "Draft not found")
        return draft

    @app.get("/scribe/api/fixtures")
    def scribe_fixtures(request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "doctor":
            raise HTTPException(403, "Role not permitted")
        fixtures = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "w3" / "dictations.jsonl"
        return [
            {"id": case["id"], "transcript": case["transcript"]}
            for case in (json.loads(line) for line in fixtures.read_text(encoding="utf-8").splitlines())
            if case.get("transcript")
        ]

    @app.post("/scribe/api/drafts")
    def create_scribe_draft(body: ScribeDraft, request: Request,
                            user: str = Depends(current_user)):
        if not user or request.state.role != "doctor":
            raise HTTPException(403, "Role not permitted")
        limit(request, user, "read")
        deps = request.app.state.deps
        draft_id = str(uuid4())
        payload = {"operation": "create", "encounter_id": body.encounter_id,
                   "transcript": body.transcript}
        key, record, replay = scribe_idempotency(request, user, payload, draft_id)
        if replay is not None:
            return replay
        draft_id = str(record["draft_id"])
        request.state.thread_id = draft_id
        try:
            with deps.lock, trace_span(deps, "node", "scribe_turn", "w3", request.state.trace_id):
                out = deps.w3.invoke(
                    {"draft_id": draft_id, "encounter_id": body.encounter_id,
                     "transcript": body.transcript, "doctor_id": user,
                     "trace_id": request.state.trace_id},
                    {"configurable": {"thread_id": draft_id}},
                )
        except LookupError as exc:
            raise HTTPException(404, "Encounter not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, "Only the treating doctor may access this encounter") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except NotConfigured as exc:
            raise HTTPException(503, str(exc)) from exc
        draft = scribe_draft(deps, draft_id, user)
        result = jsonable_encoder({"draft": draft, "flags": draft.get("flags", [])})
        deps.w3_requests.complete(user, key, result)
        return result

    @app.get("/scribe/api/drafts")
    def list_scribe_drafts(request: Request, status: Literal["draft", "parked"] = "draft",
                           user: str = Depends(current_user)):
        if not user or request.state.role != "doctor":
            raise HTTPException(403, "Role not permitted")
        deps = request.app.state.deps
        with deps.lock:
            return deps.w3_store.list_drafts(user, status)

    @app.get("/scribe/api/drafts/{draft_id}")
    def get_scribe_draft(draft_id: UUID, request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "doctor":
            raise HTTPException(403, "Role not permitted")
        deps = request.app.state.deps
        draft_id = str(draft_id)
        with deps.lock:
            return scribe_draft(deps, draft_id, user)

    @app.post("/scribe/api/drafts/{draft_id}/review")
    def review_scribe_draft(draft_id: UUID, body: ScribeReview, request: Request,
                            user: str = Depends(current_user)):
        if not user or request.state.role != "doctor":
            raise HTTPException(403, "Role not permitted")
        key = write_key(request)
        limit(request, user, "read")
        deps = request.app.state.deps
        draft_id = str(draft_id)
        payload = {"operation": "review", "draft_id": draft_id,
                   "action": body.action, "edits": body.edits}
        record, claimed = deps.w3_requests.claim(
            user, key,
            hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest(),
            draft_id,
        )
        if record["fingerprint"] != hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest():
            raise HTTPException(409, "Idempotency key belongs to another action")
        if not claimed:
            if record["status"] == "complete":
                return record["response"]
            raise HTTPException(409, "An action with this Idempotency-Key is still in progress")
        with deps.lock:
            draft = scribe_draft(deps, draft_id, user)
            if draft["status"] != "draft":
                raise HTTPException(409, "Only an active draft can be reviewed")
            try:
                out = deps.w3.invoke(
                    Command(resume={"action": body.action, "edits": body.edits}),
                    {"configurable": {"thread_id": draft_id}},
                )
            except LookupError as exc:
                raise HTTPException(404, "Draft not found") from exc
            except PermissionError as exc:
                raise HTTPException(403, "Only the treating doctor may review this draft") from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except NotConfigured as exc:
                raise HTTPException(503, str(exc)) from exc
            updated = scribe_draft(deps, draft_id, user)
            if out.get("note"):
                note = updated.get("signed_note") or out["note"]
                result = jsonable_encoder({
                    "status": "signed", "draft_id": draft_id,
                    "note_id": note["note_id"], "version": note["version"],
                    "content_hash": note["content_hash"], "signed_note": note,
                    "edit_history": updated.get("edit_history", []),
                })
            elif updated["status"] == "rejected":
                result = {"status": "rejected", "draft_id": draft_id}
            else:
                result = jsonable_encoder({"draft": updated, "flags": updated.get("flags", [])})
        deps.w3_requests.complete(user, key, result)
        return result

    @app.get("/ops/api/summary")
    def ops_summary(request: Request, user: str = Depends(current_user)):
        if not user or request.state.role != "ops":
            raise HTTPException(403, "Role not permitted")
        deps = request.app.state.deps
        with deps.lock:
            return build_ops_summary(deps)

    @app.get("/ops/api/traces/{trace_id}")
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
        fingerprint = hashlib.sha256(json.dumps(
            {"text": body.text, "age": body.age}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        thread, claimed, matches = deps.w1_requests.claim(
            user, key, uuid4().hex, fingerprint,
        )
        if not matches:
            raise HTTPException(409, "Idempotency key belongs to another action")
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
            fingerprint = hashlib.sha256(json.dumps(
                {"thread_id": thread_id, "value": body.value},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            replay = deps.replay_requests.get("w1_resume", user, key)
            if replay is not None:
                if replay["identity"] != thread_id or replay["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Idempotency key belongs to another action")
                if replay["status"] == "complete":
                    return replay["response"]
                if state.values.get("booking_id") and state.values.get("idempotency_key") == key:
                    result = w1_response(thread_id, state.values)
                    deps.replay_requests.complete("w1_resume", user, key, result)
                    return result
            if state.values.get("booking_id") and state.values.get("idempotency_key") == key:
                result = w1_response(thread_id, state.values)
                _, claimed = deps.replay_requests.claim(
                    "w1_resume", user, key, thread_id, fingerprint,
                )
                if claimed:
                    deps.replay_requests.complete("w1_resume", user, key, result)
                else:
                    replay = deps.replay_requests.get("w1_resume", user, key)
                    if replay["status"] == "running":
                        deps.replay_requests.complete("w1_resume", user, key, result)
                    else:
                        result = replay["response"]
                return result
            if not any(t.interrupts for t in state.tasks):
                raise HTTPException(409, "Thread is not paused")
            booking = any(i.value.get("type") == "pick_slot"
                          for task in state.tasks for i in task.interrupts)
            limit(request, user, "book" if booking else "read")
            if booking and body.value not in {c["slot_id"] for c in state.values.get("candidates", [])}:
                raise HTTPException(400, "Choose one of the offered slots")
            replay, claimed = deps.replay_requests.claim(
                "w1_resume", user, key, thread_id, fingerprint,
            )
            if replay["identity"] != thread_id or replay["fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key belongs to another action")
            if not claimed:
                if replay["status"] == "complete":
                    return replay["response"]
                raise HTTPException(409, "An action with this Idempotency-Key is still in progress")
            try:
                with trace_span(deps, "node", "triage_turn", "w1", thread_id) as span:
                    out = deps.w1.invoke(Command(resume=body.value, update={"idempotency_key": key}), cfg)
                    if out.get("degraded"):
                        span.set_status("degraded") if deps.tracer else None
                result = w1_response(thread_id, out)
                deps.replay_requests.complete("w1_resume", user, key, result)
            except Exception:
                deps.replay_requests.release("w1_resume", user, key)
                raise
            request.state.model_route = out.get("route")
            request.state.degraded = bool(out.get("degraded", False))
        return result

    @app.post("/w2/ask")
    def ask(body: Ask, request: Request, user: str = Depends(current_user)):
        limit(request, user, "read")
        subject = request.state.patient_id
        deps = request.app.state.deps
        with trace_span(deps, "node", "record_answer", "w2", request.state.trace_id) as span:
            with deps.lock:
                out = deps.w2.invoke(dict(actor_id=user, subject_id=subject,
                                         actor_role=request.state.role,
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
    def frontdesk_page():
        return static_html("frontdesk.html")

    @app.get("/frontdesk/api/escalations")
    def escalation_list(request: Request, status: str = "open", user: str = Depends(current_user)):
        if status not in ("open", "handled"): raise HTTPException(400, "Invalid status")
        deps = request.app.state.deps
        with deps.lock:
            result = fd.escalations(deps, status)
            fd.audit(deps, user, "read", "escalation", "queue")
            for row in result:
                if row.get("at"): row["at_display"] = ist(row["at"])
            return result

    @app.post("/frontdesk/api/escalations/{eid}/handled")
    def mark_handled(eid: int, request: Request, user: str = Depends(current_user)):
        write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.handled(deps, eid, user)

    @app.get("/frontdesk/api/bookings")
    def booking_list(request: Request, booked_by: str = "assistant", user: str = Depends(current_user)):
        if booked_by not in ("assistant", "front_desk", "patient"): raise HTTPException(400, "Invalid booked_by")
        deps = request.app.state.deps
        with deps.lock:
            result = fd.bookings(deps, booked_by)
            fd.audit(deps, user, "read", "appointment", "bookings")
            return result

    class Move(StrictRequest):
        slot_id: str = Field(min_length=1, max_length=200)

    @app.post("/frontdesk/api/bookings/{aid}/cancel")
    def cancel(aid: str, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.override(deps, user, aid, None, key)

    @app.post("/frontdesk/api/bookings/{aid}/move")
    def move(aid: str, body: Move, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        with deps.lock: return fd.override(deps, user, aid, body.slot_id, key)

    @app.post("/records/upload", status_code=202)
    async def upload(request: Request, file: UploadFile = File(...), user: str = Depends(current_user)):
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
        previous = deps.replay_requests.get("upload", user, key)
        if previous:
            if previous["identity"] != "pdf" or previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key belongs to another upload")
            if previous["status"] == "complete":
                return previous["response"]
        previous, claimed = deps.replay_requests.claim(
            "upload", user, key, "pdf", fingerprint,
        )
        if previous["identity"] != "pdf" or previous["fingerprint"] != fingerprint:
            raise HTTPException(409, "Idempotency key belongs to another upload")
        if not claimed:
            if previous["status"] == "complete":
                return previous["response"]
            raise HTTPException(409, "An upload with this Idempotency-Key is still in progress")
        try:
            from services.records.ingest import register
            doc = uuid4().hex
            root = UPLOAD_ROOT
            path = root / user / (doc + ".pdf")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest = dict(doc_id=doc, patient_id=user, type="other", file=path.name,
                            path=str(path), uri=path.as_uri(), title="Uploaded report", date=None)
            with deps.lock, deps.store.conn.transaction():
                # Serialize identical content across API instances, scoped to its owner.
                deps.store.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))", (user + fingerprint,))
                existing = deps.store.conn.execute(
                    "SELECT j.job_id,j.doc_id,j.status FROM ingest_job j JOIN document d USING(doc_id) WHERE d.patient_id=%s AND d.content_hash=%s",
                    (user, fingerprint)).fetchone()
                if existing:
                    path.unlink(missing_ok=True)
                    result = dict(job_id=str(existing[0]), doc_id=existing[1], status=existing[2])
                    deps.replay_requests.complete("upload", user, key, result)
                    return result
                register(deps.store.conn, manifest)
                deps.store.conn.execute(
                    "UPDATE document SET content_hash=%s, source='patient_upload' WHERE doc_id=%s",
                    (fingerprint, doc),
                )
                job_id = uuid4()
                deps.store.conn.execute("INSERT INTO ingest_job(job_id,doc_id,patient_id) VALUES (%s,%s,%s)", (job_id,doc,user))
            # Only explicitly injected test dependencies may execute in-process.
            if deps.upload_ingest is not None:
                deps.upload_ingest(manifest)
            result = dict(job_id=str(job_id), doc_id=doc, status="queued")
            deps.replay_requests.complete("upload", user, key, result)
            return result
        except Exception:
            if "path" in locals():
                path.unlink(missing_ok=True)
            deps.replay_requests.release("upload", user, key)
            raise

    @app.get("/records/api/jobs/{job_id}")
    def job_status(job_id: UUID, request: Request, user: str = Depends(current_user)):
        deps = request.app.state.deps
        with deps.lock:
            if not hasattr(deps.store, "conn"):
                raise HTTPException(404, "Job not found")
            rows = fd.rows(deps.store.conn, "SELECT job_id,doc_id,status,attempts,last_error,created_at,started_at,finished_at FROM ingest_job WHERE job_id=%s AND patient_id=%s", (job_id,user))
            if not rows:
                raise HTTPException(404, "Job not found")
            return rows[0]

    @app.get("/records/documents")
    def documents(request: Request, user: str = Depends(current_user)):
        deps = request.app.state.deps
        with deps.lock:
            if not hasattr(deps.store, "conn"): return []
            result = fd.rows(deps.store.conn, "SELECT doc_id,title,ingest_status,error,uploaded_at FROM document WHERE patient_id=%s ORDER BY uploaded_at DESC", (user,))
            deps.store.audit("read", "document", "list", user, "documents", request.state.trace_id)
            return [dict(r, uploaded_at_display=ist(r["uploaded_at"])) for r in result]

    @app.delete("/records/documents/{doc_id}", status_code=204)
    def erase_document(doc_id: str, request: Request, user: str = Depends(current_user)):
        from services.records.erasure import ErasureError, erase_upload

        deps = request.app.state.deps
        if not hasattr(deps.store, "conn"):
            raise HTTPException(503, "Document erasure unavailable")
        try:
            with deps.lock, trace_span(
                deps, "tool", "erase_upload", "records", request.state.trace_id
            ):
                erase_upload(
                    deps.store.conn,
                    patient_id=user,
                    doc_id=doc_id,
                    trace_id=request.state.trace_id,
                    upload_root=UPLOAD_ROOT,
                )
        except ErasureError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        return Response(status_code=204)

    @app.post("/records/consents", status_code=201)
    def grant_consent(body: CaregiverConsent, request: Request, user: str = Depends(current_user)):
        from services.records.erasure import grant_caregiver_consent

        if body.expires_at.tzinfo is None or body.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(422, "expires_at must be a future timezone-aware timestamp")
        if len(set(body.scope)) != len(body.scope):
            raise HTTPException(422, "scope entries must be unique")
        deps = request.app.state.deps
        if not hasattr(deps.store, "conn"):
            raise HTTPException(503, "Caregiver consent unavailable")
        with deps.lock, trace_span(
            deps, "tool", "grant_caregiver_consent", "records", request.state.trace_id
        ):
            grant_caregiver_consent(
                deps.store.conn,
                patient_id=user,
                caregiver_id=body.caregiver_id,
                scope=body.scope,
                purpose=body.purpose,
                expires_at=body.expires_at,
                trace_id=request.state.trace_id,
            )
        return {"status": "granted"}

    @app.delete("/records/consents/{caregiver_id}", status_code=204)
    def withdraw_consent(caregiver_id: str, request: Request, user: str = Depends(current_user)):
        from services.records.erasure import ErasureError, withdraw_caregiver_consent

        deps = request.app.state.deps
        if not hasattr(deps.store, "conn"):
            raise HTTPException(503, "Caregiver consent unavailable")
        try:
            with deps.lock, trace_span(
                deps, "tool", "withdraw_caregiver_consent", "records", request.state.trace_id
            ):
                withdraw_caregiver_consent(
                    deps.store.conn,
                    patient_id=user,
                    caregiver_id=caregiver_id,
                    trace_id=request.state.trace_id,
                )
        except ErasureError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/")
    def index(request: Request):
        view = request.query_params.get("view")
        page = "frontdesk.html" if view == "frontdesk" else "ops.html" if view == "ops" else "index.html"
        return static_html(page)

    return app


app = create_app()
