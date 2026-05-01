import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Config (all from environment) ────────────────────────────────────────────

# Must be set in production; at least 32 random bytes (e.g. `openssl rand -hex 32`)
SECRET_KEY: str = os.environ["JWT_SECRET_KEY"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# Comma-separated API keys: export API_KEYS="key-abc123,key-def456"
_raw = os.getenv("API_KEYS", "")
VALID_API_KEYS: set[str] = {k.strip() for k in _raw.split(",") if k.strip()}

# ── Internals ─────────────────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer()

def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode(
        {"sub": subject, "iat": now, "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def _decode(token: str) -> dict:
    _headers = {"WWW-Authenticate": "Bearer"}
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers=_headers,
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers=_headers,
        )


# ── Public dependency ─────────────────────────────────────────────────────────

def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> dict:
    """FastAPI dependency — validates the Bearer JWT on every protected route."""
    return _decode(credentials.credentials)
