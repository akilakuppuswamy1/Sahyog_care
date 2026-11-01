"""Count rows in every CSV under data/raw so data/inventory.md gets exact figures.

Usage: python data/inventory.py
Expects each dataset unzipped into its own folder under data/raw/<slug>/.
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

RAW = Path(__file__).parent / "raw"


def count_rows(path: Path) -> int:
    csv.field_size_limit(sys.maxsize)
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return sum(1 for _ in reader)


def main() -> None:
    files = sorted(RAW.rglob("*.csv"))
    if not files:
        print(f"No CSVs under {RAW}. Download the datasets first (see README).")
        return
    print(f"| File | Rows (excl. header) | Counted on |")
    print(f"|---|---|---|")
    for p in files:
        print(f"| {p.relative_to(RAW)} | {count_rows(p):,} | {date.today()} |")


if __name__ == "__main__":
    main()
