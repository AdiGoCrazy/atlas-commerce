"""Prevent ordinary tests from exporting traces or calling paid model APIs."""

import pytest


@pytest.fixture(autouse=True)
def disable_external_service_credentials(monkeypatch):
    """Use local-only integrations unless an individual test injects a fake."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "")
    monkeypatch.setenv("NEATLOGS_PROJECT_ID", "")
    monkeypatch.setenv("NEATLOGS_TOKEN", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
