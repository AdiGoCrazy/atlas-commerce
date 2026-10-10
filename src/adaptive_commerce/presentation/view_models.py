
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
