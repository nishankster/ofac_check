"""
Abstract base class for sanctions list sources.

This module defines the interface that all sanctions list sources must implement,
enabling the system to screen against multiple geographies (OFAC, UK OFSI, EU, UN, etc.)
in a uniform way.
"""

from abc import ABC, abstractmethod
from typing import Optional


class SanctionsEntry:
    """
    Normalized sanctions list entry from any source.

    This class provides a common data structure for sanctions entries from different
    sources (OFAC, UK OFSI, etc.), allowing unified screening logic.
    """
    __slots__ = (
        "uid", "name", "entry_type", "programs", "aliases",
        "dob", "nationality", "ids", "source", "source_list_date"
    )

    def __init__(
        self,
        uid: str,
        name: str,
        entry_type: str,
        programs: list[str],
        aliases: list[str],
        dob: Optional[str],
        nationality: Optional[str],
        ids: list[str],
        source: str,
        source_list_date: Optional[str]
    ):
        self.uid = uid                              # Unique identifier
        self.name = name                            # Normalized primary name
        self.entry_type = entry_type                # "Individual", "Entity", "Vessel"
        self.programs = programs                    # list[str] - sanction programs/regimes
        self.aliases = aliases                      # list[str] - alternative names (AKAs)
        self.dob = dob                             # str | None - date of birth
        self.nationality = nationality              # str | None - country code or name
        self.ids = ids                             # list[str] - passport/ID numbers
        self.source = source                        # str - source code (e.g., "OFAC_SDN", "UK_OFSI")
        self.source_list_date = source_list_date    # str | None - publication date of the list


class SanctionsListSource(ABC):
    """
    Abstract base class for all sanctions list sources.

    Each sanctions list provider (OFAC, UK OFSI, EU, UN, etc.) must implement
    this interface to be integrated into the screening system.

    Example implementations:
    - OFACSDNSource: US OFAC Specially Designated Nationals list
    - UKOFSISource: UK Office of Financial Sanctions Implementation list
    - EUSanctionsSource: EU consolidated sanctions list
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """
        Human-readable name of this sanctions source.

        Examples: "US OFAC SDN", "UK OFSI", "EU Consolidated List"
        """
        pass

    @property
    @abstractmethod
    def source_code(self) -> str:
        """
        Short code identifying this source (used in configuration and API responses).

        Examples: "OFAC_SDN", "UK_OFSI", "EU_SANCTIONS"
        """
        pass

    @abstractmethod
    def load(self, force_download: bool = False) -> None:
        """
        Download, cache, and parse the sanctions list.

        Args:
            force_download: If True, download fresh data even if cache exists.
                          If False, use cached data if available.

        Raises:
            Exception: If the list cannot be downloaded or parsed.
        """
        pass

    @abstractmethod
    def get_entries(self) -> list[SanctionsEntry]:
        """
        Return all loaded sanctions entries from this source.

        Returns:
            List of SanctionsEntry objects. Returns empty list if not loaded.
        """
        pass

    @property
    @abstractmethod
    def list_date(self) -> Optional[str]:
        """
        Publication date of the loaded sanctions list.

        Returns:
            ISO date string (e.g., "2025-01-15") or None if unavailable.
        """
        pass

    @property
    @abstractmethod
    def entry_count(self) -> int:
        """
        Number of entries loaded from this source.

        Returns:
            Count of entries. Returns 0 if not loaded.
        """
        pass

    @abstractmethod
    def is_loaded(self) -> bool:
        """
        Check if the list has been successfully loaded.

        Returns:
            True if load() has been called successfully, False otherwise.
        """
        pass
