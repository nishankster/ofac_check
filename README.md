# OFAC Screening API

The OFAC Screening API is a high-performance RESTful web service built with FastAPI that screens individuals and entities against the Office of Foreign Assets Control (OFAC) Specially Designated Nationals (SDN) list. It provides real-time risk decisions based on configurable fuzzy matching algorithms.

## Features

- **Automated SDN List Management**: Automatically downloads and parses the latest OFAC SDN XML list.
- **Pluggable Similarity Algorithms**: Choose between Jaro-Winkler, Levenshtein, or N-gram similarity per request. Each algorithm ships with pre-calibrated thresholds so screening sensitivity stays consistent regardless of choice.
- **OAuth 2.0 Authentication**: All screening endpoints are protected using the OAuth 2.0 Client Credentials flow (RFC 6749 §4.4) — the standard machine-to-machine authentication pattern.
- **Detailed Match Reasons**: Identifies matches not only by name but also by Date of Birth (DOB), Nationality, and National IDs (e.g., Passport, SSN).
- **Batch Processing**: Supports screening up to 100 identities in a single request.
- **Background Refresh**: Exposes an endpoint to refresh the SDN list asynchronously without blocking ongoing screening requests.
- **Containerized & Cloud-Ready**: Ships with a production Dockerfile, docker-compose for local development, and a complete AWS ECS Fargate deployment (CloudFormation + deploy script).

---

## Authentication

The API uses **OAuth 2.0 Client Credentials** (RFC 6749 §4.4) — the industry-standard flow for server-to-server API access. There are no user logins or redirect flows; your application exchanges a `client_id` + `client_secret` directly for a Bearer access token.

```
client_id + client_secret
        │
        ▼
POST /oauth/token  ──►  access_token (Bearer JWT, valid 1 hour)
                                │
                                ▼
            Authorization: Bearer <access_token>
                    on all protected endpoints
```

---

### Step 1 — Obtain an access token

Send a `POST` request to `/oauth/token` with `Content-Type: application/x-www-form-urlencoded`:

```bash
curl -X POST https://your-api/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=your-client-id&client_secret=your-client-secret"
```

**Success response `200 OK`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjb21wbGlhbmNlLXN5c3RlbSIsImlhdCI6MTY5ODI0MDAwMCwiZXhwIjoxNjk4MjQzNjAwLCJzY29wZSI6Im9mYWM6c2NyZWVuaW5nIn0.xyz",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "ofac:screening"
}
```

| Field | Description |
|-------|-------------|
| `access_token` | Bearer token to include in all subsequent requests |
| `token_type` | Always `Bearer` |
| `expires_in` | Seconds until the token expires (default: 3600 = 1 hour) |
| `scope` | Granted scopes; currently `ofac:screening` |

**Error — wrong credentials `401 Unauthorized`:**

```json
{
  "error": "invalid_client",
  "error_description": "Client authentication failed"
}
```

**Error — wrong grant type `400 Bad Request`:**

```json
{
  "error": "unsupported_grant_type",
  "error_description": "Only 'client_credentials' is supported"
}
```

---

### Step 2 — Call a protected endpoint

Include the access token in the `Authorization` header as a Bearer token:

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -X POST https://your-api/screen \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "John Doe"}'
```

**Error — missing token `401 Unauthorized`:**

```json
{ "error": "invalid_token", "error_description": "Not authenticated" }
```

**Error — expired token `401 Unauthorized`:**

```json
{ "error": "invalid_token", "error_description": "The access token has expired" }
```

**Error — malformed token `401 Unauthorized`:**

```json
{ "error": "invalid_token", "error_description": "The access token is invalid" }
```

---

### Token lifecycle

```
t=0 min    Call POST /oauth/token  →  receive access_token
t=0 min    Use token on /screen, /screen/batch, /sdn/refresh
...
t=59 min   Token still valid — continue using it
t=60 min   Token expires — requests return 401 "The access token has expired"
t=60 min   Call POST /oauth/token again for a fresh token
```

There is no refresh token in the Client Credentials flow — simply re-authenticate when the token expires.

---

### Python example (full round-trip)

```python
import requests

BASE_URL      = "https://your-api"
CLIENT_ID     = "compliance-system"
CLIENT_SECRET = "your-client-secret"

# 1. Get an OAuth 2.0 access token
resp = requests.post(
    f"{BASE_URL}/oauth/token",
    data={                          # form-encoded, not JSON
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
)
resp.raise_for_status()
token = resp.json()["access_token"]

headers = {"Authorization": f"Bearer {token}"}

# 2. Screen a single identity
resp = requests.post(
    f"{BASE_URL}/screen",
    headers=headers,
    json={
        "full_name":      "Osama Bin Laden",
        "date_of_birth":  "1957-03-10",
        "nationality":    "SA",
    },
)
resp.raise_for_status()
result = resp.json()
print(result["decision"])   # BLOCKED
print(result["score"])      # e.g. 0.9712
print(result["algorithm"])  # jaro_winkler

# 3. Batch screen with a different algorithm
resp = requests.post(
    f"{BASE_URL}/screen/batch",
    headers=headers,
    json={
        "subjects": [
            {"full_name": "Alice Smith",  "reference_id": "ref-001"},
            {"full_name": "Viktor Bout",  "reference_id": "ref-002", "algorithm": "ngram"},
        ]
    },
)
resp.raise_for_status()
for r in resp.json()["results"]:
    print(r["reference_id"], r["decision"], r["score"])
```

---

### Using the Swagger UI

The interactive API docs handle the auth flow for you:

1. Open [http://localhost:8000/docs](http://localhost:8000/docs)
2. Expand `POST /oauth/token` → **Try it out** → fill in `grant_type`, `client_id`, `client_secret` → **Execute**
3. Copy the `access_token` value from the response
4. Click **Authorize** (lock icon, top-right of the page)
5. Enter `Bearer <paste-token-here>` in the **HTTPBearer** field → click **Authorize**
6. All subsequent requests from Swagger will include the token automatically

---

## Running Locally with Docker

```bash
# 1. Create your local env file
cp .env.example .env
# Edit .env — set OAUTH_SIGNING_KEY and OAUTH_CLIENTS

# Generate a signing key:
# openssl rand -hex 32

# 2. Build and start
docker compose up --build

# 3. Check health (no auth required)
curl http://localhost:8000/health

# 4. Get an access token
curl -X POST http://localhost:8000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=replace-client-id&client_secret=replace-client-secret"

# 5. Screen an identity
TOKEN="<access_token from step 4>"
curl -X POST http://localhost:8000/screen \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "Osama Bin Laden"}'
```

---

## Running Without Docker (bare Python)

```bash
pip install -r requirements.txt

export OAUTH_SIGNING_KEY="$(openssl rand -hex 32)"
export OAUTH_CLIENTS='{"dev-client":"dev-secret"}'

uvicorn main:app --reload
```

Once running, the interactive API docs are at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## AWS ECS Deployment

### Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Docker installed
- An existing VPC with public and private subnets

### 1. Store secrets in AWS Secrets Manager

```bash
# OAuth signing key
aws secretsmanager create-secret \
  --name ofac-api/oauth-signing-key \
  --secret-string "$(openssl rand -hex 32)"

# OAuth clients (JSON: client_id → client_secret)
aws secretsmanager create-secret \
  --name ofac-api/oauth-clients \
  --secret-string '{"compliance-system":"s3cr3t1","batch-runner":"s3cr3t2"}'
```

### 2. Deploy the CloudFormation stack

```bash
aws cloudformation deploy \
  --template-file deploy/cloudformation.yml \
  --stack-name ofac-api-production \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      EnvironmentName=production \
      VpcId=vpc-xxxxxxxx \
      PublicSubnetIds="subnet-aaa,subnet-bbb" \
      PrivateSubnetIds="subnet-ccc,subnet-ddd" \
      OAuthSigningKeyArn=arn:aws:secretsmanager:REGION:ACCOUNT:secret:ofac-api/oauth-signing-key \
      OAuthClientsArn=arn:aws:secretsmanager:REGION:ACCOUNT:secret:ofac-api/oauth-clients \
      ImageUri=ACCOUNT.dkr.ecr.REGION.amazonaws.com/ofac-screening-api:latest
```

### 3. Build, push, and deploy

```bash
./deploy/deploy.sh --env production --region us-east-1
```

The script:
1. Authenticates Docker to ECR
2. Builds the image for `linux/amd64`
3. Pushes to ECR
4. Triggers an ECS rolling deployment with zero-downtime

### Architecture

```
Internet → ALB (HTTP→HTTPS redirect) → ECS Fargate tasks (private subnets)
                                              ↕
                                   AWS Secrets Manager
                                   (OAUTH_SIGNING_KEY, OAUTH_CLIENTS)
                                              ↕
                                   CloudWatch Logs (/ecs/ofac-screening-api)
```

- **Fargate** — serverless containers, no EC2 management
- **Auto Scaling** — scales 2→10 tasks on CPU > 70%
- **Deployment circuit breaker** — automatically rolls back failed deployments
- **Secrets Manager** — OAuth signing key and client registry injected as env vars at task start (never baked into the image)
- **Health check grace period** — 120 s to allow the SDN XML to download on cold start

---

## Algorithm Selection

Pass the `algorithm` field in `ScreeningRequest` to choose your similarity model. Omitting it defaults to `jaro_winkler` — existing integrations require no changes.

| Algorithm | Value | Best for |
|-----------|-------|----------|
| Jaro-Winkler | `jaro_winkler` | General name matching; handles prefixes well (default) |
| Levenshtein | `levenshtein` | Edit-distance matching; good for systematic typos and substitutions |
| N-gram (bigram Dice) | `ngram` | Cross-language transliterations and phonetic spelling variants |

---

## Decision Logic

Thresholds are pre-calibrated per algorithm so that effective screening sensitivity is equivalent across all three choices.

### Jaro-Winkler (default)

| Highest Match Score | Decision  | Description |
|---------------------|-----------|-------------|
| **≥ 0.88**          | `BLOCKED` | Strong match. Transaction must be blocked. |
| **0.80 – 0.87**     | `REVIEW`  | Possible match. Manual review required before proceeding. |
| **< 0.80**          | `CLEAR`   | No significant OFAC SDN match found. Identity cleared. |

### Levenshtein

| Highest Match Score | Decision  | Description |
|---------------------|-----------|-------------|
| **≥ 0.85**          | `BLOCKED` | Strong match. Transaction must be blocked. |
| **0.75 – 0.84**     | `REVIEW`  | Possible match. Manual review required before proceeding. |
| **< 0.75**          | `CLEAR`   | No significant OFAC SDN match found. Identity cleared. |

### N-gram

| Highest Match Score | Decision  | Description |
|---------------------|-----------|-------------|
| **≥ 0.75**          | `BLOCKED` | Strong match. Transaction must be blocked. |
| **0.65 – 0.74**     | `REVIEW`  | Possible match. Manual review required before proceeding. |
| **< 0.65**          | `CLEAR`   | No significant OFAC SDN match found. Identity cleared. |

*Note: If an exact National ID match is found, the score is automatically boosted to `1.0` (BLOCKED) regardless of algorithm.*

---

## API Endpoints

| Endpoint | Method | Auth |
|----------|--------|------|
| `/oauth/token` | POST | Public — form-encoded client credentials |
| `/health` | GET | Public — used by ALB health checks |
| `/screen` | POST | Bearer token required |
| `/screen/batch` | POST | Bearer token required |
| `/sdn/refresh` | GET | Bearer token required |

---

### 1. Get Access Token
`POST /oauth/token` — **Public** — `Content-Type: application/x-www-form-urlencoded`

```bash
curl -X POST https://your-api/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"
```

**Response `200 OK`:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "ofac:screening"
}
```

---

### 2. Health Check
`GET /health` — **Public**

```bash
curl https://your-api/health
```

**Response `200 OK`:**
```json
{
  "status": "ok",
  "sdn_entries": 12543,
  "sdn_list_date": "10/18/2023",
  "timestamp": "2023-10-25T14:30:00Z"
}
```

---

### 3. Screen Single Identity
`POST /screen` — **Requires Bearer token**

```bash
curl -X POST https://your-api/screen \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "John Doe",
    "entity_type": "individual",
    "date_of_birth": "1980-01-01",
    "nationality": "US",
    "national_id": "123456789",
    "reference_id": "txn-987654321",
    "algorithm": "jaro_winkler"
  }'
```

**Response `200 OK`:**
```json
{
  "request_id": "a1b2c3d4e5f6g7h8",
  "reference_id": "txn-987654321",
  "screened_at": "2023-10-25T14:35:00.123Z",
  "decision": "CLEAR",
  "score": 0.45,
  "matches": [],
  "message": "No OFAC SDN match found. Identity cleared.",
  "algorithm": "jaro_winkler",
  "sdn_list_date": "10/18/2023"
}
```

---

### 4. Batch Screening
`POST /screen/batch` — **Requires Bearer token**

Screens up to 100 subjects. Each subject is screened independently and may specify a different algorithm.

```bash
curl -X POST https://your-api/screen/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subjects": [
      {"full_name": "Alice Smith", "reference_id": "ref-001"},
      {"full_name": "Viktor Bout", "reference_id": "ref-002", "algorithm": "ngram"}
    ]
  }'
```

---

### 5. Refresh SDN List
`GET /sdn/refresh` — **Requires Bearer token**

Triggers a background re-download of the OFAC SDN list. Returns immediately.

```bash
curl -X GET https://your-api/sdn/refresh \
  -H "Authorization: Bearer $TOKEN"
```

**Response `200 OK`:**
```json
{ "message": "SDN list refresh initiated in background." }
```

---

## Data Models

### `ScreeningRequest`
| Field | Type | Description | Required |
|-------|------|-------------|----------|
| `full_name` | `str` | Full legal name of the individual or entity. | **Yes** |
| `entity_type` | `str` | `individual` or `entity`. Defaults to `individual`. | No |
| `date_of_birth` | `date` | Date of birth (YYYY-MM-DD). | No |
| `nationality` | `str` | ISO-3166-1 alpha-2 country code (e.g. `US`, `IR`). | No |
| `national_id` | `str` | Passport, SSN, or government-issued ID number. | No |
| `address` | `Address` | Address object (street, city, state, country, postal_code). | No |
| `reference_id` | `str` | Your internal transaction or customer reference ID. | No |
| `algorithm` | `str` | `jaro_winkler` (default), `levenshtein`, or `ngram`. | No |

### `OAuthTokenResponse`
| Field | Type | Description |
|-------|------|-------------|
| `access_token` | `str` | Bearer token for use on protected endpoints. |
| `token_type` | `str` | Always `Bearer`. |
| `expires_in` | `int` | Token lifetime in seconds. |
| `scope` | `str` | Granted scopes (e.g. `ofac:screening`). |

### `MatchDetail`
| Field | Type | Description |
|-------|------|-------------|
| `sdn_name` | `str` | Name of the entity on the SDN list. |
| `sdn_type` | `str` | Type of entity on the SDN list. |
| `sdn_program` | `str` | Sanction programs the entity is associated with. |
| `score` | `float` | Similarity score (0–1); scale depends on algorithm used. |
| `match_reason` | `str` | Reasons for the match (e.g., Name similarity, ID number match, DOB match). |

### `ScreeningResponse`
| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `str` | Auto-generated unique ID for this screening request. |
| `reference_id` | `str` | Your reference ID, echoed back from the request. |
| `screened_at` | `datetime` | UTC timestamp when the screening was performed. |
| `decision` | `str` | `BLOCKED`, `REVIEW`, or `CLEAR`. |
| `score` | `float` | Highest similarity score found (0–1). |
| `matches` | `list` | Up to 5 `MatchDetail` objects for candidates above the review threshold. |
| `message` | `str` | Human-readable explanation of the decision. |
| `algorithm` | `str` | Algorithm used to compute similarity scores. |
| `sdn_list_date` | `str` | Publication date of the SDN list used for this screening. |
