"""Dev-only bearer authentication. OIDC replaces this at Pro."""
import os
import re

from fastapi import Header, HTTPException, Request


def current_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    if os.environ.get("SAHYOG_DEMO_AUTH") == "1" and authorization:
        match = re.fullmatch(r"Bearer demo-([A-Za-z0-9][A-Za-z0-9_-]{0,127})", authorization)
        if match:
            request.state.user = match[1]
            return match[1]
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
