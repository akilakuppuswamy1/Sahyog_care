"""Exercise the mounted MCP scheduling and stock tools with synthetic demo identities."""
from __future__ import annotations

import argparse
import asyncio
import json
from uuid import uuid4

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _result_value(result):
    if result.is_error:
        message = next((item.text for item in result.content if hasattr(item, "text")),
                       "MCP tool call failed")
        raise RuntimeError(message)
    if result.structured_content is not None:
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content
    text = next(item.text for item in result.content if hasattr(item, "text"))
    return json.loads(text)


async def _with_session(endpoint: str, agent_id: str, token: str, action):
    headers = {
        "Authorization": f"Bearer demo-{token}",
        "X-Agent-Id": agent_id,
    }
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(endpoint, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)


async def run_demo(base_url: str, agent_id: str) -> dict:
    endpoint = f"{base_url.rstrip('/')}/mcp/"

    async def patient_flow(session):
        catalog = await session.list_tools()
        specialties = _result_value(await session.call_tool("list_specialties"))
        if not specialties:
            raise RuntimeError("No specialties are available")
        specialty_id = specialties[0]["specialty_id"]
        doctors = _result_value(await session.call_tool("find_doctors", {
            "specialty_id": specialty_id,
            "limit": 1,
        }))
        if not doctors:
            raise RuntimeError("No doctors are available for the selected specialty")
        doctor = doctors[0]
        slots = _result_value(await session.call_tool("next_slots", {
            "doctor_id": doctor["doctor_id"],
            "limit": 1,
        }))
        if not slots:
            raise RuntimeError("No appointment slots are available")
        booking = _result_value(await session.call_tool("book_slot", {
            "slot_id": slots[0]["slot_id"],
            "idempotency_key": uuid4().hex,
        }))
        return {
            "tools": [tool.name for tool in catalog.tools],
            "doctor": doctor,
            "slot": slots[0],
            "booking_id": booking["booking_id"],
            "clinic_id": doctor["clinic_id"],
        }

    patient_result = await _with_session(
        endpoint, agent_id, "PT-DEMO-0001", patient_flow
    )

    async def stock_flow(session):
        return _result_value(await session.call_tool("check_stock", {
            "molecule": "metformin",
            "strength": "500 mg",
            "clinic_id": patient_result["clinic_id"],
        }))

    stock = await _with_session(endpoint, agent_id, "staff-DEMO-FRONTDESK", stock_flow)
    return {
        "tools": patient_result["tools"],
        "doctor": patient_result["doctor"],
        "slot": patient_result["slot"],
        "booking_id": patient_result["booking_id"],
        "stock": stock,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()
    result = asyncio.run(run_demo(args.base_url, args.agent_id))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
