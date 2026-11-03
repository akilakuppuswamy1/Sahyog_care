"""Sourced operational metrics and trace-tree formatting."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from statistics import median

from services.api import frontdesk
from services.agent.llm import breaker_snapshot, load_test_stub_provenance

ROOT = Path(__file__).resolve().parents[2]


def _read_spans(deps, *, since: datetime | None = None, trace_id: str | None = None):
    tracer = getattr(deps, "tracer", None)
    exporter = getattr(tracer, "exporter", None)
    reader = getattr(exporter, "read", None)
    if reader is None:
        return None
    result = reader(trace_id=trace_id, since=since)
    return [asdict(span) if is_dataclass(span) else dict(span) for span in result]


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.95
    lower = math.floor(position)
    upper = math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _price(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _latest_eval(workflow: str, metric: str, runs_dir: Path) -> tuple[object, str] | None:
    latest = None
    for path in runs_dir.glob(f"{workflow}-*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("meta", {}).get("mode") == "rules":
            continue
        run_id = run.get("meta", {}).get("run_id", path.stem)
        if latest is None or run_id > latest[0]:
            if metric in run.get("metrics", {}):
                latest = (run_id, run["metrics"][metric], path.name)
    if latest is None:
        return None
    return latest[1], f"evals/runs/{latest[2]} ({latest[0]})"


def _latest_load_run(runs_dir: Path) -> tuple[dict, str] | None:
    candidates = []
    for path in runs_dir.glob("load-*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        run_id = run.get("meta", {}).get("run_id", path.stem)
        candidates.append((run_id, path, run))
    if not candidates:
        return None
    _, path, run = max(candidates, key=lambda item: (item[0], item[1].name))
    return run, f"evals/runs/{path.name}"


def _add_tile(tiles: list[dict], tile_id: str, label: str, value: object,
              source: str | None) -> None:
    if value is not None and source:
        tiles.append({"id": tile_id, "label": label, "value": value, "source": source})


def _ops_sla_minutes() -> float:
    raw = os.environ.get("OPS_REVIEW_SLA_MINUTES",
                         os.environ.get("W1_FRONT_DESK_SLA_MINUTES", "30"))
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("OPS_REVIEW_SLA_MINUTES must be a positive number")
    return value


def build_ops_summary(deps, runs_dir: Path | None = None) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    spans = _read_spans(deps, since=since)
    tiles: list[dict] = []
    if os.environ.get("LLM_STUB") == "1":
        _add_tile(tiles, "model-mode", "model mode", "load-test stub",
                  "LLM_STUB=1; SAHYOG_DEMO_AUTH=1")
        _add_tile(tiles, "stub-latency-source", "load-test stub latency source",
                  load_test_stub_provenance(), "saved eval runs; LLM_STUB_LATENCY_MS override if set")
    conn = getattr(getattr(deps, "store", None), "conn", None)
    if conn is not None and conn.execute("SELECT to_regclass('ingest_job')").fetchone()[0]:
        count, depth, age = conn.execute("""SELECT count(*),count(*) FILTER (WHERE status='queued'),
            extract(epoch FROM now()-min(created_at) FILTER (WHERE status='queued')) FROM ingest_job""").fetchone()
        if count:
            _add_tile(tiles, "ingest-queue", "ingest queue", {"depth": depth,
                      "oldest_queued_age_seconds": float(age) if age is not None else None}, "ingest_job")
    if spans is not None:
        for workflow, span_name, tile_id, label in (
            ("w1", "triage_turn", "triage-p95", "p95 triage turn"),
            ("w2", "record_answer", "record-p95", "p95 record answer"),
        ):
            matches = [span for span in spans if span["workflow"] == workflow
                       and span["name"] == span_name and span["started_at"] >= since]
            p95 = _percentile_95([float(span["duration_ms"]) for span in matches])
            if p95 is not None:
                _add_tile(tiles, tile_id, label, {"p95_ms": p95, "count": len(matches)},
                          f"trace_span {workflow}/{span_name}, last 24 h")

        measured = [span for span in spans if span["name"] in ("triage_turn", "record_answer")
                    and span["workflow"] in ("w1", "w2")]
        if measured:
            rate = sum(span["status"] == "degraded" for span in measured) / len(measured)
            _add_tile(tiles, "degraded-rate", "degraded rate", round(rate, 4),
                      "trace_span triage_turn and record_answer, last 24 h")

        _add_tile(tiles, "breaker-state", "model breaker state", breaker_snapshot(),
                  "in-process OpenRouter circuit breaker")

        model_spans = [span for span in spans if span["kind"] == "model" and span["workflow"] in ("w1", "w2")]
        workflows = {span["workflow"] for span in model_spans}
        prices = {
            "w1": (_price("PRICE_IN_PER_MTOK_W1"), _price("PRICE_OUT_PER_MTOK_W1")),
            "w2": (_price("PRICE_IN_PER_MTOK_W2"), _price("PRICE_OUT_PER_MTOK_W2")),
        }
        metered = all(span.get("tokens_in") is not None and span.get("tokens_out") is not None
                      for span in model_spans)
        if model_spans and metered and all(all(price is not None for price in prices[workflow])
                                           for workflow in workflows):
            costs: dict[str, float] = {}
            for span in model_spans:
                pin, pout = prices[span["workflow"]]
                cost = ((span.get("tokens_in") or 0) * pin + (span.get("tokens_out") or 0) * pout) / 1_000_000
                costs[span["trace_id"]] = costs.get(span["trace_id"], 0) + cost
            average = sum(costs.values()) / len(costs)
            _add_tile(tiles, "cost-per-run", "cost per run", {
                "average": round(average, 8), "runs": len(costs),
                "price_date": os.environ.get("PRICE_DATE") or "to fill",
            }, "trace_span model token counts x workflow PRICE_*_PER_MTOK")

        by_trace: dict[str, list[dict]] = {}
        for span in spans:
            if span["workflow"] == "w1" and span["kind"] == "node":
                by_trace.setdefault(span["trace_id"], []).append(span)
        waits = []
        for trace_spans in by_trace.values():
            picks = sorted((span for span in trace_spans if span["name"] == "present_and_pick"),
                           key=lambda span: span["started_at"])
            if len(picks) > 1:
                start = picks[0]["started_at"] + timedelta(milliseconds=float(picks[0]["duration_ms"]))
                waits.append(max(0.0, (picks[1]["started_at"] - start).total_seconds() * 1000))
        wait_p95 = _percentile_95(waits)
        if wait_p95 is not None:
            _add_tile(tiles, "pick-slot-wait", "pick_slot interrupt wait p95",
                      {"p95_ms": wait_p95, "count": len(waits)},
                      "span gap: first and resumed present_and_pick nodes, last 24 h")

    eval_dir = runs_dir or ROOT / "evals" / "runs"
    latest_load = _latest_load_run(eval_dir)
    if latest_load:
        load_run, source = latest_load
        summary = load_run.get("summary", {})
        meta = load_run.get("meta", {})
        observed = meta.get("observed_replicas", [])
        _add_tile(tiles, "latest-load-test", "Latest load test", {
            "run_id": meta.get("run_id"),
            "status": load_run.get("status", "not recorded"),
            "requests": summary.get("requests"),
            "requests_per_second": summary.get("requests_per_second"),
            "error_rate": summary.get("error_rate"),
            "rate_429": summary.get("rate_429"),
            "replicas": {"configured": meta.get("configured_replicas"),
                         "observed": len(observed), "ids": observed},
        }, source)
        for workflow, values in load_run.get("workflows", {}).items():
            if workflow not in {"w1", "w2", "w3"}:
                continue
            _add_tile(tiles, f"load-{workflow}-p95", f"latest load {workflow.upper()} p95",
                      {"p95_s": round(float(values["p95_ms"]) / 1000, 3)
                       if values.get("p95_ms") is not None else None,
                       "target_s": float(values["target_ms"]) / 1000,
                       "pass": bool(values["pass"]), "count": values.get("count", 0)}, source)
        for endpoint, values in load_run.get("routes", {}).items():
            endpoint_id = (endpoint.lower().replace(" /", "-").replace("/", "-")
                           .replace("{", "").replace("}", "").replace("_", "-")
                           .replace(" ", "-"))
            _add_tile(tiles, f"load-endpoint-{endpoint_id}",
                      f"latest load {endpoint} p95", {
                          "run_id": meta.get("run_id"),
                          "p95_ms": values.get("p95_ms"),
                          "target_ms": values.get("target_ms"),
                          "pass": values.get("pass"),
                          "count": values.get("count", 0),
                          "rps": values.get("rps"),
                      }, source)
        _add_tile(tiles, "load-errors", "latest load errors", {
            "error_rate": summary.get("error_rate"),
            "rate_429": summary.get("rate_429"),
            "degraded_rate": summary.get("degraded_rate"),
        }, source)
        _add_tile(tiles, "load-replicas", "latest load replicas", {
            "configured": meta.get("configured_replicas"),
            "observed": len(observed),
            "ids": observed,
        }, source)

    red_flag = _latest_eval("w1", "red_flag_recall", eval_dir)
    if red_flag:
        _add_tile(tiles, "red-flag-recall", "red-flag recall", red_flag[0], red_flag[1])
    scope_leaks = _latest_eval("w2", "scope_leaks", eval_dir)
    if scope_leaks:
        _add_tile(tiles, "scope-leaks", "scope leaks", scope_leaks[0], scope_leaks[1])

    open_escalations = frontdesk.escalations(deps, "open")
    sla_minutes = _ops_sla_minutes()
    ages = []
    for row in open_escalations:
        at = row.get("at")
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        ages.append(max(0.0, (now - at).total_seconds() / 60))
    _add_tile(tiles, "review-queue", "open review queue", {
        "open": len(open_escalations),
        "oldest_age_minutes": round(max(ages), 1) if ages else "not measured",
        "sla_minutes": sla_minutes,
        "over_sla": sum(age > sla_minutes for age in ages) if ages else "not measured",
    }, "audit_event escalations minus frontdesk_handling; OPS_REVIEW_SLA_MINUTES")

    w3_store = getattr(deps, "w3_store", None)
    if w3_store is not None:
        signed = w3_store.signed_note_metrics()
        review_seconds = [float(row["review_ms"]) / 1000 for row in signed
                          if row.get("review_ms") is not None]
        if review_seconds:
            p95 = _percentile_95(review_seconds)
            _add_tile(tiles, "scribe-review-time", "review per note", {
                "median_s": round(median(review_seconds), 3),
                "p95_s": p95,
                "count": len(review_seconds),
                "target_s": 90,
            }, "note_draft.review_started_at to clinical_note.signed_at, signed notes")
        distances = [int(row["edit_distance"]) for row in signed
                     if row.get("edit_distance") is not None]
        if distances:
            _add_tile(tiles, "scribe-edit-distance", "edit distance draft to signed", {
                "median_characters": median(distances),
                "p95_characters": _percentile_95([float(value) for value in distances]),
                "count": len(distances),
            }, "note_draft.initial_soap_json to signed clinical_note.soap_json")

    return {"generated_at": now.isoformat(), "tiles": tiles}


def build_trace_tree(deps, trace_id: str) -> dict | None:
    spans = _read_spans(deps, trace_id=trace_id)
    if not spans:
        return None
    by_id = {}
    for span in spans:
        by_id[span["span_id"]] = {
            key: span[key] for key in ("trace_id", "span_id", "parent_span_id", "workflow", "name",
                                       "kind", "started_at", "duration_ms", "status", "model",
                                       "provider", "served_by", "tokens_in", "tokens_out", "error_type", "deid_ms")
        }
        by_id[span["span_id"]]["children"] = []
    roots = []
    for span in sorted(spans, key=lambda item: item["started_at"]):
        node = by_id[span["span_id"]]
        parent = by_id.get(span["parent_span_id"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return {"trace_id": trace_id, "spans": roots}
