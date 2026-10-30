# Postmortem: a buyer could undo a pharmacist's approval by re-proposing

Blameless. Owner of this write-up: Principal Engineer. Found by: tech-lead (REVIEW finding 1, S1). Fixed by: ml-engineer (ML-09). Attack tests: qa-engineer (QA-05). Date: 2026-10-30.
All data involved is SYNTHETIC. The hole was found in review; no real user was affected (there are none).

## Summary
`approve_substitute` is pharmacist-only and enforced in the action layer, not the UI. But `propose_substitute` (pharmacist **or buyer**) had no state criterion. Write-back is append-only and "the latest edit wins" (ADR 003), so a buyer re-proposing an already approved substitute wrote a new `proposed` edit and the approval disappeared from the object view. The role rule was intact; the **state machine** had a back door.

## Impact
- A control that the PR/FAQ promised ("only a pharmacist can approve") could be bypassed in effect: a buyer cannot reject, but could reset an approved or rejected decision.
- The audit log still showed both actions, so it was detectable, not silent. Detective controls do not excuse a missing preventive one.

## How it was found
TL reproduced it on a scratch copy of the demo DB: pharmacist approves (state `approved`), buyer calls propose on the same event and alternative (SUCCESS, state `proposed`). It was in REVIEW.md before any test existed.

## Root cause
Each action was reviewed alone ("who may do this?"), not as a transition ("from which states?"). The submission criteria for approve and reject both check `state_is_proposed`; propose checked only inputs.

## Fix
New criterion `not_already_decided` on `propose_substitute` in `ontology/ontology.yaml` and `console/actions.py`: refuse when the current state is `approved` or `rejected`. Refusals are logged with that criterion id.

## Regression tests
- `tests/qa/test_review_attacks.py::test_buyer_cannot_undo_decision_by_reproposing[approve_substitute]` and `[reject_substitute]`
- `tests/qa/test_review_attacks.py::test_pharmacist_reproposing_approved_is_also_refused`
- `tests/test_actions.py::test_r3_buyer_cannot_repropose_approved_substitute`
- `tests/test_actions.py::test_r3_repropose_rejected_refused_but_repropose_proposed_ok`

Planted-bug proof: with the criterion forced to always pass, these 5 tests fail ([FLAW_HUNT.md](FLAW_HUNT.md), PB-6).

## Lessons
1. Review actions as a **state machine**: for each action, list the allowed from-states, and test every edge that should be refused.
2. "Latest edit wins" makes every write action a potential undo. Any action that can set a state must say which states it may overwrite.
3. Foundry's submission criteria are the right mental model; this project now uses them for state as well as role.
