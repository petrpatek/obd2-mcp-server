"""
DTC (Diagnostic Trouble Code) database — brand-agnostic.

Loads codes from the dtc_db/ file tree (built by the Apify scraper).
Supports all brands + generic OBD-II codes. Data is loaded lazily
on first access per brand.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_DIR = Path(__file__).parent / "dtc_db"

# ---------------------------------------------------------------------------
# Standard OBD-II prefix meanings
# ---------------------------------------------------------------------------
DTC_CATEGORIES = {
    "P0": "Powertrain — SAE standard",
    "P1": "Powertrain — Manufacturer-specific",
    "P2": "Powertrain — SAE standard (extended)",
    "P3": "Powertrain — SAE/Manufacturer shared",
    "B0": "Body — SAE standard",
    "B1": "Body — Manufacturer-specific",
    "B2": "Body — Manufacturer-specific (extended)",
    "C0": "Chassis — SAE standard",
    "C1": "Chassis — Manufacturer-specific",
    "U0": "Network — SAE standard",
    "U1": "Network — Manufacturer-specific",
    "U2": "Network — Manufacturer-specific (extended)",
}

# ---------------------------------------------------------------------------
# Lazy-loaded caches
# ---------------------------------------------------------------------------
_brand_index: dict[str, dict] | None = None       # slug → brand summary
_generic_codes: dict[str, dict] | None = None      # code → {faultLocation, ...}
_brand_codes: dict[str, dict[str, dict]] = {}       # slug → {code → entry}
_brand_procedures: dict[str, list[dict]] = {}        # slug → [procedure pages]
_brand_submodels: dict[str, list[dict]] = {}         # slug → [submodel entries]


def _load_json(path: Path) -> Any:
    """Load a JSON file, returning None on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.debug("Could not load %s: %s", path, e)
        return None


def _ensure_brand_index() -> dict[str, dict]:
    """Load the top-level index.json once."""
    global _brand_index
    if _brand_index is None:
        data = _load_json(DB_DIR / "index.json")
        if data and "brands" in data:
            _brand_index = {b["slug"]: b for b in data["brands"]}
        else:
            _brand_index = {}
    return _brand_index


def _ensure_generic_codes() -> dict[str, dict]:
    """Load all generic code files (pcodes, bcodes, ccodes, ucodes) once."""
    global _generic_codes
    if _generic_codes is None:
        _generic_codes = {}
        generic_dir = DB_DIR / "generic"
        if generic_dir.is_dir():
            for json_file in generic_dir.glob("*.json"):
                data = _load_json(json_file)
                if data and "codes" in data:
                    for entry in data["codes"]:
                        code = entry.get("code", "").upper()
                        if code:
                            _generic_codes[code] = entry
        logger.debug("Loaded %d generic codes", len(_generic_codes))
    return _generic_codes


def _ensure_brand_codes(slug: str) -> dict[str, dict]:
    """Load codes for a specific brand, keyed by code."""
    if slug not in _brand_codes:
        codes_path = DB_DIR / slug / "codes.json"
        data = _load_json(codes_path)
        code_map: dict[str, dict] = {}
        if isinstance(data, list):
            for entry in data:
                code = entry.get("code", "").upper()
                if code:
                    code_map[code] = entry
        _brand_codes[slug] = code_map
    return _brand_codes[slug]


def _ensure_brand_procedures(slug: str) -> list[dict]:
    """Load procedures for a specific brand."""
    if slug not in _brand_procedures:
        procs_path = DB_DIR / slug / "procedures.json"
        data = _load_json(procs_path)
        _brand_procedures[slug] = data if isinstance(data, list) else []
    return _brand_procedures[slug]


def _ensure_brand_submodels(slug: str) -> list[dict]:
    """Load submodels for a specific brand."""
    if slug not in _brand_submodels:
        subs_path = DB_DIR / slug / "submodels.json"
        data = _load_json(subs_path)
        _brand_submodels[slug] = data if isinstance(data, list) else []
    return _brand_submodels[slug]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_brands() -> list[dict]:
    """Return list of all available brands with summary info."""
    index = _ensure_brand_index()
    return sorted(index.values(), key=lambda b: b.get("name", ""))


def lookup_dtc(code: str, brand: str | None = None) -> str:
    """Look up a DTC code and return its description.

    Search order:
      1. Brand-specific codes (if brand given)
      2. All brand databases (scan all)
      3. Generic OBD-II codes
      4. Fallback description from prefix
    """
    code = code.upper().strip()

    # 1. Try specific brand first
    if brand:
        slug = brand.lower().strip()
        codes = _ensure_brand_codes(slug)
        if code in codes:
            entry = codes[code]
            return entry.get("faultLocation") or entry.get("probableCause") or "No description"

    # 2. Search all brands
    index = _ensure_brand_index()
    for slug in index:
        if slug == (brand or "").lower():
            continue  # Already checked
        codes = _ensure_brand_codes(slug)
        if code in codes:
            entry = codes[code]
            desc = entry.get("faultLocation") or entry.get("probableCause") or ""
            if desc:
                return desc

    # 3. Generic codes
    generic = _ensure_generic_codes()
    if code in generic:
        entry = generic[code]
        return entry.get("faultLocation") or entry.get("probableCause") or "No description"

    # 4. Fallback
    return _describe_unknown_dtc(code)


def lookup_dtc_full(code: str, brand: str | None = None) -> dict:
    """Look up a DTC code and return the full entry with all fields.

    Returns dict with: code, faultLocation, probableCause, detailUrl, source (brand slug or 'generic').
    """
    code = code.upper().strip()

    # Try specific brand
    if brand:
        slug = brand.lower().strip()
        codes = _ensure_brand_codes(slug)
        if code in codes:
            return {**codes[code], "source": slug}

    # Search all brands
    index = _ensure_brand_index()
    for slug in index:
        if slug == (brand or "").lower():
            continue
        codes = _ensure_brand_codes(slug)
        if code in codes:
            return {**codes[code], "source": slug}

    # Generic
    generic = _ensure_generic_codes()
    if code in generic:
        return {**generic[code], "source": "generic"}

    return {
        "code": code,
        "faultLocation": _describe_unknown_dtc(code),
        "probableCause": "",
        "detailUrl": None,
        "source": None,
    }


def get_procedures(brand: str, procedure_type: str | None = None) -> list[dict]:
    """Get diagnostic procedures for a brand.

    Args:
        brand: Brand slug (e.g. 'ford', 'toyota')
        procedure_type: Optional filter — 'accessing', 'clearing', 'test', 'general', etc.

    Returns list of procedure page dicts, each containing title, url, and procedures array.
    """
    slug = brand.lower().strip()
    all_procs = _ensure_brand_procedures(slug)
    if not procedure_type:
        return all_procs

    # Filter to pages that have at least one procedure matching the type
    filtered = []
    for page in all_procs:
        matching = [p for p in page.get("procedures", []) if p.get("type") == procedure_type]
        if matching:
            filtered.append({**page, "procedures": matching})
    return filtered


def get_submodels(brand: str) -> list[dict]:
    """Get submodel/vehicle data for a brand."""
    return _ensure_brand_submodels(brand.lower().strip())


def find_codes_for_brand(brand: str) -> list[dict]:
    """Return all manufacturer-specific codes for a brand."""
    slug = brand.lower().strip()
    codes = _ensure_brand_codes(slug)
    return list(codes.values())


def get_dtc_category(code: str) -> str:
    """Get the category description for a DTC code prefix."""
    code = code.upper().strip()
    if len(code) >= 2:
        prefix = code[:2]
        return DTC_CATEGORIES.get(prefix, "Unknown category")
    return "Unknown category"


def _describe_unknown_dtc(code: str) -> str:
    """Generate a generic description for an unknown DTC based on its prefix."""
    code = code.upper()
    if len(code) < 4:
        return "Invalid DTC format"
    category = get_dtc_category(code)
    return f"Unknown code ({category})"
