
def validate_candidate_region(name: str, country: str) -> str | None:
    """Return an error message, or None if valid."""
    if not name.strip():
        return "Please enter a region name."

    if not country.strip():
        return "Please enter a country."

    return None


def prepare_region_rows(regions: list[dict]) -> list[dict]:
    """Prepare region data for display."""
    return [
        {
            "name": region["name"],
            "status": region["status"],
        }
        for region in regions
    ]


def finance_empty_message(result: dict | None) -> str:
    """Return the finance panel's empty-state message."""
    if result is None:
        return "No finance assessment available yet."

    return ""


from decimal import Decimal

from adaptive_commerce.recommender.contracts import RankedProduct


def prepare_ranking_comparison(
    before: list[RankedProduct],
    after: list[RankedProduct],
) -> list[dict]:
    """Prepare a comparison of two ranked product lists."""

    before_by_id = {item.product_id: item for item in before}
    after_by_id = {item.product_id: item for item in after}

    all_ids = sorted(set(before_by_id) | set(after_by_id))

    rows = []

    for product_id in all_ids:
        old = before_by_id.get(product_id)
        new = after_by_id.get(product_id)

        old_score = old.score if old else None
        new_score = new.score if new else None

        score_change = (
            new_score - old_score
            if old_score is not None and new_score is not None
            else None
        )

        rows.append({
            "Product": product_id,
            "Before rank": old.rank if old else None,
            "After rank": new.rank if new else None,
            "Before score": old_score,
            "After score": new_score,
            "Score change": score_change,
        })

    return rows


def prepare_cycle_comparison_rows(
    before: list[dict],
    after: list[dict],
    catalog: dict,
) -> list[dict[str, str]]:
    """Prepare Arrow-safe display rows for persisted recommendation comparisons."""
    before_by_id = {item["product_id"]: item for item in before}
    after_by_id = {item["product_id"]: item for item in after}
    rows = []
    for product_id in sorted(set(before_by_id) | set(after_by_id)):
        old = before_by_id.get(product_id)
        new = after_by_id.get(product_id)
        product = catalog.get(product_id)
        old_rank = old["rank"] if old else None
        new_rank = new["rank"] if new else None
        if old_rank is not None and new_rank is not None and new_rank < old_rank:
            movement = f"↑ {old_rank - new_rank}"
        elif old_rank is not None and new_rank is not None and new_rank > old_rank:
            movement = f"↓ {new_rank - old_rank}"
        elif new_rank is not None and old_rank is None:
            movement = "New"
        elif old_rank is not None and new_rank is None:
            movement = "Removed"
        else:
            movement = "—"
        rows.append({
            "Product": product.name if product else product_id,
            "Before": str(old_rank) if old_rank is not None else "—",
            "After": str(new_rank) if new_rank is not None else "—",
            "Movement": movement,
        })
    return rows


def prepare_approval_state(state: str) -> dict:
    """Prepare approval controls for a region's lifecycle state."""

    if state == "candidate":
        return {
            "status": "Candidate",
            "show_actions": True,
            "actions_enabled": False,
            "message": "Awaiting human approval.",
        }

    if state == "active":
        return {
            "status": "Active",
            "show_actions": False,
            "actions_enabled": False,
            "message": "This region is already active.",
        }

    if state == "rejected":
        return {
            "status": "Rejected",
            "show_actions": False,
            "actions_enabled": False,
            "message": "This region has been rejected.",
        }

    return {
        "status": state.replace("_", " ").title(),
        "show_actions": False,
        "actions_enabled": False,
        "message": "No approval actions available for this state.",
    }
