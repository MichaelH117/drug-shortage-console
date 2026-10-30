"""Normalize free-text openFDA ``availability`` to Available / Limited / Unavailable / Unknown (owner: data-engineer,
AC-2.5). The mapping lives in ``config/availability_map.yaml``; nothing is hard-coded here."""

from __future__ import annotations

import math
import re

BUCKETS = ("Available", "Limited", "Unavailable", "Unknown")
RANK = {"Available": 0, "Limited": 1, "Unavailable": 2}   # Unknown has no rank (DESIGN 3.5)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_missing(raw) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return isinstance(raw, str) and not raw.strip()


def normalize_availability(raw: str | None, mapping: dict) -> tuple[str, bool]:
    """Return ``(bucket, was_mapped)``.

    * A missing value gives ``(missing_bucket, True)``: it is a known state, counted separately by DQ.
    * A value in the exact list or matching a prefix rule gives its bucket and ``True``.
    * Anything else gives ``(unmapped_bucket, False)``; DQ reports it by value.
    """
    if _is_missing(raw):
        return mapping.get("missing_bucket", "Unknown"), True
    folded = _fold(str(raw))
    for bucket, values in (mapping.get("buckets") or {}).items():
        if folded in {_fold(v) for v in values or []}:
            return bucket, True
    for rule in mapping.get("prefixes") or []:
        if folded.startswith(_fold(rule["prefix"])):
            return rule["bucket"], True
    return mapping.get("unmapped_bucket", "Unknown"), False


def is_missing(raw) -> bool:
    return _is_missing(raw)
