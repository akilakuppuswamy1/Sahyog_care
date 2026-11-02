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
            if staff and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity[6:]):
                raise HTTPException(401, "Authentication required")
            request.state.user = identity[6:] if staff else identity
            request.state.role = "front_desk" if staff else "patient"
            if request.url.path.startswith("/frontdesk") != staff:
                raise HTTPException(403, "Role not permitted")
            return request.state.user
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
