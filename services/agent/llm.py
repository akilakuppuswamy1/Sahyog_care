"""The one door to a language model: the egress adapter of ADR-001.

Every model call in every graph goes through `JsonLLM.complete_json`. The graphs never import a
vendor SDK, so swapping OpenRouter for another provider is a change to this file only (E6-2).

On every call (deck p.24): scrub on egress, a timeout, capped retries, a JSON-only contract,
token usage recorded for the cost meter, and a trace id.
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from services.agent.w1.guards import has_identifier, scrub

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ModelError(RuntimeError):
    """The model could not produce a valid answer. Callers degrade to a human (deck p.36)."""


class ModelUnavailable(ModelError):
    """A model call could not complete within its deadline or is circuit-broken."""


class _CallCancelled(Exception):
    pass


class JsonLLM(Protocol):
    def complete_json(self, system: str, user: str, *, purpose: str, trace_id: str) -> dict: ...


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)
    call_records: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass
class _Circuit:
    failures: deque = field(default_factory=deque)
    open_until: float = 0.0
    probe_in_flight: bool = False


@dataclass
class _RequestSlots:
    trace_id: str
    semaphore: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(2))
    references: int = 0


_CIRCUITS: dict[tuple[str, str], _Circuit] = {}
_CIRCUITS_LOCK = threading.Lock()
_CALLS = ThreadPoolExecutor(max_workers=32, thread_name_prefix="llm")
_REQUEST_SLOTS: dict[str, _RequestSlots] = {}
_REQUEST_SLOTS_LOCK = threading.Lock()


def _retain_request_slots(trace_id: str) -> _RequestSlots:
    with _REQUEST_SLOTS_LOCK:
        slots = _REQUEST_SLOTS.setdefault(trace_id, _RequestSlots(trace_id))
        slots.references += 1
        return slots


def _release_request_slots(slots: _RequestSlots) -> None:
    with _REQUEST_SLOTS_LOCK:
        slots.references -= 1
        if slots.references == 0 and _REQUEST_SLOTS.get(slots.trace_id) is slots:
            del _REQUEST_SLOTS[slots.trace_id]


def _acquire_call_slot(slots: _RequestSlots, timeout: float, cancelled: threading.Event) -> bool:
    deadline = time.monotonic() + timeout
    while not cancelled.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if slots.semaphore.acquire(timeout=min(remaining, 0.05)):
            if cancelled.is_set():
                slots.semaphore.release()
                return False
            return True
    return False


def _circuit_enter(key: tuple[str, str]) -> bool:
    now = time.monotonic()
    with _CIRCUITS_LOCK:
        circuit = _CIRCUITS.setdefault(key, _Circuit())
        while circuit.failures and now - circuit.failures[0] > 60:
            circuit.failures.popleft()
        if circuit.open_until:
            if now < circuit.open_until:
                raise ModelUnavailable(f"circuit open for {key[0]} model {key[1]}")
            if circuit.probe_in_flight:
                raise ModelUnavailable(f"circuit probe already in flight for {key[0]} model {key[1]}")
            circuit.probe_in_flight = True
            return True
        return False


def _circuit_preflight(key: tuple[str, str]) -> None:
    now = time.monotonic()
    with _CIRCUITS_LOCK:
        circuit = _CIRCUITS.get(key)
        if circuit and circuit.open_until and (now < circuit.open_until or circuit.probe_in_flight):
            raise ModelUnavailable(f"circuit open for {key[0]} model {key[1]}")


def _circuit_finish(key: tuple[str, str], probe: bool, success: bool) -> None:
    now = time.monotonic()
    with _CIRCUITS_LOCK:
        circuit = _CIRCUITS.setdefault(key, _Circuit())
        if probe:
            circuit.probe_in_flight = False
            if success:
                circuit.failures.clear()
                circuit.open_until = 0.0
            else:
                circuit.open_until = now + 30
            return
        if success:
            return
        circuit.failures.append(now)
        while circuit.failures and now - circuit.failures[0] > 60:
            circuit.failures.popleft()
        if len(circuit.failures) >= 5:
            circuit.open_until = now + 30


def _reset_circuit_breakers() -> None:
    with _CIRCUITS_LOCK:
        _CIRCUITS.clear()


def _env_seconds(name: str, default: float, *, allow_zero: bool = False) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw is not None and raw.strip() else default
    except ValueError as exc:
        raise ModelError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ModelError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _workflow_seconds(name: str, workflow: str, default: float, *, allow_zero: bool = False) -> float:
    return _env_seconds(f"{name}_{workflow.upper()}", default, allow_zero=allow_zero)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ModelError(f"no JSON object in model output: {text[:120]!r}")
    return json.loads(text[start:end + 1])


@dataclass
class OpenRouterLLM:
    """OpenRouter's OpenAI-compatible chat API. Reads OPENROUTER_API_KEY and OPENROUTER_MODEL."""
    model: str = field(default_factory=lambda: os.environ.get("OPENROUTER_MODEL", ""))
    workflow: str = ""      # "w1" | "w2" | "w3": OPENROUTER_MODEL_<WORKFLOW> overrides OPENROUTER_MODEL (routing, p.36)
    api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""), repr=False)
    provider_sort: str = field(default_factory=lambda: os.environ.get("OPENROUTER_PROVIDER_SORT", ""))  # "latency" | "throughput" | "price"
    max_tokens: int = 300
    timeout_s: float = 15.0
    max_retries: int = 2
    usage: Usage = field(default_factory=Usage)
    _usage_record_ids: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.workflow:
            self.model = os.environ.get(f"OPENROUTER_MODEL_{self.workflow.upper()}") or self.model
        if not self.api_key:
            raise ModelError("OPENROUTER_API_KEY is not set (put it in .env, never in code)")
        if not self.model:
            raise ModelError("OPENROUTER_MODEL is not set, e.g. a small fast model id from openrouter.ai/models")

    def complete_json(self, system: str, user: str, *, purpose: str, trace_id: str) -> dict:
        user_out, _token_map = scrub(user)          # token map stays here, in-region
        if has_identifier(user_out):                 # defence in depth: never send if a pattern survives
            raise ModelError("identifier survived scrubbing; refusing to send")
        workflow = (self.workflow or "w1").lower()
        deadline_s = _workflow_seconds("LLM_DEADLINE_S", workflow, 3.5 if workflow == "w1" else 6.0)
        hedge_after_s = _env_seconds("LLM_HEDGE_AFTER_S", 1.8 if workflow == "w1" else 3.0, allow_zero=True)
        _circuit_preflight((workflow, self.model))
        deadline = time.monotonic() + deadline_s
        fallback_model = os.environ.get(f"OPENROUTER_FALLBACK_MODEL_{workflow.upper()}") or self.model
        slots = _retain_request_slots(trace_id)
        try:
            return self._complete_with_hedge(system, user_out, purpose, trace_id, deadline_s, deadline,
                                             hedge_after_s, fallback_model, slots)
        finally:
            _release_request_slots(slots)

    def _complete_with_hedge(self, system: str, user: str, purpose: str, trace_id: str, deadline_s: float,
                             deadline: float, hedge_after_s: float, fallback_model: str,
                             slots: _RequestSlots) -> dict:
        fallback_provider_fallbacks = True
        primary_provider_fallbacks = False
        primary_cancel = threading.Event()
        futures: dict[Future, str] = {
            self._submit_model(slots, self.model, user, system, purpose, trace_id, deadline,
                               "primary", primary_provider_fallbacks, primary_cancel): "primary"
        }
        cancelled: dict[Future, threading.Event] = {future: primary_cancel for future in futures}
        hedge_at = min(deadline, time.monotonic() + hedge_after_s)
        last_error: Exception | None = None
        fallback_started = False

        while True:
            now = time.monotonic()
            if now >= deadline:
                for future in futures:
                    cancelled[future].set()
                    future.cancel()
                raise ModelUnavailable(f"model call exceeded {deadline_s:g}s deadline")
            until_hedge = max(0.0, hedge_at - now) if not fallback_started else None
            done, _ = wait(tuple(futures), timeout=until_hedge if until_hedge is not None else deadline - now,
                           return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    result, record_id = future.result()
                except (ModelError, TimeoutError, socket.timeout) as exc:
                    last_error = exc
                    continue
                with self.usage._lock:
                    for record in self.usage.call_records:
                        if record["id"] == record_id:
                            record["selected"] = True
                            break
                for other in futures:
                    if other is not future:
                        cancelled[other].set()
                        other.cancel()
                return result

            primary_done = any(role == "primary" and future.done() for future, role in futures.items())
            if not fallback_started and (primary_done or time.monotonic() >= hedge_at):
                role = "fallback" if primary_done else "hedge"
                hedge_cancel = threading.Event()
                future = self._submit_model(slots, fallback_model, user, system, purpose, trace_id,
                                            deadline, role, fallback_provider_fallbacks, hedge_cancel)
                futures[future] = role
                cancelled[future] = hedge_cancel
                fallback_started = True
                continue
            if fallback_started and all(future.done() for future in futures) and all(future in done for future in futures):
                detail = f": {last_error}" if last_error else ""
                raise ModelUnavailable(f"all model calls failed{detail}")

    def _submit_model(self, slots: _RequestSlots, model: str, user: str, system: str, purpose: str,
                      trace_id: str, deadline: float, served_by: str, allow_fallbacks: bool,
                      cancelled: threading.Event) -> Future:
        with _REQUEST_SLOTS_LOCK:
            slots.references += 1

        def run():
            try:
                return self._run_model(model, user, system, purpose, trace_id, deadline,
                                       served_by, allow_fallbacks, slots, cancelled)
            finally:
                _release_request_slots(slots)

        future = _CALLS.submit(run)
        future.add_done_callback(lambda completed: _release_request_slots(slots) if completed.cancelled() else None)
        return future

    def _run_model(self, model: str, user: str, system: str, purpose: str, trace_id: str,
                   deadline: float, served_by: str, allow_fallbacks: bool,
                   slots: _RequestSlots, cancelled: threading.Event) -> tuple[dict, int]:
        key = ((self.workflow or "w1").lower(), model)
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelUnavailable("model call deadline expired")
            req_body = {
                "model": model, "temperature": 0, "max_tokens": self.max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "provider": {"allow_fallbacks": allow_fallbacks},
            }
            if self.provider_sort:
                req_body["provider"]["sort"] = self.provider_sort
            req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(req_body).encode(), method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "X-Title": "Sahyog Care Companion (academy)", "X-Trace-Id": trace_id,
            })
            if not _acquire_call_slot(slots, remaining, cancelled):
                if cancelled.is_set():
                    raise _CallCancelled()
                raise ModelUnavailable("request model-call limit reached before deadline")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                slots.semaphore.release()
                raise ModelUnavailable("model call deadline expired")
            try:
                probe = _circuit_enter(key)
            except ModelUnavailable:
                slots.semaphore.release()
                raise
            with self.usage._lock:
                self._usage_record_ids += 1
                record_id = self._usage_record_ids
                self.usage.calls += 1
                self.usage.call_records.append({
                    "id": record_id, "workflow": key[0], "model": model, "provider": None,
                    "served_by": served_by, "purpose": purpose, "trace_id": trace_id,
                    "success": False, "selected": False,
                })
            try:
                request_timeout = max(0.001, min(self.timeout_s, deadline - time.monotonic()))
                with urllib.request.urlopen(req, timeout=request_timeout) as response:
                    payload = json.loads(response.read())
                    response_provider = response.headers.get("x-openrouter-provider")
                content = payload["choices"][0]["message"]["content"]
                result = _extract_json(content)
                usage = payload.get("usage") or {}
                provider = payload.get("provider") or payload.get("provider_name") or response_provider
                with self.usage._lock:
                    self.usage.prompt_tokens += int(usage.get("prompt_tokens", 0))
                    self.usage.completion_tokens += int(usage.get("completion_tokens", 0))
                    self.usage.by_purpose[purpose] = self.usage.by_purpose.get(purpose, 0) + int(usage.get("total_tokens", 0))
                    record = next(item for item in self.usage.call_records if item["id"] == record_id)
                    record.update(provider=provider, success=True)
                _circuit_finish(key, probe, True)
                return result, record_id
            except urllib.error.HTTPError as exc:
                last = exc
                try:
                    detail = exc.read()[:200]
                except OSError:
                    detail = b""
                if exc.code not in (429, 500, 502, 503, 504):
                    _circuit_finish(key, probe, False)
                    raise ModelUnavailable(f"OpenRouter HTTP {exc.code}: {detail!r}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError,
                    KeyError, TypeError, ModelError) as exc:
                last = exc
            finally:
                slots.semaphore.release()
            _circuit_finish(key, probe, False)
            remaining = deadline - time.monotonic()
            if attempt < self.max_retries and remaining > 0:
                time.sleep(min(4.0, 0.5 * 2 ** attempt, remaining))
        raise ModelUnavailable(f"model call failed after {self.max_retries + 1} attempts: {last}") from last


@dataclass
class ScriptedLLM:
    """Test double: returns canned JSON per purpose. Records what it was sent, so tests can assert
    that nothing identifying left the boundary."""
    responses: dict[str, dict | Exception | list]   # a list is consumed one item per call
    sent: list[tuple[str, str]] = field(default_factory=list)

    def complete_json(self, system: str, user: str, *, purpose: str, trace_id: str) -> dict:
        user_out, _ = scrub(user)
        self.sent.append((purpose, user_out))
        r = self.responses.get(purpose)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise ModelError(f"no scripted response for {purpose}")
        return dict(r)
