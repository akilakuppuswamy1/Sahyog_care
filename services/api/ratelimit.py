"""Thread-safe in-process token buckets, correct for one replica.

Architect tier moves rate limiting to the gateway/Redis.
"""
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

    def check(self, patient, ip, kind):
        """Consume patient and address buckets atomically; return retry seconds."""
        capacity, period = self.limits[kind]
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
