# Scheduling

Booking is implemented in [`services/agent/store.py`](../agent/store.py), which calls the database `book_slot()` function. The function is defined in [`infra/db/001_schema.sql`](../../infra/db/001_schema.sql); ADR-002 documents why booking capacity and related safety rules are enforced in the database.

See [ADR-002: Safety rules in the database](../../docs/adr/ADR-002-safety-rules-in-the-database.md).
