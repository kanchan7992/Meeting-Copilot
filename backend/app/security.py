"""Bearer-token checks. Only SHA-256 hashes of tokens are configured on the server."""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import AppSettings

_bearer = HTTPBearer(auto_error=False)


# Returns True when the token's hash is in the allowed set (constant-time compare). — pradeep.singh@rodicconsultants.com
def token_is_valid(token: str | None, settings: AppSettings) -> bool:
    if settings.app_auth_disabled:
        return True
    if not token:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return any(hmac.compare_digest(digest, allowed) for allowed in settings.token_hashes())


# FastAPI dependency that rejects requests without a valid bearer token. — pradeep.singh@rodicconsultants.com
def require_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if not token_is_valid(creds.credentials if creds else None, request.app.state.settings):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")
