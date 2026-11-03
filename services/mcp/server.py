"""MCP tools for appointment scheduling and clinic stock."""
from __future__ import annotations

import json
import os
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from typing import Callable
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from services.agent.store import SlotFull
from services.api.auth import current_mcp_user
from services.api.ratelimit import RateLimiter
from services.common.timefmt import ist

@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    agent_id: str
    trace_id: str
    client_ip: str


@dataclass(frozen=True)
class ToolResult:
    value: object
    action: str = "read"
    patient_id: str | None = None
    resource_id: str | None = None
    detail: dict | None = None


class ToolDenied(ToolError):
    pass


def _database_error_message(error: psycopg.Error, tool_name: str) -> str:
    constraint = getattr(getattr(error, "diag", None), "constraint_name", "") or ""
    if "patient" in constraint.lower():
        return "patient not registered"
    if tool_name == "book_slot" and (
        isinstance(error, psycopg.IntegrityError)
        or (error.sqlstate is not None and error.sqlstate.startswith("23"))
    ):
        return "slot no longer available"
    return "Tool request could not be completed"


def _approved_agents() -> set[str]:
    return {agent.strip() for agent in os.environ.get("MCP_APPROVED_AGENTS", "").split(",")
            if agent.strip()}


def _transport_security() -> TransportSecuritySettings | None:
    hosts = [value.strip() for value in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
             if value.strip()]
    origins = [value.strip() for value in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",")
               if value.strip()]
    if not hosts and not origins:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts or ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=origins or [
            "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
        ],
    )


def _audit(deps, actor_id: str, role: str, agent_id: str | None, tool_name: str,
           trace_id: str, action: str, status: str, patient_id: str | None = None,
           detail: dict | None = None) -> None:
    fields = {
        "agent_id": agent_id,
        "tool_name": tool_name,
        "trace_id": trace_id,
        "status": status,
        **(detail or {}),
    }
    if hasattr(deps.store, "conn"):
        deps.store.conn.execute(
            """INSERT INTO audit_event (actor_id, actor_role, action, resource_type,
                                        resource_id, patient_id, purpose, trace_id, detail)
               VALUES (%s, %s, %s, 'mcp_tool', %s, %s, 'mcp', %s, %s)""",
            (actor_id, role, action, tool_name, patient_id, trace_id, json.dumps(fields)),
        )
    else:
        deps.store.events.append({
            "actor_id": actor_id,
            "actor_role": role,
            "action": action,
            "resource_type": "mcp_tool",
            "resource_id": tool_name,
            "patient_id": patient_id,
            "purpose": "mcp",
            "trace_id": trace_id,
            "detail": fields,
        })


async def _requested_method(receive: Receive) -> tuple[str, str]:
    body = bytearray()
    while len(body) <= 65536:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    try:
        request = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unknown", "unknown"
    method = request.get("method", "unknown") if isinstance(request, dict) else "unknown"
    params = request.get("params", {}) if isinstance(request, dict) else {}
    tool_name = params.get("name", "unknown") if isinstance(params, dict) else "unknown"
    if not isinstance(tool_name, str):
        tool_name = "unknown"
    return str(method)[:80], tool_name[:128]


class MCPAuthMiddleware:
    def __init__(self, app: ASGIApp, deps_getter: Callable, rate_limiter: RateLimiter):
        self.app = app
        self.deps_getter = deps_getter
        self.rate_limiter = rate_limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        agent_id = request.headers.get("x-agent-id")
        trace_id = scope.setdefault("state", {}).get("trace_id") or uuid4().hex
        scope["state"]["trace_id"] = trace_id
        actor = None
        auth_error = None
        try:
            actor = current_mcp_user(request, request.headers.get("authorization"))
        except HTTPException as error:
            auth_error = error

        if agent_id not in _approved_agents():
            method, tool_name = await _requested_method(receive)
            deps = self.deps_getter()
            actor_id, role = actor if actor else ("mcp:unauthenticated", "system")
            with getattr(deps, "lock", nullcontext()):
                tracer = getattr(deps, "tracer", None)
                span = (tracer.span("tool", "mcp.rejected", workflow="mcp", trace_id=trace_id)
                        if tracer else nullcontext())
                with span:
                    _audit(deps, actor_id, role, agent_id, tool_name, trace_id,
                           "read", "unapproved_agent",
                           detail={"method": method})
            await JSONResponse({"detail": "Agent is not approved"}, status_code=403)(
                scope, receive, send
            )
            return

        if auth_error is not None:
            await JSONResponse(
                {"detail": auth_error.detail},
                status_code=auth_error.status_code,
                headers=auth_error.headers,
            )(scope, receive, send)
            return

        user_id, role = actor
        scope["state"].update(mcp_user=user_id, mcp_role=role, mcp_agent_id=agent_id)
        await self.app(scope, receive, send)


def _actor(context: Context) -> Actor:
    request = context.request_context.request
    state = request.scope.setdefault("state", {})
    user_id = state.get("mcp_user")
    role = state.get("mcp_role")
    agent_id = state.get("mcp_agent_id")
    if not user_id or not role or agent_id not in _approved_agents():
        raise ToolDenied("Authentication or approved-agent check failed")
    client = getattr(request, "client", None)
    return Actor(
        user_id=user_id,
        role=role,
        agent_id=agent_id,
        trace_id=state.get("trace_id") or uuid4().hex,
        client_ip=client.host if client else "unknown",
    )


def _execute(deps_getter: Callable, limiter: RateLimiter, context: Context,
             tool_name: str, rate_kind: str, operation: Callable[[Actor], ToolResult]):
    actor = _actor(context)
    deps = deps_getter()
    tracer = getattr(deps, "tracer", None)
    span = (tracer.span("tool", tool_name, workflow="mcp", trace_id=actor.trace_id)
            if tracer else nullcontext())
    status = "error"
    action = "write" if rate_kind == "book" else "read"
    patient_id = None
    details = {}
    result = None
    with getattr(deps, "lock", nullcontext()):
        with span as active_span:
            try:
                retry = limiter.check(
                    actor.user_id, actor.client_ip, rate_kind,
                    pool=getattr(deps, "rate_limit_pool", None),
                )
                if retry:
                    status = "rate_limited"
                    raise ToolDenied(f"Rate limit exceeded; retry after {retry} seconds")
                tool_result = operation(actor)
                result = tool_result.value
                action = tool_result.action
                patient_id = tool_result.patient_id
                details = tool_result.detail or {}
                status = "ok"
                if tool_result.resource_id is not None:
                    details["resource_id"] = tool_result.resource_id
            except ToolDenied:
                if status == "error":
                    status = "denied"
                if active_span and tracer:
                    active_span.set_status("error", "PermissionDenied")
                raise
            except SlotFull:
                status = "error"
                action = "write"
                if active_span and tracer:
                    active_span.set_status("error", "SlotUnavailable")
                raise ToolDenied("slot no longer available") from None
            except psycopg.Error as error:
                status = "error"
                if active_span and tracer:
                    active_span.set_status("error", "DatabaseError")
                raise ToolDenied(_database_error_message(error, tool_name)) from None
            finally:
                _audit(
                    deps, actor.user_id, actor.role, actor.agent_id, tool_name,
                    actor.trace_id, action, status, patient_id=patient_id, detail=details,
                )
    return result


def create_mcp_http_app(deps_getter: Callable, rate_limiter: RateLimiter,
                        streamable_http_path: str = "/") -> ASGIApp:
    server = MCPServer("Sahyog Care Scheduling and Stock")

    @server.tool(description="List specialties available for appointment scheduling.")
    def list_specialties(context: Context) -> list[dict]:
        return _execute(
            deps_getter, rate_limiter, context, "list_specialties", "read",
            lambda _actor: ToolResult(deps_getter().store.list_specialties()),
        )

    @server.tool(description="Find doctors by specialty, language, and optional clinic.")
    def find_doctors(
        context: Context,
        specialty_id: str = Field(min_length=1, max_length=80),
        language: str | None = Field(default=None, max_length=35),
        clinic_id: str | None = Field(default=None, max_length=80),
        limit: int = Field(default=10, ge=1, le=10),
    ) -> list[dict]:
        return _execute(
            deps_getter, rate_limiter, context, "find_doctors", "read",
            lambda _actor: ToolResult(
                deps_getter().store.find_doctors(specialty_id, language, clinic_id, limit)
            ),
        )

    @server.tool(description="Find available appointment slots by specialty or doctor.")
    def next_slots(
        context: Context,
        specialty_id: str | None = Field(default=None, max_length=80),
        doctor_id: str | None = Field(default=None, max_length=80),
        from_date: date | None = None,
        limit: int = Field(default=10, ge=1, le=10),
    ) -> list[dict]:
        def find(_actor: Actor) -> ToolResult:
            if (specialty_id is None) == (doctor_id is None):
                raise ToolDenied("Provide exactly one of specialty_id or doctor_id")
            slots = deps_getter().store.next_slots(specialty_id, doctor_id, from_date, limit)
            return ToolResult([
                {"slot_id": slot["slot_id"], "starts_at": ist(slot["starts_at"])}
                for slot in slots
            ])

        return _execute(deps_getter, rate_limiter, context, "next_slots", "read", find)

    @server.tool(description="Book an available slot for the authenticated patient.")
    def book_slot(
        context: Context,
        slot_id: str = Field(min_length=1, max_length=200),
        idempotency_key: str = Field(min_length=8, max_length=128),
        on_behalf_of: str | None = Field(default=None, min_length=1, max_length=128),
    ) -> dict:
        def book(actor: Actor) -> ToolResult:
            if actor.role == "patient":
                if on_behalf_of is not None:
                    raise ToolDenied("Patients cannot book for another patient")
                patient_id, booked_by = actor.user_id, "assistant"
            elif actor.role == "front_desk":
                if not on_behalf_of:
                    raise ToolDenied("Front desk bookings require on_behalf_of")
                patient_id, booked_by = on_behalf_of, "front_desk"
            else:
                raise ToolDenied("This role cannot book appointments")
            appt_id = deps_getter().store.book(
                patient_id, slot_id, idempotency_key, booked_by=booked_by, trace=False
            )
            return ToolResult(
                {"booking_id": appt_id},
                action="write",
                patient_id=patient_id,
                resource_id=appt_id,
                detail={"on_behalf_of": patient_id} if actor.role == "front_desk" else None,
            )

        return _execute(deps_getter, rate_limiter, context, "book_slot", "book", book)

    @server.tool(description="Get a booking status when you own the booking or have a staff role.")
    def get_booking(
        context: Context,
        appt_id: str = Field(min_length=1, max_length=128),
    ) -> dict:
        def lookup(actor: Actor) -> ToolResult:
            staff_access = actor.role in {"front_desk", "doctor", "ops"}
            booking = deps_getter().store.get_booking(appt_id, actor.user_id, staff_access)
            if booking is None:
                raise ToolDenied("Booking not found or not accessible")
            return ToolResult(
                {"status": booking["status"]},
                patient_id=booking["patient_id"],
                resource_id=appt_id,
            )

        return _execute(deps_getter, rate_limiter, context, "get_booking", "read", lookup)

    @server.tool(description="Check stock status for a molecule and clinic.")
    def check_stock(
        context: Context,
        molecule: str = Field(min_length=1, max_length=200),
        strength: str | None = Field(default=None, max_length=80),
        clinic_id: str = Field(min_length=1, max_length=80),
    ) -> dict:
        def check(actor: Actor) -> ToolResult:
            if actor.role not in {"front_desk", "doctor"}:
                raise ToolDenied("Stock checks require a staff or doctor role")
            stock = deps_getter().stock
            if stock is None:
                raise RuntimeError("Stock lookup is unavailable")
            result = stock.check(
                clinic_id, {"molecule": molecule, "name": molecule, "dose": strength or ""}
            )
            return ToolResult({"status": result["status"], "as_of": result["as_of"]})

        return _execute(deps_getter, rate_limiter, context, "check_stock", "read", check)

    app = server.streamable_http_app(
        streamable_http_path=streamable_http_path,
        transport_security=_transport_security(),
    )
    return MCPAuthMiddleware(app, deps_getter, rate_limiter)


def main() -> None:
    import uvicorn

    from services.api.deps import build_postgres_deps

    deps = build_postgres_deps()
    try:
        app = create_mcp_http_app(lambda: deps, RateLimiter(), streamable_http_path="/mcp")
        uvicorn.run(app, host="127.0.0.1", port=8001)
    finally:
        deps.close()


if __name__ == "__main__":
    main()
