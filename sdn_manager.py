
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request

from models import AlgorithmType, ScreeningRequest, MatchDetail
from utils import normalize, string_similarity

log = logging.getLogger("ofac_api")

OFAC_SDN_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
SDN_CACHE_PATH = Path("sdn_cache.xml")

# Per-algorithm thresholds: (BLOCKED, REVIEW)
# Each pair is calibrated so that the effective sensitivity is roughly equivalent
# across algorithms even though their raw score distributions differ.
ALGORITHM_THRESHOLDS: dict[AlgorithmType, tuple[float, float]] = {
    AlgorithmType.JARO_WINKLER: (0.88, 0.80),  # Original calibration
    AlgorithmType.LEVENSHTEIN:  (0.85, 0.75),  # Edit distance penalises harder; lower cutoffs
    AlgorithmType.NGRAM:        (0.75, 0.65),  # Bigram Dice scores lower for near-matches
}

# Convenience aliases kept for callers that reference the Jaro-Winkler defaults directly
MATCH_THRESHOLD  = ALGORITHM_THRESHOLDS[AlgorithmType.JARO_WINKLER][0]
REVIEW_THRESHOLD = ALGORITHM_THRESHOLDS[AlgorithmType.JARO_WINKLER][1]


class SDNEntry:
    __slots__ = ("uid", "name", "sdn_type", "programs", "aliases", "dob", "nationality", "ids")

    def __init__(self, uid, name, sdn_type, programs, aliases, dob, nationality, ids):
        self.uid         = uid
        self.name        = name
        self.sdn_type    = sdn_type
        self.programs    = programs
        self.aliases     = aliases          # list[str]
        self.dob         = dob              # str | None
        self.nationality = nationality      # str | None
        self.ids         = ids              # list[str] – passport / ID numbers


class SDNListManager:
    """Downloads, caches and parses the OFAC SDN XML list."""

    _NS = {"sdn": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN"}

    def __init__(self):
        self._entries: list[SDNEntry] = []
        self._list_date: Optional[str] = None
        self._loaded = False

    # ── Public helpers ──────────────────────────────────────────────────────

    def ensure_loaded(self):
        if not self._loaded:
            self.load()

    def load(self, force_download: bool = False):
        xml_bytes = self._fetch_xml(SDN_CACHE_PATH, OFAC_SDN_XML_URL, force_download)
        if xml_bytes:
            self._parse(xml_bytes)
        else:
            log.warning("SDN list unavailable – falling back to empty list.")
        self._loaded = True

    @property
    def list_date(self) -> Optional[str]:
        return self._list_date

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    # ── Screening ───────────────────────────────────────────────────────────

    def screen(self, request: ScreeningRequest) -> list[MatchDetail]:
        self.ensure_loaded()
        query_name = normalize(request.full_name)
        algorithm = request.algorithm.value
        _, review_threshold = ALGORITHM_THRESHOLDS[request.algorithm]
        results: list[MatchDetail] = []

        for entry in self._entries:
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
            if request.national_id and any(request.national_id.upper() == eid.upper() for eid in entry.ids):
                reasons.append("ID number match")
            if request.nationality and entry.nationality:
                if request.nationality.upper() == entry.nationality.upper():
                    reasons.append("Nationality match")
            if request.date_of_birth and entry.dob:
                if str(request.date_of_birth) in entry.dob or entry.dob in str(request.date_of_birth):
                    reasons.append("DOB match")

            results.append(MatchDetail(
                sdn_name     = entry.name,
                sdn_type     = entry.sdn_type,
                sdn_program  = ", ".join(entry.programs) or "UNKNOWN",
                score        = round(best_score, 4),
                match_reason = "; ".join(reasons),
            ))

        # Sort descending by score, keep top 5
        results.sort(key=lambda m: m.score, reverse=True)
        return results[:5]

    # ── Private: download / cache ───────────────────────────────────────────

    @staticmethod
    def _fetch_xml(cache_path: Path, url: str, force: bool) -> Optional[bytes]:
        if not force and cache_path.exists():
            log.info(f"Using cached SDN list at {cache_path}")
            return cache_path.read_bytes()
        log.info(f"Downloading SDN list from {url} …")
        try:
            req = Request(url, headers={"User-Agent": "OFAC-Screening-API/1.0"})
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
            cache_path.write_bytes(data)
            log.info(f"Downloaded {len(data):,} bytes → {cache_path}")
            return data
        except Exception as exc:
            log.error(f"Failed to download SDN list: {exc}")
            return None

    # ── Private: XML parsing ────────────────────────────────────────────────

    def _parse(self, xml_bytes: bytes):
        log.info("Parsing SDN XML …")
        root = ET.fromstring(xml_bytes)
        ns   = self._detect_namespace(root)
        entries = []

        pub_info = root.find(f"{ns}publshInformation") or root.find(f"{ns}publishInformation")
        if pub_info is not None:
            date_el = pub_info.find(f"{ns}Publish_Date") or pub_info.find(f"{ns}publish_date")
            if date_el is not None:
                self._list_date = date_el.text

        sdn_list = root.find(f"{ns}sdnList") or root
        for entry_el in sdn_list.findall(f"{ns}sdnEntry"):
            uid     = self._text(entry_el, f"{ns}uid")
            fname   = self._text(entry_el, f"{ns}firstName")
            lname   = self._text(entry_el, f"{ns}lastName")
            name    = normalize(f"{fname} {lname}".strip()) if fname else normalize(lname or "")
            sdn_type = self._text(entry_el, f"{ns}sdnType") or ""

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
                    ak  = normalize(f"{afn} {aln}".strip()) if afn else normalize(aln or "")
                    if ak:
                        aliases.append(ak)

            # DOB
            dob = None
            dob_list = entry_el.find(f"{ns}dateOfBirthList")
            if dob_list is not None:
                for d in dob_list.findall(f"{ns}dateOfBirthItem"):
                    dob = self._text(d, f"{ns}dateOfBirth")

            # Nationality
            nat = None
            nat_list = entry_el.find(f"{ns}nationalityList")
            if nat_list is not None:
                for n in nat_list.findall(f"{ns}nationality"):
                    nat = self._text(n, f"{ns}country")
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
                entries.append(SDNEntry(uid, name, sdn_type, programs, aliases, dob, nat, ids))

        self._entries = entries
        log.info(f"Loaded {len(entries):,} SDN entries (list date: {self._list_date})")

    @staticmethod
    def _detect_namespace(root: ET.Element) -> str:
        tag = root.tag
        m = re.match(r"\{(.+?)\}", tag)
        return f"{{{m.group(1)}}}" if m else ""

    @staticmethod
    def _text(el: ET.Element, tag: str) -> Optional[str]:
        child = el.find(tag)
        return child.text.strip() if child is not None and child.text else None
