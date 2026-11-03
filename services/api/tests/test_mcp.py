import asyncio
import json
import os
from threading import RLock
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from services.api.deps import build_test_deps
from services.api.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
    monkeypatch.setenv("MCP_APPROVED_AGENTS", "agent-demo,agent-staff")
    with TestClient(create_app(build_test_deps)) as test_client:
        yield test_client


async def _with_session(app, identity, agent_id, callback):
    headers = {
        "Authorization": f"Bearer demo-{identity}",
        "X-Agent-Id": agent_id,
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://127.0.0.1:8000",
        headers=headers,
    ) as http:
        async with streamable_http_client(
            "http://127.0.0.1:8000/mcp/", http_client=http
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await callback(session)


def _run(client, identity, agent_id, callback):
    return asyncio.run(_with_session(client.app, identity, agent_id, callback))


def _output(result):
    if result.structured_content is not None:
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content
    text = next(item.text for item in result.content if hasattr(item, "text"))
    return json.loads(text)


def _error_text(result):
    return "\n".join(
        item.text for item in result.content if hasattr(item, "text")
    )


def test_mcp_tool_catalog_schemas_and_minimal_outputs(client):
    async def exercise(session):
        catalog = await session.list_tools()
        tools = {tool.name: tool for tool in catalog.tools}
        assert set(tools) == {
            "list_specialties", "find_doctors", "next_slots", "book_slot",
            "get_booking", "check_stock",
        }
        assert tools["next_slots"].input_schema["properties"]["limit"]["maximum"] == 10
        assert tools["book_slot"].input_schema["properties"]["idempotency_key"]["minLength"] == 8
        assert "patient_id" not in tools["book_slot"].input_schema["properties"]

        specialties = await session.call_tool("list_specialties")
        doctors = await session.call_tool("find_doctors", {
            "specialty_id": "SP07", "language": "en", "limit": 2,
        })
        slots = await session.call_tool("next_slots", {"specialty_id": "SP07", "limit": 2})
        return specialties, doctors, slots

    specialties, doctors, slots = _run(client, "PT-MCP-OWNER", "agent-demo", exercise)
    assert not specialties.is_error and specialties.structured_content
    assert not doctors.is_error
    doctors_data = _output(doctors)
    slots_data = _output(slots)
    specialties_data = _output(specialties)
    assert set(doctors_data[0]) == {
        "doctor_id", "name", "clinic_id", "clinic", "languages",
    }
    assert not slots.is_error
    assert set(slots_data[0]) == {"slot_id", "starts_at"}
    assert slots_data[0]["starts_at"].endswith("IST")
    serialized = json.dumps([specialties_data, doctors_data, slots_data])
    assert "phone" not in serialized.lower() and "dob" not in serialized.lower()
    assert "PT-MCP-OWNER" not in serialized
    assert specialties_data


def test_mcp_next_slots_requires_exactly_one_selector(client):
    async def exercise(session):
        neither = await session.call_tool("next_slots", {"limit": 1})
        both = await session.call_tool(
            "next_slots", {"specialty_id": "SP07", "doctor_id": "D1", "limit": 1}
        )
        return neither, both

    neither, both = _run(client, "PT-MCP-OWNER", "agent-demo", exercise)
    assert neither.is_error and both.is_error
    events = [
        event for event in client.app.state.deps.store.events
        if event.get("resource_id") == "next_slots"
    ]
    assert len(events) == 2
    assert all(event["detail"]["status"] == "denied" for event in events)


def test_mcp_patient_booking_replay_and_owner_access(client):
    async def exercise(session):
        slots = await session.call_tool("next_slots", {"specialty_id": "SP07", "limit": 1})
        slot_id = _output(slots)[0]["slot_id"]
        args = {"slot_id": slot_id, "idempotency_key": "mcp-booking-replay-1"}
        first = await session.call_tool("book_slot", args)
        replay = await session.call_tool("book_slot", args)
        booking_id = _output(first)["booking_id"]
        status = await session.call_tool("get_booking", {"appt_id": booking_id})
        return first, replay, status

    first, replay, status = _run(client, "PT-MCP-OWNER", "agent-demo", exercise)
    assert not first.is_error and not replay.is_error
    assert _output(first) == _output(replay)
    assert set(_output(first)) == {"booking_id"}
    assert _output(status) == {"status": "booked"}
    assert "PT-MCP-OWNER" not in json.dumps([_output(first), _output(status)])

    deps = client.app.state.deps
    mcp_events = [event for event in deps.store.events if event.get("resource_type") == "mcp_tool"]
    assert len(mcp_events) == 4
    assert all(
        event["actor_id"] == "PT-MCP-OWNER"
        and event["actor_role"] == "patient"
        and event["detail"]["agent_id"] == "agent-demo"
        and event["detail"]["trace_id"] == event["trace_id"]
        for event in mcp_events
    )
    tool_spans = [span for span in deps.tracer.exporter.spans
                  if span.kind == "tool" and span.workflow == "mcp"]
    assert len(tool_spans) == 4
    assert {span.name for span in tool_spans} == {
        "next_slots", "book_slot", "get_booking",
    }
    assert all("mcp-booking-replay-1" not in span.name for span in tool_spans)


def test_mcp_patient_cannot_impersonate_and_front_desk_on_behalf_is_audited(client):
    async def patient_attempt(session):
        slots = await session.call_tool("next_slots", {"specialty_id": "SP07", "limit": 1})
        slot_id = _output(slots)[0]["slot_id"]
        return await session.call_tool("book_slot", {
            "slot_id": slot_id,
            "idempotency_key": "mcp-patient-spoof-1",
            "on_behalf_of": "PT-OTHER",
        })

    denied = _run(client, "PT-MCP-OWNER", "agent-demo", patient_attempt)
    assert denied.is_error
    assert not getattr(client.app.state.deps.store, "appointments", {})

    async def patient_id_attempt(session):
        slots = await session.call_tool("next_slots", {"specialty_id": "SP07", "limit": 1})
        return await session.call_tool("book_slot", {
            "slot_id": _output(slots)[0]["slot_id"],
            "idempotency_key": "mcp-patient-id-arg-1",
            "patient_id": "PT-OTHER",
        })

    spoofed = _run(client, "PT-MCP-OWNER", "agent-demo", patient_id_attempt)
    if not spoofed.is_error:
        assert all(
            appointment["patient_id"] == "PT-MCP-OWNER"
            for appointment in client.app.state.deps.store.appointments.values()
        )

    async def staff_book(session):
        slots = await session.call_tool("next_slots", {"specialty_id": "SP07", "limit": 1})
        return await session.call_tool("book_slot", {
            "slot_id": _output(slots)[0]["slot_id"],
            "idempotency_key": "mcp-frontdesk-book-1",
            "on_behalf_of": "PT-MCP-OWNER",
        })

    booked = _run(client, "staff-RECEPTION-1", "agent-staff", staff_book)
    assert not booked.is_error
    event = next(
        event for event in reversed(client.app.state.deps.store.events)
        if event.get("resource_type") == "mcp_tool" and event.get("resource_id") == "book_slot"
        and event.get("actor_role") == "front_desk"
    )
    assert event["actor_id"] == "RECEPTION-1"
    assert event["patient_id"] == "PT-MCP-OWNER"
    assert event["detail"]["on_behalf_of"] == "PT-MCP-OWNER"
    assert event["detail"]["agent_id"] == "agent-staff"


def test_mcp_stock_role_and_unapproved_agent_audit(client):
    async def patient_stock(session):
        return await session.call_tool("check_stock", {
            "molecule": "metformin", "clinic_id": "PUN-H01",
        })

    denied = _run(client, "PT-MCP-OWNER", "agent-demo", patient_stock)
    assert denied.is_error
    denied_event = next(event for event in reversed(client.app.state.deps.store.events)
                        if event.get("resource_id") == "check_stock")
    assert denied_event["action"] == "read"
    assert denied_event["detail"]["status"] == "denied"

    async def staff_stock(session):
        return await session.call_tool("check_stock", {
            "molecule": "metformin", "strength": "500 mg", "clinic_id": "PUN-H01",
        })

    stock = _run(client, "staff-RECEPTION-1", "agent-staff", staff_stock)
    assert not stock.is_error
    assert set(_output(stock)) == {"status", "as_of"}

    async def doctor_stock(session):
        return await session.call_tool("check_stock", {
            "molecule": "metformin", "clinic_id": "PUN-H01",
        })

    doctor_result = _run(client, "doc-DOCTOR-1", "agent-staff", doctor_stock)
    assert not doctor_result.is_error

    async def rejected_agent():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=client.app),
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer demo-PT-MCP-OWNER", "X-Agent-Id": "agent-rogue"},
        ) as http:
            response = await http.post("/mcp/", json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_specialties", "arguments": {}},
            })
            return response.status_code

    assert asyncio.run(rejected_agent()) == 403
    rejected = next(event for event in reversed(client.app.state.deps.store.events)
                    if event["detail"].get("agent_id") == "agent-rogue")
    assert rejected["action"] == "read"
    assert rejected["actor_id"] == "PT-MCP-OWNER"
    assert rejected["actor_role"] == "patient"
    assert rejected["detail"]["tool_name"] == "list_specialties"
    assert rejected["detail"]["trace_id"] == rejected["trace_id"]


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_mcp_postgres_audit_and_span_are_written(monkeypatch):
    import psycopg
    from psycopg.conninfo import make_conninfo
    from pathlib import Path
    import re
    from services.agent.store import PostgresStore
    from services.common.tracing import PostgresExporter, Tracer

    schema = "mcp_test_" + uuid4().hex
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            url = make_conninfo(os.environ["DATABASE_URL"], options=f"-csearch_path={schema},public")
            conn = psycopg.connect(url, autocommit=True)
            root = Path(__file__).resolve().parents[3]
            schema_sql = (root / "infra" / "db" / "001_schema.sql").read_text(encoding="utf-8")
            if not conn.execute(
                "SELECT 1 FROM pg_extension WHERE extname='vector'"
            ).fetchone():
                schema_sql = schema_sql.replace("vector(1024)", "real[]")
            conn.execute(re.sub(r"(?m)^\s*(BEGIN|COMMIT);\s*$", "", schema_sql))
            for migration_name in ("007_tracing.sql", "011_deid_timing.sql", "013_mcp_audit_roles.sql"):
                conn.execute((root / "infra" / "db" / migration_name).read_text(encoding="utf-8"))
            conn.execute(
                """INSERT INTO clinic VALUES
                   ('C1','Test Clinic','Pune','hospital','Pune','08:00','20:00',true,true)"""
            )
            conn.execute("INSERT INTO specialty (specialty_id, name) VALUES ('SP07', 'Orthopaedics')")
            conn.execute(
                """INSERT INTO doctor VALUES
                   ('D1','Dr Test','M','SP07','C1',ARRAY['en'],20,true)"""
            )
            conn.execute(
                "INSERT INTO patient (patient_id, name, dob) VALUES ('PT-MCP-DB', 'Demo', '1992-01-01')"
            )
            from tools.migrate import seed_demo_patients

            seed_demo_patients(conn)
            seed_demo_patients(conn)
            assert conn.execute(
                """SELECT count(*) FROM patient
                   WHERE patient_id IN ('PT-DEMO-0001', 'PT-W2-0001', 'PT-W2-0002')"""
            ).fetchone()[0] == 3
            conn.execute(
                """INSERT INTO slot (slot_id, doctor_id, clinic_id, starts_at)
                   VALUES ('S1', 'D1', 'C1', now() + interval '7 days'),
                          ('S2', 'D1', 'C1', now() + interval '8 days'),
                          ('S3', 'D1', 'C1', now() + interval '9 days')"""
            )
            from psycopg_pool import ConnectionPool
            pool = ConnectionPool(conninfo=url, kwargs={"autocommit": True}, open=True)

            class Deps:
                lock = RLock()
                store = PostgresStore(conn)
                tracer = Tracer(PostgresExporter(pool))
                stock = None

                @staticmethod
                def close():
                    pool.close()
                    conn.close()

            monkeypatch.setenv("SAHYOG_DEMO_AUTH", "1")
            monkeypatch.setenv("MCP_APPROVED_AGENTS", "agent-db-test")

            async def invoke(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8000",
                    headers={
                        "Authorization": "Bearer demo-PT-MCP-DB",
                        "X-Agent-Id": "agent-db-test",
                    },
                ) as http:
                    async with streamable_http_client(
                        "http://127.0.0.1:8000/mcp/", http_client=http
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            await session.call_tool("list_specialties")
                            slots = await session.call_tool("next_slots", {
                                "doctor_id": "D1", "limit": 1,
                            })
                            args = {
                                "slot_id": _output(slots)[0]["slot_id"],
                                "idempotency_key": "mcp-postgres-replay-1",
                            }
                            first = await session.call_tool("book_slot", args)
                            replay = await session.call_tool("book_slot", args)
                            booking = await session.call_tool(
                                "get_booking", {"appt_id": _output(first)["booking_id"]}
                            )
                            return first, replay, booking

            with TestClient(create_app(lambda: Deps)) as test_client:
                first, replay, booking = asyncio.run(invoke(test_client.app))
                assert not first.is_error and not replay.is_error and not booking.is_error
                assert _output(first) == _output(replay)
                assert _output(booking) == {"status": "booked"}
                assert conn.execute("SELECT booked FROM slot WHERE slot_id='S1'").fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM appointment").fetchone()[0] == 1

                async def unregistered_patient(session):
                    return await session.call_tool("book_slot", {
                        "slot_id": "S3",
                        "idempotency_key": "mcp-unregistered-patient",
                    })

                unregistered = asyncio.run(_with_session(
                    test_client.app, "PT-MCP-UNREGISTERED", "agent-db-test",
                    unregistered_patient,
                ))
                assert unregistered.is_error
                assert "patient not registered" in _error_text(unregistered).lower()
                assert "UnexpectedToolError" not in _error_text(unregistered)
                assert "ForeignKeyViolation" not in _error_text(unregistered)
                assert "INSERT" not in _error_text(unregistered).upper()
                assert conn.execute("SELECT count(*) FROM appointment").fetchone()[0] == 1
                assert conn.execute(
                    "SELECT booked FROM slot WHERE slot_id='S3'"
                ).fetchone()[0] == 0

                async def demo_flow(session):
                    specialties = await session.call_tool("list_specialties")
                    doctors = await session.call_tool("find_doctors", {
                        "specialty_id": "SP07", "limit": 1,
                    })
                    slots = await session.call_tool("next_slots", {
                        "doctor_id": "D1", "limit": 1,
                    })
                    if any(result.is_error for result in (specialties, doctors, slots)):
                        return specialties, doctors, slots, None
                    booked = await session.call_tool("book_slot", {
                        "slot_id": _output(slots)[0]["slot_id"],
                        "idempotency_key": "mcp-demo-flow-booking",
                    })
                    return specialties, doctors, slots, booked

                demo_results = asyncio.run(_with_session(
                    test_client.app, "PT-DEMO-0001", "agent-db-test", demo_flow,
                ))
                assert all(not result.is_error for result in demo_results)
                assert _output(demo_results[3])["booking_id"]
                assert conn.execute("SELECT count(*) FROM appointment").fetchone()[0] == 2

                async def book_full_slot(session):
                    return await session.call_tool("book_slot", {
                        "slot_id": "S1",
                        "idempotency_key": "mcp-full-slot-retry",
                    })

                full_slot = asyncio.run(_with_session(
                    test_client.app, "PT-DEMO-0001", "agent-db-test", book_full_slot,
                ))
                assert full_slot.is_error
                assert "slot no longer available" in _error_text(full_slot).lower()
                assert "UnexpectedToolError" not in _error_text(full_slot)
                assert conn.execute("SELECT count(*) FROM appointment").fetchone()[0] == 2

                rows = conn.execute(
                    """SELECT actor_id, actor_role, detail, trace_id FROM audit_event
                        WHERE resource_type = 'mcp_tool' AND detail->>'agent_id' = 'agent-db-test'"""
                ).fetchall()
                assert len(rows) == 11
                assert sum(detail["status"] == "error" for _, _, detail, _ in rows) == 2
                for actor_id, role, detail, trace_id in rows:
                    assert actor_id in {
                        "PT-MCP-DB", "PT-DEMO-0001", "PT-MCP-UNREGISTERED",
                    }
                    assert role == "patient"
                    assert detail["tool_name"] in {
                        "list_specialties", "find_doctors", "next_slots",
                        "book_slot", "get_booking",
                    }
                    assert detail["trace_id"] == trace_id
                    span_count = conn.execute(
                        """SELECT count(*) FROM trace_span
                            WHERE trace_id = %s AND kind = 'tool' AND workflow = 'mcp'""",
                        (trace_id,),
                    ).fetchone()[0]
                    assert span_count == 1
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
