# OFAC Screening API

The OFAC Screening API is a high-performance RESTful web service built with FastAPI that screens individuals and entities against the Office of Foreign Assets Control (OFAC) Specially Designated Nationals (SDN) list. It provides real-time risk decisions based on configurable fuzzy matching algorithms.

## Features

- **Automated SDN List Management**: Automatically downloads and parses the latest OFAC SDN XML list.
- **Pluggable Similarity Algorithms**: Choose between Jaro-Winkler, Levenshtein, or N-gram similarity per request. Each algorithm ships with pre-calibrated thresholds so screening sensitivity stays consistent regardless of choice.
- **JWT Authentication**: All screening endpoints are protected by short-lived JWT Bearer tokens. Clients exchange a static API key for a token via `POST /auth/token`.
- **Detailed Match Reasons**: Identifies matches not only by name but also by Date of Birth (DOB), Nationality, and National IDs (e.g., Passport, SSN).
- **Batch Processing**: Supports screening up to 100 identities in a single request.
- **Background Refresh**: Exposes an endpoint to refresh the SDN list asynchronously without blocking ongoing screening requests.
- **Containerized & Cloud-Ready**: Ships with a production Dockerfile, docker-compose for local development, and a complete AWS ECS Fargate deployment (CloudFormation + deploy script).

---

## Authentication

All endpoints except `GET /health` require a **JWT Bearer token**. The token flow is:

```
Your API key  →  POST /auth/token  →  JWT (valid 60 min)  →  Authorization: Bearer <JWT>
```

### Step 1 — Obtain a JWT token

Exchange your static API key for a short-lived JWT:

```bash
curl -X POST https://your-api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-api-key"}'
```

**Success response `200 OK`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhcGktY2xpZW50IiwiaWF0IjoxNjk4MjQwMDAwLCJleHAiOjE2OTgyNDM2MDB9.abc123",
  "token_type": "bearer",
  "expires_in": 3600
}
```

| Field | Description |
|-------|-------------|
| `access_token` | The JWT to include in every subsequent request |
| `token_type` | Always `bearer` |
| `expires_in` | Seconds until the token expires (default: 3600 = 60 min) |

**Error — invalid API key `401 Unauthorized`:**

```json
{ "detail": "Invalid API key" }
```

---

### Step 2 — Call a protected endpoint

Pass the token in the `Authorization` header as a **Bearer token**:

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -X POST https://your-api/screen \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "John Doe"}'
```

**Error — missing token `401 Unauthorized`:**

```json
{ "detail": "Not authenticated" }
```

**Error — expired token `401 Unauthorized`:**

```json
{ "detail": "Token has expired" }
```

**Error — malformed token `401 Unauthorized`:**

```json
{ "detail": "Invalid token" }
```

---

### Token lifecycle

```
t=0 min    Request token via POST /auth/token
t=0 min    Use token on /screen, /screen/batch, /sdn/refresh
...
t=59 min   Token still valid — continue using it
t=60 min   Token expires — any request returns 401 "Token has expired"
t=60 min   Request a fresh token via POST /auth/token and continue
```

Re-request a token whenever you receive `401 Token has expired`. There is no refresh endpoint — simply call `/auth/token` again with your API key.

---

### Python example (full round-trip)

```python
import requests

BASE_URL = "https://your-api"
API_KEY  = "your-api-key"

# 1. Get a token
resp = requests.post(f"{BASE_URL}/auth/token", json={"api_key": API_KEY})
resp.raise_for_status()
token = resp.json()["access_token"]

headers = {"Authorization": f"Bearer {token}"}

# 2. Screen a single identity
resp = requests.post(
    f"{BASE_URL}/screen",
    headers=headers,
    json={
        "full_name": "Osama Bin Laden",
        "date_of_birth": "1957-03-10",
        "nationality": "SA",
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
            {"full_name": "Alice Smith",    "reference_id": "ref-001"},
            {"full_name": "Viktor Bout",    "reference_id": "ref-002", "algorithm": "ngram"},
        ]
    },
)
resp.raise_for_status()
for r in resp.json()["results"]:
    print(r["reference_id"], r["decision"], r["score"])
```

---

### Using the Swagger UI

The interactive API docs automatically handle the auth flow for you:

1. Open [http://localhost:8000/docs](http://localhost:8000/docs)
2. Call `POST /auth/token` with your API key — copy the `access_token` from the response
3. Click **Authorize** (lock icon, top-right)
4. Enter `Bearer <paste-token-here>` in the **HTTPBearer** field and click **Authorize**
5. All subsequent requests from the Swagger UI will include the token automatically

---

## Running Locally with Docker

```bash
# 1. Create your local env file
cp .env.example .env
# Edit .env — set JWT_SECRET_KEY (openssl rand -hex 32) and API_KEYS

# 2. Build and start
docker compose up --build

# 3. Check health
curl http://localhost:8000/health

# 4. Get a token
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-key-from-env"}'

# 5. Screen an identity
curl -X POST http://localhost:8000/screen \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "Osama Bin Laden"}'
```

---

## AWS ECS Deployment

### Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Docker installed
- An existing VPC with public and private subnets

### 1. Deploy the CloudFormation stack

```bash
# Create the secrets in Secrets Manager first
aws secretsmanager create-secret \
  --name ofac-api/jwt-secret-key \
  --secret-string "$(openssl rand -hex 32)"

aws secretsmanager create-secret \
  --name ofac-api/api-keys \
  --secret-string "key-abc123,key-def456"

# Deploy the stack (replace parameter values for your environment)
aws cloudformation deploy \
  --template-file deploy/cloudformation.yml \
  --stack-name ofac-api-production \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      EnvironmentName=production \
      VpcId=vpc-xxxxxxxx \
      PublicSubnetIds="subnet-aaa,subnet-bbb" \
      PrivateSubnetIds="subnet-ccc,subnet-ddd" \
      JwtSecretArn=arn:aws:secretsmanager:REGION:ACCOUNT:secret:ofac-api/jwt-secret-key \
      ApiKeysSecretArn=arn:aws:secretsmanager:REGION:ACCOUNT:secret:ofac-api/api-keys \
      ImageUri=ACCOUNT.dkr.ecr.REGION.amazonaws.com/ofac-screening-api:latest
```

### 2. Build, push, and deploy

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
                              AWS Secrets Manager (JWT key, API keys)
                                        ↕
                              CloudWatch Logs (/ecs/ofac-screening-api)
```

- **Fargate** — serverless containers, no EC2 management
- **Auto Scaling** — scales 2→10 tasks on CPU > 70%
- **Deployment circuit breaker** — automatically rolls back failed deployments
- **Secrets Manager** — JWT secret key and API keys injected as env vars at runtime (never baked into the image)
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

| Endpoint | Method | Auth required |
|----------|--------|---------------|
| `/health` | GET | No — public (used by ALB health checks) |
| `/auth/token` | POST | No — this is where you obtain a token |
| `/screen` | POST | **Yes** — Bearer JWT |
| `/screen/batch` | POST | **Yes** — Bearer JWT |
| `/sdn/refresh` | GET | **Yes** — Bearer JWT |

---

### 1. Get Token
`POST /auth/token` — **Public**

Exchange a static API key for a short-lived JWT Bearer token.

**Request:**
```bash
curl -X POST https://your-api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "your-api-key"}'
```

**Response `200 OK`:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

### 2. Health Check
`GET /health` — **Public**

Returns the system health, including the current count of loaded SDN entries and the publication date of the list in use.

**Request:**
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
`POST /screen` — **Requires Bearer JWT**

Screens a single identity (individual or entity) against the loaded SDN list.

**Request:**
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
    "algorithm": "jaro_winkler",
    "address": {
      "street": "123 Main St",
      "city": "New York",
      "state": "NY",
      "country": "US",
      "postal_code": "10001"
    }
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

**Response when a match is found:**
```json
{
  "request_id": "b2c3d4e5f6g7h8i9",
  "reference_id": "txn-111222333",
  "screened_at": "2023-10-25T14:36:00.123Z",
  "decision": "BLOCKED",
  "score": 0.9712,
  "matches": [
    {
      "sdn_name": "osama bin laden",
      "sdn_type": "Individual",
      "sdn_program": "SDGT",
      "score": 0.9712,
      "match_reason": "Name similarity 0.97"
    }
  ],
  "message": "Identity matches OFAC SDN entry 'osama bin laden' (score 0.97). Transaction must be blocked.",
  "algorithm": "jaro_winkler",
  "sdn_list_date": "10/18/2023"
}
```

---

### 4. Batch Screening
`POST /screen/batch` — **Requires Bearer JWT**

Screens up to 100 subjects in a single request. Each subject is screened independently and may specify a different algorithm.

**Request:**
```bash
curl -X POST https://your-api/screen/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subjects": [
      {
        "full_name": "Alice Smith",
        "reference_id": "ref-001",
        "algorithm": "jaro_winkler"
      },
      {
        "full_name": "Bob Jones",
        "reference_id": "ref-002",
        "algorithm": "ngram"
      }
    ]
  }'
```

**Response `200 OK`:**
```json
{
  "screened_at": "2023-10-25T14:40:00.123Z",
  "total": 2,
  "results": [
    {
      "request_id": "b1b2...",
      "reference_id": "ref-001",
      "screened_at": "2023-10-25T14:40:00.123Z",
      "decision": "CLEAR",
      "score": 0.32,
      "matches": [],
      "message": "No OFAC SDN match found. Identity cleared.",
      "algorithm": "jaro_winkler",
      "sdn_list_date": "10/18/2023"
    },
    {
      "request_id": "c2c3...",
      "reference_id": "ref-002",
      "screened_at": "2023-10-25T14:40:00.123Z",
      "decision": "CLEAR",
      "score": 0.41,
      "matches": [],
      "message": "No OFAC SDN match found. Identity cleared.",
      "algorithm": "ngram",
      "sdn_list_date": "10/18/2023"
    }
  ]
}
```

---

### 5. Refresh SDN List
`GET /sdn/refresh` — **Requires Bearer JWT**

Triggers a background task to re-download and parse the latest OFAC SDN list from the Treasury website. Returns immediately; the reload happens asynchronously.

**Request:**
```bash
curl -X GET https://your-api/sdn/refresh \
  -H "Authorization: Bearer $TOKEN"
```

**Response `200 OK`:**
```json
{
  "message": "SDN list refresh initiated in background."
}
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
| `algorithm` | `str` | Similarity algorithm: `jaro_winkler` (default), `levenshtein`, or `ngram`. | No |

### `MatchDetail`
When a match is found above the review threshold, it is returned in the `matches` array. Up to the top 5 matches are returned.

| Field | Type | Description |
|-------|------|-------------|
| `sdn_name` | `str` | Name of the entity on the SDN list. |
| `sdn_type` | `str` | Type of entity on the SDN list. |
| `sdn_program` | `str` | Sanction programs the entity is associated with. |
| `score` | `float` | Similarity score (0–1); interpretation depends on algorithm used. |
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

---

## Running Without Docker (bare Python)

```bash
pip install -r requirements.txt

export JWT_SECRET_KEY="$(openssl rand -hex 32)"
export API_KEYS="dev-key-1"

uvicorn main:app --reload
```

Once running, the interactive API docs are at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
