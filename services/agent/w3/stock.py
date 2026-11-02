"""Clinic stock lookup behind one interface; graph nodes are adapter-agnostic."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class NotConfigured(RuntimeError):
    """The selected stock provider is not configured for this deployment."""


class StockSource(Protocol):
    def molecule_for(self, name: str) -> str | None: ...
    def check(self, clinic_id: str, medicine: dict) -> dict: ...


def _key(value: str) -> str:
    value = re.sub(r"\([^)]*\)", " ", value.lower())
    return " ".join(re.findall(r"[a-z0-9]+", value))


@dataclass(frozen=True)
class StockRow:
    medicine_id: str
    clinic_id: str
    name: str
    composition: str
    price_inr: str
    qty: int
    as_of: str


class CsvStockAdapter:
    """Read the nightly CSV with columns medicine_id, clinic_id, name, composition,
    price_inr, qty, as_of, source. The date is returned with every lookup.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"medicine_id", "clinic_id", "name", "composition", "price_inr", "qty", "as_of"}
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"{self.path} is missing stock columns: {sorted(missing)}")
            rows = []
            for line, row in enumerate(reader, start=2):
                try:
                    qty = int(row["qty"])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{self.path}:{line} has an invalid quantity") from exc
                if qty < 0:
                    raise ValueError(f"{self.path}:{line} has a negative quantity")
                if not row["as_of"]:
                    raise ValueError(f"{self.path}:{line} has no as_of date")
                rows.append(StockRow(
                    medicine_id=row["medicine_id"], clinic_id=row["clinic_id"], name=row["name"],
                    composition=row["composition"], price_inr=row["price_inr"], qty=qty,
                    as_of=row["as_of"],
                ))
        self.rows = tuple(rows)

    def molecule_for(self, name: str) -> str | None:
        key = _key(name)
        return next((row.composition for row in self.rows if _key(row.name) == key), None)

    def check(self, clinic_id: str, medicine: dict) -> dict:
        clinic_rows = [row for row in self.rows if row.clinic_id == clinic_id]
        name, molecule = str(medicine.get("name", "")), str(medicine.get("molecule", ""))
        name_key, molecule_key = _key(name), _key(molecule)
        exact = [row for row in clinic_rows if name_key and _key(row.name) == name_key]
        composition = self.molecule_for(name) or molecule
        composition_key = _key(composition)
        same_molecule = [row for row in clinic_rows if composition_key and _key(row.composition) == composition_key]
        requested = exact[0] if exact else (same_molecule[0] if same_molecule else None)
        available = requested is not None and requested.qty > 0
        alternatives = []
        if not available:
            alternatives = [
                {"medicine_id": row.medicine_id, "name": row.name, "composition": row.composition,
                 "price_inr": row.price_inr, "qty": row.qty, "as_of": row.as_of,
                 "suggestion_only": True}
                for row in same_molecule
                if row.qty > 0 and (requested is None or row.medicine_id != requested.medicine_id)
            ]
        as_of = max((row.as_of for row in clinic_rows), default=None)
        return {
            "medicine_id": requested.medicine_id if requested else None,
            "status": "in_stock" if available else "out_of_stock",
            "as_of": as_of,
            "alternatives": alternatives,
        }


class ApiStockAdapter:
    """API contract: query by clinic and normalized molecule, return quantity and as_of.

    Implementations must preserve the CSV adapter result shape and may only return
    same-molecule alternatives. This stub intentionally fails instead of implying
    inventory is available when no API client has been configured.
    """

    def molecule_for(self, name: str) -> str | None:
        raise NotConfigured("API stock lookup is not configured")

    def check(self, clinic_id: str, medicine: dict) -> dict:
        raise NotConfigured("API stock lookup is not configured")
