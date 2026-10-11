"""OpenRouter configuration using the official OpenAI-compatible Python SDK."""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import neatlogs
from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "openrouter/free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def load_app_environment() -> None:
    """Load ignored local settings without overriding shell-provided values."""
    repository_env = Path(__file__).resolve().parents[3] / ".env"
    load_dotenv(repository_env, override=False)


def llm_status() -> dict[str, str]:
    """Return safe provider availability details without exposing credentials."""
    load_app_environment()
    provider, key, model = _provider_configuration()
    if not key:
        return {"status": "degraded", "provider": provider, "model": model,
                "detail": f"{_key_name(provider)} is missing; assistant is unavailable."}
    return {"status": "configured", "provider": provider, "model": model,
            "detail": f"{provider} is configured; live model access has not been checked in this process."}


@lru_cache(maxsize=1)
def get_llm_client() -> Any | None:
    """Create a Neatlogs-instrumented OpenAI-compatible client once per process."""
    load_app_environment()
    provider, api_key, model = _provider_configuration()
    if not api_key:
        return None
    from adaptive_commerce.integrations.observability import initialize_tracing

    initialize_tracing()
    options: dict[str, Any] = {
        "api_key": api_key,
        "timeout": float(os.getenv("LLM_TIMEOUT_SECONDS", "25")),
        "max_retries": 1,
    }
    if provider == "OpenRouter":
        options["base_url"] = os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
        options["default_headers"] = {
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://atlas-commerce.local"),
            "X-OpenRouter-Title": "Atlas Commerce",
        }
    client = OpenAI(**options)
    try:
        return neatlogs.wrap(client, provider=provider.lower(), model=model)
    except Exception:
        logger.exception("neatlogs_openrouter_instrumentation_failed")
        return client


def configured_model() -> str:
    """Return configured model slug for the selected provider."""
    load_app_environment()
    return _provider_configuration()[2]


def _provider_configuration() -> tuple[str, str | None, str]:
    provider_setting = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
    if provider_setting == "openai":
        return "OpenAI", os.getenv("OPENAI_API_KEY"), os.getenv("LLM_MODEL", DEFAULT_OPENAI_MODEL)
    return "OpenRouter", os.getenv("OPENROUTER_API_KEY"), os.getenv("LLM_MODEL", DEFAULT_MODEL)


def _key_name(provider: str) -> str:
    return "OPENAI_API_KEY" if provider == "OpenAI" else "OPENROUTER_API_KEY"
