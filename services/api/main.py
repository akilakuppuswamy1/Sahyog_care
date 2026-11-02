"""Synchronous HTTP adapter for W1 and W2. Run one worker for the Core demo."""
import logging
import hashlib
import json
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from services.api.auth import current_user
from services.api.deps import build_postgres_deps
from services.api.ratelimit import RateLimiter

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
                specialty=out.get("specialty"))


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

    @app.middleware("http")
    async def request_log(request, call_next):
        request.state.trace_id = uuid4().hex
        started = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                response = JSONResponse({"detail": "Internal server error"}, status_code=500)
            status = response.status_code
            if request.url.path.startswith(("/w1/", "/w2/")):
                response.headers["X-Trace-Id"] = request.state.trace_id
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
                route=getattr(request.state, "model_route", None))))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/w1/triage")
    def triage(body: Triage, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        limit(request, user, "read")
        thread = uuid4().hex
        request.state.trace_id = thread
        deps = request.app.state.deps
        with deps.lock:
            if (user, key) in replays:
                result = replays[user, key]
                request.state.trace_id = result["thread_id"]
                return result
            out = deps.w1.invoke(dict(text=body.text, age=body.age, patient_id=user,
                                      trace_id=thread, idempotency_key=key),
                                 {"configurable": {"thread_id": thread}})
            result = w1_response(thread, out)
            replays[user, key] = result
        request.state.thread_id = thread
        request.state.model_route = out.get("route")
        return result

    @app.post("/w1/{thread_id}/resume")
    def resume(thread_id: str, body: Resume, request: Request, user: str = Depends(current_user)):
        key = write_key(request)
        deps = request.app.state.deps
        cfg = {"configurable": {"thread_id": thread_id}}
        with deps.lock:
            state = deps.w1.get_state(cfg)
            if not state.values or state.values.get("patient_id") != user:
                raise HTTPException(404, "Thread not found")
            request.state.trace_id = thread_id
            request.state.thread_id = thread_id
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
            out = deps.w1.invoke(Command(resume=body.value, update={"idempotency_key": key}), cfg)
            replays[user, key] = w1_response(thread_id, out)
            request.state.model_route = out.get("route")
        return w1_response(thread_id, out)

    @app.post("/w2/ask")
    def ask(body: Ask, request: Request, user: str = Depends(current_user)):
        limit(request, user, "read")
        subject = body.subject_id or user
        deps = request.app.state.deps
        with deps.lock:
            out = deps.w2.invoke(dict(actor_id=user, subject_id=subject,
                                     actor_role="patient" if subject == user else "caregiver",
                                     question=body.question))
        request.state.trace_id = out.get("trace_id") or request.state.trace_id
        request.state.model_route = out.get("route")
        return {key: out.get(key, False if key == "verified" else "")
                for key in ("route", "reply", "verified", "trace_id")}

    @app.get("/records/access-log")
    def access_log(request: Request, user: str = Depends(current_user)):
        limit(request, user, "read")
        deps = request.app.state.deps
        with deps.lock:
            return deps.audit_query(user)

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    return app


app = create_app()
