
import hashlib
import logging
from datetime import datetime

from fastapi import Depends, FastAPI, Form, HTTPException, BackgroundTasks, status

from auth import (
    TOKEN_EXPIRE_SECONDS,
    authenticate_client,
    create_access_token,
    require_auth,
)
from models import (
    BatchScreeningRequest,
    BatchScreeningResponse,
    OAuthTokenResponse,
    ScreeningDecision,
    ScreeningRequest,
    ScreeningResponse,
)
from sdn_manager import SDNListManager, ALGORITHM_THRESHOLDS

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ofac_api")

# ─── FastAPI App ──────────────────────────────────────────────────────────────
sdn_manager = SDNListManager()

app = FastAPI(
    title="OFAC Screening API",
    description=(
        "Screens individuals and entities against the OFAC Specially Designated "
        "Nationals (SDN) list. Returns a risk decision: CLEAR, REVIEW, or BLOCKED.\n\n"
        "**Authentication**: OAuth 2.0 Client Credentials (RFC 6749 §4.4).  \n"
        "Call `POST /oauth/token` with your `client_id` and `client_secret` to obtain "
        "a Bearer token, then pass it as `Authorization: Bearer <token>` on all "
        "protected endpoints."
    ),
    version="2.0.0",
    contact={"name": "Compliance Team"},
    license_info={"name": "Internal Use Only"},
)


# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    log.info("Loading SDN list on startup …")
    sdn_manager.load()
    log.info(f"SDN list ready: {sdn_manager.entry_count:,} entries")


# ─── OAuth 2.0 Token Endpoint ─────────────────────────────────────────────────
@app.post(
    "/oauth/token",
    response_model=OAuthTokenResponse,
    tags=["Auth"],
    summary="Obtain an OAuth 2.0 access token (Client Credentials)",
)
def oauth_token(
    grant_type:    str = Form(...,  description="Must be 'client_credentials'"),
    client_id:     str = Form(...,  description="Your OAuth client ID"),
    client_secret: str = Form(...,  description="Your OAuth client secret"),
    scope:         str = Form("ofac:screening", description="Requested scope (default: ofac:screening)"),
) -> OAuthTokenResponse:
    """
    **OAuth 2.0 Client Credentials token endpoint** (RFC 6749 §4.4).

    Request must use `Content-Type: application/x-www-form-urlencoded`.

    | Field | Value |
    |-------|-------|
    | `grant_type` | `client_credentials` |
    | `client_id` | Your issued client ID |
    | `client_secret` | Your client secret |
    | `scope` | `ofac:screening` (optional, this is the default) |

    Returns a signed Bearer access token valid for `expires_in` seconds.
    Pass it on protected endpoints as `Authorization: Bearer <access_token>`.
    """
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unsupported_grant_type",
                "error_description": "Only 'client_credentials' is supported",
            },
        )

    authenticate_client(client_id, client_secret)

    token = create_access_token(client_id=client_id, scope=scope)
    return OAuthTokenResponse(
        access_token=token,
        expires_in=TOKEN_EXPIRE_SECONDS,
        scope=scope,
    )


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    if sdn_manager.entry_count == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "degraded",
                "reason": "SDN list not loaded — screening is unavailable",
                "sdn_entries": 0,
            },
        )
    return {
        "status": "ok",
        "sdn_entries": sdn_manager.entry_count,
        "sdn_list_date": sdn_manager.list_date,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ─── Refresh SDN List ─────────────────────────────────────────────────────────
@app.get("/sdn/refresh", tags=["System"])
def refresh_sdn(background_tasks: BackgroundTasks, _: dict = Depends(require_auth)):
    """Trigger a background re-download and reload of the OFAC SDN list."""
    def _reload():
        sdn_manager.load(force_download=True)
        log.info("SDN list refreshed in background.")
    background_tasks.add_task(_reload)
    return {"message": "SDN list refresh initiated in background."}


# ─── Single Screening ─────────────────────────────────────────────────────────
@app.post("/screen", response_model=ScreeningResponse, tags=["Screening"])
def screen_identity(req: ScreeningRequest, _: dict = Depends(require_auth)) -> ScreeningResponse:
    if sdn_manager.entry_count == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SDN list not loaded — screening is unavailable. Check /health for status.",
        )
    """
    Screen a single identity against the OFAC SDN list.

    Requires a valid OAuth 2.0 Bearer token (`Authorization: Bearer <token>`).

    Use the `algorithm` field to choose the similarity model. Thresholds are
    pre-calibrated per algorithm so effective sensitivity is consistent.

    **Default algorithm: jaro_winkler**

    | Algorithm     | BLOCKED threshold | REVIEW threshold |
    |---------------|-------------------|------------------|
    | jaro_winkler  | ≥ 0.88            | ≥ 0.80           |
    | levenshtein   | ≥ 0.85            | ≥ 0.75           |
    | ngram         | ≥ 0.75            | ≥ 0.65           |
    """
    match_threshold, review_threshold = ALGORITHM_THRESHOLDS[req.algorithm]

    matches    = sdn_manager.screen(req)
    top_score  = matches[0].score if matches else 0.0
    request_id = hashlib.sha256(
        f"{req.full_name}{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:16]

    if top_score >= match_threshold:
        decision = ScreeningDecision.BLOCKED
        message  = (
            f"Identity matches OFAC SDN entry '{matches[0].sdn_name}' "
            f"(score {top_score:.2f}). Transaction must be blocked."
        )
    elif top_score >= review_threshold:
        decision = ScreeningDecision.REVIEW
        message  = (
            f"Possible OFAC SDN match found (score {top_score:.2f}). "
            "Manual review required before proceeding."
        )
    else:
        decision = ScreeningDecision.CLEAR
        message  = "No OFAC SDN match found. Identity cleared."

    return ScreeningResponse(
        request_id    = request_id,
        reference_id  = req.reference_id,
        screened_at   = datetime.utcnow(),
        decision      = decision,
        score         = round(top_score, 4),
        matches       = matches,
        message       = message,
        algorithm     = req.algorithm,
        sdn_list_date = sdn_manager.list_date,
    )


# ─── Batch Screening ──────────────────────────────────────────────────────────
@app.post("/screen/batch", response_model=BatchScreeningResponse, tags=["Screening"])
def screen_batch(req: BatchScreeningRequest, token_payload: dict = Depends(require_auth)) -> BatchScreeningResponse:
    """
    Screen up to 100 identities in a single request.
    Each subject is independently screened and returns its own decision.

    Requires a valid OAuth 2.0 Bearer token (`Authorization: Bearer <token>`).
    """
    screened_at = datetime.utcnow()
    # Call the business logic directly (auth already verified above)
    results = [
        screen_identity(subject, _=token_payload)
        for subject in req.subjects
    ]
    return BatchScreeningResponse(
        screened_at = screened_at,
        total       = len(results),
        results     = results,
    )
