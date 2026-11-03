from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfReader

import pytest
import yaml

from tools.load_test import (
    _argument_parser,
    _load_subjects,
    _model_mode,
    _route_stats,
    _safe_error_body,
    _synthetic_pdf,
    _synthetic_client_ip,
    _targeted_stats,
    _load_verdict,
)
from tools.seed_load_test import seed_load_test_encounters, seed_load_test_patients


def test_synthetic_upload_pdf_is_parseable_and_has_no_patient_identity():
    reader = PdfReader(BytesIO(_synthetic_pdf()))
    assert "Synthetic load test report" in reader.pages[0].extract_text()


def test_route_metrics_report_errors_429_and_degraded_rates():
    stats = _route_stats([
        {"status": 200, "latency_ms": 10, "degraded": False},
        {"status": 429, "latency_ms": 20, "degraded": True},
        {"status": 500, "latency_ms": 30, "degraded": False},
    ], duration_seconds=2)
    assert stats["count"] == 3
    assert stats["p50_ms"] == 20
    assert stats["errors"] == 2 and stats["rate_limited"] == 1
    assert stats["error_rate"] == 0.6667 and stats["degraded_rate"] == 0.3333
    assert stats["rps"] == 1.5
    assert stats["status_codes"] == {"200": 1, "429": 1, "500": 1}
    assert stats["first_error_body"] is None


def test_error_body_reporting_redacts_non_generic_details():
    class FakeResponse:
        def json(self):
            return {"detail": "Patient LOAD-PT-000001: Asha Sharma"}

    assert _safe_error_body(FakeResponse()) == (
        '{"detail": "[non-generic error detail redacted]"}'
    )


def test_real_model_mode_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="--allow-real-model"):
        _model_mode([], allow_real_model=False)

    assert _model_mode([], allow_real_model=True) == (
        "real model (explicitly allowed)", None,
    )


def test_stub_model_mode_requires_latency_provenance():
    with pytest.raises(RuntimeError, match="latency provenance"):
        _model_mode([{"id": "model-mode", "value": "load-test stub"}],
                    allow_real_model=False)


def test_default_load_profile_has_two_replicas_and_three_x_burst():
    parser = _argument_parser()
    defaults = parser.parse_args([])
    assert defaults.rate_per_hour == 1400
    assert defaults.patient_limit == 1000
    assert defaults.replicas == 2
    assert defaults.burst is True
    assert defaults.hoarding_probe is False
    assert parser.parse_args(["--hoarding-probe"]).hoarding_probe is True
    assert parser.parse_args(["--no-burst"]).burst is False


def test_endpoint_p95_is_compared_to_its_target():
    samples = [{"status": 200, "latency_ms": 100, "degraded": False}]
    assert _targeted_stats(samples, 2, 101)["pass"] is True
    assert _targeted_stats(samples, 2, 100)["pass"] is False


def test_load_verdict_requires_p95_and_error_rate_at_or_below_one_percent():
    passing = {"pass": True}
    failing = {"pass": False}
    assert _load_verdict({"w1": passing}, {"route": passing}, 0.01) == "PASS"
    assert _load_verdict({"w1": passing}, {"route": passing}, 0.0101) == "FAIL"
    assert _load_verdict({"w1": failing}, {"route": passing}, 0.0) == "FAIL"


def test_synthetic_client_ip_pool_is_distinct():
    assert len({_synthetic_client_ip(index) for index in range(1000)}) == 1000


def test_compose_aliases_all_have_anchors():
    compose_path = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
    source = compose_path.read_text(encoding="utf-8")
    tokens = list(yaml.scan(source))
    anchors = {
        token.value for token in tokens if isinstance(token, yaml.tokens.AnchorToken)
    }
    aliases = [
        token.value for token in tokens if isinstance(token, yaml.tokens.AliasToken)
    ]

    assert aliases
    assert set(aliases) <= anchors
    compose = yaml.safe_load(source)
    assert compose
    assert compose["services"]["api1"]["environment"]["FORWARDED_ALLOW_IPS"] == "172.30.240.10"
    assert compose["services"]["api2"]["environment"]["FORWARDED_ALLOW_IPS"] == "172.30.240.10"
    assert compose["services"]["api"]["environment"]["FORWARDED_ALLOW_IPS"] == ""
    assert "127.0.0.1:" in compose["services"]["nginx"]["ports"][0]
    nginx = (compose_path.parent / "nginx-scale.conf").read_text(encoding="utf-8")
    assert "real_ip_header X-Forwarded-For;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx


def test_missing_encounters_recommends_the_seed_command(monkeypatch):
    class FakeConnection:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _params):
            self.calls += 1
            rows = [("PT-W2-0001",)] if self.calls < 3 else []
            return SimpleNamespace(fetchall=lambda: rows)

    connection = FakeConnection()
    monkeypatch.setattr("tools.load_test.psycopg.connect", lambda _url: connection)

    with pytest.raises(RuntimeError, match=r"python tools/seed_load_test\.py"):
        _load_subjects("postgresql://unused", 10)


def test_load_test_encounter_seed_uses_existing_rows_and_is_idempotent():
    class FakeConnection:
        def __init__(self):
            self.encounters = set()

        def execute(self, query, params=None):
            if query.startswith("SELECT p.patient_id"):
                return SimpleNamespace(fetchall=lambda: [("PT-W2-0001",), ("PT-W2-0002",)])
            if query.startswith("SELECT doctor_id"):
                return SimpleNamespace(fetchall=lambda: [("DR0001", "C1"), ("DR0002", "C2")])
            if query.startswith("SELECT e.encounter_id"):
                return SimpleNamespace(fetchall=lambda: [(item, True) for item in self.encounters])
            encounter_id = params[0]
            rowcount = int(encounter_id not in self.encounters)
            self.encounters.add(encounter_id)
            assert "ON CONFLICT (encounter_id) DO NOTHING" in query
            return SimpleNamespace(rowcount=rowcount)

    connection = FakeConnection()

    assert seed_load_test_encounters(connection, count=4) == 4
    assert seed_load_test_encounters(connection, count=4) == 0


def test_load_patient_seed_is_idempotent():
    class FakeConnection:
        def __init__(self):
            self.patients = set()

        def execute(self, query, params):
            patient_id = params[0]
            inserted = patient_id not in self.patients
            self.patients.add(patient_id)
            assert "ON CONFLICT (patient_id) DO NOTHING" in query
            assert params[1].startswith("Synthetic Load Patient ")
            return SimpleNamespace(rowcount=int(inserted))

    connection = FakeConnection()
    assert seed_load_test_patients(connection, count=3) == 3
    assert seed_load_test_patients(connection, count=3) == 0
