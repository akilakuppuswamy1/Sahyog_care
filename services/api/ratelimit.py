"""Atomic token buckets shared by replicas through Postgres."""
import hashlib
import math
import os
import time
from threading import Lock


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.limits = {"read": (int(os.getenv("RL_READ_PER_MIN", "30")), 60),
                       "book": (int(os.getenv("RL_BOOK_PER_HOUR", "20")), 3600)}
        if any(capacity <= 0 for capacity, _ in self.limits.values()):
            raise ValueError("Rate limits must be positive")
        self.buckets = {}
        self.lock = Lock()

    @staticmethod
    def _bucket_id(subject, kind):
        return hashlib.sha256(f"{kind}\0{subject}".encode("utf-8")).hexdigest()

    def check(self, patient, ip, kind, pool=None):
        """Consume patient and address buckets atomically; return retry seconds."""
        capacity, period = self.limits[kind]
        if pool is not None:
            return self._check_postgres(pool, patient, ip, kind, capacity, period)
        with self.lock:
            now = self.clock()
            keys = [("patient", patient, kind), ("ip", ip, kind)]
            balances = []
            for key in keys:
                tokens, at = self.buckets.get(key, (capacity, now))
                balances.append(min(capacity, tokens + (now - at) * capacity / period))
            retry = max((math.ceil((1 - tokens) * period / capacity)
                         for tokens in balances if tokens < 1), default=0)
            for key, tokens in zip(keys, balances):
                self.buckets[key] = (tokens if retry else tokens - 1, now)
            return retry

    def _check_postgres(self, pool, patient, ip, kind, capacity, period):
        keys = sorted((self._bucket_id(patient, f"{kind}:patient"),
                       self._bucket_id(ip, f"{kind}:ip")))
        with pool.connection() as conn:
            with conn.transaction():
                for bucket_id in keys:
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 731))",
                        (bucket_id,),
                    )
                rows = conn.execute(
                    """SELECT bucket_id, tokens,
                              extract(epoch FROM now() - updated_at) AS elapsed
                         FROM api_rate_bucket WHERE bucket_id = ANY(%s)""",
                    (keys,),
                ).fetchall()
                state = {row["bucket_id"]: row for row in rows}
                balances = []
                for bucket_id in keys:
                    row = state.get(bucket_id)
                    if row is None:
                        balances.append((bucket_id, float(capacity)))
                    else:
                        refill = max(0.0, float(row["elapsed"])) * capacity / period
                        balances.append((bucket_id, min(capacity, float(row["tokens"]) + refill)))
                retry = max(
                    (math.ceil((1 - tokens) * period / capacity)
                     for _, tokens in balances if tokens < 1),
                    default=0,
                )
                for bucket_id, tokens in balances:
                    remaining = tokens if retry else tokens - 1
                    conn.execute(
                        """INSERT INTO api_rate_bucket(bucket_id, tokens, updated_at)
                           VALUES (%s, %s, now())
                           ON CONFLICT (bucket_id) DO UPDATE
                             SET tokens = EXCLUDED.tokens, updated_at = EXCLUDED.updated_at""",
                        (bucket_id, remaining),
                    )
                conn.execute(
                    "DELETE FROM api_rate_bucket WHERE updated_at < now() - interval '24 hours'"
                )
                return retry
