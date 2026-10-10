# Atlas Commerce Agent — Six-Hour Code Sprint Plan

## Schedule and working rules

### Recovery pass — October 10, 2026, after the published Sprint 1 window

The user explicitly authorized Ad's coding agent to complete the remaining Z,
Ar, and S Sprint 1 work after the foundation review. This recovery pass stays
on the existing `feat/s1-ad-ranking-core` branch / PR #3 to preserve its pending
public API changes. This is an explicit cross-owner completion exception;
normal ownership resumes for Sprint 2. No other task branches are overwritten.
The original four-sprint sequence remains unchanged; subsequent sprint start
times must be re-baselined by the team after this recovery PR is reviewed.
Do not interpret the original timestamps or unchecked central table as live
completion evidence. Current verification and named blockers are maintained in
`docs/evidence.md`; no merge/reviewer box is checked by the implementation agent.

This plan covers the first six hours of implementation, anchored to the published neatHack kickoff.

- **Date:** Saturday, October 10, 2026
- **Timezone:** India Standard Time (IST, UTC+05:30)
- **Build window:** `2026-10-10 12:30` through `2026-10-10 18:30 IST`
- **Sprint duration:** Four 90-minute sprints
- **Team:** Z, Ar, Ad, and S

The team works in parallel within each sprint. At each sprint boundary, S coordinates integration, review, and the next sprint's branch cut. Each contributor owns the files for their assigned slice. If work depends on a teammate's unfinished interface, use the contract in `ARCHITECTURE.md` and a small local stub; do not edit the other person's files.

## Team ownership

| Teammate | Primary ownership | Typical files |
|---|---|---|
| **Z** | Domain schemas, SQLite persistence, version history, transactions | `src/adaptive_commerce/domain/`, `src/adaptive_commerce/persistence/`, persistence tests |
| **Ar** | Streamlit screens, user flow, storefront preview, demo usability | `src/adaptive_commerce/presentation/`, `app.py`, UI tests |
| **Ad** | Agent workflow, signal processing, recommendation logic, worker, evaluation | `src/adaptive_commerce/agents/`, `application/`, `recommender/`, `worker.py`, logic tests |
| **S** | neatlogs/cfo.ai integration, integration test coordination, evidence, sprint integration | `src/adaptive_commerce/integrations/`, `docs/evidence.md`, integration tests |

S is the integration steward. Each task owner updates their own GitHub PR checklist. S alone updates the central sprint status boxes after confirming the PR exists and the required checks are green; this avoids concurrent edits to the shared sprint tracker.

## Branch and GitHub workflow

- Create the task branch shown below from the current `main` at the sprint start.
- Keep each branch limited to its owner's assigned files and deliverables.
- Use a short imperative commit subject, such as `Add bounded regional weight updates`.
- Open a pull request to `main` before the sprint end. Add the sprint label and link the exact task.
- The task owner ticks **PR uploaded and checks passed** only after pushing the branch, adding its PR URL, and seeing the required checks pass.
- The task owner leaves **reviewed and merged** for S/reviewer to tick after review and merge.
- S updates the central task checkbox after confirming the PR URL and green checks. A push without tests or a PR does not complete a checklist item.
- At the sprint boundary, S merges passing PRs in dependency order, resolves integration issues with the owner, and posts the new `main` commit used by the next sprint.
- Never push directly to shared `main`. Do not force-push shared branches. Keep secrets, local `.env` files, and real personal data out of commits.

Each task's checklist uses these two states:

```text
- [ ] PR uploaded, PR URL recorded, and required checks pass (task owner)
- [ ] Reviewed and merged to main (S/reviewer)
```

## Sprint 1 — Contracts and runnable foundation

**Time:** `2026-10-10 12:30–14:00 IST`  
**Goal:** Agree the data/service contracts, make the app start, and prove the three partner workflows can be used. Freeze domain and service names by 12:45 so parallel work stays compatible.

| Owner / checklist | Branch | Requirements and tests / acceptance |
|---|---|---|
| - [ ] **Z — domain contracts and SQLite bootstrap** | `feat/s1-z-domain-contracts` | Define Pydantic models for `Region`, `Product`, `Signal`, `RegionProfile`, `RecommendationVersion`, `FinanceAssessment`, and `UpdateCycle`; create the initial SQLite schema and seed loader. Tests: valid model round-trip; reject invalid confidence, price/cost, and lifecycle state; initialize an empty test database and load fixtures. |
| - [ ] **Ar — dashboard shell** | `feat/s1-ar-dashboard-shell` | Create the Streamlit entry point with region list, candidate-region form, finance-result panel, and storefront placeholder using the agreed service contracts. Tests: presentation view-model tests; manual launch shows seeded regions and an empty candidate flow without stack traces. |
| - [ ] **Ad — deterministic ranker** | `feat/s1-ad-ranking-core` | Implement product eligibility and deterministic tag/weight scoring with score breakdowns. Tests: same inputs produce same order; fixture festival signal changes the expected top five; excluded and out-of-stock products never appear. |
| - [ ] **S — integration baseline and business model** | `feat/s1-s-integrations-baseline` | Configure Entire for the repository and capture an initial context/checkpoint reference; initialize neatlogs and send one real test trace; create the cfo.ai business model for pricing, costs, revenue, gross margin, and runway, plus a candidate-region scenario. Tests/evidence: visible neatlogs trace ID; Entire checkpoint/context reference; cfo.ai model and scenario references recorded in `docs/evidence.md`. If account setup blocks a live connection, record the blocker and prove the documented import/degraded path—never claim a mock call is live. |

**Sprint 1 exit checklist**

- [ ] Domain and service contracts are agreed and committed.
- [ ] `uv run pytest` passes for the implemented tests.
- [ ] `uv run streamlit run app.py` opens the shell.
- [ ] Ranker fixture produces a visible, deterministic top-five change.
- [ ] One neatlogs trace, one Entire reference, and one cfo.ai model/scenario reference are recorded.
- [ ] All four task PRs are reviewed and merged, or have a named blocker and owner.

## Sprint 2 — Region assessment and persisted recommendations

**Time:** `2026-10-10 14:00–15:30 IST`  
**Goal:** Make the candidate-region approval path and before/after product screen work against durable state.

| Owner / checklist | Branch | Requirements and tests / acceptance |
|---|---|---|
| - [ ] **Z — persistence and version history** | `feat/s2-z-persistence-versions` | Implement repository operations for regions, products, signals, finance assessments, and recommendation versions. Enforce unique source IDs and preserve immutable parent-linked versions. Tests: CRUD/round-trip; duplicate signal is idempotent; parent version remains readable after a child version is saved. |
| - [ ] **Ar — candidate approval and storefront** | `feat/s2-ar-region-storefront` | Wire the region form to lifecycle application calls; show candidate financial inputs/outputs and explicit approve/reject actions; show region-specific top-five product cards with score explanations and before/after differences. Tests: presentation state tests for candidate/approved/rejected; manual flow does not activate a region until approve is clicked. |
| - [ ] **Ad — signal normalization and bounded profile proposal** | `feat/s2-ad-signal-updates` | Convert fixture source records into validated `Signal` objects; implement confidence/staleness gates, bounded weight changes, and event windows with expiry. Tests: low-confidence and stale signals do not change weights; update stays within configured bounds; event boost is absent after expiry. |
| - [ ] **S — neatlogs workflow adapter and cfo.ai input contract** | `feat/s2-s-integrations-adapters` | Add neatlogs spans for assessment, signal extraction, ranking, and update proposal. Define a Pydantic import shape for outputs from an actual cfo.ai scenario; store source workspace/model/scenario reference and assumptions with every assessment. Test: span metadata includes region/cycle/version IDs; malformed finance payload is rejected; missing finance data reports `degraded`. |

**Sprint 2 exit checklist**

- [ ] Candidate region can be assessed, approved, rejected, and persisted.
- [ ] Before/after recommendations are visible for the same region.
- [ ] New source records are idempotent; weight updates are bounded and expire.
- [ ] neatlogs trace contains assessment and recommendation spans.
- [ ] Regional finance outputs have traceable cfo.ai provenance or an explicit degraded status.
- [ ] `uv run pytest` passes; sprint PRs are reviewed and merged.

## Sprint 3 — Autonomous cycle, evaluation, and recovery

**Time:** `2026-10-10 15:30–17:00 IST`  
**Goal:** Run the active-region worker end to end, compare candidate recommendations, and safely reject bad updates.

| Owner / checklist | Branch | Requirements and tests / acceptance |
|---|---|---|
| - [ ] **Z — transactional activation and rollback** | `feat/s3-z-atomic-activation` | Add transaction-safe candidate activation, active-version lookup, and rollback to the last known-good version. Tests: failed activation leaves active pointer unchanged; rollback restores parent; concurrent duplicate cycle cannot activate twice. |
| - [ ] **Ar — autonomous activity and comparison UI** | `feat/s3-ar-cycle-results` | Show cycle status, source evidence, confidence, old/new weights, promoted/demoted products, metrics, and trace/scenario references. Add a button to run the same cycle used by the worker. Tests: renders success/rejection/degraded states; run action refreshes the selected region without changing unrelated region state. |
| - [ ] **Ad — autonomous worker and evaluator** | `feat/s3-ad-worker-evaluation` | Process unseen signals per active region; build a candidate profile; evaluate active and candidate against the same synthetic customer cohort; activate only when policy, quality, and financial checks pass. Tests: active region updates without approval; low-confidence candidate is rejected; poor candidate preserves active version; one region's exception does not stop the next region. |
| - [ ] **S — full runtime integration and evidence** | `feat/s3-s-runtime-evidence` | Trace the complete worker cycle, retries, guardrails, candidate/active metrics, and result. Read the real cfo.ai regional scenario or import its real output with provenance; display the finance decision in-app. Tests/evidence: inspectable neatlogs end-to-end trace; active/candidate metric record; cfo.ai source reference; integration failure is surfaced rather than represented as success. |

**Sprint 3 exit checklist**

- [ ] A single worker run changes recommendations for the target region.
- [ ] A second region remains isolated and unchanged.
- [ ] Bad/low-confidence updates are rejected and active version is preserved.
- [ ] A failed region cycle does not stop processing other regions.
- [ ] neatlogs shows the full cycle; cfo.ai output affects the decision and is traceable.
- [ ] `uv run pytest` passes; sprint PRs are reviewed and merged.

## Sprint 4 — Evidence, polish, and reserved improvement

**Time:** `2026-10-10 17:00–18:30 IST`  
**Goal:** Make the demo repeatable, capture proof for all partners, and reserve time for one small improvement after the core flow passes.

| Owner / checklist | Branch | Requirements and tests / acceptance |
|---|---|---|
| - [ ] **Z — data integrity and recovery pass** | `feat/s4-z-recovery-hardening` | Verify schema constraints, per-region isolation, immutable version history, expiry, and failure recovery against a fresh demo database. Tests: persistence suite and failure-path suite pass from a clean seeded database; no failed update corrupts active state. |
| - [ ] **Ar — demo polish and reset/replay** | `feat/s4-ar-demo-polish` | Add a repeatable reset/replay control, visible simulated-data labels, recommendation explanations, and clear partner evidence links/statuses. Tests: manual full demo can be reset and replayed; success, rejection, and degraded states are legible. |
| - [ ] **Ad — regression checks and one scoped improvement** | `feat/s4-ad-evaluation-improvement` | Run the before/after evaluator and fix the highest-impact discovered issue. If the core demo already passes by 17:45, use 17:45–18:00 for exactly one small improvement: second event-window expiry, stronger source confidence display, or retry/rollback clarity. Tests: baseline and candidate use identical cohort; expected promotion/demotion assertion passes; no regression in rejection/rollback. |
| - [ ] **S — partner evidence and final integration** | `feat/s4-s-final-evidence` | Complete `docs/evidence.md`; verify final neatlogs successful and rejected traces, Entire checkpoints/graph, cfo.ai business and regional scenarios; ensure all PRs and integration statuses are accurately described. Tests/evidence: evidence references open and correspond to the final demo; no placeholder IDs or claims of unverified live integration. |

**Sprint 4 exit checklist**

- [ ] Core scenario can be reset and replayed from a clean state.
- [ ] All required automated tests pass and the manual acceptance checklist in `DEMO_AND_TESTS.md` is complete.
- [ ] One reserved improvement is merged, or explicitly skipped to protect demo stability.
- [ ] Partner evidence is captured and labeled correctly.
- [ ] All task PRs are reviewed, merged, and reflected in the status boxes below.
- [ ] Final demo is rehearsed and stays within the submission video limit.

## Central task status

S updates these boxes after confirming the contributor's GitHub PR, task checks, and review status. The PR URL belongs in `docs/evidence.md` or the PR record, not in a secret-bearing file.

| Sprint | Z | Ar | Ad | S |
|---|---|---|---|---|
| 1 — Contracts/foundation | [ ] | [ ] | [ ] | [ ] |
| 2 — Assessment/recommendations | [ ] | [ ] | [ ] | [ ] |
| 3 — Autonomy/recovery | [ ] | [ ] | [ ] | [ ] |
| 4 — Evidence/polish | [ ] | [ ] | [ ] | [ ] |

## Sprint-wide test gates

Run the focused test module for the changed component before pushing. At every sprint boundary, S runs the full available test suite and launches the dashboard from the merged `main` branch. The expected commands are:

```bash
uv run pytest
uv run python -m compileall src tests app.py worker.py
uv run streamlit run app.py
```

Do not mark a test complete if the implementation is still a stub, skips the relevant assertion, or silently fakes a partner response. If a live integration cannot be tested because credentials or service access are unavailable, mark that integration degraded and keep its acceptance item open.
