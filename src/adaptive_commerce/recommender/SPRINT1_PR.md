## Sprint task

- Sprint number: 1
- Task owner: Ad
- Task title: deterministic ranker
- Task branch: `feat/s1-ad-ranking-core`
- Task: [Sprint 1 plan](https://github.com/AdiGoCrazy/atlas-commerce/blob/main/SPRINT_PLAN.md#sprint-1--contracts-and-runnable-foundation)

## Change

Add deterministic product eligibility and scoring with explainable breakdowns.
The synthetic festival fixture changes the top five from boots, bottle, pack,
poles, tent to lights, charger, pouch, boots, bottle. Excluded and out-of-stock
products cannot appear. Ties resolve by product ID; events respect region and
expiry. Include local validated ranking projections, a minimal uv dependency
baseline, the requested directory layout, and a neatlogs ranking span.

## Contributor checklist

- [ ] I pushed this task branch to GitHub and linked this PR to the assigned sprint task.
- [x] I implemented the task acceptance criteria; no placeholder path is presented as a working integration.
- [x] I ran the required focused tests and they passed.
- [x] I included test commands and results below.
- [x] I included relevant fixture results and documented integration limitations.
- [x] I documented any blocked integration and stated the evidence needed to unblock it.
- [x] I checked that no secret, local environment file, or private data is included.

The upload checkbox stays open until a PR exists and its required checks pass.

## Tests and evidence

```text
uv sync --python 3.12: passed (Python 3.12.14)
uv run pytest: 22 passed
uv run python -m compileall src tests: passed
uv run python -m neatlogs doctor --local --json: pass, Doctor v2 / schema 2
git diff --check: passed
neatlogs: local diagnostic only; live trace readback remains S's integration work
Entire: CLI and entire-graph unavailable; no checkpoint claimed
cfo.ai: not called by this ranker; scenario evidence belongs to S
```

Shared domain models were absent on base commit f12e699. Contracts are isolated
inside Ad's recommender directory; README documents mapping for Z/Ar/S.
Signal extraction and bounded profile proposals remain Sprint 2 work.
The dashboard/manual worker demo needs the other owners' implementations.

## Reviewer checklist (S or assigned reviewer)

- [ ] Required checks pass on GitHub.
- [ ] Acceptance criteria and test evidence match the sprint task.
- [ ] Integration claims are backed by real evidence or clearly marked degraded.
- [ ] Reviewed and merged to main.
