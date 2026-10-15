"""API token authentication."""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_token(
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    expected = os.environ.get("BARKHUB_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="BARKHUB_TOKEN not configured")
    supplied = x_api_token
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid token")
