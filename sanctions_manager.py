"""
Multi-source sanctions screening manager.

This module coordinates screening across multiple sanctions list sources
(OFAC, UK OFSI, etc.) and manages which sources are active.
"""

import logging
from typing import Optional

from models import AlgorithmType, ScreeningRequest, MatchDetail
from sanctions_source import SanctionsListSource, SanctionsEntry
from utils import normalize, string_similarity

log = logging.getLogger("ofac_api")

# Per-algorithm thresholds: (BLOCKED, REVIEW)
# Each pair is calibrated so that the effective sensitivity is roughly equivalent
# across algorithms even though their raw score distributions differ.
ALGORITHM_THRESHOLDS: dict[AlgorithmType, tuple[float, float]] = {
    AlgorithmType.JARO_WINKLER: (0.88, 0.80),  # Original calibration
    AlgorithmType.LEVENSHTEIN: (0.85, 0.75),  # Edit distance penalises harder; lower cutoffs
    AlgorithmType.NGRAM: (0.75, 0.65),  # Bigram Dice scores lower for near-matches
}


class SanctionsScreeningManager:
    """
    Manages multiple sanctions list sources and performs screening.

    This manager:
    - Coordinates multiple sanctions sources (OFAC, UK OFSI, etc.)
    - Configures which sources are active for screening
    - Screens requests against all active sources
    - Attributes matches to specific sources
    """

    def __init__(self, sources: list[SanctionsListSource]):
        """
        Initialize the screening manager.

        Args:
            sources: List of SanctionsListSource implementations to register.
        """
        self._sources = {src.source_code: src for src in sources}
        self._active_sources: set[str] = set(self._sources.keys())
        log.info(f"Initialized SanctionsScreeningManager with {len(self._sources)} sources: "
                 f"{', '.join(self._sources.keys())}")

    def load_all(self, force_download: bool = False):
        """
        Load all registered sanctions sources.

        Args:
            force_download: If True, download fresh data for all sources.
        """
        log.info("Loading all sanctions sources...")
        for source in self._sources.values():
            try:
                log.info(f"Loading {source.source_name}...")
                source.load(force_download)
                log.info(f"{source.source_name}: {source.entry_count:,} entries loaded")
            except Exception as exc:
                log.error(f"Failed to load {source.source_name}: {exc}")

    def set_active_sources(self, source_codes: list[str]):
        """
        Configure which sources to use for screening.

        Args:
            source_codes: List of source codes to activate (e.g., ["OFAC_SDN", "UK_OFSI"]).
                         Invalid codes are silently ignored.
        """
        self._active_sources = set(source_codes) & set(self._sources.keys())
        log.info(f"Active sources set to: {', '.join(self._active_sources)}")

    def get_all_entries(self) -> list[SanctionsEntry]:
        """
        Get all entries from active sources.

        Returns:
            Combined list of SanctionsEntry from all active sources.
        """
        all_entries = []
        for code in self._active_sources:
            all_entries.extend(self._sources[code].get_entries())
        return all_entries

    def screen(self, request: ScreeningRequest) -> list[MatchDetail]:
        """
        Screen a request against all active sanctions sources.

        Args:
            request: ScreeningRequest containing name and other identifying information.

        Returns:
            List of up to 5 top matches, sorted by score descending.
        """
        query_name = normalize(request.full_name)
        algorithm = request.algorithm.value
        _, review_threshold = ALGORITHM_THRESHOLDS[request.algorithm]
        results: list[MatchDetail] = []

        all_entries = self.get_all_entries()

        for entry in all_entries:
            # Build candidate name list: primary + all aliases
            candidates = [entry.name] + entry.aliases
            best_score = 0.0

            for candidate in candidates:
                s = string_similarity(query_name, candidate, algorithm)
                if s > best_score:
                    best_score = s

            if best_score < review_threshold:
                # Fast-path: skip weak candidates entirely
                # but still check national ID if provided
                if request.national_id and entry.ids:
                    if any(request.national_id.upper() == eid.upper() for eid in entry.ids):
                        best_score = 1.0
                    else:
                        continue
                else:
                    continue

            # Build reason string
            reasons = [f"Name similarity {best_score:.2f}"]
            if request.national_id and entry.ids:
                if any(request.national_id.upper() == eid.upper() for eid in entry.ids):
                    reasons.append("ID number match")
            if request.nationality and entry.nationality:
                if request.nationality.upper() == entry.nationality.upper():
                    reasons.append("Nationality match")
            if request.date_of_birth and entry.dob:
                if str(request.date_of_birth) in entry.dob or entry.dob in str(request.date_of_birth):
                    reasons.append("DOB match")

            results.append(MatchDetail(
                sdn_name=entry.name,
                sdn_type=entry.entry_type,
                sdn_program=", ".join(entry.programs) or "UNKNOWN",
                score=round(best_score, 4),
                match_reason="; ".join(reasons),
                source=entry.source,  # NEW: source attribution
                source_list_date=entry.source_list_date,  # NEW
            ))

        # Sort descending by score, keep top 5
        results.sort(key=lambda m: m.score, reverse=True)
        return results[:5]

    @property
    def total_entry_count(self) -> int:
        """Total entries across all active sources."""
        return sum(self._sources[code].entry_count for code in self._active_sources)

    def get_sources_info(self) -> dict:
        """
        Return metadata about all registered sources.

        Returns:
            Dictionary mapping source codes to metadata:
            {
                "OFAC_SDN": {
                    "name": "US OFAC SDN",
                    "entry_count": 12543,
                    "list_date": "2025-01-15",
                    "loaded": True,
                    "active": True
                },
                ...
            }
        """
        return {
            code: {
                "name": src.source_name,
                "entry_count": src.entry_count,
                "list_date": src.list_date,
                "loaded": src.is_loaded(),
                "active": code in self._active_sources,
            }
            for code, src in self._sources.items()
        }
