"""The one door to a language model: the egress adapter of ADR-001.

Every model call in every graph goes through `JsonLLM.complete_json`. The graphs never import a
vendor SDK, so swapping OpenRouter for another provider is a change to this file only (E6-2).

On every call (deck p.24): scrub on egress, a timeout, capped retries, a JSON-only contract,
token usage recorded for the cost meter, and a trace id.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from services.agent.w1.guards import has_identifier, scrub

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ModelError(RuntimeError):
    """The model could not produce a valid answer. Callers degrade to a human (deck p.36)."""


class JsonLLM(Protocol):
    def complete_json(self, system: str, user: str, *, purpose: str, trace_id: str) -> dict: ...


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)


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
        req_body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_out}],
        }
        if self.provider_sort:
            req_body["provider"] = {"sort": self.provider_sort}
        body = json.dumps(req_body).encode()
        req = urllib.request.Request(OPENROUTER_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Sahyog Care Companion (academy)",
            "X-Trace-Id": trace_id,
        })
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    payload = json.loads(r.read())
                content = payload["choices"][0]["message"]["content"]
                u = payload.get("usage") or {}
                self.usage.calls += 1
                self.usage.prompt_tokens += int(u.get("prompt_tokens", 0))
                self.usage.completion_tokens += int(u.get("completion_tokens", 0))
                self.usage.by_purpose[purpose] = self.usage.by_purpose.get(purpose, 0) + int(u.get("total_tokens", 0))
                return _extract_json(content)
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in (429, 500, 502, 503, 504):
                    raise ModelError(f"OpenRouter HTTP {e.code}: {e.read()[:200]!r}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ModelError) as e:
                last = e
            time.sleep(min(4.0, 0.5 * 2 ** attempt))   # capped exponential backoff
        raise ModelError(f"model call failed after {self.max_retries + 1} attempts: {last}")


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
