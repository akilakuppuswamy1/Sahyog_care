# MCP scheduling and stock tools

The Core API exposes a Streamable HTTP MCP server at `http://localhost:8000/mcp/`.
The same server can run separately with `python -m services.mcp.server` at port 8001.
Both entry points use the existing scheduling store, W3 stock adapter, rate limiter,
audit table and trace exporter; MCP does not implement a second booking or stock system.

## Tools

| Tool | Inputs | Output |
| --- | --- | --- |
| `list_specialties` | none | Specialty IDs and names |
| `find_doctors` | `specialty_id`, optional `language`, `clinic_id`, `limit` (1–10) | Doctor ID, name, clinic, languages |
| `next_slots` | exactly one of `specialty_id` or `doctor_id`, optional `from_date`, `limit` (1–10) | Slot IDs and IST display times |
| `book_slot` | `slot_id`, `idempotency_key`, optional `on_behalf_of` | Booking ID |
| `get_booking` | `appt_id` | Booking status, for the owner or a staff role |
| `check_stock` | `molecule`, `clinic_id`, optional `strength` | `in_stock`, `out_of_stock`, or `unknown`, plus `as_of` |

Tool descriptions contain no patient details. Booking outputs do not include patient
identity, and booking lookups return only status. Doctor/slot responses contain only the
fields listed above. The stock adapter's `unknown` is not treated as out of stock.

## Authentication and authorization

Every HTTP request to the MCP endpoint must include both:

- `Authorization: Bearer <same demo token accepted by the Core API>`
- `X-Agent-Id: <value listed in MCP_APPROVED_AGENTS>`

`MCP_APPROVED_AGENTS` is a comma-separated exact-match allowlist. An unknown or missing
agent is rejected before MCP dispatch and written to the audit log. The list identifies
the calling integration; it does not authenticate a person. The demo bearer tokens are
forgeable and this configuration is **demo only**, not production identity assurance.

The token determines the patient. `book_slot` has no `patient_id` argument: a patient
books only for the token subject. A `front_desk` token must provide `on_behalf_of`; the
audit row records the staff subject as `actor_id`, the patient in `patient_id`, and the
agent/tool/trace metadata. Patient tokens cannot use `on_behalf_of`; doctor and ops
tokens cannot book. `check_stock` is limited to front-desk and doctor roles.
`get_booking` permits the owning patient or a staff role; unauthorized and nonexistent
appointments receive the same response.

The mounted MCP server uses the API's in-process read/book token buckets and the same
patient/IP keys. The standalone entry point uses the same limiter implementation and
configured limits but has its own in-process buckets. MCP calls write audit events and
one argument-free `mcp` tool span per call. Audit metadata includes actor, role, agent
ID, tool name and trace ID; trace spans contain no tool arguments.

For deployments behind a named host, set `MCP_ALLOWED_HOSTS` and, for clients that send
an `Origin` header, `MCP_ALLOWED_ORIGINS` to the approved host/origin patterns. The local
example values in `.env.example` are not a production allowlist. Apply migrations with
`python tools/migrate.py`; migration 013 adds the `ops` actor role to the audit schema.

## Connect a client

Configure the MCP host to connect to the Streamable HTTP URL and send both headers on
each request. For example, an MCP host that supports remote HTTP servers and headers
can use:

```json
{
  "mcpServers": {
    "sahyog-care": {
      "url": "http://localhost:8000/mcp/",
      "headers": {
        "Authorization": "Bearer demo-PT-DEMO-0001",
        "X-Agent-Id": "agent-demo"
      }
    }
  }
}
```

The patient example is a synthetic demo identity. Use the equivalent approved agent's
staff token for stock checks. Do not put live patient identifiers or production secrets
in a client configuration or a repository.

Run the demo client after the API, database fixtures, stock CSVs and MCP agent allowlist
are configured:

```powershell
python tools/mcp_demo.py --base-url http://localhost:8000 --agent-id agent-demo
```

The script uses only the explicit synthetic demo patient/staff tokens; it does not load
`.env`. The same pinned SDK can be used to connect programmatically:

```python
import asyncio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def connect():
    async with httpx2.AsyncClient(headers={
        "Authorization": "Bearer demo-PT-DEMO-0001",
        "X-Agent-Id": "agent-demo",
    }) as http:
        async with streamable_http_client("http://localhost:8000/mcp/", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.list_tools()

asyncio.run(connect())
```
