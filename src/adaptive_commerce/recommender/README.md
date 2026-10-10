# Sprint 1 — Ad: deterministic ranker

`rank_products(products, profile, now=..., policy=..., events=..., limit=5,
cycle_id=...)` returns validated `RankedProduct` records with product ID, rank,
score, and an additive breakdown. It does not mutate inputs or write state.

## Contract for Z, Ar, and S

`contracts.py` contains local ranking projections because no shared domain
implementation exists on the Sprint 1 base commit `f12e699`. These do not replace
Z's models. Map domain IDs to strings and validate catalog fields into
`RankingProduct`; stock and eligibility must be supplied explicitly. Products
use `in_stock` from the architecture diagram. `RankingProfile` requires
`region_id`, `active_version_id`, and `weights`. Domain models with matching
field names and types can use `model_validate(obj, from_attributes=True)`.

The application supplies the selected region's explicit `RankingPolicy`, event
windows, and timezone-aware `now`. Results are serializable with
`model_dump(mode="json")`. Z's repository/application service will persist them
with the version context; this ranker does not activate recommendation versions.

Scoring is base popularity plus the sum of unique tag weights plus active event
boosts plus `margin_weight * (price - cost) / price`, minus explicit product
penalties. The default margin weight is 1. Missing tags contribute zero, losing
products receive a negative margin term, and all scores use Decimal precision
28. Equal scores sort by ascending product ID. Policy exclusions and stock
checks remove products before scoring. Events apply only to their region and
within `[starts_at, expires_at)`. Duplicate product/event IDs are rejected.

S initializes the official neatlogs SDK once at application startup. Ranking
uses a `CHAIN` span; inputs include regional/version context and `cycle_id`.
Structured completion logs include those IDs and result counts. The SDK is
uninitialized during unit tests, so those tests make no trace-export requests.
See the [official SDK documentation](https://docs.neatlogs.com/sdk/python).

## Verification

From the repository root:

```sh
uv sync --python 3.12
uv run pytest
uv run python -m compileall src tests
uv run python -m neatlogs doctor --local --json
```

Initial verification: 22 tests passed on Python 3.12.14; compilation passed;
neatlogs 1.4.26 Doctor v2 local capture passed. This is local SDK validation,
not evidence of a live dashboard trace.

The synthetic festival acceptance fixture produces these exact top-five lists:

- Before: boots, bottle, pack, poles, tent.
- After: lights, charger, pouch, boots, bottle.

The fixture supplies an already proposed profile (festival/electronics weights
increase from 0 to 4, outdoor remains 3). Signal normalization, confidence
gates, and bounded proposals belong to Sprint 2. No sales metrics are claimed.
All catalog records and region IDs in the tests are synthetic.

## Integration limits

Entire CLI and `entire-graph` are unavailable in this environment, so no Entire
checkpoint is claimed. Repository instructions and planning documents were read
directly. S owns setup and live partner evidence. cfo.ai and dashboard wiring
are not part of Ad's Sprint 1 ranker. The full dashboard/manual worker demo
requires the other owners' implementations and later sprint work.

The root `pyproject.toml` and `uv.lock` provide the minimal runnable dependency
baseline for this first code branch. Other owners should extend it when their
components merge. Empty directory placeholders reserve the layout in BUILD_PLAN.
