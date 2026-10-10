# Atlas Commerce

Atlas Commerce is a prototype for evaluating whether a new region is financially viable and, after a person approves it, adapting that region's product recommendations as regional signals arrive. The demo uses fictional regions, synthetic news/forum/social/event signals, and simulated commerce results; it is not a production commerce system.

## How it works

1. An operator submits a candidate region with market assumptions and optional launch context.
2. The financial assessment compares regional assumptions and, when available, cfo.ai base, growth, and downside scenarios. A local fallback is clearly labeled if cfo.ai is unavailable.
3. A person approves or rejects the candidate. A region never becomes active without approval.
4. For active regions, the worker processes new synthetic signals, normalizes them, checks policy, and proposes a bounded recommendation-profile update.
5. The evaluator compares active and candidate profiles on the same simulated customer cohort. Candidates activate only after policy, quality, and financial checks pass; otherwise the active version is preserved.
6. The dashboard shows before/after products, score explanations, evidence, outcomes, and integration status.

Routine updates to an active region do not need human approval. Operators can pause/resume regions and restore a prior known-good recommendation version.

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

The planned full-demo checks, once the dashboard and worker entry points exist, are:

```bash
uv run python -m compileall src tests app.py worker.py
uv run streamlit run app.py
```

The project is under active implementation. The current Sprint 1 code includes a deterministic recommender and its focused tests; the complete dashboard, lifecycle, worker, persistence, and integrations are still being built. See [`SPRINT_PLAN.md`](SPRINT_PLAN.md) for current task branches, ownership, acceptance checks, and merge order. Other product and engineering details live in [`PRODUCT.md`](PRODUCT.md), [`ARCHITECTURE.md`](ARCHITECTURE.md), [`INTEGRATIONS.md`](INTEGRATIONS.md), [`BUILD_PLAN.md`](BUILD_PLAN.md), and [`DEMO_AND_TESTS.md`](DEMO_AND_TESTS.md).

## Demo acceptance scenarios

1. Assess and human-approve a candidate region.
2. Run a synthetic festival signal and visibly change the target region's top-five recommendations, with explanations and trace evidence.
3. Run a low-confidence/low-margin signal, reject it, preserve the active version, and continue processing other regions.

Also verify duplicate-signal idempotency, event expiry, product/policy exclusions, bounded changes, rollback, same-cohort evaluation, and explicit degraded integration states. Keep the demo reproducible and label all simulated sales and evaluations.

## Team workflow

Follow `SPRINT_PLAN.md` for ownership and task branches. Keep branches focused, open a PR to `main`, and include exact test commands/results, relevant screenshots or integration references, and limitations using `.github/pull_request_template.md`. Do not push directly to shared `main` or force-push shared branches. Task owners report their own PR evidence; S coordinates integration and updates central sprint status only after checking PRs and required checks.
