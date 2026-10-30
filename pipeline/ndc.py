"""NDC normalization (ML-01, AC-4.1). Pure functions; they never raise.

Output format: ``ndc11`` is 11 plain digits (5-4-2 with no hyphens), and
``product_ndc9`` is its first 9 digits (labeler 5 + product 4).

Rules (DESIGN section 4):
- 4-4-2 -> "0" + labeler
- 5-3-2 -> "0" + product
- 5-4-1 -> "0" + package
- 5-4-2 (already 11 digits with hyphens) -> as is
- 11 digits, no hyphens -> as is
- 10 digits, no hyphens -> reason "ambiguous_10_digit_unhyphenated" (we do not guess
  which segment lost its zero; tiers 2 and 3 can still match by name)
- letters, wrong length, bad segments, empty -> a reason string, never an exception
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_PAD = {(4, 4, 2): 0, (5, 3, 2): 1, (5, 4, 1): 2, (5, 4, 2): None}
_PRODUCT_PAD = {(4, 4): 0, (5, 3): 1, (5, 4): None}
_PREFIX = re.compile(r"^\s*NDC[\s:#-]*", re.IGNORECASE)
_PRESENTATION = re.compile(r"NDC[\s:#]*(\d{4,5}-\d{3,4}-\d{1,2})", re.IGNORECASE)
_BARE = re.compile(r"(?<![\d-])(\d{4,5}-\d{3,4}-\d{1,2})(?![\d-])")


@dataclass(frozen=True)
class NdcResult:
    ndc11: str | None
    product_ndc9: str | None
    reason: str | None  # set iff ndc11 is None


def _fail(reason: str) -> NdcResult:
    return NdcResult(None, None, reason)


def _clean(raw) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    s = _PREFIX.sub("", raw).strip()
    return s or None


def normalize_ndc(raw: str | None) -> NdcResult:
    """Normalize a package NDC to 11 digits, or return a reason. Never raises."""
    try:
        s = _clean(raw)
        if s is None:
            return _fail("empty")
        s = s.replace(" ", "")
        if re.search(r"[^0-9-]", s):
            return _fail("non_numeric")
        if "-" in s:
            parts = s.split("-")
            if len(parts) != 3 or any(p == "" for p in parts):
                return _fail("bad_segment_count")
            key = tuple(len(p) for p in parts)
            if key not in _PAD:
                return _fail(f"bad_segment_lengths_{'-'.join(map(str, key))}")
            i = _PAD[key]
            if i is not None:
                parts[i] = "0" + parts[i]
            ndc11 = "".join(parts)
        else:
            if len(s) == 10:
                return _fail("ambiguous_10_digit_unhyphenated")
            if len(s) != 11:
                return _fail(f"wrong_length_{len(s)}")
            ndc11 = s
        return NdcResult(ndc11, ndc11[:9], None)
    except Exception as exc:  # defensive: the contract says never raise
        return _fail(f"error_{type(exc).__name__}")


def normalize_product_ndc(raw: str | None) -> str | None:
    """Normalize a 2-segment product NDC ("0378-6860") to 9 digits, else None."""
    try:
        s = _clean(raw)
        if s is None:
            return None
        s = s.replace(" ", "")
        parts = s.split("-")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            if len(parts) == 1 and s.isdigit() and len(s) == 9:
                return s
            return None
        key = tuple(len(p) for p in parts)
        if key not in _PRODUCT_PAD:
            return None
        i = _PRODUCT_PAD[key]
        if i is not None:
            parts[i] = "0" + parts[i]
        return "".join(parts)
    except Exception:
        return None


def extract_ndc_from_presentation(text: str) -> str | None:
    """Return the raw NDC in "... (NDC 0378-6860-77)", else the one bare hyphenated NDC, else None."""
    try:
        if not isinstance(text, str) or not text:
            return None
        m = _PRESENTATION.search(text)
        if m:
            return m.group(1)
        bare = _BARE.findall(text)
        return bare[0] if len(bare) == 1 else None
    except Exception:
        return None
