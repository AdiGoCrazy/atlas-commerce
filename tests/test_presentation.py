
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
