# SPEC: Drug Shortage Operations Console

Owner: Deployment Strategist (product-owner seat). Date: 2026-10-30. Status: DRAFT, needs Michael's approval on the open decisions in section 9.
Next seat: tech-lead (DESIGN.md, ADRs, TASKS.md).

Data rule: shortage data is **public** (openFDA). Everything about the hospital (facilities, formulary, stock, users, tasks) is **synthetic** and labeled `SYNTHETIC` in the data and in the UI. No real hospital, patient or employer data.

---

## 0. Verified facts about the data source (checked 2026-10-30)

| Fact | Evidence |
|---|---|
| Endpoint is `https://api.fda.gov/drug/shortages.json` | Live call `?limit=2` returned data, `meta.last_updated = 2026-10-29`, `meta.results.total = 1614` |
| Updated daily; covers 2012 onward | [open.fda.gov/apis/drug/drugshortages](https://open.fda.gov/apis/drug/drugshortages/) |
| 22 searchable fields: `package_ndc`, `generic_name`, `proprietary_name`, `company_name`, `contact_info`, `presentation`, `update_type`, `availability`, `related_info`, `related_info_link`, `resolved_note`, `shortage_reason`, `therapeutic_category` (array), `dosage_form`, `strength` (array), `status`, `update_date`, `change_date`, `discontinued_date`, `initial_posting_date`, `openfda` (object), `type` | [searchable-fields page](https://open.fda.gov/apis/drug/drugshortages/searchable-fields/) (no field descriptions on that page) |
| `openfda` block holds harmonized ids: `product_ndc`, `package_ndc`, `rxcui`, `unii`, `substance_name`, `route`, `manufacturer_name`, `brand_name`, `generic_name`, `application_number`, `spl_id`, `spl_set_id`, `product_type` | Live call `?limit=2` |
| **323 of 1614 records (20%) have no `openfda` block**, so no harmonized ids | Live call `?search=_missing_:openfda` |
| `status` counts: Current 1153, To Be Discontinued 442, Resolved 19 | Live call `?count=status` |
| `availability` is free text with typos: "Available" 749, "Unavailable" 263, "Limited Availability" 138, plus "Limited Availalbility", "Unvailable", and a sentence ("Limited Supply Available. Next Delivery ... June 2027") | Live call `?count=availability` |
| Dates are `MM/DD/YYYY` strings (e.g. `update_date = 10/19/2026`) | Live call `?limit=2` |
| Grain is one record per **package NDC / presentation**, not per drug | Live call: `presentation` = "Lisdexamfetamine Dimesylate, Capsule, 70 mg (NDC 0378-6860-77)" |
| Paging: `limit` max 1000, `skip` max 25000. No key: 240 req/min, 1,000 req/day per IP | [query parameters](https://open.fda.gov/apis/query-parameters/), [authentication](https://open.fda.gov/apis/authentication/) |
| openFDA says do not rely on it for medical-care decisions; data is unvalidated | [drugshortages page](https://open.fda.gov/apis/drug/drugshortages/); `meta.disclaimer` in the live response |

So a full pull is 2 requests. No API key is needed, so there is no secret to manage.

Observed but **UNVERIFIED** (sample of 2): one record had `package_ndc = 0378-6860-77` while its `openfda.package_ndc` showed `0378-6854-77`. The harmonized block seems to describe the *product* (possibly several packages), so it must not be treated as "the NDC that is short." The matcher must check this.

---

## 1. Persona and the morning decision

**Persona: Dana Ruiz, PharmD, Drug Shortage Coordinator** (fictional) at **Riverbend Health (SYNTHETIC)**, a 3-hospital system: Riverbend Main (academic, 500 beds), Riverbend North (community, 180 beds), Riverbend Kids (pediatric, 90 beds).

Grounding: the ASHP shortage guidelines (Fox and McLaughlin, AJHP 2018) recommend a pharmacy-led shortage team with a named leader ([ASPR TRACIE listing](https://asprtracie.hhs.gov/technical-resources/resource/6754/ashp-guidelines-on-managing-drug-product-shortages); [Newswise summary](https://www.newswise.com/articles/drug-shortage-guidelines-provide-strategies-to-minimize-impact-on-patient-care)). A Brigham and Women's case study describes a shortage team that met every morning to review inventory ([PMC7894056](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7894056/)). Teams routinely check the FDA and ASHP lists ([CompleteRx](https://www.completerx.com/?p=2113), vendor source). Dana's exact workflow below is a design assumption, **UNVERIFIED** with a real coordinator.

**Today (assumed):** Dana opens the FDA site, searches drug names one at a time against a spreadsheet of what Riverbend stocks, and tracks follow-ups in email. She cannot see what changed since yesterday, misses records that use a different NDC format or name, and cannot prove who approved which substitute.

**The decision she makes every morning (7:30, before the shortage huddle):**
> "Of the shortages that are new or got worse since yesterday, which ones hit drugs we actually stock, and for each one: do I approve a substitute, open a mitigation task for a buyer, or mark it 'no action, low impact'?"

**Other users (all synthetic):**
| Role | Example user | Can do |
|---|---|---|
| `pharmacist` | Dana Ruiz, Sam Okafor | everything, including **approve substitute** |
| `buyer` (supply chain) | Lee Chen, Priya Nair | propose substitute, open/close/reassign mitigation tasks |
| `viewer` (nurse manager, exec) | Jordan Blake | read only |

---

## 2. PR/FAQ (one page)

**PRESS RELEASE (internal, hypothetical)**
**Riverbend Health pharmacy cuts the morning shortage review from a manual search to one queue.**
*Riverbend, NY.* Riverbend Health's pharmacy team now starts each day with the Drug Shortage Console. Every morning it pulls the FDA's public shortage list, compares it with yesterday's, and shows only the shortages that are new or worse **and** touch a drug Riverbend stocks. From the same screen, a pharmacist approves a substitute, a buyer takes a mitigation task, and every action is written to a tamper-evident log. "I used to search drug names one by one and hope I caught the NDC variants," said Dana Ruiz, shortage coordinator (fictional). "Now I see what changed, what we stock, and who owns it, and I can show the P&T committee exactly who decided what and when."

**FAQ**
- **Q: Does it predict shortages?** No. It reports what the FDA has posted and what changed. Prediction with the available features would be guesswork and is out of scope.
- **Q: Is this real hospital data?** No. The shortage list is real public FDA data. Facilities, formulary, stock levels, users and tasks are synthetic and labeled.
- **Q: Does it recommend clinical substitutes?** No. It lists *candidate* substitutes that are mechanical matches (same ingredient and route, different package, strength or manufacturer, not itself short). A pharmacist must approve; the app never does. openFDA itself says not to rely on it for medical-care decisions.
- **Q: How do we know the matching works?** Match precision and recall are measured against a hand-labeled gold set and shown in the app, next to a naive name-only baseline.
- **Q: What if the FDA API is down?** The app runs from the last cached snapshot and shows the snapshot date with a stale-data banner.
- **Q: Who can approve a substitute?** Only the pharmacist role. The rule is enforced in the action layer, not just by hiding a button, and refused attempts are logged.
- **Q: Can someone quietly edit history?** The action log is append-only and hash-chained; a verify command names the first tampered row.
- **Q: How would this go live at a real hospital?** See `docs/decomposition.md` : integrations, data owners, rollout, and what changes. That write-up is US-9.

---

## 3. Ontology (object model)

| Object type | Key | Source | Notes |
|---|---|---|---|
| `Drug` | normalized NDC-11 (package) | openFDA records + synthetic formulary | properties: generic name, normalized ingredient, dosage form, route, strength, manufacturer, rxcui (nullable) |
| `ShortageEvent` | `package_ndc` + `initial_posting_date` | openFDA, via daily snapshots | status, availability (raw + normalized), shortage_reason, update/change dates, first_seen / last_seen snapshot dates |
| `Substitute` | substitute_id | candidate generator + user proposals (write-back) | links shortage Drug to alternative Drug; state: `candidate` / `proposed` / `approved` / `rejected` |
| `MitigationTask` | task_id | write-back only (created by actions) | state `open` / `closed`; assignee, facility, due date, resolution note |
| `Facility` | facility_id | **SYNTHETIC** seed file | 3 sites; each stocks a subset of formulary items with synthetic on-hand days of supply |

Links: `Drug 1-* ShortageEvent`; `ShortageEvent *-* Facility` (via formulary: "affects"); `ShortageEvent 1-* Substitute`; `Substitute *-1 Drug` (the alternative); `ShortageEvent 1-* MitigationTask`; `MitigationTask *-1 Facility`.

The ontology is declared in one file (e.g. `ontology.yaml`) and is the single source for tables and Python models. The tech lead picks the format.

---

## 4. User stories and acceptance criteria

Every AC must be checkable by a test that runs **offline** from fixtures, unless marked (LIVE).

### US-1 Ingest and cache the openFDA shortage list
*As Dana, I want the FDA list pulled and saved each day so the console works even if the API is down.*
- AC-1.1 One command pulls all records with paging (`limit<=1000`, `skip`) and writes the raw JSON plus `meta` to `data/raw/YYYY-MM-DD/` (date = run date). The record count saved equals `meta.results.total`, or the run fails loudly.
- AC-1.2 If the snapshot for today already exists, the command uses the cache and makes **zero** network calls unless `--refresh` is passed (test with a mocked HTTP client that fails on any call).
- AC-1.3 The only host the code calls is `api.fda.gov` (test: the HTTP wrapper rejects any other host).
- AC-1.4 All tests pass with the network disabled, using fixture files in `tests/fixtures/` (real public records, trimmed, plus synthetic variants labeled `SYNTHETIC_` in the file name).
- AC-1.5 Data-quality checks run on every load and are stored as rows in a `dq_results` table: (a) volume: record count within +/-20% of the previous snapshot, (b) freshness: `meta.last_updated` no older than 3 days, (c) schema drift: an unexpected or missing top-level field is reported by name, (d) unparseable dates counted. A fixture with a renamed field makes check (c) fail.
- AC-1.6 (LIVE) A manual run against the real API saves a snapshot whose count matches `meta.results.total`. Result recorded in HANDOFF.md.

### US-2 Daily snapshots and status history
*As Dana, I want to see what changed since yesterday, because the FDA list shows only the current state.*
- AC-2.1 Each snapshot is loaded into a status-history table with valid_from / valid_to per `ShortageEvent` (SCD type 2) on the tracked fields `status`, `availability_normalized`, `shortage_reason`, `update_date`.
- AC-2.2 Re-loading the same snapshot twice gives identical row counts and an identical content hash (idempotency test).
- AC-2.3 Given fixture days D1, D2, D3, the change feed classifies each event as exactly one of `NEW`, `WORSENED` (e.g. Available -> Limited -> Unavailable), `IMPROVED`, `RESOLVED`, `DROPPED` (present D1, absent D2), `UNCHANGED`, and the counts match a hand-written expected file.
- AC-2.4 Loading snapshots out of order (D3 before D2) either produces the same final history or is refused with a clear error. The tech lead picks which; the test proves it.
- AC-2.5 `availability` is normalized to `Available` / `Limited` / `Unavailable` / `Unknown`, including the observed typos ("Unvailable", "Limited Availalbility"). The raw value is kept. Every distinct raw value in the fixtures maps to a bucket, and unmapped values go to `Unknown` and show up in `dq_results`.

### US-3 Ontology and synthetic hospital
*As a deployment engineer, I want one object model so the app, actions and tests agree on what a Drug or a Task is.*
- AC-3.1 The five object types and six link types in section 3 exist in the ontology file, and a test asserts that each one has a backing table or view with the declared key, and that the key is unique.
- AC-3.2 Synthetic seed data: 3 facilities, a formulary of 150-300 items, and 8 users across the 3 roles. It is generated from a fixed random seed (same seed gives a byte-identical file).
- AC-3.3 The formulary deliberately mixes NDC formats (4-4-2, 5-3-2, 5-4-1 with hyphens; 11-digit without hyphens; a few with a leading-zero error) and name variants (brand vs generic, salt names, case and whitespace). At least 30% of items correspond to drugs on the fixture shortage list. The variant mix is written to a manifest so the matcher can be scored.
- AC-3.4 Every synthetic table has a `is_synthetic = true` column, and every app page that shows synthetic data shows a visible "SYNTHETIC hospital data" label.

### US-4 NDC and drug-name matching, with measured quality
*As Dana, I want shortages matched to what we stock even when NDC formats and names differ, and I want to know how good the matching is.*
- AC-4.1 NDC normalization converts every 10-digit hyphenated format (4-4-2, 5-3-2, 5-4-1) to 11-digit 5-4-2, with unit tests for each format and for invalid inputs (wrong length, letters), which return a reason instead of crashing.
- AC-4.2 The matcher runs in tiers and records which tier matched: (1) exact package NDC-11, (2) product NDC (labeler + product, any package), (3) normalized ingredient + dosage form + route (+ strength when present). Each match row has `match_tier` and `match_confidence`.
- AC-4.3 A gold set of at least 60 labeled formulary-to-shortage pairs (positives and hard negatives) lives in `tests/fixtures/gold_matches.csv`, with a written labeling rule in the file header.
- AC-4.4 An evaluation command prints precision, recall and F1 for the matcher **and** for a naive baseline (exact lowercase `generic_name` equality), overall and per tier, and writes them to `reports/match_quality.json`. The app shows these numbers.
- AC-4.5 Target: matcher recall >= 0.95 with precision >= 0.90 on the gold set. If it misses, the report says so and lists the failing pairs; nobody tunes the gold set to pass.
- AC-4.6 Records with no `openfda` block (about 20% live) are still matched through tiers 1 and 3. A test covers one.

### US-5 Morning triage queue (the operational app)
*As Dana, at 7:30 I want one screen listing only what changed and affects us, so I can decide quickly.*
- AC-5.1 The app starts with one command and opens on the triage queue: shortage events that are `NEW` or `WORSENED` in the latest snapshot and matched to at least one facility's formulary, sorted by availability (Unavailable first), then by lowest synthetic days-on-hand.
- AC-5.2 Each row shows the drug, change type, availability (raw and normalized), affected facilities, match tier, count of open tasks, and whether a substitute is approved.
- AC-5.3 Clicking a row opens a drug object view with status history (from US-2), linked substitutes, linked tasks, and that object's audit entries.
- AC-5.4 The header shows the snapshot date and `meta.last_updated`. If the newest snapshot is older than 1 day, a stale-data banner appears (test the function that decides this, not the pixels).
- AC-5.5 A user picker in the sidebar sets the acting user and role. The UI labels it as "demo identity, not authentication."
- AC-5.6 A screen recording or a set of screenshots of the full click-through (queue -> object -> action -> audit) is saved to `docs/demo/`.

### US-6 Actions with submission criteria and write-back
*As Dana, I want to approve substitutes and hand tasks to buyers from the same screen, with rules that stop the wrong person doing the wrong thing.*
Actions are plain Python functions in one action layer. The UI and the tests call the same functions.

| Action | Allowed roles | Submission criteria (refuse if not met) |
|---|---|---|
| `propose_substitute` | pharmacist, buyer | alternative Drug exists; is not the same NDC-11; reason given |
| `approve_substitute` | **pharmacist only** | substitute is in `proposed`; the alternative is **not** itself in a Current, Limited or Unavailable shortage in the latest snapshot; reason given |
| `reject_substitute` | pharmacist only | substitute is in `proposed`; reason given |
| `open_mitigation_task` | pharmacist, buyer | linked ShortageEvent exists; facility valid; assignee is an active pharmacist or buyer; due date not in the past |
| `close_mitigation_task` | the assignee or any pharmacist | task is `open`; resolution note at least 10 characters |
| `reassign_task` | pharmacist, buyer | task is `open`; new assignee differs from the current one and is an active pharmacist or buyer; reason given |

- AC-6.1 Each action in the table has a test for the allowed case and at least one refused case. A `buyer` calling `approve_substitute` raises a permission error, and a `viewer` cannot run any action.
- AC-6.2 A refused action changes no object and writes one audit row with outcome `REFUSED` and the failed criterion.
- AC-6.3 Each action is one transaction: the object edit and its audit row are both written or neither is (test by forcing an error after the edit).
- AC-6.4 Write-back: pipeline-owned tables are never modified by actions. Edits go to separate edit tables, and the object views merge base + edits (edits win). Re-running the pipeline (US-1/US-2) does not erase any edit (test: act, re-ingest, edit still there).
- AC-6.5 After a successful action in the app, the queue and object view show the new state without restarting the app.

### US-7 Append-only, hash-chained action log
*As the pharmacy director, I want proof of who approved what and when, that cannot be edited quietly.*
- AC-7.1 Every action attempt (success or refused) appends one row with: seq, action type, object type and key, actor, role, reason, old and new values, outcome, UTC timestamp, prev_hash, row_hash. The hash rule matches `vaccine-assay-pipeline/pipeline/audit.py`: `sha256(prev_hash + "|" + canonical_json)`, genesis `"0"*64`.
- AC-7.2 There is no update or delete path for the log; calling one raises an error (test).
- AC-7.3 `verify` recomputes the chain. A test that edits row k directly in the database gets `first_broken_seq == k`, and a test that deletes the newest row is caught (checkpoint or anchor, as in the source pattern).
- AC-7.4 The app has an audit page filtered by object, actor or action, with the chain-verify result and time shown at the top.
- AC-7.5 The reused code is copied with a comment naming its source file. The tech lead decides whether to extract it into a shared library; no import across project folders.

### US-8 Success metric, baseline and guardrails
*As a deployment strategist, I want to show the console improves the morning decision, without gaming a number.*
- AC-8.1 `docs/impact.md` defines the **primary metric**: *recall of affected formulary items surfaced in the morning queue*, measured on the gold set, compared with the **baseline** of manual name search (simulated as exact generic-name match, AC-4.4). Both numbers come from `reports/match_quality.json`, not typed in by hand.
- AC-8.2 **Guardrails** with thresholds, each computed by code: (a) queue precision >= 0.90 (alert fatigue), (b) queue length on the latest real snapshot reported (if it is over 40 items, the doc says the queue is not usable as-is), (c) audit chain verify = OK, (d) zero actions executed by a role not allowed (from the audit log), (e) DQ checks pass rate.
- AC-8.3 A secondary operational metric, *time from detection to first action per affected shortage*, is defined and computed from the audit log, and is clearly labeled as **measured on simulated demo usage only, not real users.**
- AC-8.4 The doc lists at least 2 ways each metric could be gamed and which guardrail catches it.

### US-9 "How I'd deploy this at a hospital system" decomposition
*As an interviewer or hospital stakeholder, I want to see how this becomes a real deployment.*
- AC-9.1 `docs/decomposition.md` exists, at most about 3 pages, and covers: the problem broken into parts (data in, identity matching, decision workflow, audit/compliance, change management); real data sources it would replace the synthetic ones with (formulary/inventory from the ERP or pharmacy system, wholesaler allocations, ASHP list), each marked as an assumption; who owns each data source; rollout (one site and one shadow week, then all sites); training plan; what to measure in week 1, 4 and 12; top risks.
- AC-9.2 It maps each part to the Foundry concept it stands in for (object type, link type, action type, submission criteria, writeback dataset, Workshop app) with a link to palantir.com/docs for each, and states plainly that this is an open-source stand-in, not Foundry.
- AC-9.3 Every claim about real hospitals or vendors has a URL or is marked UNVERIFIED.

---

## 5. Why Streamlit for the app

- Michael has shipped Streamlit before (QuantLab), so the build risk is low and he can explain every line in an interview.
- Python only: the action layer, ontology and app share one language and one test suite.
- Forms, tables, sidebar user picker and page navigation are built in, which fits about 1 day of UI work.
- **Accepted trade-offs** (go in an ADR and KNOWN_ISSUES): no real authentication (demo identity picker only); every click reruns the script, so state must come from the database and not from session memory; there is no multi-user concurrency story (a single-writer local database is fine for a demo, not for a hospital). Rejected options: FastAPI + React (too much for 2 days), Dash (no team experience), notebooks (not an operational app).
- To keep the UI swappable, all rules live in the action layer; Streamlit only calls it. The decomposition doc says what would change for a real multi-user deployment.

---

## 6. Out of scope

- Shortage **prediction** or forecasting of any kind.
- Clinical therapeutic-interchange advice (for example "use drug B instead of A"). Candidates are mechanical matches only, and a pharmacist decides.
- Real authentication, SSO or a user directory; real hospital, inventory, patient or employer data.
- EHR/ERP/wholesaler integrations (described in US-9 only).
- Scraping the ASHP shortage list or any non-openFDA source. Network calls go to `api.fda.gov` only.
- Notifications beyond an outbox table stub (no email or SMS sends).
- Cloud deployment, Docker hardening, CI beyond running tests locally (devops seat may add light CI later).
- LLM features. Nothing in this spec needs Ollama.
- Rebuilding the ontology in the Palantir AIP Developer Tier. That is an optional later step; Michael signs up himself.
- Backfilling history before the first snapshot. The API gives current state only, so history starts on the first run (plus labeled synthetic fixture days for the demo).

---

## 7. Risks and pitfalls (from the research and the live API checks)

| # | Risk | Likelihood / Impact | Mitigation | Story |
|---|---|---|---|---|
| R1 | History is lost because the API shows only the current state (only 19 records say Resolved, so resolved items probably drop off; **UNVERIFIED**) | High / High | Daily raw snapshots + SCD2; `DROPPED` change type | US-2 |
| R2 | NDC format chaos (4-4-2, 5-3-2, 5-4-1, 11-digit) and name variants make matching miss items | High / High | Tiered matcher, gold set, published precision and recall | US-4 |
| R3 | 20% of records have no `openfda` block, so no rxcui or harmonized ids | Certain / Medium | Tiers 1 and 3 do not depend on `openfda` | AC-4.6 |
| R4 | `openfda.package_ndc` is not the short package (observed once, UNVERIFIED) | Medium / High | Use the top-level `package_ndc` for tier 1; test with the observed case | US-4 |
| R5 | Free-text `availability` with typos and sentences | Certain / Medium | Normalizer + `Unknown` bucket reported in DQ | AC-2.5 |
| R6 | Schema drift or field renames upstream | Medium / High | Schema check stored in `dq_results`; fixture test | AC-1.5 |
| R7 | Synthetic hospital data mistaken for real | Low / High | `is_synthetic` column, UI label, README statement | AC-3.4 |
| R8 | Metric gaming: tuning the gold set, or a short queue that looks efficient but misses items | Medium / High | Gold set frozen before tuning; recall paired with precision and queue length guardrails | US-8 |
| R9 | Role checks only in the UI (a hidden button is not a control) | Medium / High | Rules enforced in the action layer and tested without the UI | US-6 |
| R10 | Non-idempotent loads create duplicate events | Medium / Medium | Run-twice test | AC-2.2 |
| R11 | API rate limit or outage during the demo | Low / Medium | Disk cache; offline fixtures; stale banner | US-1, AC-5.4 |
| R12 | Streamlit plus a single-writer local database lock errors | Medium / Low | One connection per action, short transactions; noted in KNOWN_ISSUES | US-6 |
| R13 | Overclaiming in the README or resume ("used by pharmacists", "production") | Medium / High | Say "portfolio project, synthetic hospital, simulated users" (research section 5) | all |
| R14 | Scope creep past 2 days | High / Medium | Build order US-1 -> 2 -> 3 -> 4 -> 6 -> 7 -> 5 -> 8 -> 9; cut AC-5.6 video to screenshots and AC-2.4 to "refuse" first if late | all |

---

## 8. Which job postings this proves skills for

**Deployment Strategist, New Grad - Warp Speed (Palantir, New York)** [jobs.lever.co/palantir/37780e70...](https://jobs.lever.co/palantir/37780e70-c7af-45e1-9be7-071df201c387)
- "Work with Forward Deployed Engineers to integrate the data into a stable and extensible pipeline" -> US-1, US-2, US-3.
- "Present the results of our work and proposals for future work to audiences ranging from analysts to C-suite executives" -> PR/FAQ, US-8 impact doc, US-9 decomposition.
- "Lead training sessions to ensure that the product is meeting user needs" -> US-9 training and rollout plan.
- "Extraordinary ability to confront open-ended problems in unstructured environments" -> persona-first framing; US-9 is a written decomposition answer.
- "Experience with programming, scripting or statistical packages (e.g. Python, R, Matlab, SQL)" -> whole build.

**Forward Deployed Software Engineer, New Grad - Commercial (Palantir, New York)** [jobs.lever.co/palantir/2e6b0ac8...](https://jobs.lever.co/palantir/2e6b0ac8-83e9-4be5-a3aa-cf319f751728)
- "Build custom applications ... and production solutions engineered for a specific customer's reality" -> US-5, US-6.
- "Wrestle with data at a scale that breaks assumptions" -> honestly, the scale is small (about 1,600 records/day); what "breaks assumptions" here is messy identity data (US-4, R2-R5), not volume. Say it that way.
- "Collaborate with engineers to make architecture and design decisions" -> ADRs from the tech lead.
- "Own relationships with stakeholders from the users in the weeds to the executives" -> persona, PR/FAQ, decomposition.

Also relevant to pharma data roles: audit trail and data-integrity controls (US-7), reused from the vaccine-assay project. Interview mapping (research section 3.2, UNVERIFIED): US-9 = decomp round; a real matcher bug + postmortem = re-engineering story.

Foundry concepts used in the vocabulary (object types, link types, action types, submission criteria, writeback) are from [palantir.com/docs ontology overview](https://www.palantir.com/docs/foundry/ontology/overview/), [action types](https://www.palantir.com/docs/foundry/action-types/overview/) and [submission criteria](https://www.palantir.com/docs/foundry/action-types/submission-criteria/), as summarized in `dev-team/research/portfolio-signals-2026-10-30.md`.

---

## 9. Open decisions for Michael

1. **Gold-set labeling:** Michael labels the 60+ match pairs himself (most honest, about 1 hour), or the team labels them with a written rule and Michael spot-checks 15. Recommended: Michael labels.
2. **Targets:** accept recall >= 0.95 at precision >= 0.90 and a queue-length guardrail of 40, or set others.
3. **Persona and names:** OK with "Dana Ruiz, PharmD" and "Riverbend Health (SYNTHETIC), 3 sites"?
4. **Live scheduling:** run a real daily pull on the spare PC for 1-2 weeks so the repo shows real (not only fixture) history? This costs nothing but calendar time.
5. **Foundry Developer Tier rebuild:** keep it as a later, optional phase (Michael signs up himself), or drop it.
