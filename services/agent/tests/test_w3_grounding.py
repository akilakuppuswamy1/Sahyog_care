"""W3 molecule rule: stated in the transcript, or a stated brand maps to exactly one formulary
composition. The model never supplies a molecule."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from services.agent.w3.formulary import PLACEHOLDER, Formulary, default_formulary
from services.agent.w3.nodes import GROUNDING_VERSION, PROMPT_VERSION, SYSTEM_PROMPT, grounding_check
from services.agent.w3.stock import CsvStockAdapter

FORMULARY = Formulary([
    {"name": "Atorva Tablet", "composition": "atorvastatin (10mg)"},
    {"name": "Atorva 20 Tablet", "composition": "atorvastatin (20mg)"},
    {"name": "Atorva Asp 75 Capsule", "composition": "aspirin (75mg) + atorvastatin (10mg)"},
    {"name": "Augmentin 625 Duo Tablet", "composition": "amoxycillin (500mg) + clavulanic acid (125mg)"},
    {"name": "Augmentin 1000 Duo Tablet", "composition": "amoxycillin (875mg) + clavulanic acid (125mg)"},
    {"name": "Dualex 5 Tablet", "composition": "amlodipine (5mg)"},
    {"name": "Dualex 5 Tablet", "composition": "telmisartan (40mg)"},
    {"name": "Multimix Tablet", "composition": "amoxycillin (500mg) + clavulanic acid (125mg)"},
    {"name": "Multimix Tablet", "composition": "amoxycillin (500mg)"},
    {"name": "metformin", "composition": "metformin (500mg)"},
])
EMPTY = Formulary([])


def ground(transcript, medicines, formulary=FORMULARY):
    state = {"transcript": transcript, "flags": [],
             "soap_json": {"S": "", "O": "", "A": "", "P": "", "medicines": []},
             "medicines": medicines}
    return grounding_check(state, None, formulary)


def medicine(name, molecule, dose, quote, frequency=""):
    return {"name": name, "molecule": molecule, "dose": dose, "frequency": frequency, "quote": quote}


def kinds(checked):
    return {flag["kind"] for flag in checked["flags"]}


@pytest.mark.parametrize("model_molecule", ["Atorva", "", "atorvastatin"])
def test_brand_is_never_the_molecule_and_resolves_through_the_formulary(model_molecule):
    transcript = "Doctor: Start Atorva 10 mg at night."
    checked = ground(transcript, [medicine("Atorva", model_molecule, "10 mg", "Atorva 10 mg at night",
                                          "at night")])
    item = checked["medicines"][0]
    assert item["molecule"] == (PLACEHOLDER if model_molecule == "Atorva" else "atorvastatin")
    assert item["source"] == (None if model_molecule == "Atorva" else "formulary:Atorva")
    if model_molecule == "Atorva":
        assert "molecule_invalid" in kinds(checked)
    assert not kinds(checked) & {"molecule_unresolved", "molecule_ambiguous"}


@pytest.mark.parametrize("model_molecule", ["Atorva", "", "atorvastatin"])
def test_unresolved_brand_gets_placeholder_and_flag_not_the_brand(model_molecule):
    checked = ground("Doctor: Start Atorva 10 mg at night.",
                     [medicine("Atorva", model_molecule, "10 mg", "Atorva 10 mg at night")], EMPTY)
    item = checked["medicines"][0]
    assert item["molecule"] == PLACEHOLDER and item["source"] is None
    assert item["name"] == "Atorva"
    assert "molecule_unresolved" in kinds(checked)


def test_molecule_stated_in_the_transcript_is_supported_with_transcript_source():
    checked = ground("Doctor: Take metformin 500 mg twice daily.",
                     [medicine("metformin", "metformin", "500 mg", "metformin 500 mg twice daily",
                               "twice daily")])
    item = checked["medicines"][0]
    assert (item["molecule"], item["source"]) == ("metformin", "transcript")
    assert not kinds(checked)


def test_without_a_formulary_a_name_echoed_as_molecule_fails_safe():
    checked = ground("Doctor: Take metformin 500 mg twice daily.",
                     [medicine("metformin", "metformin", "500 mg", "metformin 500 mg twice daily")], EMPTY)
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert "molecule_unresolved" in kinds(checked)


def test_multi_ingredient_brand_is_allowed_only_with_one_formulary_composition():
    transcript = "Doctor: Take Augmentin 625 Duo Tablet 625 mg twice daily."
    quote = "Augmentin 625 Duo Tablet 625 mg twice daily"
    one = ground(transcript, [medicine("Augmentin 625 Duo Tablet", "", "625 mg", quote, "twice daily")])
    assert one["medicines"][0]["molecule"] == "amoxycillin + clavulanic acid"
    assert one["medicines"][0]["source"] == "formulary:Augmentin 625 Duo Tablet"

    transcript = "Doctor: Take Multimix 500 mg twice daily."
    many = ground(transcript, [medicine("Multimix", "", "500 mg", "Multimix 500 mg twice daily")])
    assert many["medicines"][0]["molecule"] == PLACEHOLDER
    flag = next(flag for flag in many["flags"] if flag["kind"] == "molecule_ambiguous")
    assert flag["candidates"] == ["amoxycillin", "amoxycillin + clavulanic acid"]

    transcript = "Doctor: Take Dualex 5 tablet daily."
    split = ground(transcript, [medicine("Dualex 5", "", "5 mg", "Dualex 5 tablet daily")])
    assert split["medicines"][0]["molecule"] == PLACEHOLDER
    assert "molecule_ambiguous" in kinds(split)


def test_saved_w3_005_dose_in_molecule_field_is_rejected_and_brand_is_grounded():
    cases = [json.loads(line) for line in
             (Path(__file__).resolve().parents[3] / "evals" / "sets" / "w3_scribe.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    case = next(item for item in cases if item["id"] == "w3-005")
    model_output = {
        "name": "Augmentin 625 Duo Tablet",
        "molecule": "625 mg",
        "dose": "625 mg",
        "frequency": "twice daily",
        "quote": "Augmentin 625 Duo Tablet 625 mg twice daily",
    }

    checked = ground(case["transcript"], [model_output])

    item = checked["medicines"][0]
    assert item["name"] == "Augmentin 625 Duo Tablet"
    assert item["molecule"] == PLACEHOLDER
    assert item["dose"] == "625 mg"
    assert item["source"] is None
    assert "molecule_invalid" in kinds(checked)


@pytest.mark.parametrize("value", ["625 mg", "500", "twice daily", "zorbamycin"])
def test_non_ingredient_molecule_values_are_rejected(value):
    checked = ground(
        "Doctor: Start Atorva 10 mg twice daily.",
        [medicine("Atorva", value, "10 mg", "Atorva 10 mg twice daily", "twice daily")],
    )
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert "molecule_invalid" in kinds(checked)


@pytest.mark.parametrize("transcript", [
    "Doctor: Take metformin 500 mg twice daily. Make that 250 mg twice daily.",
    "Doctor: Take metformin 500 mg twice daily—sorry, make that 250 mg twice daily.",
])
def test_correction_quote_can_use_medicine_name_from_same_doctor_turn(transcript):
    checked = ground(
        transcript,
        [medicine("metformin", "metformin", "250 mg", "Make that 250 mg twice daily",
                  "twice daily")],
    )
    item = checked["medicines"][0]
    assert item["name"] == "metformin"
    assert item["molecule"] == "metformin"
    assert item["dose"] == "250 mg"


@pytest.mark.parametrize("transcript", [
    "Patient: Take metformin 500 mg. Doctor: Make that 250 mg.",
    "Doctor: Take metformin 500 mg. Patient: Yes. Doctor: Make that 250 mg.",
    "Doctor: Take metformin 500 mg. Make that 250 mg with aspirin.",
    "Doctor: Take metformin 500 mg. Start aspirin 75 mg. Make that 250 mg.",
])
def test_correction_quote_does_not_borrow_another_speakers_or_medicines_name(transcript):
    checked = ground(
        transcript,
        [medicine("metformin", "metformin", "250 mg", "Make that 250 mg")],
    )
    item = checked["medicines"][0]
    assert item["name"] == PLACEHOLDER
    assert item["molecule"] == PLACEHOLDER
    assert "grounding" in kinds(checked)


@pytest.mark.parametrize("model_medicines", [
    [medicine("statin", "", "", "Start a statin")],
    [medicine("statin", "atorvastatin", "", "Start the statin now")],  # quote not in the transcript
    [medicine("atorvastatin", "atorvastatin", "20 mg", "statin: atorvastatin 20 mg")],  # invalid quote
    [],
], ids=["valid-quote", "invalid-quote-statin", "invalid-quote-molecule", "model-missed-it"])
def test_class_only_mention_is_kept_with_placeholder_molecule(model_medicines):
    checked = ground("Doctor: Start a statin. Patient: Which one? Doctor: I will specify after review.",
                     model_medicines)
    statins = [item for item in checked["medicines"] if item["name"].lower() == "statin"]
    assert len(statins) == 1
    assert statins[0]["molecule"] == PLACEHOLDER
    assert "atorvastatin" not in json.dumps(checked["medicines"])
    assert "molecule_unresolved" in kinds(checked)


def test_class_word_is_not_accepted_as_a_molecule():
    checked = ground("Doctor: Start a statin 20 mg at night.",
                     [medicine("statin", "statin", "20 mg", "statin 20 mg at night")])
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert checked["medicines"][0]["source"] is None


def test_molecule_in_neither_transcript_nor_formulary_never_appears():
    checked = ground("Doctor: Start Zorbex 5 mg daily.",
                     [medicine("Zorbex", "zorbamycin", "5 mg", "Zorbex 5 mg daily", "daily")])
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert "zorbamycin" not in json.dumps(checked)
    assert "molecule_unresolved" in kinds(checked)


def test_molecule_stated_for_another_medicine_is_not_borrowed():
    transcript = "Doctor: Stop aspirin. Start clopidogrel 75 mg daily."
    checked = ground(transcript, [medicine("clopidogrel", "aspirin", "75 mg", "clopidogrel 75 mg daily",
                                          "daily")], EMPTY)
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER


def test_regrounding_is_stable_and_drops_stale_grounding_flags():
    transcript = "Doctor: Start Atorva 10 mg at night."
    first = ground(transcript, [medicine("Atorva", "", "10 mg", "Atorva 10 mg at night")])
    state = {"transcript": transcript, "soap_json": first["soap_json"], "medicines": first["medicines"],
             "flags": first["flags"] + [{"kind": "molecule_unresolved", "field": "medicines.0.molecule",
                                         "message": "stale"}]}
    second = grounding_check(state, None, FORMULARY)
    assert second["medicines"] == first["medicines"]
    assert not any(flag["message"] == "stale" for flag in second["flags"])


def test_real_formulary_resolves_atorva_and_augmentin_by_lookup():
    formulary = default_formulary()
    if not formulary.path or not formulary.path.is_file():
        pytest.skip("data/clean/formulary.csv is not generated")
    assert formulary.lookup("Atorva").molecule == "atorvastatin"
    assert formulary.lookup("Augmentin 625 Duo Tablet").status == "one"
    assert formulary.lookup("Augmentin 625 Duo Tablet").molecule == "amoxycillin + clavulanic acid"
    assert formulary.lookup("Zorbex").status == "none"


def test_prompt_defines_soap_roles_and_quote_contract():
    assert PROMPT_VERSION == "w3-prompts-v3"
    assert GROUNDING_VERSION == "w3-grounding-v2"
    for phrase in ("S (subjective)", "O (objective)", "A (assessment)", "P (plan)",
                   "P must not be empty if the doctor gave any instruction",
                   "AND the final", "ingredient in the formulary",
                   "Never put a dose, strength, frequency, brand, or medicine class in molecule",
                   "Never add, upgrade or guess a diagnosis",
                   "Do not add facts, diagnoses, medicines, molecules, doses"):
        assert phrase in " ".join(SYSTEM_PROMPT.split())


# ---- stock: molecule + strength, "unknown" when nothing matches unambiguously ----

def stock_adapter(tmp_path):
    rows = [
        ("MED1", "Atorva Tablet", "atorvastatin (10mg)", 0),
        ("MED2", "Atorva 20 Tablet", "atorvastatin (20mg)", 5),
        ("MED3", "Atorlip 10 Tablet", "atorvastatin (10mg)", 8),
        ("MED4", "Glycomet 500 Tablet", "metformin (500mg)", 10),
        ("MED5", "Glycomet 850 Tablet", "metformin (850mg)", 0),
        ("MED6", "Augmentin 625 Duo Tablet", "amoxycillin (500mg) + clavulanic acid (125mg)", 3),
        ("MED7", "Azilife 500mg Tablet", "azithromycin (500mg)", 4),
        ("MED8", "Azilife 500mg Tablet", "azithromycin (500mg)", 0),
    ]
    path = tmp_path / "stock.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["medicine_id", "clinic_id", "name", "composition", "price_inr", "qty", "as_of",
                         "source"])
        for ident, name, composition, qty in rows:
            writer.writerow([ident, "C1", name, composition, "10", qty, "2026-11-02", "csv"])
    return CsvStockAdapter(path)


@pytest.mark.parametrize("name", ["Atorva", "Atorva Tablet", "atorva tablet"])
def test_stock_matches_brand_by_molecule_and_strength_and_suggests_same_strength_only(tmp_path, name):
    result = stock_adapter(tmp_path).check(
        "C1", {"name": name, "molecule": "atorvastatin", "dose": "10 mg"})
    assert result["status"] == "out_of_stock"
    assert result["medicine_id"] == "MED1"
    assert [item["name"] for item in result["alternatives"]] == ["Atorlip 10 Tablet"]


def test_stock_normalises_generic_name_and_strength(tmp_path):
    stock = stock_adapter(tmp_path)
    check = lambda name, molecule, dose: stock.check(
        "C1", {"name": name, "molecule": molecule, "dose": dose})["status"]
    assert check("metformin 500 mg", "metformin", "500 mg") == "in_stock"
    assert check("metformin", "metformin 500 mg", "") == "in_stock"
    assert check("metformin", "metformin", "850 mg") == "out_of_stock"
    assert check("Augmentin 625 Duo Tablet", "amoxycillin + clavulanic acid", "625 mg") == "in_stock"


@pytest.mark.parametrize("medicine_", [
    {"name": "metformin", "molecule": "metformin", "dose": "1000 mg"},          # no such strength
    {"name": "metformin", "molecule": "metformin", "dose": PLACEHOLDER},        # strength unknown
    {"name": "Unlisted brand", "molecule": "atorvastatin", "dose": "10 mg"},    # brand not stocked
    {"name": "Azilife", "molecule": "azithromycin", "dose": "500 mg"},          # two products, same name
    {"name": "statin", "molecule": PLACEHOLDER, "dose": PLACEHOLDER},           # unresolved molecule
    {"name": "Zorbex", "molecule": "zorbamycin", "dose": "5 mg"},               # not stocked at all
], ids=["strength", "no-strength", "unlisted-brand", "duplicate-name", "placeholder", "not-stocked"])
def test_stock_is_unknown_not_out_of_stock_without_an_unambiguous_match(tmp_path, medicine_):
    result = stock_adapter(tmp_path).check("C1", medicine_)
    assert result["status"] == "unknown"
    assert result["alternatives"] == []


@pytest.mark.parametrize("transcript,quote", [
    ("Patient: Make that metformin 250 mg.", "Make that metformin 250 mg"),
    ("Doctor: Make that metformin 250 mg with aspirin.",
     "Make that metformin 250 mg with aspirin"),
])
def test_correction_with_explicit_name_still_requires_doctor_and_no_competitor(transcript, quote):
    checked = ground(transcript, [medicine("metformin", "metformin", "250 mg", quote)])
    assert checked["medicines"][0]["name"] == PLACEHOLDER
    assert checked["medicines"][0]["molecule"] == PLACEHOLDER
    assert "grounding" in kinds(checked)


def test_self_contained_doctor_correction_with_explicit_name_is_allowed():
    checked = ground("Doctor: Make that metformin 250 mg.", [
        medicine("metformin", "metformin", "250 mg", "Make that metformin 250 mg"),
    ])
    assert checked["medicines"][0]["name"] == "metformin"
    assert checked["medicines"][0]["molecule"] == "metformin"
    assert checked["medicines"][0]["dose"] == "250 mg"


def test_correction_dose_and_frequency_do_not_count_as_competing_brand_names():
    formulary = Formulary([
        {"name": "metformin", "composition": "metformin (500mg)"},
        {"name": "Daily 10mg Tablet", "composition": "tadalafil (10mg)"},
        {"name": "Mg 10mg Tablet", "composition": "montelukast (10mg)"},
    ])
    checked = ground("Doctor: metformin 500 mg twice daily; sorry, make that 250 mg twice daily.", [
        medicine("metformin", "metformin", "250 mg", "sorry, make that 250 mg twice daily",
                 "twice daily"),
    ], formulary)
    assert checked["medicines"][0]["name"] == "metformin"
    assert checked["medicines"][0]["molecule"] == "metformin"
    assert not kinds(checked)
