# OFAC Screening API

The OFAC Screening API is a high-performance RESTful web service built with FastAPI that screens individuals and entities against the Office of Foreign Assets Control (OFAC) Specially Designated Nationals (SDN) list. It provides real-time risk decisions based on configurable fuzzy matching algorithms.

## Features

- **Automated SDN List Management**: Automatically downloads and parses the latest OFAC SDN XML list.
- **Pluggable Similarity Algorithms**: Choose between Jaro-Winkler, Levenshtein, or N-gram similarity per request. Each algorithm ships with pre-calibrated thresholds so screening sensitivity stays consistent regardless of choice.
- **Detailed Match Reasons**: Identifies matches not only by name but also by Date of Birth (DOB), Nationality, and National IDs (e.g., Passport, SSN).
- **Batch Processing**: Supports screening up to 100 identities in a single request.
- **Background Refresh**: Exposes an endpoint to refresh the SDN list asynchronously without blocking ongoing screening requests.

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

### 1. Health Check
`GET /health`

Returns the system health, including the current count of loaded SDN entries and the publication date of the list in use.

**Response Example:**
```json
{
  "status": "ok",
  "sdn_entries": 12543,
  "sdn_list_date": "10/18/2023",
  "timestamp": "2023-10-25T14:30:00Z"
}
```

### 2. Refresh SDN List
`GET /sdn/refresh`

Triggers a background task to re-download and parse the latest OFAC SDN list.

**Response Example:**
```json
{
  "message": "SDN list refresh initiated in background."
}
```

### 3. Screen Single Identity
`POST /screen`

Screens a single identity (individual or entity) against the loaded SDN list.

**Request Body (`ScreeningRequest`):**
```json
{
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
}
```

**Response Body (`ScreeningResponse`):**
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

### 4. Batch Screening
`POST /screen/batch`

Screens multiple subjects in a single request (maximum 100 subjects). Each subject is screened independently and may use a different algorithm.

**Request Body (`BatchScreeningRequest`):**
```json
{
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
}
```

**Response Body (`BatchScreeningResponse`):**
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

## Setup and Running Locally

1. **Install Dependencies**
   Ensure you have Python installed, then install FastAPI and its dependencies.
   ```bash
   pip install fastapi pydantic uvicorn
   ```

2. **Run the API**
   Start the application using `uvicorn`. On startup, it will download and cache the OFAC SDN XML list.
   ```bash
   uvicorn main:app --reload
   ```

3. **Access API Documentation**
   FastAPI provides interactive API documentation out of the box. Once running, navigate to:
   - **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
   - **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
