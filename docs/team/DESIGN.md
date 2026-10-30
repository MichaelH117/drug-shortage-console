# DESIGN: Drug Shortage Operations Console

Owner: Tech Lead / Solution Architect (also Product Engineer, Palantir preset). Status: v1.0, 2026-10-30.
Input: SPEC.md (US-1 to US-9). Next: engineers build from TASKS.md.

Decisions by the Engineering Manager (Michael can override):
- **E1 Answer set.** The team drafts the 60+ gold pairs, each with a short rationale, marked `reviewed_by: pending`. Michael reviews later. No file, commit or doc may say he reviewed them until he does.
- **E2 Targets.** Use the spec's targets: recall >= 0.95, precision >= 0.90, queue-length guardrail 40.
- **E3 Names.** Dana Ruiz and Riverbend Health are OK, labeled SYNTHETIC.
- **E4 History.** Daily real pulls wait until Michael approves a scheduled task. The snapshot store is designed so backfilled or future daily pulls drop in without code changes (section 3.1).
- **E5 Palantir Developer Tier.** Optional later phase (section 11).

ADRs:
- [001 Streamlit app over the action layer](adr/001-streamlit-app.md)
- [002 DuckDB file, YAML ontology, SQL views; no dbt in v1](adr/002-duckdb-yaml-ontology.md)
- [003 Actions, write-back edit tables, and the hash-chained action log](adr/003-actions-writeback-audit.md)
- [004 History rebuilt from write-once snapshot files](adr/004-snapshot-store-full-rebuild.md)
- [005 `ontokit`: an in-repo, domain-free library](adr/005-ontokit-library.md)

---

## 0. Goals, non-goals, constraints

**Goals:** Dana's 7:30 queue (US-5). Matching with measured quality against a baseline (US-4, US-8). Actions with role rules and write-back (US-6). Tamper-evident log (US-7). History that survives the API showing only current state (US-2). Everything testable offline.

**Non-goals:** the spec's section 6 list: no prediction, no clinical advice, no real auth, no LLM, no Docker or cloud, and no backfill before the first snapshot.

**Constraints that shape the design:**
- **PC limit: about 2 GB free RAM.** No servers (no Postgres, no Docker, no Airflow, no dbt process at app time). One DuckDB file with `memory_limit='512MB'` and `threads=2`. Streamlit uses about 150 MB. Peak target for the whole stack is under 1 GB.
- **Data size is small:** 1,614 records a day, about 3 MB of JSON. One year is about 600k history rows. The hard part is messy identity data, not volume.
- **Network:** `api.fda.gov` only, and only from `pipeline/http.py`. Tests run with the network blocked.
- **Mirror vaccine-assay-pipeline:** project `.venv`, `python -m pipeline.run [--test]`, offline fixture tests in `tests/`, `docs/results.json` plus a README results block written by the run.

---

## 1. Architecture sketch

```
 api.fda.gov/drug/shortages.json            tests/fixtures/snapshots/SYNTHETIC_D1..D3 (offline)
        │  pipeline/http.py (host allow-list)            │
        ▼                                                ▼
 data/raw/YYYY-MM-DD/  records.json  meta.json  manifest.json   (write-once, read-only files)
        │   any producer (daily pull, backfill, fixtures) writes this format = it "slots in"
        ▼   python -m pipeline.run   (full deterministic rebuild, ADR 004)
 ┌──────────────────────── pipeline-owned (dropped and rebuilt every run) ─────────────────────────┐
 │ stage.snapshot, stage.shortage_record   parse, NDC-11, availability buckets, dates   (DE)       │
 │ core.dq_results                         volume, freshness, schema drift, dates, unmapped (DE)   │
 │ core.shortage_history (SCD2), core.change_feed, core.shortage_event               (DE)          │
 │ seed.facility / formulary_item / facility_stock / app_user   (SYNTHETIC, seeded generator) (DE) │
 │ core.drug, core.formulary_match (tiers 1-3), core.substitute_candidate            (ML)          │
 └─────────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌──────── action-owned (never touched by the pipeline, AC-6.4) ────────┐
 │ ops.substitute_edit, ops.mitigation_task_edit, ops.outbox (stub)     │  written only by
 │ audit.action_log, audit.chain_checkpoint                             │  ontokit.actions.run_action
 └──────────────────────────────────────────────────────────────────────┘
        │  views regenerated from ontology/ontology.yaml every run (ontokit.ontology)
        ▼
 obj.drug  obj.shortage_event  obj.substitute  obj.mitigation_task  obj.facility   (base + edits, edits win)
 link.*  (six link views)        mart.triage_queue (AE)
        │
        ▼  streamlit run app/Home.py   (reads views; writes ONLY via console/actions.py)
 Queue -> Drug object view -> action form -> Audit page (+ Match quality page)
```

Two databases, same code:
- `data/demo/console.duckdb`, built by `python -m pipeline.run --fixtures` from the fixture snapshots. It is offline, deterministic, and has a 3-day history with NEW and WORSENED rows. This is the default for the demo and tests.
- `data/live/console.duckdb`, built by `python -m pipeline.run` from `data/live/raw/` (uses today's cache, or pulls if it is missing).

The app picks one with the env var `DSC_DB` (`demo` is the default, or `live`) and shows which one in the header.

---

## 2. Folder layout and file ownership

Seats: DE = data-engineer (FDE-1), ML = ml-engineer (FDE-2), AE = analytics-engineer, QA, PE = principal-engineer, OPS = devops-engineer, TL = tech-lead. Do not edit files you do not own. To change a contract, ask TL and log it in HANDOFF.md.

```
drug-shortage-console/
  .venv/  .gitignore  pyproject.toml  requirements.txt              DE creates; OPS pins (OPS-01)
  config/settings.yaml           DE   paths, timezone, DQ thresholds, DuckDB limits, stale_days
  config/availability_map.yaml   DE   raw availability text -> bucket (AC-2.5)
  config/normalize.yaml          ML   salt suffixes, dosage-form buckets, form->route defaults, unit aliases
  ontology/ontology.yaml         ML   single source: object types, link types, action types (AC-3.1)
  ontokit/                       domain-free library (ADR 005); must not import pipeline/console/app
    __init__.py  ontology.py  actions.py  writeback.py   ML
    audit.py                                              DE (copied and adapted from vaccine audit.py)
  pipeline/
    __init__.py  config.py  http.py  snapshots.py  ingest.py  availability.py  dq.py
    history.py  changes.py  schema.py  run.py           DE
    ndc.py  names.py  match.py  evaluate.py  candidates.py  objects.py   ML
  generator/hospital.py          DE   SYNTHETIC facilities, formulary (+ variant manifest), stock, users
  generator/tamper.py            QA   helpers that tamper with the DB for verify tests
  console/
    identity.py  actions.py      ML   user lookup; the six domain actions (the ONLY write path)
    queries.py  freshness.py  metrics.py   AE   queue/object/audit queries, stale banner, impact metrics
    simulate.py                  AE   SIMULATED demo usage (labeled) for AC-8.3
  app/Home.py  app/pages/*.py    AE   Streamlit; calls console.* only
  tests/fixtures/                DE   openfda_page_sample.json (real, trimmed), snapshots/SYNTHETIC_D1..D3,
                                      expected_changes.csv, SYNTHETIC_renamed_field.json
  tests/fixtures/gold_matches.csv  QA (drafts, E1), frozen by gold_matches.sha256
  tests/conftest.py              DE   `demo_db` (session, built from fixtures into tmp), `mem_db`
  tests/test_<module>.py         owner of the module
  tests/qa/                      QA   edge, mutation and planted-bug tests
  reports/match_quality.json     written by pipeline.evaluate (ML)
  reports/impact_metrics.json    written by console.metrics (AE)
  docs/impact.md  docs/METRICS.md   AE
  docs/decomposition.md  docs/RISK_REGISTER.md  docs/KNOWN_ISSUES.md  PE
  docs/demo/                     AE   screenshots (AC-5.6)
  docs/team/*                    TL owns DESIGN, ADRs, TASKS, REVIEW; QA owns TEST_PLAN, BUGS
  data/                          git-ignored except data/demo/seed/*.csv (small, deterministic)
```

---

## 3. Data model (table contracts)

All timestamps are naive UTC `TIMESTAMP`. Snapshot dates are `DATE` in `America/New_York` (`config/settings.yaml: timezone`). Every synthetic table has `is_synthetic BOOLEAN NOT NULL DEFAULT true` (AC-3.4).

### 3.1 Snapshot store (files, write-once), DE
`<root>/raw/YYYY-MM-DD/`:
- `records.json`: the concatenated `results` arrays from all pages, in API order.
- `meta.json`: the `meta` block of page 1.
- `manifest.json`: `{snapshot_date, source: "live"|"backfill"|"SYNTHETIC_fixture", fetched_at_utc, record_count, meta_total, meta_last_updated, pages, sha256: {records.json, meta.json}, tool_version}`.

Rules:
- `record_count != meta_total` raises `SnapshotIncomplete`, and no folder is left behind. Write to `YYYY-MM-DD.tmp/`, then rename.
- After writing, the files are set read-only.
- An existing folder is never modified. `--refresh` writes a new folder and moves the old one to `raw/_superseded/YYYY-MM-DD-<fetched_at>/`. History uses only the newest one, and nothing is deleted.
- **Slot-in contract (E4):** anything that writes a valid folder in this format (a future scheduled `--pull-only`, or a backfill from someone else's archive marked `source: backfill`) is picked up on the next run. No code change, because history is rebuilt from all folders (ADR 004).

### 3.2 Pipeline-owned tables (rebuilt every run)
| Table | Key | Columns (contract) | Owner |
|---|---|---|---|
| `stage.snapshot` | snapshot_date | source, fetched_at_utc, meta_last_updated DATE, meta_total, record_count, records_sha256 | DE |
| `stage.shortage_record` | (snapshot_date, event_key) | package_ndc_raw, ndc11, ndc_reason, product_ndc9 (labeler5+product4), generic_name, proprietary_name, company_name, presentation, dosage_form, strength (VARCHAR, json array), therapeutic_category (json), status, availability_raw, availability_normalized, shortage_reason, update_date, change_date, initial_posting_date, discontinued_date (DATE, null if unparseable), has_openfda, openfda_rxcui (json), openfda_route (json), openfda_substance_name (json), openfda_product_ndc (json), openfda_package_ndc (json), record_hash | DE |
| `core.dq_results` | (run_id, snapshot_date, check_name) | status PASS/WARN/FAIL/SKIPPED, measured, threshold, detail, checked_at_utc | DE |
| `core.shortage_history` | (event_key, valid_from) | valid_to (null = current), is_current, present BOOLEAN, status, availability_normalized, shortage_reason, update_date, attr_hash | DE |
| `core.change_feed` | (snapshot_date, event_key) | prev_snapshot_date, change_type, prev_status, curr_status, prev_availability, curr_availability | DE |
| `core.shortage_event` | event_key | ndc11, product_ndc9, generic_name, presentation, latest status/availability (raw+norm)/reason/dates, first_seen, last_seen, present_in_latest | DE |
| `seed.facility` | facility_id | name, kind, beds, is_synthetic | DE |
| `seed.formulary_item` | item_id | ndc_raw, name_raw, strength_raw, dosage_form_raw, route_raw, is_synthetic | DE |
| `seed.facility_stock` | (facility_id, item_id) | days_on_hand, is_synthetic | DE |
| `seed.app_user` | user_id | display_name, role (pharmacist/buyer/viewer), active, is_synthetic | DE |
| `core.drug` | ndc11 | product_ndc9, generic_name, ingredient_norm, dosage_form_norm, route_norm, strength_norm, manufacturer, rxcui (nullable), source (shortage/formulary/both) | ML |
| `core.formulary_item_norm` | item_id | ndc11 (nullable), ndc_reason, product_ndc9, ingredient_norm, dosage_form_norm, route_norm, strength_norm | ML |
| `core.formulary_match` | (item_id, event_key) | match_tier 1/2/3, match_confidence, matched_on (text) | ML |
| `core.substitute_candidate` | substitute_id | event_key, alt_ndc11, alt_item_id, basis | ML |

Key rules:
- **event_key** = `ndc11 + "|" + initial_posting_date (ISO)`. If the NDC cannot be parsed: `"raw:" + package_ndc_raw + "|" + date`. If `package_ndc` is missing: `"pres:" + sha1(presentation)[:12] + "|" + date`. Each fallback adds a `dq_results` WARN row. Duplicate keys in one snapshot: keep the row with the latest `update_date`, then the first in API order, and log a WARN with the count.
- **Tier 1 uses the top-level `package_ndc`**, never `openfda.package_ndc` (SPEC R4). The openfda lists are kept only for tier 2 and as display context.
- **substitute_id** = `"S-" + sha1(event_key + "|" + alt_ndc11)[:12]`. It is deterministic, so an edit made today still joins after re-ingest (AC-6.4). A user-proposed pair that matches a candidate gets the same id.
- **task_id** = `"T-%05d"`, next number assigned inside the action transaction.

### 3.3 Action-owned tables (never dropped by the pipeline)
| Table | Columns |
|---|---|
| `ops.substitute_edit` | edit_seq PK, substitute_id, event_key, alt_ndc11, state (`proposed`/`approved`/`rejected`), reason, actor_id, action_seq (FK to audit seq), edited_at_utc |
| `ops.mitigation_task_edit` | edit_seq PK, task_id, event_key, facility_id, assignee_id, due_date, state (`open`/`closed`), note, actor_id, action_seq, edited_at_utc |
| `ops.outbox` (stub) | outbox_seq PK, action_seq, kind, payload_json, created_at_utc. Nothing reads it, and nothing is sent. |
| `audit.action_log` | seq PK, action_type, object_type, object_key, actor_id, role, reason, old_value (json), new_value (json), outcome (`SUCCESS`/`REFUSED`), failed_criterion, ts_utc, prev_hash, row_hash |
| `audit.chain_checkpoint` | checkpoint_seq PK, head_seq, head_hash, ts_utc (one per append, same as the source pattern) |

Edit tables are **append-only event rows**: every action adds a row and never updates one. The object views take the latest edit per key, so edits win.

### 3.4 Object and link views (generated from `ontology/ontology.yaml`)
| View | Built from |
|---|---|
| `obj.drug` | `core.drug` |
| `obj.shortage_event` | `core.shortage_event` |
| `obj.facility` | `seed.facility` |
| `obj.substitute` | `core.substitute_candidate` FULL OUTER JOIN latest `ops.substitute_edit` per substitute_id. Base-only rows have state `candidate`. |
| `obj.mitigation_task` | latest `ops.mitigation_task_edit` per task_id (write-back only object) |
| `link.drug_shortage_event` (1-*) | shortage_event.ndc11 |
| `link.shortage_event_facility` (*-*) | formulary_match JOIN facility_stock |
| `link.shortage_event_substitute` (1-*) | substitute.event_key |
| `link.substitute_alt_drug` (*-1) | substitute.alt_ndc11 |
| `link.shortage_event_task` (1-*) | mitigation_task.event_key |
| `link.task_facility` (*-1) | mitigation_task.facility_id |
| `mart.triage_queue` (AE) | change_feed (latest snapshot, NEW or WORSENED) JOIN link.shortage_event_facility, plus counts from obj views. Sort: Unavailable, Limited, Unknown, Available, then min days_on_hand ascending. |

Views are plain DuckDB views, not tables, so a successful action shows up on the next Streamlit rerun with no rebuild (AC-6.5).

### 3.5 Change classification (AC-2.3); first rule that applies wins
Compare each event_key between the latest snapshot `S` and the previous snapshot `P` (by date, not load order).

| # | Condition | change_type |
|---|---|---|
| 1 | in P, not in S | DROPPED |
| 2 | not in P, in S | NEW (also on the first snapshot ever) |
| 3 | S.status = Resolved and P.status != Resolved | RESOLVED |
| 4 | P.status = Resolved and S.status != Resolved | WORSENED (reopened) |
| 5 | rank(S.avail) > rank(P.avail), or status Current -> To Be Discontinued | WORSENED |
| 6 | rank(S.avail) < rank(P.avail) | IMPROVED |
| 7 | otherwise (including any move to or from `Unknown`) | UNCHANGED |

Rank: Available 0, Limited 1, Unavailable 2. `Unknown` has no rank. A move to `Unknown` is reported in `dq_results` and never silently counted as WORSENED.

---

## 4. Python interfaces (signatures are the contract)

```python
# pipeline/config.py (DE)
@dataclass(frozen=True)
class Paths: root: Path; raw: Path; db: Path; seed: Path; reports: Path
def paths_for(mode: Literal["demo", "live"], base: Path | None = None) -> Paths
def load_settings() -> dict
def connect(db: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection   # sets memory_limit, threads

# pipeline/http.py (DE): the only network code
ALLOWED_HOSTS = frozenset({"api.fda.gov"})
class HostNotAllowed(ValueError): ...
class HttpClient(Protocol):
    def get_json(self, url: str, params: dict) -> dict: ...
def default_client(timeout_s: float = 30) -> HttpClient       # urllib; checks host before every call

# pipeline/snapshots.py (DE)
class SnapshotIncomplete(RuntimeError): ...
def fetch_all(client: HttpClient, page_size: int = 1000) -> tuple[list[dict], dict]   # (records, meta)
def ensure_snapshot(raw_root: Path, run_date: date, client: HttpClient | None, refresh: bool = False) -> Path
     # cache hit and refresh=False -> zero client calls (AC-1.2)
def list_snapshots(raw_root: Path) -> list[SnapshotRef]        # sorted by date; validates manifest + sha256

# pipeline/availability.py (DE)
def normalize_availability(raw: str | None, mapping: dict) -> tuple[str, bool]   # (bucket, was_mapped)

# pipeline/ingest.py, dq.py, history.py, changes.py (DE)
def parse_snapshot(ref: SnapshotRef, mapping: dict) -> pd.DataFrame              # stage.shortage_record rows
def run_dq(con, run_id: str, snapshots: list[SnapshotRef]) -> pd.DataFrame        # also writes core.dq_results
def build_history(stage: pd.DataFrame) -> pd.DataFrame                            # SCD2, pure, order-independent
def classify_changes(prev: pd.DataFrame | None, curr: pd.DataFrame) -> pd.DataFrame

# pipeline/ndc.py (ML): pure
@dataclass(frozen=True)
class NdcResult: ndc11: str | None; product_ndc9: str | None; reason: str | None   # reason set iff ndc11 None
def normalize_ndc(raw: str | None) -> NdcResult
     # 4-4-2 -> 0+4-4-2; 5-3-2 -> 5-0+3-2; 5-4-1 -> 5-4-0+1; 11 digits no hyphens -> as is;
     # 10 digits no hyphens -> reason "ambiguous_10_digit_unhyphenated"; letters or wrong length -> reason
def extract_ndc_from_presentation(text: str) -> str | None     # "(NDC 0378-6860-77)"

# pipeline/names.py (ML): pure
def normalize_ingredient(name: str, brand_map: dict[str, str], salts: list[str]) -> str
def normalize_dosage_form(raw: str) -> str
def default_route(dosage_form_norm: str) -> str | None
def parse_strength(raw: str | list | None) -> tuple[float, str] | None

# pipeline/match.py (ML): pure DataFrame in, DataFrame out (no DB), so it can be built in parallel with DE
def match(formulary_norm: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame
     # core.formulary_match columns. Best tier per (item, event). Confidence: t1 1.00, t2 0.90,
     # t3 with strength 0.80, t3 without strength 0.60
def baseline_match(formulary: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame   # lower(generic_name) ==

# pipeline/evaluate.py (ML)
def evaluate(pred: pd.DataFrame, gold: pd.DataFrame) -> dict   # P/R/F1 overall + per tier + per variant_type
def main() -> None   # python -m pipeline.evaluate -> reports/match_quality.json (includes gold sha256,
                     # pass/fail vs targets, list of failing pairs; never edits the gold file)

# pipeline/candidates.py, objects.py (ML)
def substitute_candidates(drugs: pd.DataFrame, events: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame
def create_object_views(con, ontology: Ontology) -> None

# pipeline/run.py (DE)
# python -m pipeline.run [--fixtures] [--refresh] [--pull-only] [--seed 42] [--test]
def build(paths: Paths, *, client: HttpClient | None, refresh: bool, seed: int) -> dict   # summary
#   order: snapshots -> stage -> dq -> history/changes/events -> seed -> norm/match/candidates
#          -> create_object_views -> init edit/audit tables IF NOT EXISTS -> evaluate -> metrics -> results.json
#   drops and recreates ONLY schemas stage, core, seed, obj, link, mart

# ontokit/ontology.py (ML)
@dataclass(frozen=True)
class ObjectType: name: str; key: list[str]; view: str; synthetic: bool; properties: dict[str, str]
@dataclass(frozen=True)
class LinkType: name: str; from_type: str; to_type: str; cardinality: str; view: str
@dataclass(frozen=True)
class ActionType: name: str; object_type: str; roles: frozenset[str]; criteria: list[str]
@dataclass(frozen=True)
class Ontology: objects: dict[str, ObjectType]; links: dict[str, LinkType]; actions: dict[str, ActionType]
def load_ontology(path: Path) -> Ontology          # validates references; raises OntologyError
def check_backing(con, onto: Ontology) -> list[str]   # problems: missing view, key not unique (AC-3.1)

# ontokit/audit.py (DE): copied from vaccine-assay-pipeline/pipeline/audit.py, header names the source
GENESIS_HASH = "0" * 64
class AppendOnlyError(RuntimeError): ...
def init_audit(con) -> None
def append(con, *, action_type, object_type, object_key, actor_id, role, reason,
           old_value, new_value, outcome, failed_criterion=None, now=None) -> int   # caller owns txn
def update(*a, **k) -> NoReturn;  def delete(*a, **k) -> NoReturn                 # raise AppendOnlyError
@dataclass
class ChainResult: ok: bool; rows_checked: int; first_broken_seq: int | None; reason: str | None; checked_at_utc: datetime
def verify_chain(con, anchor: Path | dict | None = None) -> ChainResult
def write_anchor(con, path: Path) -> dict                                        # head seq + hash, external file

# ontokit/actions.py (ML)
class ActionRefused(Exception): criterion: str
class ActionPermissionError(ActionRefused, PermissionError): ...
@dataclass(frozen=True)
class Actor: user_id: str; role: str; active: bool
@dataclass(frozen=True)
class ActionResult: outcome: str; seq: int; object_key: str; new_value: dict
Criterion = tuple[str, Callable[[con, dict], bool]]          # (criterion_id, predicate)
def run_action(con, spec: ActionType, actor: Actor, params: dict, *, object_key: str,
               criteria: list[Criterion], apply: Callable[[con, dict], tuple[dict, dict]],
               now: datetime | None = None) -> ActionResult
#  1 BEGIN. 2 role check: actor.active and actor.role in spec.roles, else criterion "role".
#  3 criteria in declared order; the first failure gives criterion id = its name.
#  4 refused: ROLLBACK, then a separate txn appends one REFUSED row, then raise.
#  5 allowed: (old, new) = apply(con, params) writes edit rows, then audit.append(SUCCESS), COMMIT.
#  6 any exception in 5: ROLLBACK and re-raise (no edit, no audit row: AC-6.3).

# ontokit/writeback.py (ML)
def latest_edit_view_sql(base: str | None, edits: str, key: str, view: str) -> str
def init_edit_tables(con, ddl: list[str]) -> None     # CREATE ... IF NOT EXISTS only, never DROP

# console/identity.py, console/actions.py (ML)
def get_actor(con, user_id: str) -> Actor            # role comes from seed.app_user, never from the caller
def propose_substitute(con, user_id, event_key, alt_ndc11, reason, now=None) -> ActionResult
def approve_substitute(con, user_id, substitute_id, reason, now=None) -> ActionResult
def reject_substitute(con, user_id, substitute_id, reason, now=None) -> ActionResult
def open_mitigation_task(con, user_id, event_key, facility_id, assignee_id, due_date, reason, now=None) -> ActionResult
def close_mitigation_task(con, user_id, task_id, resolution_note, now=None) -> ActionResult
def reassign_task(con, user_id, task_id, new_assignee_id, reason, now=None) -> ActionResult

# console/queries.py, freshness.py, metrics.py (AE)
def triage_queue(con) -> pd.DataFrame
def drug_object(con, event_key: str) -> dict          # history, substitutes, tasks, audit rows
def audit_entries(con, object_key=None, actor_id=None, action_type=None) -> pd.DataFrame
def is_stale(snapshot_date: date, today: date, max_age_days: int = 1) -> bool
def compute_impact_metrics(con, match_quality: dict) -> dict   # -> reports/impact_metrics.json
```

**Approve-substitute rule, exact:** the alternative counts as short if some event in the latest snapshot has `ndc11 = alt_ndc11` or `product_ndc9 = alt product_ndc9`, and also `status = 'Current'` or `availability_normalized IN ('Limited','Unavailable')`.

**Time:** every action takes `now`, defaulting to UTC now, so the tests and the AC-8.3 simulation are deterministic. Due-date checks compare against `now` in `America/New_York`.

---

## 5. Matching and the gold set

**Tiers (AC-4.2).**
1. Formulary ndc11 = event ndc11.
2. Formulary product_ndc9 = event product_ndc9, or = any `openfda.product_ndc` normalized (only when `has_openfda`).
3. ingredient_norm + dosage_form_norm + route_norm equal, and strength equal when both sides have one. The event route comes from openfda.route, else `default_route(dosage_form)`. This is what lets the 20% with no openfda block match (AC-4.6).

**Brand map:** built from `openfda.brand_name -> generic_name` pairs in the snapshot, plus `proprietary_name -> generic_name`. No external lists.

**Gold set (E1, AC-4.3), owner QA, so the matcher author does not grade their own work.**

`tests/fixtures/gold_matches.csv`. The header comment holds the labeling rule. Columns: `pair_id, item_id, event_key, label (1/0), pair_kind (positive/hard_negative), variant_type, rationale, labeled_by, reviewed_by`. `reviewed_by` = `pending` on every row.

Labeling rule (TL): a pair is **1** if the formulary item is the shortage record's package, or another package of the same product, or the same active ingredient (salt-insensitive) with the same dosage form and route and, when the shortage record lists a strength, the same strength. Otherwise it is **0**.

Hard negatives must include:
- the same ingredient by a different route
- the same ingredient at a different strength
- look-alike names (for example hydroxyzine vs hydralazine)
- the same labeler with a different product
- the leading-zero NDC error that points at a different product

Mix: at least 35 positives and 25 hard negatives. Every variant type in the generator manifest has at least 3 pairs. At least 10 pairs are hand-written cases that are not produced by the generator's variant rules, so the matcher cannot just learn the generator.

**Freeze:** `gold_matches.sha256` is committed with the file, and a test asserts the hash. The eval report prints the hash. Any change needs a HANDOFF line with the reason. Nobody edits the gold set after seeing matcher results (AC-4.5, SPEC R8). Michael's later review may change labels; that is logged as a new gold version, and both scores are reported.

---

## 6. Test strategy (offline by default)

- `tests/conftest.py` installs a socket guard (monkeypatch `socket.socket.connect` to raise) for every test, which proves AC-1.4. Tests marked `@pytest.mark.live` are skipped unless `DSC_LIVE=1`.
- `demo_db` (session): runs `build()` in fixtures mode into `tmp_path`. `mem_db`: an empty in-memory DuckDB for unit tests.
- Fixture days: `SYNTHETIC_D1..D3` are real trimmed records (`openfda_page_sample.json`, about 80 records pulled once by DE) with labeled synthetic changes between days. `expected_changes.csv` is hand-written by DE before `classify_changes` exists.
- Required proofs: idempotency (build twice, same counts and same content hash per table, AC-2.2); out-of-order (D1, D3, D2 equals D1, D2, D3, AC-2.4); re-ingest keeps edits (AC-6.4); forced error after the edit leaves no edit and no audit row (AC-6.3); tamper row k gives k; delete the newest row is caught by the checkpoint (AC-7.3); `ontokit` imports nothing from pipeline, console or app (ADR 005).
- QA adds at least one **planted-bug proof**: revert one rule (for example, tier 1 uses `openfda.package_ndc`) and show that a test fails. This is recorded in BUGS.md.
- `python -m pipeline.run --fixtures --test` runs pytest and writes the pass/fail counts to `docs/results.json` (vaccine pattern).

---

## 7. Dependencies (light on purpose)

`duckdb, pandas, pyyaml, streamlit, pytest, ruff`. No requests (stdlib `urllib`), no dbt (ADR 002), no pydantic (frozen dataclasses), no Ollama (no LLM in scope). OPS pins exact versions.

---

## 8. Cross-cutting concerns

- **Privacy:** no PHI or PII anywhere. The FDA data is public. The hospital, users, stock, tasks and simulated usage are synthetic and labeled in data (`is_synthetic`), the UI (a banner on every page), the README, and the snapshot manifest (`SYNTHETIC_fixture`).
- **Security:** no secrets (the API needs no key). Network calls go only to the allow-listed host. The demo identity picker is not authentication: role is looked up server-side from `seed.app_user`, so the UI cannot pass a role in.
- **Integrity:** the hash chain, checkpoints and an optional external anchor `data/<mode>/anchor.json` (vaccine pattern). DuckDB limitation (vaccine ADR 001): anyone with the file can run SQL. The chain detects this; it does not prevent it. This goes in KNOWN_ISSUES.
- **Observability:** `core.dq_results`, `docs/results.json`, stage counts in the run summary, and the chain verify shown on the audit page.
- **Concurrency:** DuckDB allows one read-write process. The app opens a connection per rerun and closes it, and never caches one in `st.cache_resource`. `pipeline.run` fails with a plain message ("close the app, the database is in use") if the file is locked. This is fine for one demo user and is the first thing to change for a real deployment (decomposition doc).

---

## 9. Risks (design level; PE folds these into RISK_REGISTER.md)

| # | Risk | L / I | Mitigation | Owner |
|---|---|---|---|---|
| T1 | 2 GB RAM: Streamlit, DuckDB and pytest together exhaust memory | M / M | DuckDB memory_limit 512MB, threads 2; no dbt/Postgres/Docker; fixture DB is tiny; OPS measures peak RSS once | OPS |
| T2 | First live day: everything is NEW, so the live queue is huge and the queue-length guardrail is meaningless | Certain / M | Guardrail (b) reports `n/a: 1 snapshot` until there are 2 live days; the demo uses fixture history; scheduled pulls (E4) fix it | AE |
| T3 | Gold set is derived from the generator, so the matcher overfits the generator's variant rules | M / H | QA drafts it (not ML); 10+ hand-written pairs outside the generator rules; frozen hash | QA |
| T4 | Labels drafted by the team, not a pharmacist | Certain / M | `reviewed_by: pending`; README and impact.md say "team-labeled, pending owner review" | QA, AE |
| T5 | Fixture history days are synthetic but the records are real, so a reader may think the changes are real FDA history | M / H | `SYNTHETIC_` folder names, `source: SYNTHETIC_fixture`, header label "demo history is synthetic" | DE, AE |
| T6 | `openfda.package_ndc` is product-level (UNVERIFIED) and is used by mistake for tier 1 | M / H | Tier 1 is top-level only; regression test from the observed 0378-6860-77 vs 0378-6854-77 case | ML |
| T7 | 10-digit unhyphenated NDCs are ambiguous | M / M | Return a reason, do not guess; tiers 2 and 3 can still match on name | ML |
| T8 | DuckDB file lock between the app and a pipeline run | M / L | Per-rerun connections; clear error; KNOWN_ISSUES | DE, AE |
| T9 | Streamlit reruns lose state, or a double click submits twice | M / M | All state comes from the DB; forms use `st.form` and a submit key; duplicate approve is refused by the "state is proposed" criterion | AE |
| T10 | Snapshot date timezone: a late-night run lands on the wrong day | L / M | One configured timezone; the manifest stores fetched_at_utc too | DE |
| T11 | Full rebuild gets slow as history grows | L (v1) / L | 600k rows per year is trivial for DuckDB; a per-snapshot parquet cache is the planned next step, noted in ADR 004 | DE |
| T12 | Upstream schema drift breaks parsing | M / H | Schema-drift check by field name; unknown fields kept in record_hash and reported, missing fields become null plus FAIL | DE |
| T13 | Library extraction becomes gold-plating | M / M | ontokit holds only 4 modules that the app needs anyway; no packaging or publishing in v1 (ADR 005) | TL |
| T14 | Overclaiming (SPEC R13) | M / H | PE reviews README and impact.md wording; "simulated usage" labels come from code | PE |

---

## 10. Story -> component map

| Story | Components |
|---|---|
| US-1 | http, snapshots, ingest, dq, fixtures |
| US-2 | availability, history, changes |
| US-3 | ontology.yaml, ontokit.ontology, objects, generator/hospital |
| US-4 | ndc, names, match, evaluate, gold set |
| US-5 | console.queries, freshness, app |
| US-6 | ontokit.actions, writeback, console.actions, identity |
| US-7 | ontokit.audit, tamper, app audit page |
| US-8 | console.metrics, simulate, docs/impact.md |
| US-9 | docs/decomposition.md |

## 11. Optional later phase: Palantir AIP Developer Tier (E5)

`ontology/ontology.yaml` is written so it maps one-to-one onto Foundry: object types with primary keys, link types with cardinality, and action types with roles and submission-criterion ids. If Michael signs up later, the rebuild is: upload the `obj.*` views as datasets, recreate the object and link types from the YAML, and re-express the six actions with the same criteria. Nothing in v1 depends on it. Michael signs up himself, and no Palantir calls appear in the code.

---

## 12. Contract changes log (Tech Lead approves)

| Date | Change | Source | Status |
|---|---|---|---|
| 2026-10-30 | Matcher guards on NDC tiers; v2 corroborated name guard, near-miss tier 3, brand-key cleanup, `baseline_v2` | ADR 006 + addendum | Accepted with conditions (ingredient-set guard, brand-key component rule, new held-out set) |
| 2026-10-30 | `ObjectType`/`LinkType` gain optional `source`, `edits`, `defaults`, `sql`, `from_key`, `to_key`; `latest_edit_view_sql` takes keyword-only `base_columns`/`edit_columns`/`defaults`/`order_by` | ML handoff | Accepted |
| 2026-10-30 | `run_action` passes `_actor`, `_now`, `_action_seq` to criteria and `apply`; keys starting with `_` are stripped from audit JSON | ML handoff | Accepted |
| 2026-10-30 | Detection time = snapshot that first put the event in the queue, not `first_seen` | AE METRICS.md | Accepted |
| 2026-10-30 | First snapshot is a baseline (not in the daily queue); actionable filter; item-grain baseline worklist; ranked, visible cap of 40; guardrail (b) on the uncapped daily queue. Supersedes the T2 mitigation in section 9 | ADR 007 | Accepted, SPEC wording pending product-owner |
