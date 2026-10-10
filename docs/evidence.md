# Sprint 1 recovery evidence

Date: October 10, 2026. The user authorized Ad's coding agent to complete the
remaining Z/Ar/S foundation work. Review baseline: main `95d1142`.
This manifest records observed results, not partner completion claims.

## Repository and automated verification

- Baseline main: 56 tests passed in an isolated checkout.
- Recovery `uv run --locked pytest -q`: **74 passed** on Python 3.12.14.
- `uv run --locked python -m compileall -q src tests app.py`: passed.
- `git diff --check`: passed.
- Streamlit launched on `127.0.0.1:8502`; `/_stcore/health` returned `ok`.
  AppTest executed the actual shell and submitted valid/invalid candidate forms
  without exceptions. No interactive browser screenshot was captured.
- Shared ranker compatibility tests: Cedar changes during its seeded event,
  Aspen stays unchanged, expiry restores baseline, and exclusions override boosts.
- Recovery fixes: Decimal values serialize losslessly; failed seed validation
  rolls back seed writes while preserving the caller's enclosing transaction.
- Dashboard: Streamlit shell, seeded region picker, persistent candidate form,
  simulated finance panel, active-profile storefront, and explicit degraded status.
- Finance: deterministic base/growth/downside model; validated assumptions and
  operator-export import boundary. The fixture's invented provider reference was
  removed and its profit corrected to match the monthly assumptions.
- GitHub CI now runs pytest and compilation on Python 3.11 and 3.12. A workflow
  file alone is not proof of green remote checks; record results after upload.

## neatlogs — diagnostic connection and Atlas workflow export verified

- Official installed SDK: 1.4.26 at the local diagnostic run.
- `uv run python -m neatlogs doctor --probe --json`: PASS on October 10, 2026,
  SDK 1.4.26. Authenticated API readback confirmed the diagnostic trace was
  visible, finalized, and had a valid hierarchy and attributes.
- A synthetic Cedar regional cycle completed with `activated`; the Neatlogs
  SDK flush completed and the app trace ID was
  `4739d856bad6e3c249325cf178ee5423`. This verifies app-side trace export.
- Exact app-trace readback via `neatlogs trace get` remains unverified because
  the CLI also requires `NEATLOGS_PROJECT_ID` and `NEATLOGS_TOKEN`. Neither is
  configured in this checkout. Set them from the Neatlogs project settings to
  inspect the Atlas trace directly; do not commit them.
- Actual candidate/storefront services were also executed with a private
  in-memory OpenTelemetry exporter through neatlogs. Captured span names:
  `submit_candidate`, `simulate_financial_scenarios`, `storefront_preview`,
  `rank_products`. That earlier local-only capture is retained as historical
  evidence; the live synthetic cycle export is recorded above.
- Process initialization is cached; missing `NEATLOGS_API_KEY` disables export.
  Candidate assessment and storefront services have WORKFLOW spans, finance
  and ranking have CHAIN spans, and ranker logs include region/version/cycle.
- `.env` startup loading was added; `NEATLOGS_API_KEY` configures SDK export.
  Shell-provided values take precedence. Tests explicitly set an empty key so
  they remain local and do not export test traces.
- Worker traces now include content-free signal fetch/normalization, policy
  gate counts, paired ranking, same-cohort evaluation outcome, version outcome,
  retries, and regional cycle context. Region lifecycle actions also emit spans.
- The Sprint 2 adapter adds content-free spans for candidate finance assessment,
  signal extraction, recommendation ranking, and update proposals. Signal spans
  include region IDs; proposal spans include region, cycle, and candidate-version
  IDs. The worker lifecycle coverage and live export/readback checks are updated
  above.
- Reference: https://docs.neatlogs.com/sdk/python

## Entire — repository search verified

- CLI installed and enabled for Codex; Git hooks and approved Codex hooks detected.
- Authenticated `entire search 'ranking domain contracts' --json --compact --limit 3`
  succeeded and returned repository commits `eb5b3b5`, `beb8aa2`, and `95d1142`.
  Used those results with AGENTS.md and all six planning documents for this pass.
- Initial checkpoint list was empty. `entire doctor` subsequently found a shadow
  checkpoint on the tracked Codex session and advised condensation; this is not
  yet a published checkpoint reference.
- `entire-graph` executable is unavailable. Search is verified; graph output is
  still missing. Owner S must provide graph evidence and confirm published
  implementation/improvement checkpoints before partner acceptance.
- Reference: https://docs.entire.io/

## cfo.ai — blocked, explicitly degraded

- No cfo.ai tool is exposed in this session. Plugin discovery returned no cfo.ai
  integration; `codex mcp list --json` returned an empty server list.
- No authenticated workspace, model, or scenario operation has occurred.
- `FinanceInputs`, `ScenarioResult`, and `CfoScenarioImport` specify a validated
  boundary for actual provider output and preserve workspace/model/scenario/URL.
  Synthetic import tests prove validation only, not provider authenticity.
- The local model is reproducible and labeled simulated. Importing an export
  does not mark the MCP connection successful or authorize activation.
- The Sprint 2 import boundary accepts the supplied cfo.ai export field names,
  validates required assumptions and calculated outputs, and stores the full
  operator-supplied payload plus model/scenario reference alongside the local
  assessment. A database reload test verifies persistence. This code-level
  acceptance is not a claim that the pasted export was imported at runtime or
  that the MCP connection was authenticated; status remains degraded/unverified.
- Owner S: connect `https://api.cfo.ai/mcp`, authenticate in the browser, verify
  the intended workspace, create/read the regional model and three scenarios,
  and capture actual output references. Use the local model's explicit monthly
  assumptions and formulas as the model specification.
- Setup reference: https://docs.cfo.ai/integrations/mcp-server

## Sprint 1 recovery gate at initial review

Local foundation completion does not establish full Sprint 1 acceptance.
Live neatlogs and cfo.ai evidence, Entire graph/checkpoint evidence, remote CI,
and independent PR review remain explicit gates. Development against the tested
contracts can proceed, but the team must resolve or explicitly carry these
named blockers and announce the merged baseline before declaring the transition.
The worker/manual autonomous demo is later-sprint scope and is not claimed here.

## Imported partner evidence from integration baseline

The following evidence was added by the integration baseline branch and retained
during merge. It supplements the local recovery evidence above; it does not
claim that the application itself has a live cfo.ai MCP client.

### neatlogs

- Authenticated Doctor probe: `60df973d2a67ac1df473c6237eb999f5`.
- Result: Doctor v2 authenticated probe passed; this is SDK connectivity evidence,
  not an Atlas Commerce workflow trace.

### Entire

- Checkpoint commit: `e917a7005874888c542a6822f6fe6ea4d3c519dc`.
- Checkpoint reference: `01M4JHH3NAKDHVX6M0465MBD7Q`.
- Repository graph: `entire-graph` was unavailable.

### cfo.ai

- Model: `Atlas Commerce — Harbor Bay` (`#5ed`); scenarios Base (`#52f`),
  Growth (`#363`), and Downside (`#035`).
- Export: [Harbor Bay workbook](evidence/cfo-ai-harbor-bay.xlsx).
- Period/currency: monthly run-rate, USD, October 2026.
- Reported base outputs: $106,250 revenue, $37,781.37 gross profit, 35.56%
  gross margin, and $19,281.37 contribution after listed costs.
- Reported growth/downside outputs and assumptions are retained in the workbook.
  Product cost and infrastructure assumptions are explicitly provisional or
  synthetic; campaign ROI is a labeled proxy rather than causal attribution.
- This provider evidence was supplied by the integration baseline branch. The
  cfo.ai MCP connection from the application remains unverified here; the local
  adapter continues to report degraded status unless a validated export is
  supplied.

## Consolidated sprint implementation — October 10, 2026 (initial pass)

- Added candidate approval/rejection, active-region pause/resume/archive, and a
  recorded operator action history. Candidate activation remains gated by the
  latest local finance recommendation and explicit operator approval.
- Added durable regional update cycles, signal-consumption idempotency, bounded
  retries with per-region isolation, same-cohort simulated relevance/profit
  evaluation, transactional activation, and parent-version rollback.
- Added exact-signal cycle replay, repeatable fixture reset, a synthetic signal
  input, and a polling worker (`worker.py --once` or `--interval`).
- Replaced dashboard placeholders with overview, region operations, finance,
  recommendation comparison, cycle history, replay, and reset screens.
- Verification on Python 3.12.14: `python -m pytest -q` — **147 passed**;
  `python -m compileall -q src tests app.py worker.py` — passed;
  `git diff --check` — passed.
- Worker smoke run against a fresh temporary SQLite database processed both
  seeded active regions: Cedar activated a version and Aspen recorded its
  unchanged/no-action result. The live Streamlit health endpoint returned
  `ok` with HTTP 200 on port 8502; the app process was then stopped.
- Test dependencies were installed in `/private/tmp/atlas-commerce-venv` because
  `uv` is absent from PATH in this environment. The repository lockfile was not
  changed by that install.
- Historical neatlogs check from this initial pass: local SDK diagnostic passed,
  but no API key was loaded by the app. That state was superseded by the `.env`
  loader and authenticated checks documented in the current Neatlogs section.
- Entire: `entire status` reports enabled; semantic repository search failed
  because this environment has no Entire login. `entire checkpoint list` found
  no local checkpoints and could not reach the remote. `entire-graph` is not
  installed. Existing imported checkpoint evidence above is retained, but no
  new checkpoint or graph is claimed.
- cfo.ai: no MCP tool is exposed in this session. The UI and finance adapter
  continue to show local simulation as degraded; no live scenario operation is
  claimed. Existing Harbor Bay workbook evidence remains documented above.
- This working tree has not been uploaded as a task PR or merged, so the central
  sprint boxes remain unchecked pending branch, PR, independent review, remote
  checks, and live partner evidence where required.

## Cycle feedback and navigation correction — October 10, 2026

- Inspected the local demo database after the reported repeated rejections. Cedar
  had a prior `activated` cycle and a persisted active version change, followed
  by later `rejected/no_actionable_signals` cycles with no new signal IDs. This
  confirms the store was persisting updates; subsequent runs had no unconsumed
  evidence because signals are intentionally processed once.
- Dashboard now chooses an active region with pending signals by default, shows
  the pending count, disables a one-region run when there is no new evidence,
  preserves the chosen dashboard section after actions, and reports no-op and
  evaluation rejection outcomes as warnings instead of green success banners.
- AppTest covers successful cycle activation, persisted signal consumption, and
  retaining the Region operations section. Python 3.12.14:
  `/private/tmp/atlas-commerce-venv/bin/python -m pytest -q` — **148 passed**;
  compileall and `git diff --check` passed.
- Synthetic UI evidence now uses a stable content fingerprint as its source ID,
  so repeated identical submissions do not accumulate into a saturated update.
  Confidence remains a validated signal field and produces a deterministic
  bounded weight change. Aspen calibration confirms `0.65` fails the confidence
  gate, `0.70` activates a festival ranking change, and `0.80` proposes a larger
  change that remains blocked by the profit guardrail.
- After this calibration coverage was added, Python 3.12.14:
  `/private/tmp/atlas-commerce-venv/bin/python -m pytest -q` — **152 passed**;
  compileall and `git diff --check` passed.
