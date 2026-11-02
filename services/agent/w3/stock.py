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
            required = {"medicine_id", "clinic_id", "name", "composition", "price_inr", "qty", "as_of", "source"}
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
                if row["source"] != "csv":
                    raise ValueError(f"{self.path}:{line} has an invalid stock source")
                rows.append(StockRow(
                    medicine_id=row["medicine_id"], clinic_id=row["clinic_id"], name=row["name"],
                    composition=row["composition"], price_inr=row["price_inr"], qty=qty,
                    as_of=row["as_of"],
                ))
        self.rows = tuple(rows)
        by_name: dict[str, list[StockRow]] = {}
        by_clinic: dict[str, list[StockRow]] = {}
        by_composition: dict[tuple[str, str], list[StockRow]] = {}
        for row in self.rows:
            by_name.setdefault(_key(row.name), []).append(row)
            by_clinic.setdefault(row.clinic_id, []).append(row)
            by_composition.setdefault((row.clinic_id, _key(row.composition)), []).append(row)
        self._by_name = by_name
        self._by_clinic = by_clinic
        self._by_composition = by_composition

    def molecule_for(self, name: str) -> str | None:
        key = _key(name)
        rows = self._by_name.get(key, [])
        compositions = {_key(row.composition) for row in rows}
        return next(iter(compositions)) if len(compositions) == 1 else None

    def check(self, clinic_id: str, medicine: dict) -> dict:
        clinic_rows = self._by_clinic.get(clinic_id, [])
        name, molecule = str(medicine.get("name", "")), str(medicine.get("molecule", ""))
        name_key = _key(name)
        exact = [row for row in self._by_name.get(name_key, []) if row.clinic_id == clinic_id]
        composition = self.molecule_for(name) or molecule
        composition_key = _key(composition)
        same_molecule = self._by_composition.get((clinic_id, composition_key), [])
        requested = exact[0] if exact else None
        generic_name = not name_key or name_key == _key(molecule)
        available = requested.qty > 0 if requested else (
            generic_name and any(row.qty > 0 for row in same_molecule)
        )
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


class ClinicCsvStockAdapter:
    """Load only the selected clinic inventory from the generated stock directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(f"W3 stock directory does not exist: {self.directory}")
        self.paths = sorted(self.directory.glob("*.csv"))
        if not self.paths:
            raise FileNotFoundError(f"W3 stock directory has no clinic CSV files: {self.directory}")
        self._by_clinic = {path.stem: path for path in self.paths}
        self._loaded: dict[str, CsvStockAdapter] = {}
        self._molecule_adapter: CsvStockAdapter | None = None

    def _adapter(self, clinic_id: str) -> CsvStockAdapter:
        path = self._by_clinic.get(clinic_id)
        if path is None:
            raise NotConfigured(f"no stock CSV is configured for clinic {clinic_id}")
        if clinic_id not in self._loaded:
            self._loaded[clinic_id] = CsvStockAdapter(path)
        return self._loaded[clinic_id]

    def molecule_for(self, name: str) -> str | None:
        if self._molecule_adapter is None:
            self._molecule_adapter = self._adapter(self.paths[0].stem)
        return self._molecule_adapter.molecule_for(name)

    def check(self, clinic_id: str, medicine: dict) -> dict:
        return self._adapter(clinic_id).check(clinic_id, medicine)


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
