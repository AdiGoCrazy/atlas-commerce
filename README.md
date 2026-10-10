# Atlas Commerce

Atlas Commerce is a prototype for assessing and approving candidate regions, ranking products, and proposing bounded profile changes from synthetic signals. The demo uses fictional regions and simulated commerce results; it is not a production commerce system. Autonomous execution, evaluation, and rollback are not yet wired into the dashboard.

## How it works

1. An operator submits a candidate region; the app stores it and a simulated finance assessment in SQLite.
2. The dashboard displays local base/growth/downside results and may display a validated, operator-supplied cfo.ai export. No live cfo.ai MCP read is implemented.
3. A human can approve or reject a saved candidate. Approval creates an active region with a neutral initial recommendation profile.
4. The dashboard previews rankings for active regions and compares saved versions for the same region at one evaluation time. It does not run the autonomous worker.
5. Application services can normalize fixture signals and propose bounded profile changes, but autonomous evaluation/activation/rollback are not connected as an end-to-end workflow.

The planned product includes autonomous updates and rollback. Those controls are not present in the current demo.

## Architecture

The planned implementation is a Python modular monolith: Streamlit presentation calls application services; domain, ranking, and integration logic stay outside the UI. SQLite stores durable state and immutable recommendation versions. External services are accessed through small adapters and must expose clear success, degraded, or failed status.

```text
Streamlit dashboard → application services → region lifecycle / finance
                   → autonomous worker → signals → policy checks
                                         → candidate ranking → evaluation
                                         → activate or reject
                   → recommendation service → storefront preview
                   → SQLite state/version history

neatlogs: runtime traces and evaluations
Entire: development context and checkpoints
cfo.ai: financial model and regional scenarios
```

The main domain concepts are `Region`, `RegionProfile`, `Product`, `Signal`, `EventWindow`, `FinanceAssessment`, `RecommendationVersion`, `RecommendationItem`, `UpdateCycle`, and `EvidenceReference`. SQLite is the prototype source of truth; activation and rollback must be transactional.

## Recommendation and safety rules

The initial ranker is deterministic and explainable:

```text
score = base popularity + regional interest match + temporary event boost
        + margin bonus - policy penalty
```

LLMs may extract or translate signals and explain decisions, but they do not choose final ranking scores. Weight changes are bounded, versioned, and reversible. Work is idempotent by source/region/event/cycle identifiers; stale or low-confidence signals do not trigger updates, and temporary event boosts expire.

Explicit product, inventory, regional, and campaign policies exclude unsafe or unavailable products. Do not infer sensitive attributes. A failed region cycle must not stop other regions. Network calls need explicit timeouts and bounded retries. Preserve the active version unless a candidate passes validation. Use synthetic fixtures and label simulated results. Keep secrets and private data out of source, fixtures, logs, and evidence.

## Integrations

- **neatlogs** is runtime observability: trace the full assessment/update workflow, research and tool calls, guardrails, ranking, evaluation, activation, failures, and retries. Include region, cycle, version, and result context. Tests can use local no-network mode; the demo should produce real inspectable traces when credentials are available.
- **Entire** is development evidence, not runtime recommendation storage. Use repository context/`entire-graph` and capture checkpoints for meaningful code changes. Runtime profiles remain versioned application data in SQLite.
- **cfo.ai** provides financial models and regional scenarios covering traffic, conversion, order value, product/fulfillment and infrastructure costs, campaign spend, revenue, gross profit/margin, runway, opportunity cost, and ROI. Preserve model/scenario provenance. Never present a fallback calculation as a cfo.ai result.

If an integration is unavailable, document the exact limitation and show degraded status rather than claiming it succeeded. Store non-secret references and evidence in `docs/evidence.md` when created.

## Development

Requirements: Python 3.11 or 3.12 and `uv`. Dependencies and pytest configuration are in `pyproject.toml`; `uv.lock` is checked in.

```bash
uv sync
uv run pytest
```

Available demo checks are:

```bash
uv run python -m compileall src tests app.py
uv run streamlit run app.py
```

The current app includes validated domain models, SQLite seeding and CRUD,
lossless Decimal storage, deterministic ranking, candidate assessment, fixture
signal normalization, bounded profile proposals, human candidate approval/rejection,
and same-region comparison of saved recommendation versions. The Streamlit shell
does not run autonomous worker orchestration or rollback.

Run the shell with `uv run streamlit run app.py`. Set `ATLAS_DB_PATH` to use a
different SQLite file. Tests use temporary databases and do not need credentials.
Set `NEATLOGS_API_KEY` in the process environment to configure trace export;
without it, the UI explicitly reports local-only tracing. A configured key does
not prove live delivery: verify separately with
`uv run python -m neatlogs doctor --probe --json` and record the returned evidence.

cfo.ai is currently degraded, with deterministic local simulation. The model
includes revenue, product/fulfillment costs, campaign/inference/infrastructure
costs, margin, campaign ROI, cash runway, and opportunity cost. Base/growth/downside
vary traffic by 1 / 1.25 / 0.75. Set `CFO_SCENARIO_PATH` to an operator-supplied
JSON export matching `CfoScenarioImport` to inspect provider outputs/provenance;
imported data is explicitly unverified and does not replace the local decision.
No live cfo.ai success is claimed. The UI shows all financial assumptions.

See [`docs/evidence.md`](docs/evidence.md) for actual checks and integration
blockers and [`SPRINT_PLAN.md`](SPRINT_PLAN.md) for the recovery ownership
exception, task branches, and merge gates. Product details remain in
[`PRODUCT.md`](PRODUCT.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
[`INTEGRATIONS.md`](INTEGRATIONS.md), [`BUILD_PLAN.md`](BUILD_PLAN.md), and
[`DEMO_AND_TESTS.md`](DEMO_AND_TESTS.md).

## Demo acceptance scenarios

1. Assess and human-approve a candidate region.
2. Run a synthetic festival signal and visibly change the target region's top-five recommendations, with explanations and trace evidence.
3. Run a low-confidence/low-margin signal, reject it, preserve the active version, and continue processing other regions.

Also verify duplicate-signal idempotency, event expiry, product/policy exclusions, bounded changes, rollback, same-cohort evaluation, and explicit degraded integration states. Keep the demo reproducible and label all simulated sales and evaluations.

## Team workflow

Follow `SPRINT_PLAN.md` for ownership and task branches. Keep branches focused, open a PR to `main`, and include exact test commands/results, relevant screenshots or integration references, and limitations using `.github/pull_request_template.md`. Do not push directly to shared `main` or force-push shared branches. Task owners report their own PR evidence; S coordinates integration and updates central sprint status only after checking PRs and required checks.
