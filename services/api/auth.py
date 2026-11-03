"""Dev-only bearer authentication. OIDC replaces this at Pro."""
import os
import re

from fastapi import Header, HTTPException, Request


def current_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    if os.environ.get("SAHYOG_DEMO_AUTH") == "1" and authorization:
        match = re.fullmatch(r"Bearer demo-([A-Za-z0-9][A-Za-z0-9_-]{0,127})", authorization)
        if match:
            identity = match[1]
            if identity.startswith("caregiver-"):
                caregiver, separator, patient = identity[len("caregiver-"):].rpartition("-for-")
                valid_id = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
                if not separator or not re.fullmatch(valid_id, caregiver) or not re.fullmatch(valid_id, patient):
                    raise HTTPException(401, "Authentication required")
                if request.url.path != "/w2/ask":
                    raise HTTPException(403, "Caregiver tokens are limited to W2 record access")
                request.state.user = caregiver
                request.state.patient_id = patient
                request.state.role = "caregiver"
                return caregiver
            staff = identity.startswith("staff-")
            ops = identity.startswith("ops-")
            doctor = identity.startswith("doc-")
            subject = identity[5:] if ops else identity[6:] if staff else identity[4:] if doctor else identity
            if (staff or ops or doctor) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", subject):
                raise HTTPException(401, "Authentication required")
            request.state.user = subject
            request.state.patient_id = subject
            request.state.role = ("ops" if ops else "front_desk" if staff else
                                  "doctor" if doctor else "patient")
            if request.url.path.startswith("/frontdesk") != staff or request.url.path.startswith("/ops") != ops:
                raise HTTPException(403, "Role not permitted")
            if request.url.path.startswith("/scribe") != doctor:
                raise HTTPException(403, "Role not permitted")
            if ops and not request.url.path.startswith("/ops"):
                raise HTTPException(403, "Role not permitted")
            return request.state.user
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})


def current_mcp_user(request: Request, authorization: str | None) -> tuple[str, str]:
    token = authorization.partition(" ")[2] if authorization else ""
    if token.startswith("demo-"):
        token = token[5:]
    if token.startswith("staff-"):
        path = "/frontdesk/mcp"
    elif token.startswith("ops-"):
        path = "/ops/mcp"
    elif token.startswith("doc-"):
        path = "/scribe/api/mcp"
    else:
        path = "/mcp"
    auth_request = Request({**request.scope, "path": path})
    user = current_user(auth_request, authorization)
    request.state.user = user
    request.state.role = auth_request.state.role
    return user, request.state.role
