# D4 data contracts

14 contracts, one per table in `infra/db/001_schema.sql`. Template from deck p.53. Accepted when the compliance officer (in role) signs the sensitivity column.

| Table | Sensitivity | Owner |
|---|---|---|
| [clinic](clinic.md) | operational | Network operations lead |
| [specialty](specialty.md) | operational | Clinical safety lead |
| [doctor](doctor.md) | direct (name) / operational (rest) | Medical administration lead |
| [patient](patient.md) | direct | Patient registration lead |
| [consent](consent.md) | quasi | Compliance officer (role) |
| [slot](slot.md) | operational | Front desk lead |
| [appointment](appointment.md) | operational, tied to a patient so audited | Front desk lead |
| [encounter](encounter.md) | clinical | Medical records lead |
| [clinical_note](clinical_note.md) | clinical | Chief medical officer (role) |
| [prescription](prescription.md) | clinical | Pharmacy lead |
| [medicine_stock](medicine_stock.md) | operational | Pharmacy lead |
| [document](document.md) | clinical | Medical records lead |
| [doc_chunk](doc_chunk.md) | clinical | Medical records lead |
| [audit_event](audit_event.md) | quasi (ids only, no clinical text) | Compliance officer (role) |
