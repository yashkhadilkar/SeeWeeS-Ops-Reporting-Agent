"""
Item Master lookup tables, transcribed from the SeeWeeS Specialty Dispatch
Playbook, Appendix A. Used by the reconciliation logic in csv_tools.py to
resolve dirty CSV rows (missing IDs, name variants, legacy codes) into
canonical items.

If the playbook is updated, update these tables to match. Each section below
corresponds to a subsection of Appendix A.
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# A.1 Canonical Item Master (Authoritative)
# ---------------------------------------------------------------------------
# Note: item_id is NOT globally unique. Example: 10021 maps to both RMD-100
# (Remdesivir 100mg) and RMD-200 (Remdesivir 200mg). Disambiguation happens
# via item_name (rule D6).
CANONICAL_ITEMS: List[Dict] = [
    {"canonical_item_id": "RMD-100",    "item_id": 10021, "canonical_item_name": "Remdesivir 100mg",                "medicine_type": "Antiviral",            "temp_control": "Cold (2-8C)",            "product_class": "Antiviral"},
    {"canonical_item_id": "RMD-200",    "item_id": 10021, "canonical_item_name": "Remdesivir 200mg",                "medicine_type": "Antiviral",            "temp_control": "Cold (2-8C)",            "product_class": "Antiviral"},
    {"canonical_item_id": "INS-LIS",    "item_id": 10022, "canonical_item_name": "Insulin Lispro",                  "medicine_type": "Hormone",              "temp_control": "Cold (2-8C)",            "product_class": "Endocrine"},
    {"canonical_item_id": "PMB-KEY",    "item_id": 10035, "canonical_item_name": "Pembrolizumab",                   "medicine_type": "Monoclonal Antibody",  "temp_control": "Cold (2-8C)",            "product_class": "Oncology Biologic"},
    {"canonical_item_id": "EPI-AI",     "item_id": 10040, "canonical_item_name": "Epinephrine Auto-Injector",       "medicine_type": "Emergency Drug",       "temp_control": "Room Temp (20-25C)",     "product_class": "Emergency"},
    {"canonical_item_id": "HEP-SOD",    "item_id": 10050, "canonical_item_name": "Heparin Sodium",                  "medicine_type": "Anticoagulant",        "temp_control": "Room Temp (20-25C)",     "product_class": "Anticoagulant"},
    {"canonical_item_id": "MOR-SUL",    "item_id": 10060, "canonical_item_name": "Morphine Sulfate",                "medicine_type": "Opioid Analgesic",     "temp_control": "Controlled Storage",     "product_class": "Controlled"},
    {"canonical_item_id": "ALB-INH",    "item_id": 10070, "canonical_item_name": "Albuterol Inhaler",               "medicine_type": "Bronchodilator",       "temp_control": "Room Temp (20-25C)",     "product_class": "Respiratory"},
    {"canonical_item_id": "EXP-ONC-CT", "item_id": 99999, "canonical_item_name": "Experimental Oncology Drug (Clinical Trial)", "medicine_type": "Clinical Trial Drug", "temp_control": "Strict Cold Chain (-20C)", "product_class": "Clinical Trial"},
    {"canonical_item_id": "LEV-INH",    "item_id": 10071, "canonical_item_name": "Levalbuterol Inhaler",            "medicine_type": "Bronchodilator",       "temp_control": "Room Temp (20-25C)",     "product_class": "Respiratory"},
    {"canonical_item_id": "INS-ASP",    "item_id": 10023, "canonical_item_name": "Insulin Aspart",                  "medicine_type": "Hormone",              "temp_control": "Cold (2-8C)",            "product_class": "Endocrine"},
]


# ---------------------------------------------------------------------------
# A.2 Name Alias / Variant Table
# ---------------------------------------------------------------------------
# Maps typo'd or variant item_name values to canonical_item_id.
# Matching is case-insensitive and whitespace-normalized (see helper below).
NAME_ALIASES: Dict[str, str] = {
    "Remdesivir 100 mg":          "RMD-100",
    "Remdesivir 200 mg":          "RMD-200",
    "Pembrolizumab (Keytruda)":   "PMB-KEY",
    "EpiPen Auto Injector":       "EPI-AI",
    "Heparin Na":                 "HEP-SOD",
    "Morphine Sulphate":          "MOR-SUL",
    "Albuterol Inhaler 90mcg":    "ALB-INH",
}


# ---------------------------------------------------------------------------
# A.3 Legacy / Deprecated / Invalid Identifier Mapping
# ---------------------------------------------------------------------------
# Maps old / truncated / placeholder item_id values to canonical_item_id.
LEGACY_ID_MAP: Dict[int, Dict[str, str]] = {
    10020: {"canonical_item_id": "RMD-100",    "rule": "LEGACY_ID_MAP",  "rationale": "Vendor legacy ID for Remdesivir 100mg"},
    20021: {"canonical_item_id": "RMD-200",    "rule": "LEGACY_ID_MAP",  "rationale": "Old system used 200xx for strength variants"},
    1070:  {"canonical_item_id": "ALB-INH",    "rule": "LEGACY_ID_MAP",  "rationale": "Truncated ID found in older CSV exports"},
    99999: {"canonical_item_id": "EXP-ONC-CT", "rule": "SPECIAL_CASE",   "rationale": "Clinical trial placeholder ID; requires strict cold chain"},
}


# ---------------------------------------------------------------------------
# A.4 Substitution Policy
# ---------------------------------------------------------------------------
SUBSTITUTIONS: List[Dict] = [
    {"canonical_item_id": "ALB-INH", "substitute_canonical_item_id": "LEV-INH",
     "allowed_when": "Same medicine_type=Bronchodilator AND temp_control matches AND hospital approves interchange",
     "not_allowed_when": "Patient-specific order forbids substitution or formulary restriction applies"},
    {"canonical_item_id": "INS-LIS", "substitute_canonical_item_id": "INS-ASP",
     "allowed_when": "Cold-chain endocrine hormones only; substitution requires pharmacist approval",
     "not_allowed_when": "Patient-specific shipment or dosing regimen differs"},
]


# ---------------------------------------------------------------------------
# A.5 unique_item_id Format Rules (per product_class)
# ---------------------------------------------------------------------------
UNIQUE_ID_PATTERNS: Dict[str, re.Pattern] = {
    "Antiviral":         re.compile(r"^RMD-\d{4}-\d{4}$"),
    "Oncology Biologic": re.compile(r"^PMB-\d{4}-\d{5}$"),
    "Emergency":         re.compile(r"^EPI-\d{4}-\d{4}$"),
    "Controlled":        re.compile(r"^CTRL-\d{4}-\d{6}$"),
    "Respiratory":       re.compile(r"^INH-\d{4}-\d{5}$"),
    "Clinical Trial":    re.compile(r"^CT-\d{4}-[A-Z0-9]{6}$"),
}


# ---------------------------------------------------------------------------
# Helper indices (built once at import time for fast lookup)
# ---------------------------------------------------------------------------
# Index by canonical_item_id -> full row dict
_CANONICAL_BY_ID: Dict[str, Dict] = {row["canonical_item_id"]: row for row in CANONICAL_ITEMS}

# Index by item_id -> list of canonical rows (because item_id is NOT unique)
_CANONICAL_BY_ITEM_ID: Dict[int, List[Dict]] = {}
for row in CANONICAL_ITEMS:
    _CANONICAL_BY_ITEM_ID.setdefault(row["item_id"], []).append(row)

# Index by normalized canonical_item_name -> canonical_item_id
def _norm(s: str) -> str:
    """Normalize a name for matching: lowercase, collapse whitespace."""
    if s is None:
        return ""
    return " ".join(str(s).strip().lower().split())

_CANONICAL_BY_NAME: Dict[str, str] = {
    _norm(row["canonical_item_name"]): row["canonical_item_id"] for row in CANONICAL_ITEMS
}

# Normalized aliases
_ALIASES_NORM: Dict[str, str] = {_norm(k): v for k, v in NAME_ALIASES.items()}


# ---------------------------------------------------------------------------
# Public lookup functions
# ---------------------------------------------------------------------------
def lookup_canonical(canonical_item_id: str) -> Optional[Dict]:
    """Return the full canonical row for a given canonical_item_id, or None."""
    return _CANONICAL_BY_ID.get(canonical_item_id)


def find_by_item_id(item_id) -> List[Dict]:
    """
    Return all canonical rows that share this item_id (may be 0, 1, or more).
    Returns multiple rows for ambiguous IDs like 10021 (RMD-100 + RMD-200).
    """
    try:
        item_id_int = int(item_id)
    except (ValueError, TypeError):
        return []
    return _CANONICAL_BY_ITEM_ID.get(item_id_int, [])


def find_by_name(item_name: str) -> Optional[str]:
    """
    Resolve an item_name to a canonical_item_id, checking both the canonical
    names (A.1) and the alias table (A.2). Returns canonical_item_id or None.
    """
    n = _norm(item_name)
    if not n:
        return None
    if n in _CANONICAL_BY_NAME:
        return _CANONICAL_BY_NAME[n]
    if n in _ALIASES_NORM:
        return _ALIASES_NORM[n]
    return None


def find_by_legacy_id(item_id) -> Optional[Dict]:
    """
    Look up a legacy / deprecated item_id (A.3). Returns the mapping dict
    {canonical_item_id, rule, rationale} or None.
    """
    try:
        item_id_int = int(item_id)
    except (ValueError, TypeError):
        return None
    return LEGACY_ID_MAP.get(item_id_int)


def validate_unique_id(unique_item_id: str, product_class: str) -> bool:
    """
    Check unique_item_id against the regex for its product_class (A.5).
    Returns True if valid, False otherwise. If we don't have a pattern for
    the product_class, returns True (don't reject what we can't validate).
    """
    if not unique_item_id:
        return False
    pattern = UNIQUE_ID_PATTERNS.get(product_class)
    if pattern is None:
        return True
    return bool(pattern.match(unique_item_id))