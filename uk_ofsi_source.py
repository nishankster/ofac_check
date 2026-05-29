"""
UK OFSI (Office of Financial Sanctions Implementation) list source implementation.

This module implements the UK sanctions list as a SanctionsListSource.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request

from sanctions_source import SanctionsListSource, SanctionsEntry
from utils import normalize

log = logging.getLogger("ofac_api")


class UKOFSISource(SanctionsListSource):
    """
    UK Office of Financial Sanctions Implementation (OFSI) sanctions list.

    Downloads and parses the official UK Sanctions List XML from the UK FCDO.
    URL: https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml
    """

    UK_SANCTIONS_XML_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"

    def __init__(self, cache_path: Path = Path("cache/uk_ofsi.xml")):
        """
        Initialize UK OFSI source.

        Args:
            cache_path: Path to cache the downloaded XML file.
        """
        self._cache_path = cache_path
        self._entries: list[SanctionsEntry] = []
        self._list_date: Optional[str] = None
        self._loaded = False

    # ── SanctionsListSource interface implementation ────────────────────────

    @property
    def source_name(self) -> str:
        return "UK OFSI"

    @property
    def source_code(self) -> str:
        return "UK_OFSI"

    def load(self, force_download: bool = False) -> None:
        """Load the UK OFSI list from cache or download fresh."""
        xml_bytes = self._fetch_xml(force_download)
        if xml_bytes:
            self._parse(xml_bytes)
        else:
            log.warning("UK OFSI list unavailable – falling back to empty list.")
        self._loaded = True

    def get_entries(self) -> list[SanctionsEntry]:
        return self._entries

    @property
    def list_date(self) -> Optional[str]:
        return self._list_date

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def is_loaded(self) -> bool:
        return self._loaded

    # ── Private: download / cache ───────────────────────────────────────────

    def _fetch_xml(self, force: bool) -> Optional[bytes]:
        """Download or load cached UK OFSI XML."""
        if not force and self._cache_path.exists():
            log.info(f"Using cached UK OFSI list at {self._cache_path}")
            return self._cache_path.read_bytes()

        log.info(f"Downloading UK OFSI list from {self.UK_SANCTIONS_XML_URL} …")
        try:
            # Ensure cache directory exists
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)

            req = Request(self.UK_SANCTIONS_XML_URL, headers={"User-Agent": "OFAC-Screening-API/2.0"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            self._cache_path.write_bytes(data)
            log.info(f"Downloaded {len(data):,} bytes → {self._cache_path}")
            return data
        except Exception as exc:
            log.error(f"Failed to download UK OFSI list: {exc}")
            return None

    # ── Private: XML parsing ────────────────────────────────────────────────

    def _parse(self, xml_bytes: bytes):
        """Parse UK Sanctions List XML and populate entries."""
        log.info("Parsing UK OFSI XML …")
        root = ET.fromstring(xml_bytes)
        entries = []

        # Try to extract list date from XML metadata
        # UK format may have DateGenerated or similar field
        date_generated = root.find(".//DateGenerated")
        if date_generated is not None and date_generated.text:
            self._list_date = date_generated.text

        # Parse designation entries
        # UK format: <Designations><Designation>...</Designation></Designations>
        designations = root.findall(".//Designation")
        if not designations:
            # Try alternative path
            designations = root.findall(".//designation")

        for designation in designations:
            # Extract unique ID
            uid = self._get_text(designation, "UniqueID") or self._get_text(designation, "OFSIGroupID") or ""

            # Extract names from Names/Name/Name6 structure
            names = []
            primary_name = None
            aliases = []

            names_elem = designation.find("Names")
            if names_elem is not None:
                for name_elem in names_elem.findall("Name"):
                    # Check Name6, Name1, etc.
                    name_text = None
                    for i in range(6, 0, -1):  # Try Name6 down to Name1
                        name_text = self._get_text(name_elem, f"Name{i}")
                        if name_text:
                            break

                    if not name_text:
                        continue

                    # Check name type
                    name_type = self._get_text(name_elem, "NameType") or ""
                    if "primary" in name_type.lower():
                        primary_name = normalize(name_text)
                    else:
                        aliases.append(normalize(name_text))

            if not primary_name and aliases:
                # If no primary name found, use first alias as primary
                primary_name = aliases[0]
                aliases = aliases[1:]

            if not primary_name:
                continue  # Skip entries without names

            # Entity type: Individual, Entity, or Vessel
            entity_type = self._get_text(designation, "IndividualEntityShip") or \
                         self._get_text(designation, "EntityType") or "Entity"

            # Regime/Program name
            regime = self._get_text(designation, "RegimeName") or ""
            programs = [regime] if regime else []

            # Date of birth
            dob = self._get_text(designation, "DateOfBirth") or \
                  self._get_text(designation, "DOB")

            # Nationality
            nationality = self._get_text(designation, "Nationality") or None

            # ID numbers (passport, national ID, etc.)
            ids = []
            passport = self._get_text(designation, "PassportNumber")
            if passport:
                ids.append(passport)

            national_id = self._get_text(designation, "NationalIdentificationNumber")
            if national_id:
                ids.append(national_id)

            # Create sanctions entry
            entries.append(SanctionsEntry(
                uid=uid,
                name=primary_name,
                entry_type=entity_type,
                programs=programs,
                aliases=aliases,
                dob=dob,
                nationality=nationality,
                ids=ids,
                source=self.source_code,
                source_list_date=self._list_date
            ))

        self._entries = entries
        log.info(f"Loaded {len(entries):,} UK OFSI entries (list date: {self._list_date})")

    @staticmethod
    def _get_text(element: ET.Element, tag: str) -> Optional[str]:
        """
        Safely extract text content from XML element.

        Tries both direct child and case-insensitive search.
        """
        # Try direct child first
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()

        # Try case-insensitive search
        child = element.find(tag.lower())
        if child is not None and child.text:
            return child.text.strip()

        return None
