# ADR 006 (addendum to DESIGN section 5): matcher guards on NDC tiers

Status: Accepted with conditions 2026-10-30 (Tech Lead, see the decision at the end). Proposed by ml-engineer (FDSE-2).
Written and implemented BEFORE the first evaluation run against the frozen gold set, so it is not tuned on it.

## Context
DESIGN section 5, tier 2, matches a formulary item when its product NDC equals "any `openfda.product_ndc`" of the
record. The openfda product list covers the whole product family, including **other strengths** (QA note: record
0378-6860 lists 0378-6855 = 20 mg). The labeling rule says a different strength is a 0, so tier 2 as written gives
false positives. QA's gold rows of variant `openfda_sibling_strength` measure this.

Tier 1 has a related risk: a mis-padded NDC (the "leading-zero error") can normalize to another real product's
NDC. The labeling header says "an NDC is evidence, not truth".

## Options
1. Keep DESIGN as written. Simple, but it is known to produce false positives.
2. Drop tier 2 via openfda entirely. Safe, but it loses real sibling packages and the 2b evidence on records
   whose own product NDC differs from the formulary's.
3. **Keep tier 2 via openfda, but require agreement:** drop the match when both sides have a dosage form and they
   differ, or both have a strength and no comparable form is equal. Also add a **name sanity guard** on tiers 1
   and 2: drop an NDC match when both sides have a normalized ingredient and they share no word.
4. Fuzzy name scores (rapidfuzz). Rejected: look-alike names (hydroxyzine/hydralazine) are exactly the risk, and
   exact matching after normalization is enough. No new dependency, stdlib only.

## Choice
Option 3, in `pipeline/match.py`:
- tier 2a (same product_ndc9 as the record's own package): accepted unless there is a name conflict;
- tier 2b (product only in `openfda.product_ndc`): also needs no dosage-form conflict and no strength conflict;
- tier 1: accepted unless there is a name conflict.

"Conflict" means both sides have a value and they disagree. Missing data never blocks a match, so records with
no openfda block or no strength still match (AC-4.6).

## Consequences
- Fewer false positives from product families and typo'd NDCs.
- Risk: a formulary item named only by a brand that the snapshot never mentions shares no word with the generic,
  so a correct tier-1 match is dropped. The brand map (proprietary_name, openfda brand_name, first segment of
  `presentation`) lowers this risk. The evaluation lists any such misses as FN.
- The tier-2 confidence (0.90) is unchanged.
- Release form (ER/DR/ODT) is part of the dosage form, following the gold header's written rule.

---

## Addendum v2 (2026-10-30, ml-engineer). Status: Proposed, needs TL approval.

### Context
v1 scored P 0.980 / R 0.877 on the v1 gold set (frozen as `reports/match_quality_v1.json`). The error analysis put
the misses in 3 classes: the v1 name guard blocked correct NDC matches, misspellings with no NDC, and brand
names the brand map did not resolve. The EM also asked for a fairer baseline. These changes are informed by
**seen** v1 errors, so v1's re-score is optimistic. The honest v2 number is QA's held-out set
`holdout_v2.csv`, which the matcher author never opens, scored once.

### Changes (pipeline/match.py, pipeline/names.py, config/normalize.yaml)
1. **Guard needs corroboration.** A tier-1/2 NDC match is dropped only if the names disagree (no shared word and
   not a near-miss spelling) **and** the dosage form or strength also conflicts. NDC evidence beats a name
   alone, but not a name plus a form or strength mismatch, which is how a typo'd NDC shows up.
   The tier-2 openfda-list rule (form/strength agreement) is unchanged.
2. **Near-miss spelling in tier 3** (stdlib `difflib` plus a small Levenshtein, still no rapidfuzz). Every
   condition must hold:
   - both names are single-ingredient, at least 6 letters
   - ratio >= 0.88 and edit distance <= 2
   - the formulary spelling is not itself an ingredient in the snapshot
   - the pair is not on the configured look-alike list (hydroxyzine/hydralazine, prednisone/prednisolone, ...)
   - same dosage form and a compatible route
   - **both** sides have a strength, and it is equal
   Confidence is 0.50, and `matched_on` starts with `near-miss` so a pharmacist can see it.
3. **Brand keys:**
   - Salt words are stripped from brand keys ("Cleocin HCl" = "Cleocin Hydrochloride").
   - Route abbreviations and device words (iv, im, sc, po, pen, autoinjector, ...) are noise words.
   - `openfda.brand_name` (now staged by DE) feeds the brand map.
   - A brand that never appears in the snapshot (for example Saxenda) is still unresolvable without an
     external list, which stays out of scope.
4. **Strength from `presentation`.** This was already in v1. The `strength` field is empty on all live records,
   so the event strength is parsed from the presentation text after the name segment. Concentrations are
   normalized per mL and parentheticals are ignored. A test now covers it.
5. **Fairer baseline `baseline_v2`**: implements QA's spec in TEST_PLAN.md section 6.2 exactly
   (`pipeline.match.base_name` / `baseline_v2_match`; word list fixed before scoring). It is reported as
   `baseline_v2`, next to the v1 AC-4.4 exact-lowercase `baseline`.

### Consequences
- Fuzzy matching adds a look-alike risk. It is limited by the conditions above and the look-alike list, and
  every near-miss is labeled. Prefer adding to the list over loosening thresholds.
- A name-only disagreement no longer blocks an NDC match, so a typo'd NDC with the same form and strength as
  another product now matches it. This trade is accepted, since NDC typos that keep form and strength are rarer
  than unrecognized brand names.

---

## Tech Lead decision (2026-10-30, TL-01)

**APPROVED WITH CONDITIONS** (v1 guards and addendum v2, items 1-5). The changes are sound, stdlib only, and were
scored honestly (holdout_v2 unseen: P 0.957 / R 0.936, recall target still missed and reported as missed).

Conditions, before a v3 score:
1. **Ingredient-set guard on NDC tiers (blocking).** A tier-1/2 match where both sides parse to a component set
   (`ingredient_norm.split(" / ")`) and the sets differ must be dropped. Today `_name_conflict` only asks for a
   shared word, and `strengths_equal` accepts any one equal strength, so "bupivacaine" vs "bupivacaine /
   epinephrine" passes tier 2b (reproduced: `pipeline/match.py:266-271`, holdout HP-006/HP-007). Apply it to 2b at
   minimum; apply it to 1 and 2a too unless a test shows a real package where the parsed sets legitimately differ.
2. **Brand key must not be a component of its own generic** (`pipeline/match.py:121`): skip the key when
   `brand_key(c)` is in the generic's component set. Fixes the HP-051 regression.
3. Add the "Film, Extended Release" form to `config/normalize.yaml` (HP-050).
4. Add a test that pins the accepted trade in addendum item 1: a name-only disagreement with equal form and
   strength keeps the NDC match. A future change must break it on purpose.
5. Score v3 once on a NEW held-out set from QA (holdout_v2 is now seen). Report gold and holdout_v2 as seen.

DESIGN section 12 records this decision and the ML contract additions.

## Round 3 implementation of the TL conditions (2026-10-30, ml-engineer)
1. Component-set guard on tiers 1, 2a and 2b (`match._component_conflict`). An NDC match is dropped when the
   ingredient sets differ and either overlap (combination vs one of its parts) or are both multi-ingredient.
   Two disjoint single names are not a component conflict; they stay under addendum item 1. No real package in
   the fixtures showed legitimately different overlapping sets, so the guard applies to all three NDC tiers.
2. `build_brand_map` skips a brand key that is an ingredient of its own generic.
3. `normalize.yaml`: "film extended release" maps to the `patch` bucket (transdermal).
4. `tests/test_match.py::test_r3_pinned_trade_name_only_disagreement_keeps_ndc_match` pins the trade on purpose.
5. v3 is scored once on QA's `holdout_v3.csv`. gold and holdout_v2 are reported as seen.
