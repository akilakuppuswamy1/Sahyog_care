"""One test per known-mess row on deck p.19. Fixtures are tiny frames that reproduce each mess."""
from __future__ import annotations

import pandas as pd
import pytest

from data.transforms import disease_symptom, healthcare, medicines, noshows, transcriptions
from data.transforms.common import SchemaDrift

CLINICS = ["PUN-C01", "PUN-H01", "HYD-C01", "HYD-H01"]


# ------------------------------------------------------------------ No Shows
def _noshow(**kw):
    row = dict(PatientId=2.987250e13, AppointmentID=5642903, Gender="F",
               ScheduledDay="2016-04-29T18:38:08Z", AppointmentDay="2016-04-29T00:00:00Z", Age=62,
               Neighbourhood="JARDIM DA PENHA", Scholarship=0, Hipertension=1, Diabetes=0,
               Alcoholism=0, Handcap=0, SMS_received=0, **{"No-show": "No"})
    row.update(kw)
    return row


def test_noshows_drops_negative_age_and_tallies_it():
    raw = pd.DataFrame([_noshow(), _noshow(AppointmentID=2, Age=-1)])
    out, t = noshows.transform(raw, CLINICS)
    assert len(out) == 1 and t.dropped == {"negative_or_implausible_age": 1}


def test_noshows_drops_schedule_after_visit_but_keeps_same_day():
    raw = pd.DataFrame([_noshow(),  # same calendar day, later time: valid
                        _noshow(AppointmentID=2, ScheduledDay="2016-05-02T08:00:00Z")])
    out, t = noshows.transform(raw, CLINICS)
    assert len(out) == 1 and t.dropped == {"scheduled_after_visit": 1}
    assert out.loc[0, "lead_days"] == 0


def test_noshows_target_is_renamed_and_reversed():
    raw = pd.DataFrame([_noshow(), _noshow(AppointmentID=2, **{"No-show": "Yes"})])
    out, _ = noshows.transform(raw, CLINICS)
    assert out["attended"].tolist() == [True, False]
    assert "No-show" not in out.columns


def test_noshows_patient_id_keeps_exact_integer():
    out, _ = noshows.transform(pd.DataFrame([_noshow()]), CLINICS)
    assert out.loc[0, "source_patient_id"] == "29872500000000"


def test_noshows_neighbourhood_mapping_is_deterministic():
    raw = pd.DataFrame([_noshow(), _noshow(AppointmentID=2)])
    out, _ = noshows.transform(raw, CLINICS)
    assert out["clinic_id"].nunique() == 1 and out.loc[0, "clinic_id"] in CLINICS


def test_noshows_schema_drift_is_loud():
    with pytest.raises(SchemaDrift):
        noshows.transform(pd.DataFrame([{"PatientId": 1}]), CLINICS)


# ------------------------------------------------------------------ Disease Symptom
CROSSWALK = pd.DataFrame([
    dict(disease="Diabetes", specialty_id="SP16", alternative_specialty_id="SP01", red_flag=False, reviewed_by="lead"),
    dict(disease="Heart attack", specialty_id="SP03", alternative_specialty_id="SP04", red_flag=True, reviewed_by=None),
])


def test_disease_symptom_melts_strips_underscores_and_trailing_spaces():
    raw = pd.DataFrame([
        {"Disease": "Diabetes ", "Symptom_1": " increased_appetite", "Symptom_2": " fatigue", "Symptom_3": None},
        {"Disease": "Diabetes ", "Symptom_1": " increased_appetite", "Symptom_2": None, "Symptom_3": None},
        {"Disease": "Heart attack", "Symptom_1": " chest_pain", "Symptom_2": " breathlessness", "Symptom_3": None},
    ])
    out, t = disease_symptom.transform(raw, CROSSWALK)
    assert set(out["symptom"]) == {"increased appetite", "fatigue", "chest pain", "breathlessness"}
    assert out.loc[out["symptom"] == "increased appetite", "support"].item() == 2
    assert out.loc[out["disease"] == "Heart attack", "red_flag"].all()
    assert t.notes["unreviewed_crosswalk_rows"] == 1


def test_disease_without_specialty_fails_loudly():
    raw = pd.DataFrame([{"Disease": "Unknown fever", "Symptom_1": " fever"}])
    with pytest.raises(SchemaDrift, match="Unknown fever"):
        disease_symptom.transform(raw, CROSSWALK)


# ------------------------------------------------------------------ Transcriptions
LABELS = pd.DataFrame([dict(source_label="Cardiovascular / Pulmonary", specialty_id="SP03"),
                       dict(source_label="SOAP / Chart / Progress Notes", specialty_id=None)])


def _fake_scrub(text: str) -> str:
    return text.replace("John Smith", "<PERSON>")


def test_transcriptions_split_headers_dedupe_map_and_scrub():
    body = "SUBJECTIVE:,  John Smith reports chest pain.  PLAN:,  ECG."
    raw = pd.DataFrame([
        dict(description="d", medical_specialty=" Cardiovascular / Pulmonary", sample_name=" x", transcription=body),
        dict(description="d", medical_specialty=" SOAP / Chart / Progress Notes", sample_name="x", transcription=body),
        dict(description="d", medical_specialty=" SOAP / Chart / Progress Notes", sample_name="y", transcription=None),
    ])
    out, t = transcriptions.transform(raw, LABELS, _fake_scrub)
    assert t.dropped == {"empty_transcription": 1, "duplicate_text": 1}
    row = out.iloc[0]
    assert row["specialty_id"] == "SP03"
    assert row["sections"] == {"SUBJECTIVE": "<PERSON> reports chest pain.", "PLAN": "ECG."}
    assert "John Smith" not in row["text"] and ":," not in row["text"]


def test_transcriptions_unknown_label_fails_loudly():
    raw = pd.DataFrame([dict(description="d", medical_specialty="Space Medicine", sample_name="x", transcription="a")])
    with pytest.raises(SchemaDrift):
        transcriptions.transform(raw, LABELS, _fake_scrub)


# ------------------------------------------------------------------ Medicines
def _med(**kw):
    row = {"id": 1, "name": "Augmentin 625 Duo Tablet", "price(₹)": "₹223.42", "Is_discontinued": "FALSE",
           "manufacturer_name": "GSK", "type": "allopathy", "pack_size_label": "strip of 10 tablets",
           "short_composition1": "Amoxycillin  (500mg) ", "short_composition2": " Clavulanic Acid (125mg)"}
    row.update(kw)
    return row


@pytest.mark.parametrize("raw,expected", [("₹1,250.50", 1250.5), ("223.42", 223.42), ("Rs. 12", 12.0),
                                          ("N/A", None), ("1.2.3", None), (None, None)])
def test_price_parsing(raw, expected):
    assert medicines.parse_price(raw) == expected


@pytest.mark.parametrize("label,size,unit", [("strip of 10 tablets", 10, "tablet"),
                                             ("bottle of 100 ml Syrup", 100, "ml"),
                                             ("tube of 15 gm Cream", 15, "gm"),
                                             ("vial of 1 Injection", 1, "injection"),
                                             ("something odd", None, None)])
def test_pack_size_regex(label, size, unit):
    assert medicines.parse_pack(label) == (size, unit)


def test_medicines_groups_brands_by_composition_and_drops_bad_rows():
    raw = pd.DataFrame([
        _med(),
        _med(id=2, name="Moxikind-CV 625", **{"price(₹)": "198"},
             short_composition1="Clavulanic Acid (125mg)", short_composition2="Amoxycillin (500mg)"),
        _med(id=3, Is_discontinued="TRUE"),
        _med(id=4, **{"price(₹)": "free sample"}),
        _med(id=1),  # duplicate id
    ])
    out, t = medicines.transform(raw)
    assert t.dropped == {"discontinued": 1, "unparseable_price": 1, "duplicate_id": 1}
    assert out["composition"].nunique() == 1  # two brands, one composition: substitute candidates
    assert out["composition"].iloc[0] == "amoxycillin (500mg) + clavulanic acid (125mg)"


# ------------------------------------------------------------------ Healthcare
def _hc(**kw):
    row = {"Name": "bOBBy JacksOn", "Age": 30, "Gender": "Male", "Blood Type": "B-", "Medical Condition": "Cancer",
           "Date of Admission": "2024-01-31", "Doctor": "Matthew Smith", "Hospital": "Sons and Miller",
           "Insurance Provider": "Blue Cross", "Billing Amount": 18856.28, "Room Number": 328,
           "Admission Type": "Urgent", "Discharge Date": "2024-02-02", "Medication": "Paracetamol",
           "Test Results": "Normal"}
    row.update(kw)
    return row


def test_healthcare_date_order_violations_fall_to_zero_and_names_are_gone():
    raw = pd.DataFrame([_hc(), _hc(Name="Leslie Terry", **{"Discharge Date": "2024-01-01"}), _hc()])
    out, t = healthcare.transform(raw, ["PUN-H01", "HYD-H01"])
    assert t.dropped == {"discharge_before_admission": 1, "exact_duplicate": 1}
    assert t.notes["date_order_violations_after"] == 0
    assert "Name" not in out.columns and not out.astype(str).apply(lambda c: c.str.contains("JacksOn")).any().any()


def test_duplicate_note_keeps_most_specific_label_and_records_all():
    """Found on the real file: keep-first put 976 notes under the generic 'Surgery'."""
    labels = pd.DataFrame([dict(source_label="Surgery", specialty_id="SP12"),
                           dict(source_label="Neurosurgery", specialty_id="SP06"),
                           dict(source_label="Consult - History and Phy.", specialty_id=None)])
    body = "PROCEDURE:, Ventriculostomy.,  FINDINGS:, Clear CSF.,"
    raw = pd.DataFrame([dict(description="d", medical_specialty=lab, sample_name="x", transcription=body)
                        for lab in (" Consult - History and Phy.", " Surgery", " Neurosurgery")])
    out, t = transcriptions.transform(raw, labels, lambda s: s)
    assert len(out) == 1 and t.dropped == {"duplicate_text": 2}
    assert out.loc[0, "specialty_id"] == "SP06"
    assert out.loc[0, "all_labels"] == ["Consult - History and Phy.", "Neurosurgery", "Surgery"]
    assert ".," not in out.loc[0, "text"] and out.loc[0, "text"].splitlines() == [
        "PROCEDURE: Ventriculostomy.", "FINDINGS: Clear CSF."]


def test_dot_comma_is_a_line_break_even_without_a_space():
    """Found on the real file: 'clear.,Neck: Supple' and 'cheaper.,2. Samples'."""
    assert transcriptions.clean_text("TMs were clear.,Neck: Supple.,2. Samples given.,") == \
        "TMs were clear.\nNeck: Supple.\n2. Samples given."
