"""
OAuth 2.0 Client Credentials flow (RFC 6749 §4.4).

Flow:
  1. Client sends client_id + client_secret to POST /oauth/token
     (application/x-www-form-urlencoded, grant_type=client_credentials)
  2. Server returns a signed JWT Bearer access token
  3. Client passes the token on every protected request:
       Authorization: Bearer <access_token>
"""

import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Config (injected at runtime; never hard-coded) ────────────────────────────

# Signing key — generate with: openssl rand -hex 32
_SIGNING_KEY: str = os.environ["OAUTH_SIGNING_KEY"]
_ALGORITHM = "HS256"

# Token lifetime (default: 3600 s = 1 hour)
TOKEN_EXPIRE_SECONDS: int = int(os.getenv("OAUTH_TOKEN_EXPIRE_SECONDS", "3600"))

# Registered clients — JSON object: {"client_id": "client_secret", ...}
# e.g. export OAUTH_CLIENTS='{"compliance-system":"s3cr3t","batch-runner":"p4ssw0rd"}'
_raw_clients = os.getenv("OAUTH_CLIENTS", "{}")
try:
    OAUTH_CLIENTS: dict[str, str] = json.loads(_raw_clients)
except json.JSONDecodeError:
    OAUTH_CLIENTS = {}

# ── FastAPI bearer scheme ─────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer()

# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(client_id: str, scope: str = "ofac:screening") -> str:
    """Sign and return a JWT access token for the given client."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(seconds=TOKEN_EXPIRE_SECONDS)
    payload = {
        "sub":   client_id,
        "iat":   now,
        "exp":   expire,
        "scope": scope,
    }
    return jwt.encode(payload, _SIGNING_KEY, algorithm=_ALGORITHM)

# ── Client authentication ─────────────────────────────────────────────────────

def authenticate_client(client_id: str, client_secret: str) -> None:
    """
    Verify client_id / client_secret against the registered client store.
    Raises HTTP 401 with an RFC 6749-compliant error body on failure.
    """
    expected = OAUTH_CLIENTS.get(client_id)
    if not expected or not secrets.compare_digest(expected, client_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_client",
                "error_description": "Client authentication failed",
            },
            headers={"WWW-Authenticate": 'Bearer realm="ofac-api"'},
        )

# ── Token validation ──────────────────────────────────────────────────────────

def _decode_token(token: str) -> dict:
    _www_auth = {"WWW-Authenticate": 'Bearer realm="ofac-api"'}
    try:
        return jwt.decode(token, _SIGNING_KEY, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_token",
                "error_description": "The access token has expired",
            },
            headers=_www_auth,
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_token",
                "error_description": "The access token is invalid",
            },
            headers=_www_auth,
        )

# ── FastAPI dependency ────────────────────────────────────────────────────────

def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> dict:
    """FastAPI dependency — validates the OAuth Bearer token on every protected route."""
    return _decode_token(credentials.credentials)
