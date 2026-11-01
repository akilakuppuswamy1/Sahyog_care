"""Medical Appointment No Shows (Kaggle, Joni Hoppen / OpenML 43439). Deck p.19.

Known mess -> fix:
  * negative (and implausible) ages            -> drop, tallied
  * ScheduledDay after AppointmentDay           -> drop, tallied (compare dates; ScheduledDay has a time)
  * target "No-show" reads backwards ("Yes" = did not come) -> renamed to attended = (No-show == "No")
  * Brazilian neighbourhoods                    -> mapped deterministically onto the 28 fictional clinics
  * duplicate AppointmentID                     -> drop, tallied
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from data.transforms.common import Tally, require_columns, stable_bucket

COLUMNS = {"PatientId", "AppointmentID", "Gender", "ScheduledDay", "AppointmentDay", "Age",
           "Neighbourhood", "SMS_received", "No-show"}


def transform(raw: pd.DataFrame, clinic_ids: list[str]) -> tuple[pd.DataFrame, Tally]:
    require_columns(raw, COLUMNS, "noshows")
    t = Tally("noshows", rows_in=len(raw))
    df = raw.copy()

    df["scheduled_on"] = pd.to_datetime(df["ScheduledDay"], utc=True, errors="coerce").dt.date
    df["appointment_on"] = pd.to_datetime(df["AppointmentDay"], utc=True, errors="coerce").dt.date
    df = t.drop("unparseable_date", df["scheduled_on"].isna() | df["appointment_on"].isna(), df)
    df = t.drop("negative_or_implausible_age", (df["Age"] < 0) | (df["Age"] > 115), df)
    df = t.drop("scheduled_after_visit", df["scheduled_on"] > df["appointment_on"], df)
    df = t.drop("bad_target", ~df["No-show"].isin(["Yes", "No"]), df)
    df = t.drop("duplicate_appointment", df.duplicated("AppointmentID", keep="first"), df)

    sorted_clinics = sorted(clinic_ids)
    out = pd.DataFrame({
        "source_appointment_id": df["AppointmentID"].astype("int64").astype(str),
        # PatientId arrives as a float in scientific notation; keep the exact integer as text.
        "source_patient_id": df["PatientId"].map(lambda x: f"{int(x)}"),
        "gender": df["Gender"],
        "age": df["Age"].astype(int),
        "scheduled_on": df["scheduled_on"],
        "appointment_on": df["appointment_on"],
        "lead_days": [(a - s).days for s, a in zip(df["scheduled_on"], df["appointment_on"])],
        "sms_received": df["SMS_received"].astype(int).astype(bool),
        "attended": df["No-show"].eq("No"),       # the backwards target, renamed
        "clinic_id": df["Neighbourhood"].map(
            lambda n: sorted_clinics[stable_bucket(str(n).strip().upper(), len(sorted_clinics))]),
    }).reset_index(drop=True)

    t.rows_out = len(out)
    t.notes["no_show_rate_pct"] = round(100 * (1 - out["attended"].mean()), 1) if len(out) else 0
    t.check()
    return out, t


def load(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
