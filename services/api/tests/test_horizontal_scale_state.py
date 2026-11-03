import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from services.api.deps import ReplayRequests
from services.api.ratelimit import RateLimiter


@pytest.fixture
def shared_replica_pools():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")

    schema = f"scale_test_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        url = make_conninfo(dsn, options=f"-csearch_path={schema},public")
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                conn.execute(
                    """CREATE TABLE w1_request (
                           user_id text, idempotency_key text, thread_id text,
                           fingerprint text
                       )"""
                )
                migration = (
                    Path(__file__).resolve().parents[3]
                    / "infra" / "db" / "014_horizontal_api_state.sql"
                )
                conn.execute(migration.read_text(encoding="utf-8"))
            pools = [
                ConnectionPool(
                    conninfo=url, min_size=1, max_size=2, open=True,
                    kwargs={"autocommit": True, "row_factory": dict_row},
                )
                for _ in range(2)
            ]
            yield pools
        finally:
            for pool in locals().get("pools", []):
                pool.close()
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_postgres_replay_rate_limit_and_expired_lease_are_shared_across_replicas(
    shared_replica_pools, monkeypatch,
):
    pool_a, pool_b = shared_replica_pools
    replay_a, replay_b = ReplayRequests(pool_a), ReplayRequests(pool_b)

    monkeypatch.setenv("RL_READ_PER_MIN", "2")
    limiter_a, limiter_b = RateLimiter(), RateLimiter()
    assert limiter_a.check("PT-SHARED", "198.51.100.32", "read", pool=pool_a) == 0
    assert limiter_b.check("PT-SHARED", "198.51.100.32", "read", pool=pool_b) == 0
    assert limiter_a.check("PT-SHARED", "198.51.100.32", "read", pool=pool_a) == 30

    response = {"thread_id": "thread-shared", "booking_id": "booking-shared"}
    _, claimed = replay_a.claim(
        "w1_resume", "PT-SHARED", "booking-key-shared",
        "thread-shared", "fingerprint-shared",
    )
    assert claimed
    replay_a.complete("w1_resume", "PT-SHARED", "booking-key-shared", response)
    stored, claimed = replay_b.claim(
        "w1_resume", "PT-SHARED", "booking-key-shared",
        "thread-shared", "fingerprint-shared",
    )
    assert not claimed
    assert stored["response"] == response

    lease_key = "expired-key-shared"
    identity, fingerprint = "thread-expired", "fingerprint-expired"
    _, claimed = replay_a.claim("w1_resume", "PT-SHARED", lease_key, identity, fingerprint)
    assert claimed
    with pool_a.connection() as conn:
        conn.execute(
            """UPDATE api_replay_request SET lease_until = now() - interval '1 second'
                 WHERE action = 'w1_resume' AND owner_id = %s AND idempotency_key = %s""",
            ("PT-SHARED", lease_key),
        )

    _, claimed = replay_b.claim(
        "w1_resume", "PT-SHARED", lease_key, "different-thread", fingerprint,
    )
    assert not claimed
    _, claimed = replay_b.claim(
        "w1_resume", "PT-SHARED", lease_key, identity, "different-fingerprint",
    )
    assert not claimed
    reclaimed, claimed = replay_b.claim("w1_resume", "PT-SHARED", lease_key, identity, fingerprint)
    assert claimed and reclaimed["status"] == "running"
