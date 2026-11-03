"""Exercise the scale profile with synthetic requests; live models require explicit opt-in."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
TRAFFIC_MIX = {"w1_triage_and_booking": 0.30, "w2_record_question": 0.25,
               "w3_note_draft": 0.15, "upload": 0.10, "ops_read": 0.20}
HTTP_REQUESTS_PER_ACTION = {
    "w1_triage_and_booking": 2, "w2_record_question": 1, "w3_note_draft": 1,
    "upload": 1, "ops_read": 1,
}
P95_TARGET_MS = {"w1": 4000, "w2": 8000, "w3": 20000}
ROUTE_P95_TARGET_MS = {
    "POST /w1/triage": 4000,
    "POST /w1/{thread_id}/resume": 4000,
    "POST /w2/ask": 8000,
    "POST /scribe/api/drafts": 20000,
    "POST /records/upload": 4000,
    "GET /ops/api/summary": 4000,
}
W2_QUESTIONS = (
    "What is my hemoglobin result?",
    "What is my LDL cholesterol result?",
    "What is my creatinine result?",
)
ERROR_RATE_LIMIT = 0.01
SAFE_ERROR_DETAILS = {
    "Rate limit exceeded",
    "this encounter already has an active doctor review",
    "Only the treating doctor may access this encounter",
    "Encounter not found",
    "Encounter clinic does not match the treating doctor's clinic",
}


def _percentile(samples: list[float], percentile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _route_stats(samples: list[dict], duration_seconds: float | None = None) -> dict:
    if not samples:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None,
                "rps": 0.0 if duration_seconds else None, "errors": 0,
                "error_rate": 0.0, "rate_limited": 0, "degraded_rate": 0.0,
                "status_codes": {}, "first_error_body": None}
    failures = sum(not 200 <= row["status"] < 300 for row in samples)
    limited = sum(row["status"] == 429 for row in samples)
    degraded = sum(row["degraded"] for row in samples)
    latencies = [row["latency_ms"] for row in samples]
    status_codes = {}
    for row in samples:
        code = str(row["status"])
        status_codes[code] = status_codes.get(code, 0) + 1
    return {
        "count": len(samples),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "rps": round(len(samples) / duration_seconds, 4) if duration_seconds else None,
        "errors": failures,
        "error_rate": round(failures / len(samples), 4),
        "rate_limited": limited,
        "degraded_rate": round(degraded / len(samples), 4),
        "status_codes": dict(sorted(status_codes.items())),
        "first_error_body": next(
            (row.get("error_body") for row in samples
             if not 200 <= row["status"] < 300),
            None,
        ),
    }


def _safe_error_body(response: httpx.Response | None) -> str:
    if response is None:
        return "No HTTP response body"
    try:
        payload = response.json()
    except ValueError:
        return "[non-JSON response body redacted]"
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), str):
        return "[structured response body redacted]"
    detail = payload["detail"]
    if detail.startswith("no stock CSV is configured for clinic "):
        detail = "no stock CSV is configured for clinic [redacted]"
    elif detail not in SAFE_ERROR_DETAILS:
        detail = "[non-generic error detail redacted]"
    return json.dumps({"detail": detail}, ensure_ascii=False, sort_keys=True)


def _targeted_stats(samples: list[dict], duration_seconds: float, target_ms: int) -> dict:
    stats = _route_stats(samples, duration_seconds)
    p95 = stats["p95_ms"]
    return {**stats, "target_ms": target_ms, "pass": p95 is not None and p95 < target_ms}


def _load_verdict(workflows: dict, routes: dict, error_rate: float) -> str:
    return "PASS" if (
        error_rate <= ERROR_RATE_LIMIT
        and all(value["pass"] for value in workflows.values())
        and all(value["pass"] for value in routes.values())
    ) else "FAIL"


def _synthetic_client_ip(index: int) -> str:
    return str(ipaddress.ip_address(int(ipaddress.ip_address("198.18.0.1")) + index))


def _model_mode(tiles: list[dict], allow_real_model: bool) -> tuple[str, dict | None]:
    mode = next((tile["value"] for tile in tiles if tile.get("id") == "model-mode"), None)
    if mode == "load-test stub":
        provenance = next(
            (tile["value"] for tile in tiles if tile.get("id") == "stub-latency-source"),
            None,
        )
        if not provenance:
            raise RuntimeError("Ops summary is missing load-test stub latency provenance")
        return mode, provenance
    if not allow_real_model:
        raise RuntimeError(
            "API did not report model mode 'load-test stub'; refusing to run. "
            "Pass --allow-real-model to explicitly permit live model calls."
        )
    return "real model (explicitly allowed)", None


def _synthetic_pdf() -> bytes:
    body = b"BT /F1 12 Tf 72 720 Td (Synthetic load test report. Hemoglobin 13 g/dL.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
        b"<< /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(body)).encode("ascii") + b" >>\nstream\n" + body + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, item in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii") + item + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _load_subjects(database_url: str, patient_limit: int) -> dict:
    with psycopg.connect(database_url) as conn:
        patients = [row[0] for row in conn.execute(
            "SELECT patient_id FROM patient WHERE synthetic ORDER BY patient_id LIMIT %s",
            (patient_limit,),
        ).fetchall()]
        record_patients = [row[0] for row in conn.execute(
            """SELECT p.patient_id FROM patient p
                 WHERE p.synthetic AND EXISTS (
                       SELECT 1 FROM doc_chunk c WHERE c.patient_id = p.patient_id)
                 ORDER BY p.patient_id LIMIT %s""",
            (patient_limit,),
        ).fetchall()]
        encounters = [row for row in conn.execute(
            """SELECT e.encounter_id, e.doctor_id FROM encounter e
                 JOIN doctor d ON d.doctor_id = e.doctor_id
                WHERE d.synthetic
                  AND NOT EXISTS (
                      SELECT 1 FROM note_draft nd WHERE nd.encounter_id = e.encounter_id)
                ORDER BY e.encounter_id LIMIT %s""",
            (patient_limit,),
        ).fetchall()]
    if not patients:
        raise RuntimeError("No synthetic patients are loaded in Postgres")
    if not record_patients:
        raise RuntimeError("No synthetic patients with indexed records are loaded in Postgres")
    if not encounters:
        raise RuntimeError(
            "No synthetic doctor encounters are loaded in Postgres. "
            "Run `python tools/seed_load_test.py` after `python tools/migrate.py`."
        )
    return {"patients": patients, "record_patients": record_patients, "encounters": encounters}


async def run_load(args: argparse.Namespace) -> dict:
    if not args.database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url; the load client does not read .env")
    subjects = _load_subjects(args.database_url, args.patient_limit)
    routes: dict[str, list[dict]] = {}
    probe_routes: dict[str, list[dict]] = {}
    workflows: dict[str, list[dict]] = {name: [] for name in P95_TARGET_MS}
    replicas: set[str] = set()
    selector = random.Random(args.seed)
    booking_patients = list(subjects["patients"])
    selector.shuffle(booking_patients)
    if args.hoarding_probe:
        if len(booking_patients) < 2:
            raise RuntimeError("--hoarding-probe requires at least two synthetic patients")
        probe_patient = booking_patients.pop()
    else:
        probe_patient = None
    booking_patient_index = 0
    draft_encounters = list(subjects["encounters"])
    selector.shuffle(draft_encounters)
    draft_encounter_index = 0
    patient_ips = {
        patient: _synthetic_client_ip(index)
        for index, patient in enumerate(subjects["patients"])
    }
    actions = tuple(TRAFFIC_MIX)
    weights = tuple(TRAFFIC_MIX.values())
    semaphore = asyncio.Semaphore(args.concurrency)
    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": "Bearer demo-ops-loadtest"}
    request_count = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=args.timeout_seconds) as client:
        # Require a positive server-side mode signal before generating load.
        check = await client.get("/ops/api/summary", headers=headers)
        if not check.is_success:
            raise RuntimeError(f"Ops preflight failed with HTTP {check.status_code}")
        model_mode, stub_provenance = _model_mode(
            check.json().get("tiles", []), args.allow_real_model,
        )

        async def send(route: str, method: str, path: str, *, probe: bool = False,
                       client_patient: str | None = None, **kwargs):
            nonlocal request_count
            if client_patient is not None:
                kwargs.setdefault("headers", {})["X-Forwarded-For"] = patient_ips[client_patient]
            started = time.perf_counter()
            try:
                response = await client.request(method, path, **kwargs)
                status = response.status_code
                degraded = response.headers.get("X-Degraded") == "true"
                replica = response.headers.get("X-Replica-ID")
                if replica:
                    replicas.add(replica)
            except httpx.HTTPError:
                response = None
                status = 0
                degraded = False
            row = {"status": status, "latency_ms": (time.perf_counter() - started) * 1000,
                   "degraded": degraded,
                   "error_body": _safe_error_body(response) if not 200 <= status < 300 else None}
            (probe_routes if probe else routes).setdefault(route, []).append(row)
            request_count += 1
            return response

        def next_booking_patient() -> str:
            nonlocal booking_patient_index
            if booking_patient_index >= len(booking_patients):
                raise RuntimeError(
                    "Synthetic patient pool exhausted; run `python tools/seed_load_test.py` "
                    "or raise --patient-limit."
                )
            patient = booking_patients[booking_patient_index]
            booking_patient_index += 1
            return patient

        def next_draft_encounter() -> tuple[str, str]:
            nonlocal draft_encounter_index
            if draft_encounter_index >= len(draft_encounters):
                raise RuntimeError(
                    "Unused synthetic encounters exhausted; run `python tools/seed_load_test.py`."
                )
            encounter = draft_encounters[draft_encounter_index]
            draft_encounter_index += 1
            return encounter

        def finish_workflow(name: str, started: float, status: int, degraded: bool) -> None:
            workflows[name].append({
                "status": status,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "degraded": degraded,
            })

        async def triage_action():
            async with semaphore:
                started = time.perf_counter()
                patient = next_booking_patient()
                token = f"Bearer demo-{patient}"
                response = await send(
                    "POST /w1/triage", "POST", "/w1/triage",
                    client_patient=patient,
                    headers={"Authorization": token, "Idempotency-Key": uuid4().hex,
                             "X-Forwarded-For": patient_ips[patient]},
                    json={"text": "Mild knee ache after walking for several weeks.", "age": 42},
                )
                status = response.status_code if response else 0
                degraded = bool(response and response.headers.get("X-Degraded") == "true")
                if response and response.is_success:
                    payload = response.json()
                    if payload.get("status") == "needs_input":
                        options = (payload.get("interrupt") or {}).get("options") or []
                        if options:
                            resumed = await send(
                                "POST /w1/{thread_id}/resume", "POST",
                                f"/w1/{payload['thread_id']}/resume",
                                client_patient=patient,
                                headers={"Authorization": token, "Idempotency-Key": uuid4().hex,
                                         "X-Forwarded-For": patient_ips[patient]},
                                json={"value": options[0]["slot_id"]},
                            )
                            status = resumed.status_code if resumed else 0
                            degraded = degraded or bool(
                                resumed and resumed.headers.get("X-Degraded") == "true"
                            )
                        else:
                            status = 500
                finish_workflow("w1", started, status, degraded)

        async def record_action():
            async with semaphore:
                started = time.perf_counter()
                patient = selector.choice(subjects["record_patients"])
                response = await send(
                    "POST /w2/ask", "POST", "/w2/ask",
                    client_patient=patient,
                    headers={"Authorization": f"Bearer demo-{patient}"},
                    json={"question": selector.choice(W2_QUESTIONS)},
                )
                status = response.status_code if response else 0
                degraded = bool(response and response.headers.get("X-Degraded") == "true")
                if response and response.is_success:
                    degraded = degraded or bool(response.json().get("degraded", False))
                finish_workflow("w2", started, status, degraded)

        async def draft_action():
            async with semaphore:
                started = time.perf_counter()
                encounter_id, doctor_id = next_draft_encounter()
                response = await send(
                    "POST /scribe/api/drafts", "POST", "/scribe/api/drafts",
                    headers={"Authorization": f"Bearer demo-doc-{doctor_id}",
                             "Idempotency-Key": uuid4().hex},
                    json={
                        "encounter_id": encounter_id,
                        "transcript": "Doctor: Routine follow-up. Patient: I feel well today.",
                    },
                )
                status = response.status_code if response else 0
                degraded = bool(response and response.headers.get("X-Degraded") == "true")
                finish_workflow("w3", started, status, degraded)

        async def upload_action():
            async with semaphore:
                patient = selector.choice(subjects["patients"])
                await send(
                    "POST /records/upload", "POST", "/records/upload",
                    client_patient=patient,
                    headers={"Authorization": f"Bearer demo-{patient}",
                             "Idempotency-Key": uuid4().hex},
                    files={"file": ("synthetic-load-report.pdf", _synthetic_pdf(), "application/pdf")},
                )

        async def ops_action():
            async with semaphore:
                await send("GET /ops/api/summary", "GET", "/ops/api/summary",
                           headers=headers)

        action_functions = {
            "w1_triage_and_booking": triage_action, "w2_record_question": record_action,
            "w3_note_draft": draft_action, "upload": upload_action, "ops_read": ops_action,
        }
        duration_seconds = args.duration_minutes * 60
        expected_requests = sum(
            TRAFFIC_MIX[action] * HTTP_REQUESTS_PER_ACTION[action] for action in TRAFFIC_MIX
        )
        mean_requests_per_action = sum(TRAFFIC_MIX.values())
        expected_requests /= mean_requests_per_action
        interval_at_base_rate = expected_requests * 3600 / args.rate_per_hour
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        deadline = started + duration_seconds
        next_start = started
        tasks = []
        burst_seconds = min(120, duration_seconds) if args.burst else 0
        while next_start < deadline:
            elapsed = next_start - started
            multiplier = 3 if args.burst and elapsed < burst_seconds else 1
            await asyncio.sleep(max(0, next_start - time.monotonic()))
            if next_start >= deadline:
                break
            action = selector.choices(actions, weights=weights, k=1)[0]
            tasks.append(asyncio.create_task(action_functions[action]()))
            next_start += interval_at_base_rate / multiplier
        if tasks:
            await asyncio.gather(*tasks)
        workload_duration = time.monotonic() - started
        probe_result = {"enabled": False, "status": "NOT RUN", "expected_429s": 0, "routes": {}}
        if args.hoarding_probe:
            assert probe_patient is not None
            for _ in range(21):
                triage = await send(
                    "POST /w1/triage", "POST", "/w1/triage", probe=True,
                    client_patient=probe_patient,
                    headers={"Authorization": f"******",
                             "Idempotency-Key": uuid4().hex},
                    json={"text": "Mild knee ache after walking for several weeks.", "age": 42},
                )
                if not triage or not triage.is_success:
                    continue
                payload = triage.json()
                options = (payload.get("interrupt") or {}).get("options") or []
                if payload.get("status") == "needs_input" and options:
                    await send(
                        "POST /w1/{thread_id}/resume", "POST",
                        f"/w1/{payload['thread_id']}/resume", probe=True,
                        client_patient=probe_patient,
                        headers={"Authorization": f"******",
                                 "Idempotency-Key": uuid4().hex},
                        json={"value": "load-hoarding-probe-invalid-choice"},
                    )
            probe_results = {
                name: _route_stats(samples)
                for name, samples in sorted(probe_routes.items())
            }
            expected_429s = probe_results.get(
                "POST /w1/{thread_id}/resume", {},
            ).get("rate_limited", 0)
            probe_result = {
                "enabled": True,
                "status": "PASS" if expected_429s > 0 else "FAIL",
                "expected_429s": expected_429s,
                "routes": probe_results,
            }
        finished_at = datetime.now(timezone.utc)

    actual_duration = time.monotonic() - started
    route_results = {
        name: _targeted_stats(
            samples, workload_duration, ROUTE_P95_TARGET_MS[name],
        ) for name, samples in sorted(routes.items())
    }
    workflow_results = {}
    for name, target in P95_TARGET_MS.items():
        workflow_results[name] = _targeted_stats(workflows[name], workload_duration, target)
    all_requests = [sample for samples in routes.values() for sample in samples]
    total = len(all_requests)
    errors = sum(not 200 <= row["status"] < 300 for row in all_requests)
    rate_limited = sum(row["status"] == 429 for row in all_requests)
    degraded = sum(row["degraded"] for row in all_requests)
    error_rate = errors / total if total else 0.0
    status_codes = _route_stats(all_requests)["status_codes"]
    return {
        "meta": {
            "run_id": started_at.strftime("%Y%m%d-%H%M%S"),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(actual_duration, 3),
            "requested_rate_per_hour": args.rate_per_hour,
            "scheduled_http_requests": round(args.rate_per_hour * duration_seconds / 3600),
            "actual_http_requests": request_count,
            "config": {
                "base_url": args.base_url,
                "rate_per_hour": args.rate_per_hour,
                "duration_minutes": args.duration_minutes,
                "patient_limit": args.patient_limit,
                "replicas": args.replicas,
                "concurrency": args.concurrency,
                "timeout_seconds": args.timeout_seconds,
                "seed": args.seed,
                "burst": args.burst,
                "allow_real_model": args.allow_real_model,
                "hoarding_probe": args.hoarding_probe,
            },
            "burst": {"enabled": args.burst, "peak_multiplier": 3 if args.burst else 1,
                      "peak_seconds": burst_seconds},
            "traffic_mix": TRAFFIC_MIX,
            "configured_replicas": args.replicas,
            "observed_replicas": sorted(replicas),
            "model_mode": model_mode,
            "model_latency": stub_provenance,
            "model_latency_note": (
                "LLM calls are stubbed; de-identification, rules, retrieval, database, tracing, "
                "and upload ingestion queue paths remain real."
                if model_mode == "load-test stub" else
                "Live model calls were explicitly permitted with --allow-real-model."
            ),
        },
        "routes": route_results,
        "workflows": workflow_results,
        "summary": {
            "requests": total,
            "error_rate": round(error_rate, 4),
            "error_count": errors,
            "error_rate_limit": ERROR_RATE_LIMIT,
            "error_rate_pass": error_rate <= ERROR_RATE_LIMIT,
            "rate_429": round(rate_limited / total, 4) if total else 0.0,
            "degraded_rate": round(degraded / total, 4) if total else 0.0,
            "requests_per_second": round(total / workload_duration, 4) if total else 0.0,
            "status_codes": status_codes,
        },
        "hoarding_probe": probe_result,
        "status": _load_verdict(workflow_results, route_results, error_rate),
    }


def _markdown(run: dict) -> str:
    meta = run["meta"]
    summary = run["summary"]
    lines = [
        f"# Load test {meta['run_id']}", "",
        f"**Result: {run['status']}**", "",
        (("Model latency is stubbed. Only LLM calls are replaced; de-identification, rules, "
          "retrieval, Postgres, tracing, and the upload queue run normally.")
         if meta["model_mode"] == "load-test stub" else
         "Live model calls were explicitly permitted with `--allow-real-model`."), "",
        (f"Stub latency source: `{json.dumps(meta['model_latency'], sort_keys=True)}`."
         if meta.get("model_latency") else ""),
        "", "## Configuration", "",
        "| Setting | Value |", "|---|---|",
        *[f"| {key} | `{json.dumps(value, sort_keys=True)}` |"
          for key, value in sorted(meta["config"].items())],
        "",
        f"- Duration: {meta['duration_seconds']} s",
        f"- Requested rate: {meta['requested_rate_per_hour']} HTTP requests/hour",
        f"- Actual requests: {summary['requests']}",
        f"- Actual RPS: {summary['requests_per_second']}",
        f"- Errors (all non-2xx, including 429): {summary['error_rate']:.2%} "
        f"({summary['error_count']}/{summary['requests']})",
        f"- Error-rate target: ≤ {summary['error_rate_limit']:.2%} "
        f"({'PASS' if summary['error_rate_pass'] else 'FAIL'})",
        f"- 429 rate: {summary['rate_429']:.2%}",
        f"- Status-code breakdown: `{json.dumps(summary['status_codes'], sort_keys=True)}`",
        f"- Degraded rate: {summary['degraded_rate']:.2%}",
        f"- Replicas observed: {len(meta['observed_replicas'])}/{meta['configured_replicas']} "
        f"({', '.join(meta['observed_replicas']) or 'none'})",
        f"- Burst: {meta['burst']}",
        "",
        "## Workflow p95 targets", "",
        "| Workflow | Requests | p50 ms | p95 ms | p99 ms | Target ms | Result |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for workflow, values in sorted(run["workflows"].items()):
        lines.append(
            f"| {workflow.upper()} | {values['count']} | {values['p50_ms']} | "
            f"{values['p95_ms']} | {values['p99_ms']} | {values['target_ms']} | "
            f"{'PASS' if values['pass'] else 'FAIL'} |"
        )
    lines += [
        "", "## Per-route latency and failures", "",
        "| Endpoint | Requests | RPS | p50 ms | p95 ms | p99 ms | Target ms | Result | Error rate | 429 | Degraded rate |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for route, values in sorted(run["routes"].items()):
        lines.append(
            f"| {route} | {values['count']} | {values['rps']} | "
            f"{values['p50_ms']} | {values['p95_ms']} | {values['p99_ms']} | "
            f"{values['target_ms']} | {'PASS' if values['pass'] else 'FAIL'} | "
            f"{values['error_rate']:.2%} | {values['rate_limited']} | "
            f"{values['degraded_rate']:.2%} |"
        )
    lines += [
        "", "## Per-route status codes and first error bodies", "",
        "| Endpoint | Status-code breakdown | First error body (PHI-redacted) |",
        "|---|---|---|",
    ]
    for route, values in sorted(run["routes"].items()):
        body = (values["first_error_body"] or "—").replace("|", "\\|").replace("`", "\\`")
        lines.append(
            f"| {route} | `{json.dumps(values['status_codes'], sort_keys=True)}` | "
            f"`{body}` |"
        )
    probe = run["hoarding_probe"]
    lines += ["", "## D7 hoarding probe", ""]
    if not probe["enabled"]:
        lines.append("Not run; pass `--hoarding-probe` to opt in.")
    else:
        lines.append(
            f"Result: **{probe['status']}**; expected booking-control 429s: "
            f"{probe['expected_429s']}. Probe traffic is isolated from workload verdict metrics."
        )
        lines += [
            "", "| Endpoint | Status-code breakdown | First error body (PHI-redacted) |",
            "|---|---|---|",
        ]
        for route, values in sorted(probe["routes"].items()):
            body = (values["first_error_body"] or "—").replace("|", "\\|").replace("`", "\\`")
            lines.append(
                f"| {route} | `{json.dumps(values['status_codes'], sort_keys=True)}` | "
                f"`{body}` |"
            )
    return "\n".join(lines) + "\n"


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--rate-per-hour", type=int, default=1400)
    parser.add_argument("--duration-minutes", type=float, default=15)
    parser.add_argument("--patient-limit", type=int, default=1000)
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-burst", dest="burst", action="store_false",
                        help="Disable the default 3x peak for the first two minutes")
    parser.add_argument("--allow-real-model", action="store_true",
                        help="Explicitly permit live model calls; default requires LLM_STUB=1")
    parser.add_argument("--hoarding-probe", action="store_true",
                        help="Run an isolated D7 probe against one patient/IP booking bucket")
    parser.set_defaults(burst=True)
    return parser


def main() -> None:
    parser = _argument_parser()
    args = parser.parse_args()
    if args.rate_per_hour <= 0 or args.duration_minutes <= 0 or args.patient_limit <= 0:
        parser.error("rate, duration, and patient limit must be positive")
    if args.replicas <= 0 or args.concurrency <= 0 or args.timeout_seconds <= 0:
        parser.error("replicas, concurrency, and timeout must be positive")
    if args.duration_minutes * 60 < 1:
        parser.error("duration must be at least one second")

    run = asyncio.run(run_load(args))
    out_dir = ROOT / "evals" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"load-{run['meta']['run_id']}"
    json_path, markdown_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_tmp = json_path.with_suffix(".json.tmp")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_tmp.write_text(_markdown(run), encoding="utf-8")
    json_tmp.replace(json_path)
    markdown_tmp.replace(markdown_path)
    print(f"{run['status']}: {json_path.relative_to(ROOT)}; {markdown_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
