"""Assistant grounding, capability boundaries, and trace-linked repair tests."""

import json
from types import SimpleNamespace

import pytest

from adaptive_commerce.application.assistant import (
    _available_tools, _execute_tool, ask_assistant, repair_signal_from_trace,
)
from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, submit_candidate,
)
from adaptive_commerce.application.lifecycle import approve_region
from adaptive_commerce.application.operator import inject_demo_signal
from adaptive_commerce.application.worker import run_region_cycle
from adaptive_commerce.integrations.finance import FinanceInputs
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


def test_region_snapshot_resolves_custom_region_by_name(tmp_path):
    path = tmp_path / "region-name.db"
    bootstrap_database(path)
    candidate = submit_candidate(path, CandidateRequest(name="Test Coast", assumptions=FinanceInputs()))
    approve_region(path, candidate.region.id)

    snapshot = _execute_tool(
        path, "get_region_snapshot", {"region_id": "test coast"}, trace_reader=lambda _trace_id: None,
    )

    assert snapshot["region"]["id"] == candidate.region.id
    assert snapshot["region"]["state"] == "active"
    assert snapshot["recommendations"]
    assert {"name", "price", "score"} <= snapshot["recommendations"][0].keys()
    duplicate = _execute_tool(path, "create_candidate_region", {
        "region_name": "test coast",
    }, trace_reader=lambda _trace_id: None)
    assert duplicate["status"] == "already_exists"
    assert duplicate["region"]["id"] == candidate.region.id
    assert duplicate["recommendations"]


def test_explicit_candidate_create_is_exposed_and_empty_model_reply_uses_result(tmp_path, monkeypatch):
    path = tmp_path / "assistant-create.db"
    bootstrap_database(path)
    create_call = SimpleNamespace(id="create-1", function=SimpleNamespace(
        name="create_candidate_region", arguments=json.dumps({
            "region_name": "Diwali Coast", "locale": "en_IN", "monthly_traffic": 72000,
        })))
    client = FakeClient([
        _response(_message(tool_calls=[create_call])),
        _response(_message(content="")),
    ])

    result = ask_assistant(path, "Create a candidate region called Diwali Coast in India.", client=client)

    exposed = {tool["function"]["name"] for tool in client.requests[0]["tools"]}
    assert "create_candidate_region" in exposed
    assert "set_region_lifecycle" not in exposed
    assert result.status == "answered"
    assert "pending operator approval" in result.answer
    assert "simulated" in result.answer
    with connect(path) as conn:
        region = Repository(conn).get_region(result.evidence[0].record_id)
    assert region.name == "Diwali Coast"
    assert region.locale == "en_IN"
    assert region.state.value == "candidate"


def test_candidate_update_and_remove_preserve_lifecycle_history(tmp_path):
    path = tmp_path / "candidate-crud.db"
    bootstrap_database(path)
    candidate = submit_candidate(path, CandidateRequest(name="Harbor Draft", assumptions=FinanceInputs()))

    updated = _execute_tool(path, "update_candidate_region", {
        "region_id": "Harbor Draft", "region_name": "Harbor Market",
        "finance_updates": {"monthly_traffic": 91000},
    }, trace_reader=lambda _trace_id: None)
    removed = _execute_tool(path, "remove_region", {
        "region_id": candidate.region.id,
    }, trace_reader=lambda _trace_id: None)

    assert updated["status"] == "updated"
    assert "Harbor Market" in updated["summary"]
    assert removed["status"] == "removed"
    with connect(path) as conn:
        region = Repository(conn).get_region(candidate.region.id)
        assessments = Repository(conn).get_finance_assessments_by_region(candidate.region.id)
        actions = Repository(conn).list_operator_actions(candidate.region.id)
    assert region.state.value == "rejected"
    assert len(assessments) == 2
    assert actions[-1]["action"] == "rejected"


def test_explicit_chat_approval_uses_lifecycle_gate(tmp_path):
    path = tmp_path / "assistant-approval.db"
    bootstrap_database(path)
    candidate = submit_candidate(path, CandidateRequest(name="Approval Bay", assumptions=FinanceInputs()))
    approval_call = SimpleNamespace(id="approve-1", function=SimpleNamespace(
        name="set_region_lifecycle", arguments=json.dumps({
            "region_id": "Approval Bay", "action": "approve",
        })))
    client = FakeClient([
        _response(_message(tool_calls=[approval_call])),
        _response(_message(content="")),
    ])

    result = ask_assistant(path, "Approve candidate Approval Bay.", client=client)

    assert "Approved and activated Approval Bay" in result.answer
    with connect(path) as conn:
        assert Repository(conn).get_region(candidate.region.id).state.value == "active"


def test_chat_pipeline_tools_add_synthetic_signal_and_run_engine(tmp_path):
    path = tmp_path / "assistant-pipeline.db"
    bootstrap_database(path)
    candidate = submit_candidate(path, CandidateRequest(name="Festival Bay", assumptions=FinanceInputs()))
    approve_region(path, candidate.region.id)

    signal = _execute_tool(path, "add_synthetic_signal", {
        "region_id": "Festival Bay", "topics": ["festival", "electronics"],
        "confidence": 0.70,
        "evidence": "Synthetic regional event coverage reports strong festival electronics interest.",
        "source_type": "event",
    }, trace_reader=lambda _trace_id: None)
    cycle = _execute_tool(path, "run_region_cycle", {
        "region_id": "Festival Bay",
    }, trace_reader=lambda _trace_id: None)
    replay = _execute_tool(path, "replay_region_cycle", {
        "cycle_id": cycle["cycle_id"],
    }, trace_reader=lambda _trace_id: None)

    assert signal["status"] == "signal_added"
    assert signal["signal_id"]
    assert cycle["status"] == "cycle_completed"
    assert "Festival Bay" in cycle["summary"]
    assert cycle["cycle_id"]
    assert replay["status"] == "cycle_completed"
    assert replay["cycle_id"] != cycle["cycle_id"]
    assert "Replayed cycle" in replay["summary"]


def test_catalog_crud_uses_validated_service_and_preserves_removed_product(tmp_path):
    path = tmp_path / "assistant-catalog.db"
    bootstrap_database(path)
    create = _execute_tool(path, "manage_product_catalog", {
        "action": "create", "product_id": "festival-lantern", "name": "Festival Lantern",
        "tags": ["festival", "outdoor"], "price": 32.5, "cost": 12.0,
        "base_popularity": 4.0,
    }, trace_reader=lambda _trace_id: None)
    update = _execute_tool(path, "manage_product_catalog", {
        "action": "update", "product_id": "festival-lantern", "price": 36.0,
    }, trace_reader=lambda _trace_id: None)
    remove = _execute_tool(path, "manage_product_catalog", {
        "action": "remove", "product_id": "festival-lantern",
    }, trace_reader=lambda _trace_id: None)
    catalog = _execute_tool(path, "get_product_catalog", {}, trace_reader=lambda _trace_id: None)

    assert "Created product Festival Lantern" in create["summary"]
    assert "Updated product Festival Lantern" in update["summary"]
    assert "Soft-removed product Festival Lantern" in remove["summary"]
    stored = next(item for item in catalog["products"] if item["id"] == "festival-lantern")
    assert stored["price"] == 36.0
    assert stored["in_stock"] is False
    assert stored["eligible"] is False
    actions = [action for action in catalog["recent_changes"]
               if action["product_id"] == "festival-lantern"]
    assert [action["action"] for action in reversed(actions)] == ["create", "update", "remove"]


def test_signal_delete_only_removes_pending_record_and_keeps_audit_snapshot(tmp_path):
    path = tmp_path / "assistant-signal-delete.db"
    bootstrap_database(path)
    signal = inject_demo_signal(
        path, "aspen", ["festival"], 0.7,
        "Synthetic pending festival evidence that is safe to withdraw before a cycle.",
    )

    result = _execute_tool(path, "remove_pending_signal", {"signal_id": signal.id},
                           trace_reader=lambda _trace_id: None)

    assert result["status"] == "signal_removed"
    with connect(path) as conn:
        assert Repository(conn).get_signal(signal.id) is None
        audit = conn.execute(
            "SELECT signal_json FROM signal_deletions WHERE signal_id = ?", (signal.id,),
        ).fetchone()
    assert json.loads(audit["signal_json"])["topics"] == ["festival"]


def test_agent_write_capabilities_require_explicit_operation_language():
    assert "create_candidate_region" not in _available_tools("What is happening in the portfolio?")
    assert "set_region_lifecycle" not in _available_tools("Show the status of active regions.")
    assert "create_candidate_region" in _available_tools("Create a new region candidate.")
    assert "set_region_lifecycle" in _available_tools("Approve the candidate region.")
    assert "manage_product_catalog" not in _available_tools("What is the price of each product?")
    assert "manage_product_catalog" in _available_tools("Create a new product in the catalog.")
    assert "manage_product_catalog" not in _available_tools("What happens if I remove a product?")
    assert "remove_pending_signal" in _available_tools("Remove this pending signal.")
    assert "remove_pending_signal" not in _available_tools("What happens if I remove this signal?")
    assert "set_region_lifecycle" not in _available_tools("Should I approve this candidate?")
    assert "replay_region_cycle" in _available_tools("Replay cycle abc123.")
