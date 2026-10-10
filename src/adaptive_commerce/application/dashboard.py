"""Application services for candidates, finance reports, and storefront views."""

import json
import logging
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import neatlogs
from pydantic import BaseModel, ConfigDict, Field

from adaptive_commerce.domain import Product, Region, RegionProfile, RegionState
from adaptive_commerce.integrations.finance import CfoScenarioImport, FinanceInputs, FinanceReport, assess_finance
from adaptive_commerce.integrations.observability import trace_step
from adaptive_commerce.persistence import Repository, connect, initialize, load_fixtures
from adaptive_commerce.recommender import (
    RankingEvent, RankingPolicy, RankingProduct, RankingProfile, RankedProduct, rank_products,
)

logger = logging.getLogger(__name__)
FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"


class CandidateRequest(BaseModel):
    """Validated form input; a financial recommendation never activates a region."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    assumptions: FinanceInputs


class CandidateResult(BaseModel):
    """Durable candidate identity and its explicitly simulated assessment."""

    region: Region
    finance: FinanceReport


def bootstrap_database(path: Path) -> None:
    """Initialize and seed a database without overwriting existing rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as conn:
        initialize(conn)
        load_fixtures(conn, FIXTURES)


def list_regions(path: Path) -> list[Region]:
    """Return validated regions in deterministic order."""
    with closing(connect(path)) as conn:
        result = []
        for row in conn.execute("SELECT * FROM regions ORDER BY name"):
            payload = dict(row)
            payload["available_source_types"] = json.loads(payload["available_source_types"])
            result.append(Region.model_validate(payload))
        return result


@neatlogs.span(kind="WORKFLOW", name="submit_candidate")
def submit_candidate(path: Path, request: CandidateRequest) -> CandidateResult:
    """Atomically save a candidate and fallback assessment; never activate it."""
    now = datetime.now(timezone.utc)
    region = Region(id=str(uuid4()), name=request.name, state=RegionState.candidate,
                    locale="en_US", created_at=now)
    with trace_step("CHAIN", "finance_assessment", region_id=region.id):
        report = assess_finance(request.assumptions)
    with closing(connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO regions (id, name, state, locale, created_at) VALUES (?, ?, ?, ?, ?)",
            (region.id, region.name, region.state.value, region.locale, now.isoformat()),
        )
        conn.execute(
            """INSERT INTO finance_assessments
            (id, region_id, viability_score, projected_profit, decision, assumptions, cfo_reference, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), region.id, "1" if report.recommendation == "approve" else "0",
             str(report.scenarios[0].profit), report.recommendation,
             report.model_dump_json(), None, now.isoformat()),
        )
    logger.info("candidate_registered", extra={"region_id": region.id, "result_status": "candidate"})
    return CandidateResult(region=region, finance=report)


def candidate_report(path: Path, region_id: str) -> FinanceReport | None:
    """Load a saved form assessment; legacy seed assumptions are not live evidence."""
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT assumptions FROM finance_assessments WHERE region_id = ? ORDER BY created_at DESC LIMIT 1",
            (region_id,),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["assumptions"])
    payload = payload.get("local_report", payload)
    if payload.get("source") == "local_simulation":
        return FinanceReport.model_validate(payload)
    return None


def count_pending_signals(path: Path, region_id: str) -> int:
    """Return how many new signals remain available for a regional cycle."""
    with closing(connect(path)) as conn:
        return Repository(conn).count_unprocessed_signals(region_id)


def persist_cfo_import(path: Path, imported: CfoScenarioImport) -> bool:
    """Attach validated provider provenance to the region's latest assessment."""
    with closing(connect(path)) as conn, conn:
        row = conn.execute(
            "SELECT id, assumptions FROM finance_assessments WHERE region_id = ? ORDER BY created_at DESC LIMIT 1",
            (imported.region_id,),
        ).fetchone()
        if row is None:
            return False
        current = json.loads(row["assumptions"])
        local_report = current.get("local_report", current)
        stored = {"local_report": local_report, "cfo_ai_import": imported.model_dump(mode="json")}
        reference = f"{imported.model_id}/{imported.scenario_id}"
        conn.execute(
            "UPDATE finance_assessments SET assumptions = ?, cfo_reference = ? WHERE id = ?",
            (json.dumps(stored, allow_nan=False), reference, row["id"]),
        )
    return True


@neatlogs.span(kind="WORKFLOW", name="storefront_preview")
def preview_products(path: Path, region_id: str) -> list[RankedProduct]:
    """Rank a stored profile; event injection remains a later workflow action."""
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT * FROM region_profiles WHERE region_id = ?", (region_id,)).fetchone()
        if row is None:
            return []
        payload = dict(row)
        payload["weights"] = json.loads(payload["weights"])
        profile = RankingProfile.model_validate(RegionProfile.model_validate(payload))
        products = []
        for row in conn.execute("SELECT * FROM products"):
            payload = dict(row)
            payload["tags"] = json.loads(payload["tags"])
            products.append(RankingProduct.model_validate(Product.model_validate(payload)))
        from adaptive_commerce.persistence import Repository
        repository = Repository(conn)
        policy = repository.get_ranking_policy(region_id)
        events = repository.get_active_events(region_id, datetime.now(timezone.utc).isoformat())
    with trace_step(
        "CHAIN", "recommendation_ranking", region_id=profile.region_id,
        version_id=profile.active_version_id,
    ):
        return rank_products(
            products, profile, now=datetime.now(timezone.utc), policy=RankingPolicy.model_validate(policy),
            events=[RankingEvent.model_validate(event) for event in events],
        )


def list_region_cycles(path: Path, region_id: str):
    """Return recent autonomous cycles for the selected region."""
    from adaptive_commerce.persistence import Repository
    with closing(connect(path)) as conn:
        return Repository(conn).list_update_cycles(region_id)


def list_region_actions(path: Path, region_id: str) -> list[dict]:
    """Return recent operator controls for the selected region."""
    from adaptive_commerce.persistence import Repository
    with closing(connect(path)) as conn:
        return Repository(conn).list_operator_actions(region_id)


def get_product_catalog(path: Path) -> dict[str, Product]:
    """Load the validated local product catalog for presentation labels."""
    with closing(connect(path)) as conn:
        result = {}
        for row in conn.execute("SELECT * FROM products ORDER BY name"):
            payload = dict(row)
            payload["tags"] = json.loads(payload["tags"])
            result[payload["id"]] = Product.model_validate(payload)
        return result
