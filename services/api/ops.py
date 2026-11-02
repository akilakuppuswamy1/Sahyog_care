"""Sourced operational metrics and trace-tree formatting."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path

from services.api import frontdesk
from services.agent.llm import breaker_snapshot

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
                                       "provider", "served_by", "tokens_in", "tokens_out", "error_type")
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
