"""Assistant grounding, capability boundaries, and trace-linked repair tests."""

import json
from types import SimpleNamespace

import pytest

from adaptive_commerce.application.assistant import (
    _execute_tool, ask_assistant, repair_signal_from_trace,
)
from adaptive_commerce.application.dashboard import bootstrap_database
from adaptive_commerce.application.operator import inject_demo_signal
from adaptive_commerce.application.worker import run_region_cycle
from adaptive_commerce.integrations.llm import DEFAULT_MODEL
from adaptive_commerce.integrations.llm import configured_model, llm_status
from adaptive_commerce.integrations.trace_readback import TraceReadback, TraceSpanSummary
from adaptive_commerce.persistence import Repository, connect


def _message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def local_neatlogs_only(monkeypatch):
    """Keep assistant service tests from exporting traces to a developer account."""
    monkeypatch.setenv("NEATLOGS_API_KEY", "")


def test_assistant_uses_allowlisted_read_tool_and_returns_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", DEFAULT_MODEL)
    bootstrap_database(tmp_path / "atlas.db")
    path = tmp_path / "atlas.db"
    call = SimpleNamespace(id="tool-1", function=SimpleNamespace(
        name="get_portfolio_snapshot", arguments="{}"))
    client = FakeClient([_response(_message(tool_calls=[call])), _response(_message("There are two active demo regions."))])

    result = ask_assistant(path, "How many active regions are there?", client=client)

    assert result.status == "answered"
    assert "active demo regions" in result.answer
    assert result.evidence[0].kind == "portfolio"
    tool_names = {tool["function"]["name"] for tool in client.requests[0]["tools"]}
    assert "repair_signal" not in tool_names
    assert client.requests[0]["model"] == DEFAULT_MODEL


def test_assistant_degrades_without_openrouter_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    result = ask_assistant(tmp_path / "unused.db", "What changed?")
    assert result.status == "degraded"
    assert "OpenRouter credentials" in result.answer


def test_provider_can_switch_to_official_openai_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert llm_status()["provider"] == "OpenAI"
    assert llm_status()["status"] == "configured"
    assert configured_model() == "gpt-4o-mini"


def test_assistant_preview_uses_real_confidence_gate_and_does_not_persist(tmp_path):
    path = tmp_path / "preview.db"
    bootstrap_database(path)
    low = _execute_tool(path, "preview_recommendation_change",
                        {"region_id": "aspen", "topic": "festival", "confidence": 0.65},
                        trace_reader=lambda _trace_id: None)
    high = _execute_tool(path, "preview_recommendation_change",
                         {"region_id": "aspen", "topic": "festival", "confidence": 0.70},
                         trace_reader=lambda _trace_id: None)

    assert low["status"] == "unchanged"
    assert "low_confidence" in low["gate_results"]
    assert high["status"] == "proposal"
    assert high["persisted"] is False
    assert high["passed_signal_gates"] is True
    with connect(path) as conn:
        assert Repository(conn).get_region_profile("aspen").active_version_id == "v-aspen-1"


def test_repair_requires_verified_trace_and_preserves_original(tmp_path):
    path = tmp_path / "repair.db"
    bootstrap_database(path)
    inject_demo_signal(path, "aspen", ["outdoor"], 0.94,
                       "Synthetic outdoor demand evidence is ready for a trace linked repair test.")
    cycle = run_region_cycle(path, "aspen")
    signal_id = cycle.cycle.signal_ids[0]
    def verified_reader(trace_id):
        assert trace_id == cycle.metrics["neatlogs_trace_id"]
        return TraceReadback(status="verified", detail="verified", trace_id=trace_id,
                             spans=[TraceSpanSummary(name="regional_update_cycle", status="OK")])

    result = repair_signal_from_trace(
        path, region_id="aspen", cycle_id=cycle.cycle.id, signal_id=signal_id,
        corrected_topics=["festival"], corrected_confidence=0.91,
        reason="The trace confirms the persisted label should be festival.", trace_reader=verified_reader,
    )

    with connect(path) as conn:
        repo = Repository(conn)
        original = repo.get_signal(signal_id)
        repaired = repo.get_signal(result["corrected_signal_id"])
        audit = conn.execute("SELECT * FROM signal_corrections WHERE corrected_signal_id = ?",
                             (result["corrected_signal_id"],)).fetchone()
    assert original.topics == ["outdoor"]
    assert repaired.topics == ["festival"]
    assert repaired.evidence_excerpt == original.evidence_excerpt
    assert audit["source_signal_id"] == signal_id
    assert audit["trace_id"] == cycle.metrics["neatlogs_trace_id"]


def test_repair_refuses_unverified_trace_without_writing(tmp_path):
    path = tmp_path / "repair-fail.db"
    bootstrap_database(path)
    inject_demo_signal(path, "aspen", ["outdoor"], 0.94,
                       "Synthetic outdoor demand evidence is ready for a trace linked repair test.")
    cycle = run_region_cycle(path, "aspen")
    signal_id = cycle.cycle.signal_ids[0]

    def unavailable_reader(trace_id):
        return TraceReadback(status="degraded", detail="missing credentials", trace_id=trace_id)

    with pytest.raises(ValueError, match="missing credentials"):
        repair_signal_from_trace(
            path, region_id="aspen", cycle_id=cycle.cycle.id, signal_id=signal_id,
            corrected_topics=["festival"], corrected_confidence=0.91,
            reason="Repair based on trace", trace_reader=unavailable_reader,
        )
    with connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM signal_corrections").fetchone()[0]
    assert count == 0


def test_assistant_requires_trace_inspection_before_repair_tool(tmp_path):
    path = tmp_path / "assistant-repair.db"
    bootstrap_database(path)
    inject_demo_signal(path, "aspen", ["outdoor"], 0.94,
                       "Synthetic outdoor demand evidence for assistant tool sequence test.")
    cycle = run_region_cycle(path, "aspen")
    signal_id = cycle.cycle.signal_ids[0]
    repair_call = SimpleNamespace(id="repair-1", function=SimpleNamespace(
        name="repair_signal", arguments=json.dumps({
            "region_id": "aspen", "cycle_id": cycle.cycle.id, "signal_id": signal_id,
            "corrected_topics": ["festival"], "corrected_confidence": 0.91,
            "reason": "The trace confirmed a corrected topic.",
        })))
    client = FakeClient([_response(_message(tool_calls=[repair_call])), _response(_message("I need to inspect the trace first."))])

    result = ask_assistant(path, "Please repair this misclassified signal.", client=client)

    assert result.status == "answered"
    tool_message = next(item for item in client.requests[1]["messages"] if item["role"] == "tool")
    assert "inspect and verify" in tool_message["content"]
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM signal_corrections").fetchone()[0] == 0
