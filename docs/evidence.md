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

## neatlogs — local capture verified, live evidence blocked

- Official installed SDK: 1.4.26 at the local diagnostic run.
- `uv run python -m neatlogs doctor --local --json`: PASS, Doctor v2, schema 2,
  Python runtime, four normalized spans, successful flush.
- That diagnostic is local-only; its generated trace identifier is not a
  dashboard trace reference.
- Actual candidate/storefront services were also executed with a private
  in-memory OpenTelemetry exporter through neatlogs. Captured span names:
  `submit_candidate`, `simulate_financial_scenarios`, `storefront_preview`,
  `rank_products`. The submitted region remained `candidate`. No network export
  or live trace ID is claimed for this application capture.
- Process initialization is cached; missing `NEATLOGS_API_KEY` disables export.
  Candidate assessment and storefront services have WORKFLOW spans, finance
  and ranking have CHAIN spans, and ranker logs include region/version/cycle.
- Live trace readback is blocked: no `NEATLOGS_API_KEY` in this environment.
  Owner S: configure the project key, run `--probe --json`, retrieve a real
  candidate/storefront trace, and record the evidence here.
- Full worker/guardrail/evaluation/retry spans remain Sprint 2/3 work.
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
- Owner S: connect `https://api.cfo.ai/mcp`, authenticate in the browser, verify
  the intended workspace, create/read the regional model and three scenarios,
  and capture actual output references. Use the local model's explicit monthly
  assumptions and formulas as the model specification.
- Setup reference: https://docs.cfo.ai/integrations/mcp-server

## Sprint gate

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
