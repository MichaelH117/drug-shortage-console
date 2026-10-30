"""SYNTHETIC hospital: facilities, formulary (with a variant manifest), stock and users (owner: data-engineer, DE-06).

AC-3.2: 3 facilities, 150-300 formulary items, 8 users; a fixed seed gives byte-identical CSVs.
AC-3.3: formulary items mix NDC formats and name variants on purpose. At least 30% of items come from records on the
        fixture shortage list. Every item's variant type and the answer the matcher should give are in
        ``variant_manifest.csv``, so detection can be scored (the KNOWN injected issues).
AC-3.4: every table has ``is_synthetic = true``.

The shortage drugs come from ``tests/fixtures/openfda_page_sample.json`` (real public records), in BOTH demo and live
mode, so the hospital is the same whichever database is built. Nothing here is a real hospital, person or stock level.

Variant types (``variant_type`` in the manifest; expected tier = what a correct matcher should report):

========================  =========================================================================  ==============
variant_type              what the item looks like                                                   expected
========================  =========================================================================  ==============
ndc_as_listed             the shortage package NDC exactly as the FDA prints it (4-4-2, 5-3-2, ...)  match, tier 1
ndc_11digit               the same package NDC as 11 digits with no hyphens                          match, tier 1
ndc_5_4_2_hyphen          the same package padded to 5-4-2 with hyphens                              match, tier 1
ndc_other_package         same labeler + product, a different (synthetic) package code               match, tier 2
ndc_leading_zero_error    11 digits with the pad zero put at the FRONT (a different, wrong NDC);     match, tier 3
                          the name, form, route and strength are still right
name_brand                no NDC; the brand name instead of the generic                              match, tier 3
name_salt                 no NDC; the salt dropped ("Morphine" for "Morphine Sulfate")               match, tier 3
name_case_whitespace      no NDC; upper case and extra spaces                                        match, tier 3
alt_manufacturer          synthetic NDC from another labeler; same ingredient, form, route, strength match, tier 3
diff_strength             synthetic NDC; same ingredient and form, a different strength              no match
diff_route                synthetic NDC; same ingredient, a different dosage form and route          no match
filler                    a common drug that is not on the fixture shortage list                     no match
========================  =========================================================================  ==============

Run: ``python -m generator.hospital [--seed 42] [--out data/demo/seed]``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "openfda_page_sample.json"
DEFAULT_OUT = ROOT / "data" / "demo" / "seed"
N_ITEMS = 200

FACILITIES = [
    # facility_id, name, kind, beds, share of formulary stocked
    ("F-MAIN", "Riverbend Main (SYNTHETIC)", "academic", 500, 0.90),
    ("F-NORTH", "Riverbend North (SYNTHETIC)", "community", 180, 0.60),
    ("F-KIDS", "Riverbend Kids (SYNTHETIC)", "pediatric", 90, 0.40),
]
USERS = [
    # user_id, display_name, role, active
    ("dana.ruiz", "Dana Ruiz, PharmD", "pharmacist", True),
    ("sam.okafor", "Sam Okafor, PharmD", "pharmacist", True),
    ("morgan.ellis", "Morgan Ellis, PharmD", "pharmacist", False),   # inactive: actions must be refused
    ("lee.chen", "Lee Chen", "buyer", True),
    ("priya.nair", "Priya Nair", "buyer", True),
    ("alex.kim", "Alex Kim", "buyer", True),
    ("jordan.blake", "Jordan Blake", "viewer", True),
    ("casey.morgan", "Casey Morgan", "viewer", True),
]
# Common hospital drugs used as filler; none of these ingredients is on the fixture shortage list (checked at runtime).
FILLER = [
    ("Acetaminophen", "Tablet", "Oral", ["325 mg", "500 mg"]),
    ("Acetaminophen", "Injection", "Intravenous", ["1000 mg/100 mL"]),
    ("Amoxicillin", "Capsule", "Oral", ["250 mg", "500 mg"]),
    ("Amlodipine Besylate", "Tablet", "Oral", ["5 mg", "10 mg"]),
    ("Apixaban", "Tablet", "Oral", ["2.5 mg", "5 mg"]),
    ("Atorvastatin Calcium", "Tablet", "Oral", ["20 mg", "40 mg"]),
    ("Azithromycin", "Tablet", "Oral", ["250 mg"]),
    ("Cefazolin Sodium", "Injection", "Intravenous", ["1 g", "2 g"]),
    ("Ceftriaxone Sodium", "Injection", "Intravenous", ["1 g", "2 g"]),
    ("Cephalexin", "Capsule", "Oral", ["500 mg"]),
    ("Ciprofloxacin", "Tablet", "Oral", ["500 mg"]),
    ("Docusate Sodium", "Capsule", "Oral", ["100 mg"]),
    ("Enoxaparin Sodium", "Injection", "Subcutaneous", ["40 mg/0.4 mL"]),
    ("Escitalopram Oxalate", "Tablet", "Oral", ["10 mg"]),
    ("Famotidine", "Tablet", "Oral", ["20 mg"]),
    ("Famotidine", "Injection", "Intravenous", ["20 mg/2 mL"]),
    ("Gabapentin", "Capsule", "Oral", ["100 mg", "300 mg"]),
    ("Haloperidol Lactate", "Injection", "Intramuscular", ["5 mg/mL"]),
    ("Heparin Sodium", "Injection", "Subcutaneous", ["5000 units/mL"]),
    ("Hydralazine Hydrochloride", "Tablet", "Oral", ["25 mg"]),
    ("Hydrochlorothiazide", "Tablet", "Oral", ["25 mg"]),
    ("Hydroxyzine Hydrochloride", "Tablet", "Oral", ["25 mg"]),
    ("Ibuprofen", "Tablet", "Oral", ["400 mg", "600 mg"]),
    ("Insulin Glargine", "Injection", "Subcutaneous", ["100 units/mL"]),
    ("Insulin Lispro", "Injection", "Subcutaneous", ["100 units/mL"]),
    ("Labetalol Hydrochloride", "Injection", "Intravenous", ["5 mg/mL"]),
    ("Levetiracetam", "Tablet", "Oral", ["500 mg"]),
    ("Levothyroxine Sodium", "Tablet", "Oral", ["50 mcg", "100 mcg"]),
    ("Lisinopril", "Tablet", "Oral", ["10 mg", "20 mg"]),
    ("Losartan Potassium", "Tablet", "Oral", ["50 mg"]),
    ("Magnesium Sulfate", "Injection", "Intravenous", ["1 g/100 mL"]),
    ("Melatonin", "Tablet", "Oral", ["3 mg"]),
    ("Metformin Hydrochloride", "Tablet", "Oral", ["500 mg", "1000 mg"]),
    ("Metronidazole", "Tablet", "Oral", ["500 mg"]),
    ("Montelukast Sodium", "Tablet", "Oral", ["10 mg"]),
    ("Naloxone Hydrochloride", "Injection", "Intravenous", ["0.4 mg/mL"]),
    ("Nitroglycerin", "Tablet", "Sublingual", ["0.4 mg"]),
    ("Omeprazole", "Capsule, Delayed Release", "Oral", ["20 mg"]),
    ("Ondansetron Hydrochloride", "Injection", "Intravenous", ["4 mg/2 mL"]),
    ("Ondansetron", "Tablet, Orally Disintegrating", "Oral", ["4 mg"]),
    ("Oxycodone Hydrochloride", "Tablet", "Oral", ["5 mg"]),
    ("Pantoprazole Sodium", "Tablet, Delayed Release", "Oral", ["40 mg"]),
    ("Piperacillin and Tazobactam", "Injection", "Intravenous", ["3.375 g"]),
    ("Prednisone", "Tablet", "Oral", ["5 mg", "20 mg"]),
    ("Propofol", "Injectable Emulsion", "Intravenous", ["10 mg/mL"]),
    ("Rocuronium Bromide", "Injection", "Intravenous", ["10 mg/mL"]),
    ("Senna", "Tablet", "Oral", ["8.6 mg"]),
    ("Sertraline Hydrochloride", "Tablet", "Oral", ["50 mg"]),
    ("Simvastatin", "Tablet", "Oral", ["20 mg"]),
    ("Sodium Chloride", "Injection", "Intravenous", ["0.9% 1000 mL"]),
    ("Tamsulosin Hydrochloride", "Capsule", "Oral", ["0.4 mg"]),
    ("Trazodone Hydrochloride", "Tablet", "Oral", ["50 mg"]),
    ("Vancomycin Hydrochloride", "Injection", "Intravenous", ["1 g"]),
    ("Warfarin Sodium", "Tablet", "Oral", ["5 mg"]),
    ("Zolpidem Tartrate", "Tablet", "Oral", ["5 mg"]),
    ("Albumin Human", "Solution", "Intravenous", ["25%"]),
    ("Allopurinol", "Tablet", "Oral", ["100 mg"]),
    ("Baclofen", "Tablet", "Oral", ["10 mg"]),
    ("Carvedilol", "Tablet", "Oral", ["6.25 mg"]),
    ("Clopidogrel Bisulfate", "Tablet", "Oral", ["75 mg"]),
    ("Diphenhydramine Hydrochloride", "Capsule", "Oral", ["25 mg"]),
    ("Folic Acid", "Tablet", "Oral", ["1 mg"]),
    ("Glipizide", "Tablet", "Oral", ["5 mg"]),
    ("Spironolactone", "Tablet", "Oral", ["25 mg"]),
]
ROUTE_FOR_FORM = [  # first match wins; used when the shortage record has no openfda.route
    ("suppositor", "Rectal"), ("irrigant", "Irrigation"), ("aerosol", "Inhalation"), ("spray", "Nasal"),
    ("intraperitoneal", "Intraperitoneal"), ("dialysis", "Intraperitoneal"), ("injection", "Intravenous"),
    ("powder, for solution", "Intravenous"), ("tablet", "Oral"), ("capsule", "Oral"), ("oral", "Oral"),
    ("solution", "Oral"),
]
# Shortage form -> a different form and route for the diff_route decoy.
OTHER_FORM = {"Oral": ("Injection", "Intravenous"), "Intravenous": ("Tablet", "Oral")}
SALT_WORDS = ["Hydrochloride", "Sulfate", "Sodium", "Phosphate", "Citrate", "Acetate", "Tromethamine", "Dimesylate",
              "Hyclate", "Succinate", "Bitartrate", "Monohydrate", "Propionate", "Xinafoate"]

ITEM_COLS = ["item_id", "ndc_raw", "name_raw", "strength_raw", "dosage_form_raw", "route_raw", "is_synthetic"]
MANIFEST_COLS = ["item_id", "variant_type", "source_package_ndc", "source_initial_posting_date", "expected_match",
                 "expected_tier", "note", "is_synthetic"]


def _digits(ndc: str) -> str:
    return re.sub(r"\D", "", ndc or "")


def _segments(ndc: str) -> tuple[str, str, str] | None:
    parts = (ndc or "").split("-")
    return tuple(parts) if len(parts) == 3 else None   # type: ignore[return-value]


def ndc_to_542(ndc: str) -> str | None:
    """Hyphenated 10-digit NDC -> 5-4-2 with hyphens (the generator's own padding, independent of pipeline.ndc)."""
    seg = _segments(ndc)
    if seg is None:
        return None
    a, b, c = seg
    return f"{a.zfill(5)}-{b.zfill(4)}-{c.zfill(2)}"


def leading_zero_error(ndc: str) -> str | None:
    """The classic conversion bug: the pad zero goes at the front of the whole number, not into its segment.
    4-4-2 inputs are left alone (their correct pad IS a leading zero), so they return None."""
    seg = _segments(ndc)
    if seg is None or len(seg[0]) == 4:
        return None
    digits = "".join(seg)
    return ("0" + digits)[:11] if len(digits) == 10 else None


def parse_presentation(rec: dict) -> tuple[str, str]:
    """``(first segment, strength)`` from 'Name, Form, Strength (NDC x)'. The form may contain commas
    ('Tablet, Extended Release'), so it is removed using ``split_generic``."""
    text = (rec.get("presentation") or "").rsplit("(NDC", 1)[0].strip()
    first, _, rest = text.partition(",")
    rest = rest.strip()
    _, form = split_generic(rec)
    if form and rest.lower().startswith(form.lower()):
        strength = rest[len(form):]
    else:
        strength = rest.partition(",")[2]
    return first.strip(), strength.strip(" ,")


def split_generic(rec: dict) -> tuple[str, str]:
    """``(ingredient name, full dosage form)``: the longest word-boundary suffix of ``generic_name`` that also starts
    the presentation's form text. 'Potassium Chloride Tablet, Extended Release' -> ('Potassium Chloride',
    'Tablet, Extended Release'). Falls back to the ``dosage_form`` field."""
    generic = (rec.get("generic_name") or "").strip()
    text = (rec.get("presentation") or "").rsplit("(NDC", 1)[0]
    rest = text.partition(",")[2].strip().lower()
    starts = [0] + [m.end() for m in re.finditer(r"\s+", generic)]
    for i in starts[1:]:
        suffix = generic[i:]
        if suffix and rest.startswith(suffix.lower()):
            return generic[:i].strip(" ,"), suffix
    form = rec.get("dosage_form") or ""
    if form and generic.lower().endswith(form.lower()):
        return generic[: -len(form)].strip(" ,"), form
    return generic, form


def route_for(rec: dict) -> str | None:
    routes = [r.title() for r in (rec.get("openfda") or {}).get("route") or []]
    for preferred in ("Intravenous", "Oral", "Intramuscular", "Subcutaneous"):
        if preferred in routes:
            return preferred
    if routes:
        return routes[0]
    form = (rec.get("dosage_form") or rec.get("generic_name") or "").lower()
    for needle, route in ROUTE_FOR_FORM:
        if needle in form:
            return route
    return None


def drop_salt(name: str) -> str | None:
    """'Morphine Sulfate' -> 'Morphine'. Only a trailing salt word on a single-ingredient name is dropped, so
    'Sodium Bicarbonate' and combination products are left alone (return None)."""
    if any(ch in name for ch in ",;/") or " and " in name.lower():
        return None
    words = name.split()
    if len(words) >= 2 and words[-1] in SALT_WORDS:
        return " ".join(words[:-1])
    return None


def _synthetic_ndc(rng: random.Random, used: set[str]) -> str:
    """A 5-4-2 NDC on a labeler code in the 99xxx range, used only for SYNTHETIC items."""
    while True:
        ndc = f"99{rng.randint(100, 999)}-{rng.randint(1000, 9999)}-{rng.randint(10, 99)}"
        if ndc not in used:
            used.add(ndc)
            return ndc


def _alt_strength(strength: str) -> str | None:
    m = re.match(r"\s*([\d.]+)\s*(.*)", strength or "")
    if not m:
        return None
    value = float(m.group(1))
    new = value * 2 if value < 1000 else value / 2
    text = f"{new:g}"
    return f"{text} {m.group(2)}".strip() if m.group(2) else text


def _unique_sources(records: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in records:
        k = (r.get("package_ndc"), r.get("initial_posting_date"))
        if k[0] and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def generate(seed: int = 42, records: list[dict] | None = None) -> dict[str, pd.DataFrame]:
    """All SYNTHETIC hospital tables as DataFrames. Deterministic for a given seed and input."""
    rng = random.Random(seed)
    if records is None:
        records = json.loads(SAMPLE.read_text(encoding="utf-8"))["results"]
    sources = _unique_sources(records)
    used_ndcs = {r["package_ndc"] for r in sources}
    items: list[dict] = []
    manifest: list[dict] = []

    def add(ndc, name, strength, form, route, variant, src, match, tier, note):
        items.append({"ndc_raw": ndc, "name_raw": name, "strength_raw": strength or None, "dosage_form_raw": form,
                      "route_raw": route})
        manifest.append({"variant_type": variant, "source_package_ndc": src.get("package_ndc") if src else None,
                         "source_initial_posting_date": src.get("initial_posting_date") if src else None,
                         "expected_match": match, "expected_tier": tier, "note": note})

    # 1) One positive item per shortage source, cycling the variant types so each appears several times.
    positive_cycle = ["ndc_as_listed", "ndc_11digit", "name_salt", "ndc_other_package",
                      "name_case_whitespace", "ndc_leading_zero_error", "ndc_5_4_2_hyphen", "alt_manufacturer"]
    order = sources[:]
    rng.shuffle(order)
    # Brand names are rare in the data, so they get a dedicated pass first (at least 3 items).
    branded = []
    for src in order:
        first, strength = parse_presentation(src)
        name, form = split_generic(src)
        if first and name and first.split()[0].lower() not in (src.get("generic_name") or "").lower():
            if any(first.split()[0].lower() == b.split()[0].lower() for b, _ in branded):
                continue
            branded.append((first, src))
            add(None, first, strength, form, route_for(src), "name_brand", src, 1, 3,
                "brand name from the presentation, no NDC on the item")
        if len(branded) >= 5:
            break
    done = {id(src) for _, src in branded}
    k = 0
    for src in order:
        if id(src) in done:
            continue
        generic = src.get("generic_name") or ""
        name, form = split_generic(src)
        name = name or generic
        brand, strength = parse_presentation(src)
        route = route_for(src)
        ndc = src["package_ndc"]
        placed = False
        for attempt in range(len(positive_cycle)):
            v = positive_cycle[(k + attempt) % len(positive_cycle)]
            if v == "ndc_as_listed":
                add(ndc, name, strength, form, route, v, src, 1, 1, "package NDC as the FDA prints it")
            elif v == "ndc_11digit":
                add(_digits(ndc_to_542(ndc) or ""), name, strength, form, route, v, src, 1, 1,
                    "11 digits, no hyphens")
            elif v == "ndc_5_4_2_hyphen":
                if len(_digits(ndc)) == 11:
                    continue
                add(ndc_to_542(ndc), name, strength, form, route, v, src, 1, 1, "padded 5-4-2 with hyphens")
            elif v == "ndc_other_package":
                seg = _segments(ndc)
                if seg is None:
                    continue
                pkg = str((int(seg[2]) + 7) % (10 ** len(seg[2]))).zfill(len(seg[2]))
                other = f"{seg[0]}-{seg[1]}-{pkg}"
                if other in used_ndcs:
                    continue
                used_ndcs.add(other)
                add(other, name.upper(), strength, form, route, v, src, 1, 2,
                    "same labeler and product, another package code (SYNTHETIC)")
            elif v == "ndc_leading_zero_error":
                bad = leading_zero_error(ndc)
                if bad is None:
                    continue
                add(bad, name, strength, form, route, v, src, 1, 3,
                    "pad zero put in front: the NDC points elsewhere; name/form/strength still right")
            elif v == "name_salt":
                short = drop_salt(name)
                if short is None:
                    continue
                add(None, short, strength, form, route, v, src, 1, 3, "salt dropped, no NDC on the item")
            elif v == "name_case_whitespace":
                add(None, "  " + "  ".join(name.upper().split()) + " ", strength, form.lower(), route, v, src, 1, 3,
                    "upper case and extra spaces, no NDC on the item")
            elif v == "alt_manufacturer":
                add(_synthetic_ndc(rng, used_ndcs), name, strength, form, route, v, src, 1, 3,
                    "another (SYNTHETIC) labeler, same ingredient/form/route/strength: a substitute candidate")
            placed = True
            break
        if placed:
            k += 1

    # 2) Hard-negative decoys on a seeded subset of sources.
    decoy_sources = rng.sample(order, k=min(24, len(order)))
    for i, src in enumerate(decoy_sources):
        name, form = split_generic(src)
        name = name or src.get("generic_name")
        _, strength = parse_presentation(src)
        route = route_for(src)
        if i % 2 == 0:
            alt = _alt_strength(strength)
            if alt:
                add(_synthetic_ndc(rng, used_ndcs), name, alt, form, route, "diff_strength", src, 0, None,
                    f"strength {alt} vs shortage {strength}")
                continue
        other = OTHER_FORM.get(route or "")
        if other:
            add(_synthetic_ndc(rng, used_ndcs), name, strength.split(",")[0] if strength else None, other[0],
                other[1], "diff_route", src, 0, None, f"{other[0]} / {other[1]} vs shortage {form} / {route}")

    # 3) Filler: common drugs not on the fixture shortage list, until N_ITEMS.
    short_words = {w.lower() for r in records for w in re.findall(r"[A-Za-z]+", r.get("generic_name") or "")
                   if len(w) > 4}
    filler = [f for f in FILLER if f[0].split()[0].lower() not in short_words]
    combos = [(n, form, route, s) for n, form, route, strengths in filler for s in strengths]
    while len(items) < N_ITEMS and combos:
        n, form, route, s = combos.pop(rng.randrange(len(combos)))
        add(_synthetic_ndc(rng, used_ndcs), n, s, form, route, "filler", None, 0, None, "not on the shortage list")

    # Shuffle, then number.
    idx = list(range(len(items)))
    rng.shuffle(idx)
    formulary = pd.DataFrame([items[i] for i in idx])
    man = pd.DataFrame([manifest[i] for i in idx])
    formulary.insert(0, "item_id", [f"FI-{n:04d}" for n in range(1, len(idx) + 1)])
    man.insert(0, "item_id", formulary["item_id"])
    formulary["is_synthetic"] = True
    man["is_synthetic"] = True
    man["expected_tier"] = man["expected_tier"].astype("Int64")

    facility = pd.DataFrame([{"facility_id": f, "name": n, "kind": kd, "beds": b, "is_synthetic": True}
                             for f, n, kd, b, _ in FACILITIES])
    stock = []
    shortage_items = set(man.loc[man["expected_match"] == 1, "item_id"])
    for item_id in formulary["item_id"]:
        stocked = [f for f, _, _, _, share in FACILITIES if rng.random() < share]
        if not stocked and item_id in shortage_items:
            stocked = ["F-MAIN"]           # every shortage-derived item is stocked somewhere
        for f in stocked:
            low = rng.random() < 0.15
            days = rng.randint(0, 3) if low else rng.randint(4, 45)
            stock.append({"facility_id": f, "item_id": item_id, "days_on_hand": days, "is_synthetic": True})
    facility_stock = pd.DataFrame(stock).sort_values(["facility_id", "item_id"]).reset_index(drop=True)
    users = pd.DataFrame([{"user_id": u, "display_name": d, "role": r, "active": a, "is_synthetic": True}
                          for u, d, r, a in USERS])
    return {"facility": facility, "formulary_item": formulary[ITEM_COLS], "facility_stock": facility_stock,
            "app_user": users, "variant_manifest": man[MANIFEST_COLS]}


def write(tables: dict[str, pd.DataFrame], out: Path = DEFAULT_OUT) -> dict[str, str]:
    """Write one CSV per table (LF line endings, so the bytes are the same on every OS). Returns name -> sha256."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, df in tables.items():
        path = out / f"{name}.csv"
        text = df.to_csv(index=False, lineterminator="\n")
        path.write_bytes(text.encode("utf-8"))
        hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashes


def main() -> None:
    ap = argparse.ArgumentParser(description="Write the SYNTHETIC hospital seed CSVs.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()
    tables = generate(a.seed)
    for name, h in write(tables, a.out).items():
        print(f"{name}: {len(tables[name])} rows, sha256 {h[:12]}")


if __name__ == "__main__":
    main()
