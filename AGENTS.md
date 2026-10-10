# Atlas Commerce Agent — Repository Instructions

## Purpose

This repository contains Atlas Commerce, a prototype that evaluates candidate regions financially, registers approved regions, and autonomously updates regional product recommendations from simulated news, forum, social, and event signals.

Read these documents before making implementation changes:

1. `PRODUCT.md` — product behavior, scope, terminology, and acceptance criteria.
2. `ARCHITECTURE.md` — system boundaries, components, data flow, state, and extension points.
3. `INTEGRATIONS.md` — required neatlogs, Entire, and cfo.ai usage and evidence.
4. `BUILD_PLAN.md` — implementation order, time budget, and deliberately deferred work.
5. `SPRINT_PLAN.md` — timestamped sprint tasks, teammate ownership, branches, tests, and checklists.
6. `DEMO_AND_TESTS.md` — reproducible demo scenarios, tests, and submission evidence.

## Team sprint and GitHub rules

- Follow `SPRINT_PLAN.md` for sprint timestamps, assigned tasks, task branches, acceptance checks, and merge order. If the sprint clock moves, keep the sequence and update the timestamp in the plan rather than silently reassigning work.
- Team ownership is fixed unless the team explicitly changes it: Z owns domain/persistence; Ar owns presentation/demo; Ad owns orchestration/recommendation/evaluation; S owns integrations/evidence and coordinates merges.
- Work only in the assigned branch and owned files. Read the planned service contracts before using a teammate's in-progress work. Add a small interface stub in your own branch if necessary; tell S about contract mismatches before changing another owner's files.
- Use the branch name listed for your task. Start from the latest shared `main` at each sprint boundary. Push a focused branch and open a GitHub PR to `main` before the sprint ends.
- In the PR, include the sprint/task, changed behavior, exact test commands and results, screenshots or trace/scenario references where relevant, and any known limitation. Use `.github/pull_request_template.md`.
- After pushing a PR, the task owner's coding agent must tick its PR-upload checklist item only after the branch is on GitHub, the PR URL is recorded, and the required tests/checks have passed. A push by itself does not qualify. Do not mark the reviewer/merge item; S or the assigned reviewer owns that item.
- S maintains the central status table in `SPRINT_PLAN.md`. S checks a task's box after independently confirming the PR URL and green required checks. Mark the merge status only after review and merge to `main`. Do not mark unfinished, stubbed, or skipped tests as passing.
- At sprint boundaries, S merges passing PRs in dependency order and announces the exact `main` commit for the next sprint. Resolve integration failures with the task owner; do not hide them by weakening tests.
- Never commit secrets, local environment files, or private source data. Never force-push a shared branch or push directly to `main`.

## Required technology stack

- Python 3.11 or 3.12.
- `uv` for project dependencies and the lockfile.
- Streamlit for the demo dashboard.
- SQLite for durable prototype state.
- Pydantic for validated domain models and integration payloads.
- The official OpenAI-compatible SDK for LLM calls, behind a small provider interface.
- The official `neatlogs` Python SDK for runtime traces and evaluations.
- Entire CLI and `entire-graph` for repository context and coding-session checkpoints.
- cfo.ai MCP for the financial model and regional viability scenarios, subject to authenticated connection verification.
- `pytest` for focused behavior tests.

Keep the application as a modular monolith. Do not introduce microservices, a vector database, a queue, a full ML training pipeline, or a large agent framework during the hackathon unless a measured requirement makes one necessary.

## Implementation rules

- Use four-space indentation, type annotations, docstrings on public functions/classes, and `snake_case` names.
- Keep business logic independent of Streamlit. UI code belongs in the presentation layer and must call application services.
- Use Pydantic models at file, database, LLM, and external-integration boundaries.
- Keep the recommender deterministic and explainable. LLMs may extract signals, translate text, and explain decisions; they must not directly determine final ranking scores.
- Treat autonomous updates as versioned configuration changes. Never let a runtime agent rewrite arbitrary source code.
- Apply changes transactionally. Preserve the active version until a candidate passes validation.
- Make autonomous work idempotent using source IDs, region IDs, event IDs, and update IDs.
- Add bounded retries and explicit timeouts around network calls. A failed region cycle must not stop other regions.
- Use structured logging and neatlogs spans. Do not rely on `print()` for important state transitions.
- Keep secrets in environment variables or a local ignored `.env` file. Never commit API keys, tokens, or personal data.
- Use synthetic fixtures for all demo content. Label simulated sales and evaluation results as simulated.
- Use explicit product and regional policies for exclusions. Do not infer protected or sensitive attributes about customers or regions.

## Integration requirements

Every implementation must preserve these three product roles:

- **neatlogs:** instrument the complete orchestrator run, research agents, guardrails, ranking, evaluations, failures, retries, and autonomous updates. Use the neatlogs API or dashboard to retrieve evidence when available.
- **Entire:** use repository context, `AGENTS.md`, checkpoints, and `entire-graph` during development. Record evidence that the coding agent used repository context and that an improvement was checkpointed.
- **cfo.ai:** create and use a financial model covering regional viability, pricing, costs, revenue, margin, ROI, and runway. Prefer the cfo.ai MCP connection for model/scenario operations. If authentication or an operation is unavailable, isolate the adapter and document the exact limitation; never silently pretend an external call succeeded.

## Change workflow

Before coding:

1. Read the relevant planning documents.
2. Identify the domain objects and state transition affected.
3. Use Entire repository context/search where it is available.

While coding:

1. Implement the smallest coherent change.
2. Add or update focused tests for behavior and failure paths.
3. Add neatlogs instrumentation for new agent or workflow steps.
4. Preserve an extension seam for later real data sources, ranking models, and schedulers.

After coding:

1. Run the documented tests and a manual demo cycle.
2. Check that recommendation before/after results visibly change in the intended fixture.
3. Check that an unchanged or unsafe signal does not produce an update.
4. Capture integration evidence for neatlogs, Entire, and cfo.ai.
5. Update the relevant documentation when behavior or scope changes.

## Definition of done

A feature is complete only when it has validated inputs, observable execution, durable state, a failure path, focused tests, and documentation. A feature that only works when an external integration succeeds must also provide a clear degraded mode and an explicit status in the UI.
