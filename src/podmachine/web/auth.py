from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# auto_error=False: with the default HTTPBasic(), FastAPI raises 401 itself
# before require_admin ever runs, which would leak that admin auth exists
# even when admin.password is unset. Disabling that lets require_admin check
# configuration first and 404 in that case instead.
_security = HTTPBasic(auto_error=False)


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    """Gate for every /admin/* route. 404s (rather than 401s) when no admin
    password is configured, so an un-configured admin UI isn't reachable at
    all on an otherwise unauthenticated LAN device."""
    password = request.app.state.config.admin.password
    if not password:
        raise HTTPException(status_code=404)

    # Username isn't meaningful here (single shared password); comparison is
    # still constant-time to avoid leaking the password's length/content.
    if credentials is None or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )
