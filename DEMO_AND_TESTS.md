# Atlas Commerce Agent — Demo and Test Plan

## Demonstration story

Use fictional regions and products so the result is reproducible and safe to share.

The main demo is a UML sequence model:

```mermaid
sequenceDiagram
    actor Operator
    participant UI as Dashboard
    participant CFO as cfo.ai
    participant Worker as Autonomous worker
    participant Ranker as Recommendation service
    participant Logs as neatlogs
    participant Entire as Entire evidence

    Operator->>UI: Submit candidate region
    UI->>CFO: Request base/growth/downside assessment
    CFO-->>UI: Viability and opportunity-cost outputs
    Operator->>UI: Approve region
    UI->>Worker: Activate region
    Worker->>Logs: Trace initial recommendation run
    UI-->>Operator: Show initial top-five products
    Worker->>Worker: Consume synthetic festival signal
    Worker->>Ranker: Propose bounded weight update
    Ranker-->>Worker: Candidate before/after rankings
    Worker->>Logs: Record signal, weights, and evaluation
    alt Candidate passes policy, quality, and finance checks
        Worker->>Ranker: Activate recommendation version
        UI-->>Operator: Show changed products and explanations
    else Candidate fails a check
        Worker->>Ranker: Preserve active version
        UI-->>Operator: Show rejection and rollback status
    end
    Entire-->>Operator: Show development checkpoint evidence
```

### Scenario A — region evaluation and activation

1. Open the dashboard with two active regions and one candidate region.
2. Submit the candidate region with traffic, conversion, margin, campaign cost, and data-availability assumptions.
3. Run the financial assessment.
4. Show cfo.ai base, growth, and downside scenarios.
5. Show the viability score, expected profit, and opportunity cost.
6. Approve the region.
7. Show the region entering `active` with a default recommendation profile.

### Scenario B — autonomous update changes products

1. Display the region's current top-five products.
2. Inject a timestamped synthetic festival signal.
3. Run the autonomous cycle.
4. Show research output, normalized topics, confidence, and evidence.
5. Show the old and candidate weights.
6. Show products promoted and demoted.
7. Show the new top-five products in the storefront preview.
8. Show the neatlogs trace containing the cycle and recommendation update.

### Scenario C — rejected update and recovery

1. Inject a low-confidence or low-margin signal.
2. Run the autonomous cycle.
3. Show that the candidate version fails a finance, quality, or policy check.
4. Keep the active recommendation version unchanged.
5. Show the rejection in the UI and neatlogs.
6. Confirm that the next cycle can continue.

The update outcome is a UML state model:

```mermaid
stateDiagram-v2
    [*] --> ActiveVersion
    ActiveVersion --> CandidateVersion: actionable signal
    CandidateVersion --> Evaluating: candidate stored
    Evaluating --> Activated: policy, quality, and finance pass
    Evaluating --> Rejected: any check fails
    Activated --> ActiveVersion: new version becomes active
    Rejected --> ActiveVersion: preserve last known-good version
    ActiveVersion --> RolledBack: post-activation regression
    RolledBack --> ActiveVersion: restore parent version
```

### Scenario D — Entire development evidence

Show:

- the project `AGENTS.md` and planning documents supplied to the coding agent;
- repository graph/context output where available;
- a checkpoint for the first implementation;
- a later checkpoint tied to a fix or improvement;
- the commit or checkpoint references in `docs/evidence.md`.

## Manual verification checklist

- A new region cannot become active without approval.
- An already active region can process a routine update without approval.
- A duplicate source record is processed only once.
- A stale event does not produce a new update.
- A temporary event boost expires.
- A disallowed product never appears in the final ranking.
- An out-of-stock product never appears in the final ranking.
- A candidate update cannot exceed the configured weight-change limit.
- A failed evaluation leaves the active version unchanged.
- A failed region cycle does not prevent another region from running.
- Recommendation score breakdowns explain before/after rank movement.
- Simulated sales use the same customer cohort for active and candidate comparisons.
- A cfo.ai outage is shown as degraded/fallback, not falsely reported as success.
- Missing neatlogs credentials make integration status explicit.
- All secrets are absent from source, fixtures, logs, and evidence.

## Focused automated tests

Use `pytest` tests with names such as:

```text
test_candidate_region_requires_approval
test_active_region_runs_autonomous_cycle
test_duplicate_signal_is_idempotent
test_event_boost_expires
test_recommendation_ranking_changes_after_signal
test_policy_excluded_product_is_not_recommended
test_out_of_stock_product_is_not_recommended
test_weight_update_is_bounded
test_failed_evaluation_preserves_active_version
test_region_failure_does_not_stop_other_regions
test_finance_policy_rejects_low_margin_campaign
test_cfo_unavailable_is_reported_as_degraded
test_neatlogs_trace_context_contains_region_and_cycle
```

## Required before/after evidence

For the primary update, record:

- region ID;
- active version ID;
- candidate version ID;
- input signal IDs;
- old weights and new weights;
- old top-five products;
- new top-five products;
- promoted/demoted products;
- confidence and evidence excerpts;
- active/candidate conversion and contribution-profit metrics;
- neatlogs trace/evaluation reference;
- cfo.ai scenario/reference when finance influenced the decision.

## Demo timing

Target a three-minute recording:

- 0:00–0:25 — problem and candidate region;
- 0:25–0:55 — cfo.ai financial assessment and human approval;
- 0:55–1:45 — autonomous signal cycle and changing products;
- 1:45–2:15 — neatlogs trace and before/after metrics;
- 2:15–2:40 — rejected update and rollback;
- 2:40–3:00 — Entire checkpoint and explanation of the three integrations.

## Validation commands

Once the project is implemented, run commands similar to:

```bash
uv sync
uv run pytest
uv run python -m compileall src tests app.py worker.py
uv run streamlit run app.py
```

Record the exact commands and results in the project README before submission.
