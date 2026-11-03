"""Clinic stock lookup behind one interface; graph nodes are adapter-agnostic."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from services.agent.w3.formulary import (
    PLACEHOLDER, dose_strength, ingredient_key, molecule_parts, numbers_in, parse_composition,
    parse_product, split_name,
)


class NotConfigured(RuntimeError):
    """The selected stock provider is not configured for this deployment."""


class StockSource(Protocol):
    def check(self, clinic_id: str, medicine: dict) -> dict: ...


@dataclass(frozen=True)
class StockRow:
    medicine_id: str
    clinic_id: str
    name: str
    composition: str
    price_inr: str
    qty: int
    as_of: str
    words: tuple[str, ...] = ()
    key: tuple[str, ...] = ()


class CsvStockAdapter:
    """Read the nightly CSV with columns medicine_id, clinic_id, name, composition,
    price_inr, qty, as_of, source. The date is returned with every lookup.

    Matching is by normalised molecule + strength. A brand resolves to a single product row;
    anything that cannot be matched unambiguously is reported as "unknown", never out of stock.
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
                    as_of=row["as_of"], words=split_name(row["name"]).words,
                    key=ingredient_key(parse_composition(row["composition"])[0]),
                ))
        self.rows = tuple(rows)
        by_clinic: dict[str, list[StockRow]] = {}
        by_molecule: dict[tuple[str, tuple[str, ...]], list[StockRow]] = {}
        for row in self.rows:
            by_clinic.setdefault(row.clinic_id, []).append(row)
            by_molecule.setdefault((row.clinic_id, row.key), []).append(row)
        self._by_clinic = by_clinic
        self._by_molecule = by_molecule

    def check(self, clinic_id: str, medicine: dict) -> dict:
        as_of = max((row.as_of for row in self._by_clinic.get(clinic_id, [])), default=None)
        unknown = {"medicine_id": None, "status": "unknown", "as_of": as_of, "alternatives": []}
        name, molecule = str(medicine.get("name", "")), str(medicine.get("molecule", ""))
        if not molecule or molecule == PLACEHOLDER:
            return unknown
        key = ingredient_key(molecule_parts(molecule))
        same = self._by_molecule.get((clinic_id, key), [])
        if not key or not same:
            return unknown
        brand = split_name(name)
        strength = (brand.strength or split_name(molecule).strength
                    or dose_strength(medicine.get("dose", "")))
        if not brand.words or ingredient_key(molecule_parts(name)) == key:
            matched = [row for row in same if strength and strength in parse_composition(row.composition)[1]]
            if not matched:
                return unknown
            status = "in_stock" if any(row.qty > 0 for row in matched) else "out_of_stock"
            return {"medicine_id": None, "status": status, "as_of": as_of, "alternatives": []}

        named = [row for row in same if row.words == brand.words]
        wanted = set(brand.numbers) | numbers_in(brand.strength)
        named = [row for row in named if wanted <= parse_product(row.name, row.composition).pool]
        if len(named) > 1 and strength:
            by_strength = [row for row in named if strength in parse_composition(row.composition)[1]]
            named = by_strength or named
        if len(named) != 1:
            return unknown
        requested = named[0]
        available = requested.qty > 0
        alternatives = []
        if not available:
            composition = parse_composition(requested.composition)
            alternatives = [
                {"medicine_id": row.medicine_id, "name": row.name, "composition": row.composition,
                 "price_inr": row.price_inr, "qty": row.qty, "as_of": row.as_of,
                 "suggestion_only": True}
                for row in same
                if row.qty > 0 and row.medicine_id != requested.medicine_id
                and parse_composition(row.composition) == composition
            ]
        return {
            "medicine_id": requested.medicine_id,
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

    def _adapter(self, clinic_id: str) -> CsvStockAdapter:
        path = self._by_clinic.get(clinic_id)
        if path is None:
            raise NotConfigured(f"no stock CSV is configured for clinic {clinic_id}")
        if clinic_id not in self._loaded:
            self._loaded[clinic_id] = CsvStockAdapter(path)
        return self._loaded[clinic_id]

    def check(self, clinic_id: str, medicine: dict) -> dict:
        return self._adapter(clinic_id).check(clinic_id, medicine)


class ApiStockAdapter:
    """API contract: query by clinic and normalized molecule, return quantity and as_of.

    Implementations must preserve the CSV adapter result shape and may only return
    same-molecule alternatives. This stub intentionally fails instead of implying
    inventory is available when no API client has been configured.
    """

    def check(self, clinic_id: str, medicine: dict) -> dict:
        raise NotConfigured("API stock lookup is not configured")
