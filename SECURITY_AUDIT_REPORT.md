# SECURITY AUDIT REPORT
## Multi-Geography Sanctions Screening API

**Date:** May 8, 2026  
**Reviewer:** Senior Security Engineer & Expert Code Auditor  
**Framework:** FastAPI + PyJWT  
**Standard:** OWASP Top 10

---

## EXECUTIVE SUMMARY

This audit identified **15 security vulnerabilities** affecting data confidentiality, authentication integrity, and denial-of-service resilience. Critical issues include XML External Entity (XXE) injection, missing rate limiting, weak input validation, and insufficient authentication checks. Immediate remediation is required before production deployment.

---

## CRITICAL FINDINGS

---

### 1. XML External Entity (XXE) Injection Vulnerability

**Vulnerability Name:** XML External Entity (XXE) Injection  
**Severity:** 🔴 **CRITICAL** (OWASP A03:2021 – Injection)  
**Location:** [ofac_source.py](ofac_source.py#L91) and [uk_ofsi_source.py](uk_ofsi_source.py#L118)

**Description:**  
The code uses `xml.etree.ElementTree.fromstring()` without disabling external entity parsing. An attacker can craft malicious XML payloads to:
- Read arbitrary files from the server (e.g., `/etc/passwd`)
- Perform server-side request forgery (SSRF)
- Execute denial-of-service attacks via billion laughs/XML bombs

```python
# VULNERABLE CODE
root = ET.fromstring(xml_bytes)  # Line 91 in ofac_source.py
```

An attacker could poison the cache or intercept the download to include:
```xml
<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<sdnList>
  <sdnEntry>
    <uid>&xxe;</uid>
  </sdnEntry>
</sdnList>
```

**Remediation:**
```python
# SECURE CODE
import defusedxml.ElementTree as ET  # Use defusedxml library

# In _parse() method:
def _parse(self, xml_bytes: bytes):
    """Parse XML safely without XXE vulnerabilities."""
    # defusedxml automatically disables dangerous features
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.error(f"XML parsing failed: {e}")
        raise
```

**Steps:**
1. Install `defusedxml`: `pip install defusedxml`
2. Replace `import xml.etree.ElementTree as ET` with `import defusedxml.ElementTree as ET`
3. No code changes needed—defusedxml is a drop-in replacement that disables XXE, Billion Laughs, and other XML bomb attacks

---

### 2. Missing Authentication on Critical Endpoints

**Vulnerability Name:** Missing Authentication (OWASP A01:2021 – Broken Access Control)  
**Severity:** 🔴 **CRITICAL**  
**Location:** [main.py](main.py#L60), lines 60-75 (`/health` endpoint)

**Description:**  
The `/health` endpoint is publicly accessible without authentication, exposing sensitive system information:
```python
@app.get("/health", tags=["System"])  # NO authentication required!
def health():
    return {
        "status": "ok",
        "total_entries": sanctions_manager.total_entry_count,  # Leaks data volume
        "sources": sanctions_manager.get_sources_info(),  # Leaks operational details
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
```

An attacker can:
- Enumerate when sanctions lists were last updated
- Map the internal system architecture
- Detect when the service is down (for targeted DOS)
- Monitor version/deployment changes

**Remediation:**
```python
from fastapi import Depends

@app.get("/health", tags=["System"])
def health(_: dict = Depends(require_auth)):  # Add authentication
    """
    System health check (authenticated).
    
    Returns:
        Status and metadata about loaded sanctions lists.
    """
    return {
        "status": "ok",
        "total_entries": sanctions_manager.total_entry_count,
        "sources": sanctions_manager.get_sources_info(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
```

**Alternative (Mitigated Risk):**  
If unauthenticated health checks are needed for infrastructure (ALB, container orchestrators), return minimal info:
```python
@app.get("/health", tags=["System"])
def health():
    """Minimal health check for load balancers (no sensitive info)."""
    return {"status": "ok"} if sanctions_manager.is_loaded() else None
```

---

### 3. No Rate Limiting on Sensitive Endpoints

**Vulnerability Name:** Unrestricted Resource Consumption (DoS)  
**Severity:** 🔴 **CRITICAL** (OWASP A04:2021 – Insecure Deserialization)  
**Location:** [main.py](main.py#L107-130) (all endpoints), especially `/screen/batch`

**Description:**  
No rate limiting is implemented on:
- `/auth/token` – Brute-force API key enumeration
- `/screen` – Resource exhaustion via large batch requests
- `/screen/batch` – Accepts up to 100 subjects per request with no time limits

An attacker can:
```python
# Brute-force attack on API keys (no rate limiting)
for key in WORDLIST:
    response = requests.post("/auth/token", json={"api_key": key})
    if response.status_code == 200:
        print(f"Found API key: {key}")

# DOS attack via expensive batch screening
for _ in range(1000):
    requests.post("/screen/batch", headers={"Authorization": "Bearer " + token},
        json={"subjects": [ScreeningRequest(...) for _ in range(100)]})
```

**Remediation:**

Install rate limiting library:
```bash
pip install slowapi
```

Apply rate limiting in [main.py](main.py):
```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: JSONResponse(
    status_code=429,
    content={"detail": "Rate limit exceeded"},
))

# Apply to endpoints
@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"])
@limiter.limit("5/minute")  # 5 token requests per minute per IP
def get_token(request: Request, req: TokenRequest) -> TokenResponse:
    """Exchange API key for JWT (rate limited)."""
    if req.api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = create_access_token(subject="api-client")
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@app.post("/screen", response_model=ScreeningResponse, tags=["Screening"])
@limiter.limit("30/minute")  # 30 screening requests per minute
def screen_identity(request: Request, req: ScreeningRequest, _: dict = Depends(require_auth)) -> ScreeningResponse:
    """Screen identity (rate limited)."""
    # ... existing code ...

@app.post("/screen/batch", response_model=BatchScreeningResponse, tags=["Screening"])
@limiter.limit("10/minute")  # 10 batch requests per minute
def screen_batch(request: Request, req: BatchScreeningRequest, _: dict = Depends(require_auth)) -> BatchScreeningResponse:
    """Screen batch (rate limited)."""
    # ... existing code ...
```

Update [requirements.txt](requirements.txt):
```
slowapi==0.1.9
```

---

### 4. Insufficient Input Validation on National ID

**Vulnerability Name:** Improper Input Validation (OWASP A03:2021)  
**Severity:** 🔴 **CRITICAL**  
**Location:** [sanctions_manager.py](sanctions_manager.py#L80), ID comparison logic

**Description:**  
National ID comparison is case-sensitive and doesn't normalize input:

```python
# VULNERABLE: Case-sensitive, no trimming, no format validation
if any(request.national_id.upper() == eid.upper() for eid in entry.ids):
    best_score = 1.0  # Automatic BLOCKED match!
```

An attacker with ID `AB-CD-123 ` could bypass the check if the system stores `AB-CD-123` (without space):
- Could cause false CLEAR verdicts for sanctioned individuals
- Could false-positive trigger BLOCKED verdicts for legitimate users

**Remediation:**

Create ID normalization utility in [utils.py](utils.py):
```python
def normalize_id(id_string: str) -> str:
    """Normalize ID numbers for consistent comparison.
    
    Removes:
    - Leading/trailing whitespace
    - Internal spaces and dashes  
    - Converts to uppercase
    
    Args:
        id_string: Raw ID number from user input
        
    Returns:
        Normalized ID suitable for comparison
    """
    if not id_string:
        return ""
    # Remove whitespace, dashes, underscores; convert to uppercase
    return re.sub(r"[\s\-_]", "", id_string.strip()).upper()
```

Update [sanctions_manager.py](sanctions_manager.py#L80):
```python
from utils import normalize, normalize_id, string_similarity

# In screen() method:
if request.national_id and entry.ids:
    normalized_input_id = normalize_id(request.national_id)
    normalized_entry_ids = [normalize_id(eid) for eid in entry.ids]
    if any(normalized_input_id == entry_id for entry_id in normalized_entry_ids):
        best_score = 1.0
        # Continue to next entry if ID doesn't match
    else:
        continue
```

Also update the matching reason:
```python
# Build reason string
reasons = [f"Name similarity {best_score:.2f}"]
if request.national_id and entry.ids:
    normalized_input_id = normalize_id(request.national_id)
    normalized_entry_ids = [normalize_id(eid) for eid in entry.ids]
    if any(normalized_input_id == entry_id for entry_id in normalized_entry_ids):
        reasons.append("ID number match")
```

---

## HIGH SEVERITY FINDINGS

---

### 5. Weak JWT Token Generation

**Vulnerability Name:** Weak Token Generation / Insufficient Randomness (OWASP A02:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [main.py](main.py#L111-115), request ID generation

**Description:**  
Request IDs use SHA256 of predictable inputs:
```python
# WEAK: Request ID is deterministic and predictable
request_id = hashlib.sha256(
    f"{req.full_name}{datetime.utcnow().isoformat()}".encode()
).hexdigest()[:16]
```

Issues:
- Same name + same second = identical request_id (collision possible)
- Attackers can pre-compute IDs for common names
- Request IDs are used for audit/logging; weak generation compromises traceability

**Remediation:**

Use cryptographically secure random generation:
```python
import secrets

# SECURE: Use secrets module for cryptographic randomness
request_id = secrets.token_hex(8)  # 16-character hex string, 64 bits entropy
```

Alternative (if audit trail requires traceable IDs):
```python
import uuid
import secrets
from datetime import datetime

def generate_request_id() -> str:
    """Generate secure, unique request ID using UUID v4."""
    return str(uuid.uuid4())

# In screen_identity():
request_id = generate_request_id()
```

---

### 6. No HTTPS Enforcement in Production

**Vulnerability Name:** Transmission of Sensitive Data Over Unencrypted Channel (OWASP A02:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [main.py](main.py), FastAPI app configuration; [Dockerfile](Dockerfile#L33)

**Description:**  
The API doesn't enforce HTTPS. In production, JWT tokens and API keys can be intercepted:

```python
# Current: No HTTPS redirection or enforcement
app = FastAPI(
    title="Multi-Geography Sanctions Screening API",
    ...
)
```

If deployed without TLS termination in front (e.g., ALB without HTTPS listener), credentials are transmitted over HTTP.

**Remediation:**

Add HTTPS enforcement middleware in [main.py](main.py):
```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.scheme != "https" and os.getenv("ENVIRONMENT") == "production":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url=url, status_code=307)
        return await call_next(request)

# Add after app initialization
app.add_middleware(HTTPSRedirectMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.example.com"])  # Configure domain

# Add HSTS header
from fastapi.responses import Response

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    if os.getenv("ENVIRONMENT") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
    return response
```

Additionally, ensure CloudFormation enforces TLS:
```yaml
# In deploy/cloudformation.yml, ALB listener should use HTTPS:
ALBListener:
  Type: AWS::ElasticLoadBalancingV2::Listener
  Properties:
    DefaultActions:
      - Type: forward
        TargetGroupArn: !Ref TargetGroup
    ListenerArn: !Ref ALB
    Port: 443  # HTTPS only, not 80
    Protocol: HTTPS
    Certificates:
      - CertificateArn: !Ref CertificateArn  # ACM certificate required
```

---

### 7. Missing CORS Configuration

**Vulnerability Name:** Cross-Origin Request Security (OWASP A05:2021 – Security Misconfiguration)  
**Severity:** 🟠 **HIGH**  
**Location:** [main.py](main.py), app initialization

**Description:**  
No CORS (Cross-Origin Resource Sharing) headers are configured. If a browser-based client calls the API from a different origin, it could be vulnerable to:
- Malicious JavaScript on attacker.com making requests to api.example.com
- No origin validation

**Remediation:**

Add CORS middleware with strict origin allowlist:
```python
from fastapi.middleware.cors import CORSMiddleware

# Configure only trusted origins
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",")  # e.g., "https://app.example.com"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # Explicit allowlist, NOT "*"
    allow_credentials=True,
    allow_methods=["POST", "GET"],  # Only needed methods
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,  # Cache preflight for 1 hour
)
```

Update `.env.example`:
```
ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
```

---

### 8. Insufficient Logging of Security Events

**Vulnerability Name:** Insufficient Logging & Monitoring (OWASP A09:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [main.py](main.py#L66-73), `get_token()` endpoint

**Description:**  
Failed authentication attempts are not logged:
```python
@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"])
def get_token(req: TokenRequest) -> TokenResponse:
    if req.api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")  # No logging!
```

Cannot detect:
- Brute-force API key attacks
- Compromised credentials
- Suspicious patterns

**Remediation:**

Add security event logging in [main.py](main.py):
```python
import logging
from datetime import datetime
from pythonjsonlogger import jsonlogger  # pip install python-json-logger

# Setup JSON logging for security events
security_log = logging.getLogger("security")
handler = logging.FileHandler("logs/security.log")
formatter = jsonlogger.JsonFormatter()
handler.setFormatter(formatter)
security_log.addHandler(handler)
security_log.setLevel(logging.INFO)

@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"])
def get_token(request: Request, req: TokenRequest) -> TokenResponse:
    """Exchange API key for JWT (with security logging)."""
    if req.api_key not in VALID_API_KEYS:
        # Log failed authentication attempt
        security_log.warning("Failed authentication", extra={
            "event": "auth_failure",
            "remote_ip": request.client.host,
            "timestamp": datetime.utcnow().isoformat(),
            "attempted_key_prefix": req.api_key[:4] + "***",  # Don't log full key
        })
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Log successful authentication
    security_log.info("Authentication success", extra={
        "event": "auth_success",
        "remote_ip": request.client.host,
        "timestamp": datetime.utcnow().isoformat(),
    })
    
    token = create_access_token(subject="api-client")
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@app.post("/screen", response_model=ScreeningResponse, tags=["Screening"])
def screen_identity(request: Request, req: ScreeningRequest, _: dict = Depends(require_auth)) -> ScreeningResponse:
    """Screen identity with audit logging."""
    # Log screening request
    security_log.info("Screening request", extra={
        "event": "screening_initiated",
        "remote_ip": request.client.host,
        "entity_name": req.full_name[:50],  # Truncate for privacy
        "entity_type": req.entity_type,
        "reference_id": req.reference_id,
        "timestamp": datetime.utcnow().isoformat(),
    })
    
    # ... existing screening logic ...
    
    # Log decision
    security_log.info("Screening decision", extra={
        "event": "screening_decision",
        "decision": decision.value,
        "score": top_score,
        "matches_found": len(matches),
        "reference_id": req.reference_id,
        "timestamp": datetime.utcnow().isoformat(),
    })
    
    return response
```

Update [requirements.txt](requirements.txt):
```
python-json-logger==2.0.7
```

---

### 9. Weak API Key Format Validation

**Vulnerability Name:** Insufficient Input Validation (OWASP A03:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [auth.py](auth.py#L16-17), API key parsing

**Description:**  
API keys are parsed from environment without validation:
```python
# WEAK: No format validation, no length check
_raw = os.getenv("API_KEYS", "")
VALID_API_KEYS: set[str] = {k.strip() for k in _raw.split(",") if k.strip()}
```

Issues:
- Empty keys are accepted (`if k.strip()` still allows single spaces)
- Weak keys (e.g., "password123") are not rejected
- No entropy validation

**Remediation:**

Add API key validation in [auth.py](auth.py):
```python
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Minimum API key requirements
MIN_API_KEY_LENGTH = 32  # At least 32 characters
API_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9\-_]{32,}$")  # Alphanumeric, dash, underscore

SECRET_KEY: str = os.environ["JWT_SECRET_KEY"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

def _parse_api_keys() -> set[str]:
    """Parse and validate API keys from environment."""
    _raw = os.getenv("API_KEYS", "")
    if not _raw:
        raise ValueError("API_KEYS environment variable not set or empty")
    
    keys = set()
    for key in _raw.split(","):
        key = key.strip()
        if not key:
            continue
        
        # Validate key format
        if not API_KEY_PATTERN.match(key):
            raise ValueError(
                f"Invalid API key format. Must be {MIN_API_KEY_LENGTH}+ characters, "
                f"alphanumeric, dash, or underscore. Got: {key[:10]}..."
            )
        
        if len(key) < MIN_API_KEY_LENGTH:
            raise ValueError(
                f"API key too short: {len(key)} chars. Minimum: {MIN_API_KEY_LENGTH}"
            )
        
        keys.add(key)
    
    if not keys:
        raise ValueError("No valid API keys found in API_KEYS environment variable")
    
    return keys

VALID_API_KEYS: set[str] = _parse_api_keys()

_bearer_scheme = HTTPBearer()

# ... rest of auth.py remains the same ...
```

Update `.env.example`:
```bash
# API keys must be at least 32 characters, alphanumeric with dashes/underscores
# Generate with: openssl rand -hex 16 | tr -d '\n' && echo
API_KEYS=sk-abc123456789012345678901234567890,sk-def123456789012345678901234567890
```

---

### 10. Cache File Integrity Not Validated

**Vulnerability Name:** Insecure File Operations (OWASP A05:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [ofac_source.py](ofac_source.py#L68), [uk_ofsi_source.py](uk_ofsi_source.py#L60)

**Description:**  
Cached XML files are read without integrity verification. An attacker with write access to `/app/cache/` could modify cached files:
```python
# NO INTEGRITY CHECK: File could be tampered with
if not force and self._cache_path.exists():
    return self._cache_path.read_bytes()  # Might be modified!
```

An attacker could:
- Whitelist sanctioned entities (remove them from cache)
- Blacklist innocent individuals (add them to cache)
- Cause DOS via malformed XML

**Remediation:**

Add cache file integrity verification using HMAC-SHA256:

Create utility in [utils.py](utils.py):
```python
import hashlib
import hmac
from pathlib import Path

def compute_cache_hash(file_path: Path, secret: str) -> str:
    """
    Compute HMAC-SHA256 of cache file for integrity verification.
    
    Args:
        file_path: Path to cache file
        secret: Secret key (e.g., from environment)
        
    Returns:
        Hex-encoded HMAC-SHA256
    """
    with open(file_path, "rb") as f:
        return hmac.new(
            secret.encode(),
            f.read(),
            hashlib.sha256
        ).hexdigest()

def verify_cache_integrity(file_path: Path, expected_hash: str, secret: str) -> bool:
    """Verify cache file hasn't been tampered with."""
    computed_hash = compute_cache_hash(file_path, secret)
    return hmac.compare_digest(computed_hash, expected_hash)
```

Update [ofac_source.py](ofac_source.py):
```python
import os
from utils import compute_cache_hash, verify_cache_integrity

class OFACSDNSource(SanctionsListSource):
    def __init__(self, cache_path: Path = Path("cache/ofac_sdn.xml")):
        self._cache_path = cache_path
        self._hash_path = cache_path.with_suffix(".sha256")  # .../ofac_sdn.xml.sha256
        self._cache_secret = os.environ.get("CACHE_HMAC_SECRET", "")
        if not self._cache_secret:
            log.warning("CACHE_HMAC_SECRET not set; cache integrity verification disabled")
        # ... rest of init ...

    def _fetch_xml(self, force: bool) -> Optional[bytes]:
        """Download or load cached OFAC SDN XML with integrity check."""
        if not force and self._cache_path.exists():
            # Verify cache integrity if available
            if self._cache_secret and self._hash_path.exists():
                expected_hash = self._hash_path.read_text().strip()
                if not verify_cache_integrity(self._cache_path, expected_hash, self._cache_secret):
                    log.warning("Cache file integrity check FAILED; re-downloading...")
                    # Don't use corrupted cache; proceed to download
                else:
                    log.info(f"Cache integrity verified: {self._cache_path}")
                    return self._cache_path.read_bytes()
            else:
                log.info(f"Using cached OFAC SDN list at {self._cache_path}")
                return self._cache_path.read_bytes()

        # Download fresh data
        log.info(f"Downloading OFAC SDN list from {self.OFAC_SDN_XML_URL}...")
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            req = Request(self.OFAC_SDN_XML_URL, headers={"User-Agent": "OFAC-Screening-API/2.0"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            
            self._cache_path.write_bytes(data)
            
            # Compute and store integrity hash
            if self._cache_secret:
                hash_value = compute_cache_hash(self._cache_path, self._cache_secret)
                self._hash_path.write_text(hash_value)
                log.info(f"Cache integrity hash saved: {self._hash_path}")
            
            log.info(f"Downloaded {len(data):,} bytes → {self._cache_path}")
            return data
        except Exception as exc:
            log.error(f"Failed to download OFAC SDN list: {exc}")
            return None
```

Update `.env.example`:
```bash
# Secret key for cache integrity verification (32+ random characters)
CACHE_HMAC_SECRET=your-secret-key-here-minimum-32-characters
```

---

### 11. Missing Input Size Limits

**Vulnerability Name:** Unrestricted Upload Size / DOS (OWASP A04:2021)  
**Severity:** 🟠 **HIGH**  
**Location:** [main.py](main.py), request models

**Description:**  
No size limits on batch requests or individual fields:
```python
class BatchScreeningRequest(BaseModel):
    subjects: list[ScreeningRequest] = Field(..., min_length=1, max_length=100)
    # No max_items_size on the entire JSON
```

An attacker could send:
```json
{
  "subjects": [
    {
      "full_name": "A" * 10_000_000,  // 10MB for a single field
      ...
    }
  ]
}
```

This could:
- Consume excessive memory
- Crash the server (OOM)
- Trigger DOS

**Remediation:**

Add request size limits in [main.py](main.py):
```python
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

MAX_REQUEST_SIZE = 1_000_000  # 1 MB limit

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_SIZE:
            return Response("Request body too large", status_code=413)
        return await call_next(request)

app.add_middleware(RequestSizeLimitMiddleware)
```

Update Pydantic models for field limits:
```python
class ScreeningRequest(BaseModel):
    full_name: str = Field(
        ..., 
        min_length=1,
        max_length=256,  # Reasonable limit for names
        description="Full legal name"
    )
    reference_id: Optional[str] = Field(
        None,
        max_length=128,  # Limit reference IDs
        description="Your internal reference"
    )
    # ... other fields ...

class BatchScreeningRequest(BaseModel):
    subjects: list[ScreeningRequest] = Field(
        ..., 
        min_length=1,
        max_length=100,
        description="Up to 100 subjects per request"
    )
```

---

## MEDIUM SEVERITY FINDINGS

---

### 12. Weak Algorithm Selection Logic

**Vulnerability Name:** Algorithm Substitution Attack (OWASP A04:2021)  
**Severity:** 🟡 **MEDIUM**  
**Location:** [main.py](main.py#L101-102), algorithm parameter acceptance

**Description:**  
Users can select any algorithm, affecting sensitivity thresholds:
```python
# Users control algorithm selection!
match_threshold, review_threshold = ALGORITHM_THRESHOLDS[req.algorithm]
```

An attacker could:
- Select "ngram" algorithm (lowest thresholds: BLOCKED ≥ 0.75, REVIEW ≥ 0.65) to increase false positives
- Bypass genuine matches by selecting wrong algorithm

**Remediation:**

Enforce default algorithm, restrict choices:
```python
# In models.py
class ScreeningRequest(BaseModel):
    # ... other fields ...
    algorithm: AlgorithmType = Field(
        AlgorithmType.JARO_WINKLER,  # Default, cannot be changed
        description="String similarity algorithm (use default unless specifically configured)"
    )
```

Or log algorithm selection for audit:
```python
@app.post("/screen", response_model=ScreeningResponse, tags=["Screening"])
def screen_identity(request: Request, req: ScreeningRequest, _: dict = Depends(require_auth)) -> ScreeningResponse:
    # Log algorithm selection
    if req.algorithm != AlgorithmType.JARO_WINKLER:
        security_log.warning("Non-default algorithm selected", extra={
            "algorithm": req.algorithm.value,
            "remote_ip": request.client.host,
        })
    # ... rest of function ...
```

---

### 13. Date of Birth Matching Uses Substring

**Vulnerability Name:** Weak String Matching (OWASP A03:2021)  
**Severity:** 🟡 **MEDIUM**  
**Location:** [sanctions_manager.py](sanctions_manager.py#L90-92)

**Description:**  
DOB comparison uses substring matching:
```python
# WEAK: Substring match can cause false positives
if request.date_of_birth and entry.dob:
    if str(request.date_of_birth) in entry.dob or entry.dob in str(request.date_of_birth):
        reasons.append("DOB match")
```

Example: Searching for DOB `1990-01-15` could match `1990-01-15T00:00:00` (fine) but also `11990-01-15` (false positive).

**Remediation:**

Use proper date parsing and comparison:
```python
from datetime import datetime

def dob_matches(user_dob: date, entry_dob_str: Optional[str]) -> bool:
    """Check if user DOB matches entry DOB.
    
    Handles various date formats (YYYY-MM-DD, YYYY-MM, YYYY, etc.)
    """
    if not entry_dob_str:
        return False
    
    try:
        # Normalize both dates for comparison
        user_dob_str = str(user_dob)  # YYYY-MM-DD
        entry_dob_str = entry_dob_str.strip()
        
        # Exact match
        if user_dob_str == entry_dob_str:
            return True
        
        # Partial matches (year-month, year only)
        if entry_dob_str.startswith(user_dob_str[:7]):  # Year-month match
            return True
        
        return False
    except (ValueError, AttributeError):
        return False

# In screen() method:
if request.date_of_birth and entry.dob:
    if dob_matches(request.date_of_birth, entry.dob):
        reasons.append("DOB match")
```

---

### 14. No JWT Token Revocation

**Vulnerability Name:** Insufficient Authentication Validation (OWASP A01:2021)  
**Severity:** 🟡 **MEDIUM**  
**Location:** [auth.py](auth.py#L34-50)

**Description:**  
JWT tokens cannot be revoked. A compromised token remains valid until expiration:
```python
# No revocation mechanism
def _decode(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
```

If an API key is compromised, old tokens remain valid.

**Remediation:**

Implement token blacklist using Redis:

Install Redis:
```bash
pip install redis
```

Create token blacklist service:
```python
# Create new file: token_blacklist.py
import os
import redis
from datetime import timedelta

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)

def add_to_blacklist(token: str, expire_in_seconds: int):
    """Add token to blacklist (revocation list)."""
    redis_client.setex(
        f"blacklist:{token}",
        timedelta(seconds=expire_in_seconds),
        "1"
    )

def is_blacklisted(token: str) -> bool:
    """Check if token is blacklisted (revoked)."""
    return redis_client.exists(f"blacklist:{token}") > 0

def logout(token: str):
    """Immediately revoke a token."""
    # Set TTL to token's remaining lifetime
    redis_client.setex(f"blacklist:{token}", timedelta(hours=24), "1")
```

Update [auth.py](auth.py):
```python
from token_blacklist import is_blacklisted

def _decode(token: str) -> dict:
    _headers = {"WWW-Authenticate": "Bearer"}
    
    # Check if token is blacklisted
    if is_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers=_headers,
        )
    
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

# Add logout endpoint
@app.post("/auth/logout", tags=["Auth"])
def logout(request: Request, _: dict = Depends(require_auth)):
    """Revoke the current JWT token."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        add_to_blacklist(token, ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        security_log.info("Token revoked", extra={"event": "logout"})
    
    return {"message": "Logged out successfully"}
```

Update [requirements.txt](requirements.txt):
```
redis==5.0.0
```

---

### 15. No Sensitive Data Masking in Logs

**Vulnerability Name:** Sensitive Data Exposure (OWASP A02:2021)  
**Severity:** 🟡 **MEDIUM**  
**Location:** [main.py](main.py) – throughout application logging

**Description:**  
Full names are logged in requests. If logs are stored insecurely, they expose PII:
```python
# Logs might contain names, DoB, nationality
log.info(f"Screening {req.full_name}...")  # Logs PII!
```

**Remediation:**

Mask sensitive fields in logs:
```python
def mask_pii(value: str, show_chars: int = 2) -> str:
    """Mask personally identifiable information.
    
    Example: "John Doe" → "Jo*****"
    """
    if len(value) <= show_chars:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars)

# In logging calls:
security_log.info("Screening request", extra={
    "masked_name": mask_pii(req.full_name),
    "reference_id": req.reference_id,
})
```

---

## SUMMARY TABLE

| # | Vulnerability | Severity | Location | Status |
|---|---|---|---|---|
| 1 | XXE Injection | 🔴 CRITICAL | ofac_source.py:91, uk_ofsi_source.py:118 | Requires Remediation |
| 2 | Missing Auth on `/health` | 🔴 CRITICAL | main.py:60 | Requires Remediation |
| 3 | No Rate Limiting | 🔴 CRITICAL | main.py (all endpoints) | Requires Remediation |
| 4 | Weak ID Validation | 🔴 CRITICAL | sanctions_manager.py:80 | Requires Remediation |
| 5 | Weak Request ID Generation | 🟠 HIGH | main.py:111 | Requires Remediation |
| 6 | No HTTPS Enforcement | 🟠 HIGH | main.py, Dockerfile | Requires Remediation |
| 7 | Missing CORS Config | 🟠 HIGH | main.py | Requires Remediation |
| 8 | Insufficient Security Logging | 🟠 HIGH | main.py:66-73 | Requires Remediation |
| 9 | Weak API Key Format | 🟠 HIGH | auth.py:16-17 | Requires Remediation |
| 10 | No Cache Integrity Check | 🟠 HIGH | ofac_source.py:68 | Requires Remediation |
| 11 | Missing Request Size Limits | 🟠 HIGH | main.py models | Requires Remediation |
| 12 | Algorithm Substitution | 🟡 MEDIUM | main.py:101 | Audit/Restrict |
| 13 | Weak DOB String Matching | 🟡 MEDIUM | sanctions_manager.py:90 | Requires Remediation |
| 14 | No JWT Revocation | 🟡 MEDIUM | auth.py:34-50 | Optional Enhancement |
| 15 | No PII Masking in Logs | 🟡 MEDIUM | main.py (logging) | Requires Remediation |

---

## REMEDIATION PRIORITY

### Phase 1 (CRITICAL – Address Before Production)
1. ✅ Fix XXE injection (install defusedxml)
2. ✅ Add authentication to `/health` endpoint
3. ✅ Implement rate limiting on all endpoints
4. ✅ Add national ID normalization
5. ✅ Enforce HTTPS in production

### Phase 2 (HIGH – Address Within 1 Week)
6. ✅ Add API key format validation
7. ✅ Implement cache integrity verification
8. ✅ Add request size limits
9. ✅ Enable security event logging
10. ✅ Configure CORS properly

### Phase 3 (MEDIUM – Address Within 1 Month)
11. ✅ Fix DOB string matching logic
12. ✅ Use cryptographically secure RNG for request IDs
13. ✅ Add PII masking in logs
14. ✅ Implement JWT token revocation (optional)

---

## COMPLIANCE NOTES

**OWASP Top 10 Coverage:**
- ✅ A01 – Broken Access Control: Missing auth on `/health`, CORS misconfiguration
- ✅ A02 – Cryptographic Failures: HTTPS not enforced, weak RNG
- ✅ A03 – Injection: XXE vulnerability, weak input validation
- ✅ A04 – Insecure Design: No rate limiting, no request size limits
- ✅ A05 – Security Misconfiguration: Cache not protected, CORS not configured
- ✅ A09 – Logging & Monitoring: Insufficient security event logging, no PII masking

**Recommendations:**
1. Deploy an API Gateway (AWS API Gateway, Kong) for additional protection (rate limiting, WAF)
2. Use AWS Secrets Manager for API key and JWT secret management
3. Enable CloudTrail and S3 access logs for audit trail
4. Implement WAF rules to detect XXE and injection attempts
5. Use AWS Certificate Manager for automated HTTPS certificate management

---

**Report Generated:** May 8, 2026  
**Next Review:** After implementing Phase 1 remediations
