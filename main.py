
import hashlib
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks

from models import (
    ScreeningDecision,
    ScreeningRequest,
    ScreeningResponse,
    BatchScreeningRequest,
    BatchScreeningResponse,
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
        "Nationals (SDN) list. Returns a risk decision: CLEAR, REVIEW, or BLOCKED."
    ),
    version="1.0.0",
    contact={"name": "Compliance Team"},
    license_info={"name": "Internal Use Only"},
)


# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    log.info("Loading SDN list on startup …")
    sdn_manager.load()
    log.info(f"SDN list ready: {sdn_manager.entry_count:,} entries")


# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "sdn_entries": sdn_manager.entry_count,
        "sdn_list_date": sdn_manager.list_date,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ─── Refresh SDN List ─────────────────────────────────────────────────────────
@app.get("/sdn/refresh", tags=["System"])
def refresh_sdn(background_tasks: BackgroundTasks):
    """Trigger a background re-download and reload of the OFAC SDN list."""
    def _reload():
        sdn_manager.load(force_download=True)
        log.info("SDN list refreshed in background.")
    background_tasks.add_task(_reload)
    return {"message": "SDN list refresh initiated in background."}


# ─── Single Screening ─────────────────────────────────────────────────────────
@app.post("/screen", response_model=ScreeningResponse, tags=["Screening"])
def screen_identity(req: ScreeningRequest) -> ScreeningResponse:
    """
    Screen a single identity against the OFAC SDN list.

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

    matches     = sdn_manager.screen(req)
    top_score   = matches[0].score if matches else 0.0
    request_id  = hashlib.sha256(
        f"{req.full_name}{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:16]

    if top_score >= match_threshold:
        decision = ScreeningDecision.BLOCKED
        message  = (
            f"Identity matches OFAC SDN entry \'{matches[0].sdn_name}\' "
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
def screen_batch(req: BatchScreeningRequest) -> BatchScreeningResponse:
    """
    Screen up to 100 identities in a single request.
    Each subject is independently screened and returns its own decision.
    """
    screened_at = datetime.utcnow()
    results = [screen_identity(subject) for subject in req.subjects]
    return BatchScreeningResponse(
        screened_at = screened_at,
        total       = len(results),
        results     = results,
    )
