# Integration Evidence

## neatlogs

- Project: Atlas Commerce
- Authenticated Doctor probe: `60df973d2a67ac1df473c6237eb999f5`
- Result: Doctor v2 authenticated probe passed; exact four-span diagnostic trace was visible and finalized.
- Scope: SDK connectivity diagnostic only, not an Atlas Commerce workflow trace.

## Entire

- Repository: Atlas Commerce
- Checkpoint commit: `e917a7005874888c542a6822f6fe6ea4d3c519dc`
- Checkpoint reference: `01M4JHH3NAKDHVX6M0465MBD7Q`
- Repository graph: `entire-graph` was unavailable in the development environment.

## cfo.ai

- Model: `Atlas Commerce — Harbor Bay` (`#5ed`)
- Candidate region: Harbor Bay (`harbor`)
- Scenarios: Base (`#52f`), Growth (`#363`), Downside (`#035`)
- Export: [`docs/evidence/cfo-ai-harbor-bay.xlsx`](evidence/cfo-ai-harbor-bay.xlsx)
- Period/currency: monthly run-rate, USD, October 2026.
- Base inputs from the SQLite seed: 50,000 visits/month, 2.5% conversion, $85 AOV, $12 fulfillment/order, $15,000 campaign/month, and $3,000 inference/month.
- Product cost: provisional 50.3234% catalog cost-to-sales ratio from an equal-unit mix of eight products; actual sales mix is unknown.
- Synthetic assumptions: $500/month infrastructure and $30,000 opening cash, applied to all scenarios.
- Growth and Downside assumptions are simulated sensitivities, not seed data.
- Base outputs: $106,250 revenue, $37,781.37 gross profit, 35.56% gross margin, $19,281.37 contribution after listed costs, and 151.88% campaign ROI proxy.
- Growth outputs: $175,500 revenue, $65,732.41 gross profit, 37.45% gross margin, $41,732.41 contribution after listed costs, and 228.66% campaign ROI proxy.
- Downside outputs: $56,160 revenue, $17,818.37 gross profit, 31.73% gross margin, -$4,181.63 contribution after listed costs, and -1.01% campaign ROI proxy.
- Campaign ROI is a labeled non-causal proxy: `(gross profit - campaign spend) / campaign spend`; it excludes inference and infrastructure costs.
- Downside runway: 6.17 months remaining after the October forecast month, calculated from the $25,818.37 ending cash balance divided by the $4,181.63 monthly loss. Base and Growth are cash-flow positive, so the model does not report burn-limited runway for them.
- Seed `projected_profit` of $125,000 was flagged as inconsistent with the $106,250 Base revenue and was not used as a calculated output.
- Provenance: cfo.ai model and scenario references and outputs were confirmed by the workspace user; the downloaded workbook is retained above. cfo.ai MCP access from the application is not verified by this evidence.
