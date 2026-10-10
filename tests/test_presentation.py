
from adaptive_commerce.presentation.view_models import (
    validate_candidate_region,
    prepare_region_rows,
    finance_empty_message,
)


def test_valid_candidate():
    assert validate_candidate_region("Chennai", "India") is None


def test_empty_region_name():
    assert validate_candidate_region("", "India") == (
        "Please enter a region name."
    )


def test_whitespace_region_name():
    assert validate_candidate_region("   ", "India") == (
        "Please enter a region name."
    )


def test_empty_country():
    assert validate_candidate_region("Chennai", "") == (
        "Please enter a country."
    )


def test_region_rows():
    regions = [{"name": "Mumbai", "status": "Active"}]

    result = prepare_region_rows(regions)

    assert result == [{"name": "Mumbai", "status": "Active"}]


def test_empty_region_rows():
    assert prepare_region_rows([]) == []


def test_no_finance_result():
    assert finance_empty_message(None) == (
        "No finance assessment available yet."
    )


from decimal import Decimal

from adaptive_commerce.presentation.view_models import (
    prepare_ranking_comparison,
)
from adaptive_commerce.recommender.contracts import (
    RankedProduct,
    ScoreBreakdown,
)


def make_ranked(product_id, rank, score):
    return RankedProduct(
        product_id=product_id,
        rank=rank,
        score=Decimal(score),
        breakdown=ScoreBreakdown(
            base_popularity=Decimal(score),
            regional_interest_match=Decimal("0"),
            temporary_event_boost=Decimal("0"),
            margin_bonus=Decimal("0"),
            policy_penalty=Decimal("0"),
            interest_by_tag={},
            event_by_id={},
        ),
    )


def test_ranking_comparison_score_change():
    before = [make_ranked("bottle", 1, "6.5")]
    after = [make_ranked("bottle", 2, "7.0")]

    rows = prepare_ranking_comparison(before, after)

    assert len(rows) == 1
    assert rows[0]["Before rank"] == 1
    assert rows[0]["After rank"] == 2
    assert rows[0]["Score change"] == Decimal("0.5")


def test_ranking_comparison_new_product():
    before = []
    after = [make_ranked("boots", 1, "5.0")]

    rows = prepare_ranking_comparison(before, after)

    assert rows[0]["Before rank"] is None
    assert rows[0]["After rank"] == 1
    assert rows[0]["Score change"] is None


def test_ranking_comparison_removed_product():
    before = [make_ranked("tent", 1, "4.0")]
    after = []

    rows = prepare_ranking_comparison(before, after)

    assert rows[0]["Before rank"] == 1
    assert rows[0]["After rank"] is None
    assert rows[0]["Score change"] is None


from adaptive_commerce.presentation.view_models import (
    prepare_approval_state,
)


def test_candidate_state_waits_for_assessment():
    result = prepare_approval_state("candidate")

    assert result["status"] == "Candidate"
    assert result["show_actions"] is False
    assert result["actions_enabled"] is False


def test_awaiting_approval_state_enables_human_actions():
    result = prepare_approval_state("awaiting_approval")

    assert result["status"] == "Awaiting approval"
    assert result["show_actions"] is True
    assert result["actions_enabled"] is True


def test_active_approval_state():
    result = prepare_approval_state("active")

    assert result["status"] == "Active"
    assert result["show_actions"] is False
    assert result["actions_enabled"] is False


def test_rejected_approval_state():
    result = prepare_approval_state("rejected")

    assert result["status"] == "Rejected"
    assert result["show_actions"] is False
    assert result["actions_enabled"] is False


def test_unknown_approval_state():
    result = prepare_approval_state("paused")

    assert result["show_actions"] is False
    assert result["actions_enabled"] is False
