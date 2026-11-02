"""DIAGNOSIS must let lab units (mg/dL, mg/L) through while still catching doses."""
import pytest

from services.agent.w1.guards import DIAGNOSIS


@pytest.mark.parametrize("text", [
    "Your LDL cholesterol is 162 mg/dL (reference below 100), above the range [E1].",
    "Total bilirubin: 0.9 mg/dL",
    "Fasting blood glucose: 156 mg/dl",
    "CRP 5 mg/L",
    "238 mg/dL",
    "5 mg/L",
])
def test_lab_units_are_not_flagged(text):
    assert not DIAGNOSIS.search(text)


@pytest.mark.parametrize("text", [
    "take 500 mg",
    "Metformin 500 mg twice a day",
    "500 mg/day",
    "500 mg",
    "2.5 mg twice daily",
    "you have diabetes",
    "it could be hepatitis",
    "this could be hepatitis",
])
def test_doses_and_diagnoses_are_still_flagged(text):
    assert DIAGNOSIS.search(text)
