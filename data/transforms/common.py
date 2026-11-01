"""Shared helpers: column contracts and drop tallies.

Every transform returns (clean_frame, tally). The tally is the proof on deck p.19:
"counts before and after, with the drop reasons tallied".
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd


class SchemaDrift(RuntimeError):
    """Raised when a raw file does not have the columns the transform was written for."""


def require_columns(df: pd.DataFrame, expected: set[str], source: str) -> None:
    missing = expected - set(df.columns)
    if missing:
        raise SchemaDrift(f"{source}: missing columns {sorted(missing)}; got {sorted(df.columns)}")


@dataclass
class Tally:
    source: str
    rows_in: int = 0
    rows_out: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    notes: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, mask: pd.Series, df: pd.DataFrame) -> pd.DataFrame:
        n = int(mask.sum())
        if n:
            self.dropped[reason] = self.dropped.get(reason, 0) + n
        return df.loc[~mask]

    def check(self) -> None:
        if self.rows_in - sum(self.dropped.values()) != self.rows_out:
            raise AssertionError(f"{self.source}: tally does not balance: {self.as_dict()}")

    def as_dict(self) -> dict:
        return {"source": self.source, "rows_in": self.rows_in, "rows_out": self.rows_out,
                "dropped": self.dropped, "notes": self.notes}


def stable_bucket(key: str, n: int) -> int:
    """Deterministic, platform-independent bucket (Python's hash() is salted per process)."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n
