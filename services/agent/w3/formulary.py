"""Deterministic brand-to-composition lookup against the cleaned formulary.

W3 rule: a molecule is supported only if the transcript states it, or a brand the transcript
states maps to exactly one composition here. The model never supplies a molecule.
"""
from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER = "[doctor to specify]"
FORM_WORDS = frozenset({
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "syrup",
    "suspension", "oral", "injection", "inj", "drops", "drop", "cream", "gel", "ointment",
    "lotion", "solution", "infusion", "sachet", "powder", "inhaler", "spray",
})
_STRENGTH = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(mcg|μg|ug|mg|gm|g|iu|ml)\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_PAREN = re.compile(r"\([^)]*\)")
_SPLIT_MOLECULES = re.compile(r"\s*\+\s*|\s+and\s+|\s*,\s*|\s*&\s*")


@dataclass(frozen=True)
class NameParts:
    words: tuple[str, ...]
    numbers: frozenset[str]
    strength: str | None


_UNIT_ALIASES = {"gm": "g", "μg": "mcg", "ug": "mcg"}


def _strength(number: str, unit: str) -> str:
    return number + _UNIT_ALIASES.get(unit, unit)


def split_name(text: object) -> NameParts:
    """Split 'Atorva 20 Tablet' or 'metformin 500 mg' into brand words, bare numbers and strength."""
    found: list[str] = []

    def take(match: re.Match) -> str:
        if match.group(2) != "ml":
            found.append(_strength(match.group(1), match.group(2)))
        return " "

    cleaned = _STRENGTH.sub(take, _PAREN.sub(" ", str(text).lower()))
    tokens = _TOKEN.findall(cleaned)
    return NameParts(
        words=tuple(token for token in tokens if token[0].isalpha() and token not in FORM_WORDS),
        numbers=frozenset(token for token in tokens if token[0].isdigit()),
        strength=found[0] if found else None,
    )


def dose_strength(dose: object) -> str | None:
    """A mass dose such as '10 mg' doubles as the tablet strength; volumes and counts do not."""
    match = _STRENGTH.search(str(dose).lower())
    return _strength(match.group(1), match.group(2)) if match and match.group(2) != "ml" else None


def numbers_in(value: str | None) -> set[str]:
    return set(_NUMBER.findall(value or ""))


def molecule_parts(text: object) -> tuple[str, ...]:
    """Ingredient names in free text, with strengths, forms and parentheticals removed."""
    cleaned = _STRENGTH.sub(" ", _PAREN.sub(" ", str(text).lower()))
    names = (" ".join(word for word in re.findall(r"[a-z0-9]+", part) if word not in FORM_WORDS)
             for part in _SPLIT_MOLECULES.split(cleaned))
    return tuple(name for name in names if name)


def ingredient_key(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(names))


@lru_cache(maxsize=None)
def parse_composition(composition: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """('amoxycillin (500mg) + clavulanic acid (125mg)') -> (names, strengths)."""
    names, strengths = [], []
    for part in re.split(r"\s*\+\s*", composition.lower()):
        paren = re.search(r"\(([^)]*)\)", part)
        if paren:
            strengths.append(re.sub(r"\s+", "", paren.group(1)))
        name = " ".join(re.findall(r"[a-z0-9]+", _PAREN.sub(" ", part)))
        if name:
            names.append(name)
    return tuple(names), tuple(strengths)


@dataclass(frozen=True)
class Product:
    name: str
    composition: str
    words: tuple[str, ...]
    pool: frozenset[str]
    ingredients: tuple[str, ...]
    strengths: tuple[str, ...]

    @property
    def key(self) -> tuple[str, ...]:
        return ingredient_key(self.ingredients)

    @property
    def molecule(self) -> str:
        return " + ".join(self.ingredients)


def parse_product(name: str, composition: str) -> Product:
    parts = split_name(name)
    ingredients, strengths = parse_composition(composition)
    pool = set(parts.numbers) | numbers_in(parts.strength)
    for strength in strengths:
        pool |= numbers_in(strength)
    return Product(name, composition, parts.words, frozenset(pool), ingredients, strengths)


def narrow(products: list[Product], parts: NameParts) -> list[Product]:
    wanted = set(parts.numbers) | numbers_in(parts.strength)
    return [product for product in products if wanted <= product.pool]


@dataclass(frozen=True)
class Lookup:
    status: str  # "one" | "ambiguous" | "none"
    molecule: str = ""
    product: str = ""
    candidates: tuple[str, ...] = ()


class Formulary:
    """Brand lookup over name/composition rows; the CSV is only read on the first lookup."""

    def __init__(self, rows: Iterable[Mapping[str, str]] | None = None, path: str | Path | None = None):
        self._rows = rows
        self.path = Path(path) if path else None
        self._index: dict[tuple[str, ...], list[tuple[str, str]]] | None = None

    @classmethod
    def from_csv(cls, path: str | Path) -> "Formulary":
        if not Path(path).is_file():
            raise FileNotFoundError(f"W3 formulary does not exist: {path}")
        return cls(path=path)

    def _read(self) -> Iterable[Mapping[str, str]]:
        if self._rows is not None:
            return self._rows
        if self.path is None or not self.path.is_file():
            return ()  # no formulary deployed: every brand stays unresolved and is flagged
        with self.path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def _build(self) -> dict[tuple[str, ...], list[tuple[str, str]]]:
        if self._index is None:
            index: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
            for row in self._read():
                name, composition = (row.get("name") or "").strip(), (row.get("composition") or "").strip()
                if name and composition:
                    index[split_name(name).words].append((name, composition))
            self._index = dict(index)
        return self._index

    @property
    def available(self) -> bool:
        """False when no formulary is deployed; brand and molecule cannot then be told apart."""
        return bool(self._build())

    def products(self, text: str) -> list[Product]:
        parts = split_name(text)
        if not parts.words:
            return []
        candidates = [parse_product(*row) for row in self._build().get(parts.words, ())]
        return narrow(candidates, parts)

    def lookup(self, text: str) -> Lookup:
        products = self.products(text)
        molecules = {product.key: product.molecule for product in products}
        if not molecules:
            return Lookup("none")
        if len(molecules) > 1:
            return Lookup("ambiguous", candidates=tuple(sorted(molecules.values()))[:5])
        product = products[0].name if len(products) == 1 else text.strip()
        return Lookup("one", molecule=next(iter(molecules.values())), product=product)

    def is_brand_only(self, text: str) -> bool:
        """True when the text names formulary products but is not one of their ingredients."""
        products = self.products(text)
        wanted = set(molecule_parts(text))
        return bool(products) and bool(wanted) and not any(wanted <= set(p.ingredients) for p in products)


_DEFAULT: Formulary | None = None


def default_formulary() -> Formulary:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Formulary(path=os.environ.get("W3_FORMULARY_PATH", ROOT / "data" / "clean" / "formulary.csv"))
    return _DEFAULT
