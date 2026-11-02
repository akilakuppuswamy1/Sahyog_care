"""Dev-only bearer authentication. OIDC replaces this at Pro."""
import os
import re

from fastapi import Header, HTTPException, Request


def current_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    if os.environ.get("SAHYOG_DEMO_AUTH") == "1" and authorization:
        match = re.fullmatch(r"Bearer demo-([A-Za-z0-9][A-Za-z0-9_-]{0,127})", authorization)
        if match:
            identity = match[1]
            staff = identity.startswith("staff-")
            ops = identity.startswith("ops-")
            subject = identity[5:] if ops else identity[6:] if staff else identity
            if (staff or ops) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", subject):
                raise HTTPException(401, "Authentication required")
            request.state.user = subject
            request.state.role = "ops" if ops else "front_desk" if staff else "patient"
            if request.url.path.startswith("/frontdesk") != staff or request.url.path.startswith("/ops") != ops:
                raise HTTPException(403, "Role not permitted")
            if ops and not request.url.path.startswith("/ops"):
                raise HTTPException(403, "Role not permitted")
            return request.state.user
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
