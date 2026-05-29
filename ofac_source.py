"""
OFAC SDN (Specially Designated Nationals) list source implementation.

This module implements the US Treasury OFAC sanctions list as a SanctionsListSource.
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request

from sanctions_source import SanctionsListSource, SanctionsEntry
from utils import normalize

log = logging.getLogger("ofac_api")


class OFACSDNSource(SanctionsListSource):
    """
    US OFAC Specially Designated Nationals (SDN) list.

    Downloads and parses the official OFAC SDN XML from the US Treasury.
    URL: https://www.treasury.gov/ofac/downloads/sdn.xml
    """

    _NS = {"sdn": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN"}
    OFAC_SDN_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

    def __init__(self, cache_path: Path = Path("cache/ofac_sdn.xml")):
        """
        Initialize OFAC SDN source.

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
        return "US OFAC SDN"

    @property
    def source_code(self) -> str:
        return "OFAC_SDN"

    def load(self, force_download: bool = False) -> None:
        """Load the OFAC SDN list from cache or download fresh."""
        xml_bytes = self._fetch_xml(force_download)
        if xml_bytes:
            self._parse(xml_bytes)
        else:
            log.warning("OFAC SDN list unavailable – falling back to empty list.")
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
        """Download or load cached OFAC SDN XML."""
        if not force and self._cache_path.exists():
            log.info(f"Using cached OFAC SDN list at {self._cache_path}")
            return self._cache_path.read_bytes()

        log.info(f"Downloading OFAC SDN list from {self.OFAC_SDN_XML_URL} …")
        try:
            # Ensure cache directory exists
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)

            req = Request(self.OFAC_SDN_XML_URL, headers={"User-Agent": "OFAC-Screening-API/2.0"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            self._cache_path.write_bytes(data)
            log.info(f"Downloaded {len(data):,} bytes → {self._cache_path}")
            return data
        except Exception as exc:
            log.error(f"Failed to download OFAC SDN list: {exc}")
            return None

    # ── Private: XML parsing ────────────────────────────────────────────────

    def _parse(self, xml_bytes: bytes):
        """Parse OFAC SDN XML and populate entries."""
        log.info("Parsing OFAC SDN XML …")
        root = ET.fromstring(xml_bytes)
        ns = self._detect_namespace(root)
        entries = []

        # Extract publication date
        pub_info = root.find(f"{ns}publshInformation") or root.find(f"{ns}publishInformation")
        if pub_info is not None:
            date_el = pub_info.find(f"{ns}Publish_Date") or pub_info.find(f"{ns}publish_date")
            if date_el is not None:
                self._list_date = date_el.text

        # Parse SDN entries
        sdn_list = root.find(f"{ns}sdnList") or root
        for entry_el in sdn_list.findall(f"{ns}sdnEntry"):
            uid = self._text(entry_el, f"{ns}uid")
            fname = self._text(entry_el, f"{ns}firstName")
            lname = self._text(entry_el, f"{ns}lastName")
            name = normalize(f"{fname} {lname}".strip()) if fname else normalize(lname or "")
            sdn_type = self._text(entry_el, f"{ns}sdnType") or "Entity"

            # Programs
            programs = [
                self._text(p, f"{ns}program") or ""
                for p in (entry_el.find(f"{ns}programList") or [])
            ]

            # Aliases
            aliases = []
            aka_list = entry_el.find(f"{ns}akaList")
            if aka_list is not None:
                for aka in aka_list.findall(f"{ns}aka"):
                    afn = self._text(aka, f"{ns}firstName")
                    aln = self._text(aka, f"{ns}lastName")
                    ak = normalize(f"{afn} {aln}".strip()) if afn else normalize(aln or "")
                    if ak:
                        aliases.append(ak)

            # DOB
            dob = None
            dob_list = entry_el.find(f"{ns}dateOfBirthList")
            if dob_list is not None:
                for d in dob_list.findall(f"{ns}dateOfBirthItem"):
                    dob = self._text(d, f"{ns}dateOfBirth")
                    if dob:
                        break

            # Nationality
            nat = None
            nat_list = entry_el.find(f"{ns}nationalityList")
            if nat_list is not None:
                for n in nat_list.findall(f"{ns}nationality"):
                    nat = self._text(n, f"{ns}country")
                    if nat:
                        break

            # ID numbers (passports etc.)
            ids = []
            id_list = entry_el.find(f"{ns}idList")
            if id_list is not None:
                for id_el in id_list.findall(f"{ns}id"):
                    id_num = self._text(id_el, f"{ns}idNumber")
                    if id_num:
                        ids.append(id_num.strip())

            if name:
                entries.append(SanctionsEntry(
                    uid=uid or "",
                    name=name,
                    entry_type=sdn_type,
                    programs=programs,
                    aliases=aliases,
                    dob=dob,
                    nationality=nat,
                    ids=ids,
                    source=self.source_code,
                    source_list_date=self._list_date
                ))

        self._entries = entries
        log.info(f"Loaded {len(entries):,} OFAC SDN entries (list date: {self._list_date})")

    @staticmethod
    def _detect_namespace(root: ET.Element) -> str:
        """Extract XML namespace from root element tag."""
        tag = root.tag
        m = re.match(r"\{(.+?)\}", tag)
        return f"{{{m.group(1)}}}" if m else ""

    @staticmethod
    def _text(el: ET.Element, tag: str) -> Optional[str]:
        """Safely extract text content from XML element."""
        child = el.find(tag)
        return child.text.strip() if child is not None and child.text else None
