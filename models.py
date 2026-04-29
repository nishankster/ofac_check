
import hashlib
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ScreeningDecision(str, Enum):
    CLEAR      = "CLEAR"         # No match found
    REVIEW     = "REVIEW"        # Possible match – human review required
    BLOCKED    = "BLOCKED"       # Strong match – transaction must be blocked


class EntityType(str, Enum):
    INDIVIDUAL  = "individual"
    ENTITY      = "entity"


class AlgorithmType(str, Enum):
    JARO_WINKLER = "jaro_winkler"  # Default; prefix-weighted character similarity
    LEVENSHTEIN  = "levenshtein"   # Normalized edit distance
    NGRAM        = "ngram"         # Bigram Dice coefficient; good for transliterations


class Address(BaseModel):
    street:  Optional[str] = None
    city:    Optional[str] = None
    state:   Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None


class ScreeningRequest(BaseModel):
    full_name:    str  = Field(..., description="Full legal name of the individual or entity")
    entity_type:  EntityType = Field(EntityType.INDIVIDUAL, description="individual or entity")
    date_of_birth: Optional[date] = Field(None, description="DOB (individuals only), YYYY-MM-DD")
    nationality:  Optional[str] = Field(None, description="ISO-3166-1 alpha-2 country code, e.g. 'IR'")
    national_id:  Optional[str] = Field(None, description="Passport, SSN, or government-issued ID number")
    address:      Optional[Address] = None
    reference_id: Optional[str] = Field(None, description="Your internal transaction / customer reference")
    algorithm:    AlgorithmType = Field(
        AlgorithmType.JARO_WINKLER,
        description="String similarity algorithm to use. Thresholds are pre-calibrated per algorithm.",
    )

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name must not be blank")
        return v.strip()


class MatchDetail(BaseModel):
    sdn_name:     str
    sdn_type:     str
    sdn_program:  str
    score:        float = Field(..., description="String similarity score 0–1 (algorithm-dependent)")
    match_reason: str


class ScreeningResponse(BaseModel):
    request_id:   str
    reference_id: Optional[str]
    screened_at:  datetime
    decision:     ScreeningDecision
    score:        float  = Field(..., description="Highest match score found (0–1)")
    matches:      list[MatchDetail]
    message:      str
    algorithm:    AlgorithmType = Field(..., description="Algorithm used to compute similarity scores")
    sdn_list_date: Optional[str] = Field(None, description="Publication date of the SDN list used")


class BatchScreeningRequest(BaseModel):
    subjects: list[ScreeningRequest] = Field(..., min_length=1, max_length=100)


class BatchScreeningResponse(BaseModel):
    screened_at: datetime
    total:       int
    results:     list[ScreeningResponse]
