"""Drug name, dosage form, route and strength normalization (ML-03, DESIGN section 5). Pure.

Rules come from ``config/normalize.yaml``. Matching on these values is exact (after normalization), never
fuzzy, so look-alike names (hydroxyzine / hydralazine) cannot match.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

NORMALIZE_PATH = Path(__file__).resolve().parents[1] / "config" / "normalize.yaml"

_PAREN = re.compile(r"\([^)]*\)")
_NDC = re.compile(r"\bndc[\s:#]*[\d-]+", re.IGNORECASE)
_SPLIT = re.compile(r"\s*(?:[,;/+&]|\band\b|\bwith\b)\s*")
_TOKEN = re.compile(r"[a-z][a-z0-9'-]*")
_NUM = r"(\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_NUMBER_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\b|%")


@lru_cache(maxsize=4)
def _cfg(path: str = str(NORMALIZE_PATH)) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "salts" not in data:
        raise ValueError(f"{path}: normalize config has no `salts`")
    return data


def load_normalize_config() -> dict:
    return _cfg()


def default_salts() -> list[str]:
    return list(_cfg()["salts"])


@lru_cache(maxsize=4)
def _strength_re() -> re.Pattern:
    units = sorted(_cfg()["units"], key=len, reverse=True)
    unit_alt = "|".join(re.escape(u) for u in units)
    return re.compile(
        rf"{_NUM}\s*(%|{unit_alt})(?![a-z])"
        rf"(?:\s*(?:/|per)\s*{_NUM}?\s*(ml|l)(?![a-z]))?",
        re.IGNORECASE,
    )


def _clean_text(text) -> str:
    if not isinstance(text, str):
        return ""
    s = text.lower()
    for ch in "®™©*\"":
        s = s.replace(ch, " ")
    s = _NDC.sub(" ", s)
    s = _PAREN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_strengths(s: str) -> str:
    s = _strength_re().sub(" ", s)
    return _NUMBER_TOKEN.sub(" ", s)


def _tokens(component: str, noise: set[str]) -> list[str]:
    return [t for t in _TOKEN.findall(component) if t not in noise]


def brand_key(name) -> str:
    """Key used for brand-map lookups: lowercase words with strengths, form/device words and salt words removed
    ("Cleocin HCl" and "Cleocin Hydrochloride" both give "cleocin")."""
    noise = set(_cfg()["noise_words"])
    salts = set(_cfg()["salts"])
    toks = _tokens(_strip_strengths(_clean_text(name)), noise)
    kept = [t for t in toks if t not in salts] if toks and toks[0] not in salts else toks
    return " ".join(kept or toks)


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (stdlib, small strings only)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@lru_cache(maxsize=1)
def _lookalike_pairs() -> frozenset:
    return frozenset(frozenset(p) for p in _cfg().get("lookalikes", []))


def is_lookalike(a: str, b: str) -> bool:
    pairs = _lookalike_pairs()
    return any(frozenset({x, y}) in pairs for x in a.split() for y in b.split())


def near_miss(a: str, b: str) -> bool:
    """True when two single-ingredient names look like a misspelling of each other (config ``fuzzy``)."""
    import difflib

    cfg = _cfg().get("fuzzy") or {}
    if not a or not b or a == b or "/" in a or "/" in b:
        return False
    if min(len(a), len(b)) < cfg.get("min_length", 6) or is_lookalike(a, b):
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() < cfg.get("min_ratio", 0.88):
        return False
    return edit_distance(a, b) <= cfg.get("max_edits", 2)


def normalize_ingredient(name: str, brand_map: dict[str, str] | None = None,
                         salts: list[str] | None = None) -> str:
    """Normalize an ingredient name: lowercase, brand -> generic, strengths/forms/salts removed.

    Combination products become sorted components joined by " / ". Salt words are dropped only when they
    follow the active moiety ("lidocaine hydrochloride" -> "lidocaine", but "sodium bicarbonate" stays).
    Returns "" when nothing is left.
    """
    try:
        brand_map = brand_map or {}
        salt_set = set(salts if salts is not None else _cfg()["salts"])
        noise = set(_cfg()["noise_words"])
        s = _strip_strengths(_clean_text(name))
        whole = brand_key(s)
        if whole in brand_map:
            s = _strip_strengths(_clean_text(brand_map[whole]))
        parts: set[str] = set()
        for comp in _SPLIT.split(s):
            toks = _tokens(comp, noise)
            if not toks:
                continue
            key = brand_key(comp)
            if key in brand_map:
                toks = _tokens(_strip_strengths(_clean_text(brand_map[key])), noise) or toks
            if toks[0] not in salt_set:
                toks = [t for t in toks if t not in salt_set] or toks
            parts.add(" ".join(toks))
        return " / ".join(sorted(parts))
    except Exception:
        return ""


def _has_phrase(s: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", s) is not None


def normalize_dosage_form(raw: str) -> str:
    """Map free-text dosage form (or a whole drug name) to a bucket, e.g. "tablet_er". "" if unknown."""
    try:
        s = _clean_text(raw).replace(",", " ")
        s = re.sub(r"\s+", " ", s)
        if not s:
            return ""
        cfg = _cfg()
        bucket = ""
        for entry in cfg["dosage_forms"]:
            if any(_has_phrase(s, p) for p in entry["patterns"]):
                bucket = entry["bucket"]
                break
        if bucket in ("tablet", "capsule"):
            mods = cfg.get("release_modifiers", {})
            if any(_has_phrase(s, w) for w in mods.get("er", [])):
                bucket += "_er"
            elif any(_has_phrase(s, w) for w in mods.get("dr", [])):
                bucket += "_dr"
        return bucket
    except Exception:
        return ""


def default_route(dosage_form_norm: str) -> str | None:
    if not dosage_form_norm:
        return None
    return _cfg()["default_routes"].get(dosage_form_norm)


def normalize_route(raw) -> frozenset[str]:
    """Route text or list (openfda.route) -> a set of canonical routes. Empty set if unknown."""
    try:
        if raw is None:
            return frozenset()
        items = raw if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
        aliases = _cfg()["route_aliases"]
        out: set[str] = set()
        for item in items:
            if not isinstance(item, str):
                continue
            for part in re.split(r"\s*(?:[,;/|]|\bor\b)\s*", item.lower().strip()):
                part = part.strip()
                if part:
                    out.add(aliases.get(part, part))
        return frozenset(out)
    except Exception:
        return frozenset()


def _expand(routes: frozenset[str]) -> frozenset[str]:
    group = frozenset(_cfg()["route_groups"]["parenteral"])
    if "parenteral" in routes:
        return routes | group
    if routes & group:
        return routes | {"parenteral"}
    return routes


def routes_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """True when either side is unknown, or the (parenteral-expanded) route sets overlap."""
    if not a or not b:
        return True
    return bool(_expand(a) & _expand(b))


def _num(x: str | None) -> float | None:
    if x is None or x == "":
        return None
    return float(x.replace(",", ""))


def strength_forms(raw) -> frozenset[tuple[str, float]]:
    """All comparable forms of the first strength in ``raw``: concentration per mL and, for "100 mg/10 mL",
    also the total amount. Empty if none is found."""
    try:
        if isinstance(raw, (list, tuple)):
            raw = next((r for r in raw if isinstance(r, str) and r.strip()), None)
        s = _clean_text(raw) if isinstance(raw, str) else ""
        if not s:
            return frozenset()
        # keep parenthetical concentrations out of the way: the first strength wins
        m = _strength_re().search(s)
        if not m:
            return frozenset()
        n1, unit, n2, den = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        v1 = _num(n1)
        if unit == "%":
            return frozenset({("mg/ml", round(v1 * 10.0, 6))})
        canon, mult = _cfg()["units"][unit]
        amount = v1 * float(mult)
        if den:
            vol = _num(n2) if n2 else 1.0
            if den.lower() == "l":
                vol *= 1000.0
            if not vol:
                return frozenset()
            forms = {(f"{canon}/ml", round(amount / vol, 6))}
            if n2 and vol != 1.0:
                forms.add((canon, round(amount, 6)))
            return frozenset(forms)
        return frozenset({(canon, round(amount, 6))})
    except Exception:
        return frozenset()


def parse_strength(raw: str | list | None) -> tuple[float, str] | None:
    """Primary strength as (value, unit), concentrations per mL, e.g. "50 mg/5 mL" -> (10.0, "mg/ml")."""
    forms = strength_forms(raw)
    if not forms:
        return None
    conc = [f for f in forms if f[0].endswith("/ml")]
    unit, value = (conc or sorted(forms))[0]
    return value, unit


def format_strength(st: tuple[float, str] | None) -> str | None:
    if st is None:
        return None
    value, unit = st
    return f"{value:g} {unit}"


def strengths_equal(a: frozenset, b: frozenset) -> bool | None:
    """None if either side has no strength, else whether any comparable form is equal."""
    if not a or not b:
        return None
    for ua, va in a:
        for ub, vb in b:
            if ua == ub and abs(va - vb) <= 1e-3 * max(1.0, abs(va)):
                return True
    return False
