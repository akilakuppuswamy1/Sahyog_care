# Coding agent house rules

- Never read, print, edit, or commit `.env`. Never put keys or identified patient data in any file.
- For tests, set `$env:DATABASE_URL = "postgresql://sahyog:sahyog_local_only@localhost:55432/sahyog"`, run the full `pytest` suite, and report its summary line. Never weaken or delete a test to make it pass.
- Do not run evals, call OpenRouter, or build Docker images unless the user asks.
- Patient IDs come from auth, never from a request body or a model. Red-flag rules run before any model call.
- At the end of every task that changes code, tests, data, or docs, append to or update the current PR's entry in `AI-COLLABORATION.md`, using its template. Fill `agent`, `proposed`, and `notes` (what the agent got wrong and which test or log caught it). Leave `accepted`, `rejected`, and `why` as `_pod to fill_`; only humans may write those. Keep one entry per PR/commit group and add to an open entry rather than creating duplicates.
