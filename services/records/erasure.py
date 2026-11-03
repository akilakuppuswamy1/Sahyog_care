"""Patient-scoped deletion and caregiver-consent operations for uploaded records."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4


class ErasureError(Exception):
    status_code = 500


class RecordNotFound(ErasureError):
    status_code = 404


class RecordForbidden(ErasureError):
    status_code = 403


class RecordProtected(ErasureError):
    status_code = 409


class ErasureCleanupFailed(ErasureError):
    status_code = 500


def _upload_path(uri: str, upload_root: Path) -> tuple[Path, Path]:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise RecordProtected("Upload location is not a local file under the upload root.")

    raw_path = unquote(parsed.path)
    if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    root = upload_root.resolve()
    path = Path(raw_path).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RecordProtected("Upload file is outside the configured upload root.") from exc
    if not relative.parts or relative.parts[0] == ".erasing":
        raise RecordProtected("Upload file is outside the configured upload root.")
    return root, path


def _audit(conn, *, actor_id: str, action: str, resource_type: str, resource_id: str,
           patient_id: str, purpose: str, trace_id: str, detail: dict) -> None:
    conn.execute(
        """INSERT INTO audit_event
           (actor_id, actor_role, action, resource_type, resource_id, patient_id, purpose, trace_id, detail)
           VALUES (%s, 'patient', %s, %s, %s, %s, %s, %s, %s)""",
        (actor_id, action, resource_type, resource_id, patient_id, purpose, trace_id, json.dumps(detail)),
    )


def erase_upload(conn, *, patient_id: str, doc_id: str, trace_id: str, upload_root: Path) -> None:
    staged_path: Path | None = None
    original_path: Path | None = None
    try:
        with conn.transaction():
            candidate = conn.execute(
                "SELECT patient_id, source FROM document WHERE doc_id=%s",
                (doc_id,),
            ).fetchone()
            if candidate is None:
                note = conn.execute(
                    """SELECT e.patient_id FROM clinical_note n
                         JOIN encounter e USING (encounter_id)
                        WHERE n.note_id::text=%s""",
                    (doc_id,),
                ).fetchone()
                if note is not None and note[0] != patient_id:
                    raise RecordForbidden("This signed W3 note belongs to another patient.")
                if note is not None:
                    raise RecordProtected("Signed W3 notes are immutable and cannot be erased here.")
                raise RecordNotFound("Document not found.")

            if candidate[0] != patient_id:
                raise RecordForbidden("This document belongs to another patient.")
            if candidate[1] != "patient_upload":
                raise RecordProtected("Hospital-issued reports are protected and cannot be erased here.")

            # Wait for the worker before locking rows it needs to complete its job.
            job = conn.execute(
                "SELECT job_id FROM ingest_job WHERE doc_id=%s",
                (doc_id,),
            ).fetchone()
            if job is not None:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (str(job[0]),),
                )
                conn.execute(
                    "SELECT job_id FROM ingest_job WHERE doc_id=%s FOR UPDATE",
                    (doc_id,),
                )

            document = conn.execute(
                "SELECT patient_id, source, uri FROM document WHERE doc_id=%s FOR UPDATE",
                (doc_id,),
            ).fetchone()
            if document is None:
                raise RecordNotFound("Document not found.")
            owner_id, source, uri = document
            if owner_id != patient_id:
                raise RecordForbidden("This document belongs to another patient.")
            if source != "patient_upload":
                raise RecordProtected("Hospital-issued reports are protected and cannot be erased here.")

            root, original_path = _upload_path(uri, upload_root)
            if original_path.exists():
                if not original_path.is_file():
                    raise RecordProtected("Upload path is not a regular file.")
                staging_dir = root / ".erasing"
                staging_dir.mkdir(parents=True, exist_ok=True)
                staged_path = staging_dir / f"{uuid4().hex}.bin"
                os.replace(original_path, staged_path)

            conn.execute("DELETE FROM ingest_job WHERE doc_id=%s", (doc_id,))
            conn.execute("DELETE FROM document WHERE doc_id=%s", (doc_id,))
            _audit(
                conn,
                actor_id=patient_id,
                action="delete",
                resource_type="document",
                resource_id=doc_id,
                patient_id=patient_id,
                purpose="upload_erasure",
                trace_id=trace_id,
                detail={"operation": "erasure"},
            )
    except Exception:
        if staged_path is not None and staged_path.exists() and original_path is not None:
            try:
                os.replace(staged_path, original_path)
            except OSError as restore_error:
                raise ErasureCleanupFailed(
                    "Erasure transaction failed and the staged upload could not be restored."
                ) from restore_error
        raise

    if staged_path is not None and staged_path.exists():
        try:
            staged_path.unlink()
        except OSError as exc:
            raise ErasureCleanupFailed(
                "Database records were erased, but the staged upload file could not be removed."
            ) from exc


def grant_caregiver_consent(
    conn, *, patient_id: str, caregiver_id: str, scope: list[str], purpose: str,
    expires_at, trace_id: str,
) -> None:
    with conn.transaction():
        row = conn.execute(
            """INSERT INTO consent (patient_id, grantee_id, scope, purpose, expires_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING consent_id""",
            (patient_id, caregiver_id, scope, purpose, expires_at),
        ).fetchone()
        _audit(
            conn,
            actor_id=patient_id,
            action="write",
            resource_type="consent",
            resource_id=str(row[0]),
            patient_id=patient_id,
            purpose="caregiver_consent",
            trace_id=trace_id,
            detail={"operation": "grant", "scope": scope},
        )


def withdraw_caregiver_consent(
    conn, *, patient_id: str, caregiver_id: str, trace_id: str,
) -> None:
    with conn.transaction():
        rows = conn.execute(
            """UPDATE consent SET revoked_at=now()
                 WHERE patient_id=%s AND grantee_id=%s AND revoked_at IS NULL
                   AND expires_at > now()
             RETURNING consent_id""",
            (patient_id, caregiver_id),
        ).fetchall()
        if not rows:
            raise RecordNotFound("Active caregiver consent not found.")
        for row in rows:
            _audit(
                conn,
                actor_id=patient_id,
                action="write",
                resource_type="consent",
                resource_id=str(row[0]),
                patient_id=patient_id,
                purpose="caregiver_consent_withdrawal",
                trace_id=trace_id,
                detail={"operation": "withdraw"},
            )
