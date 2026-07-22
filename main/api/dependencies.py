from __future__ import annotations

import hmac
from fastapi import Header, HTTPException, Request


def services(request: Request) -> dict:
    return request.app.state.services


def require_auth(request: Request, authorization: str = Header(default=""), x_aniyaagent_token: str = Header(default="")) -> None:
    token = str(request.app.state.auth_token or "")
    if not token:
        return
    supplied = x_aniyaagent_token
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail={"error":"authentication_required","message":"Authentication is required."})
