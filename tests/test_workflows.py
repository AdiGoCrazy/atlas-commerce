"""Approval, idempotency, and destructive-operation boundaries for agent workflows."""

import pytest
from types import SimpleNamespace

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, list_regions, submit_candidate,
)
from adaptive_commerce.application import orchestration
from adaptive_commerce.application.orchestration import (
    WorkflowService, _execute_hard_delete, _hard_delete_impact, _validate_arguments,
)
from adaptive_commerce.application.workflows import (
    ProposedOperation, SpecialistReport, WorkflowPlan, WorkflowProposal,
    approve_workflow, claim_action, create_workflow, get_workflow, save_proposal,
)
from adaptive_commerce.agents import subagent
from adaptive_commerce.integrations.finance import FinanceInputs
from adaptive_commerce.persistence import connect


def _proposal(action: ProposedOperation) -> WorkflowProposal:
    plan = WorkflowPlan(title="Test workflow", summary="A scoped test operation.", actions=[action])
    reports = [
        SpecialistReport(role=role, status="complete", label="Reviewed", summary="Test evidence.")
        for role in ("research", "finance", "recommendation")
    ]
    review = SpecialistReport(role="review", status="complete", label="Reviewed", summary="Plan is in scope.")
    return WorkflowProposal(plan=plan, reports=reports, review=review)


def test_workflow_plan_requires_matching_operator_approval_before_action_claim(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    workflow_id = create_workflow(path, "Create a candidate called Test Market")
    action = ProposedOperation(
        agent_role="finance", tool_name="create_candidate_region",
        arguments={"region_name": "Test Market"}, rationale="Operator requested a new candidate.",
        impact="Creates a candidate assessed with simulated finance assumptions.",
    )
    plan_hash = save_proposal(path, workflow_id, _proposal(action), "trace-test")
    workflow = get_workflow(path, workflow_id)
    action_id = workflow["actions"][0]["id"]

    with pytest.raises(ValueError, match="changed or is no longer awaiting"):
        approve_workflow(path, workflow_id, "wrong-hash", "operator")
    with pytest.raises(ValueError, match="approved write scope"):
        claim_action(path, workflow_id, action_id, plan_hash)

    approve_workflow(path, workflow_id, plan_hash, "operator")
    claimed = claim_action(path, workflow_id, action_id, plan_hash)
    assert claimed["already_complete"] is False
    assert get_workflow(path, workflow_id)["status"] == "running"
    with pytest.raises(ValueError, match="already running or has failed"):
        claim_action(path, workflow_id, action_id, plan_hash)


def test_hard_delete_requires_unchanged_review_snapshot_and_preserves_audit(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    region_id = list_regions(path)[0].id
    arguments = {"target_type": "region", "target_id": region_id}
    workflow_id = create_workflow(path, f"Permanently delete region {region_id}")
    action = ProposedOperation(
        agent_role="review", tool_name="hard_delete_record", arguments=arguments,
        rationale="Explicit deletion request.", impact="Permanent region deletion.",
    )
    plan = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id, WorkflowPlan(title="Delete region", summary="Remove exact requested region", actions=[action]),
    )
    action = plan.actions[0]
    proposal = _proposal(action).model_copy(update={"plan": plan})
    plan_hash = save_proposal(path, workflow_id, proposal, None)
    workflow = get_workflow(path, workflow_id)
    operation_id = workflow["actions"][0]["id"]
    approve_workflow(path, workflow_id, plan_hash, "operator")

    result = _execute_hard_delete(path, workflow_id, operation_id, action.impact,
                                  arguments, action.impact_details, "operator")
    assert result["status"] == "hard_deleted"
    with connect(path) as conn:
        assert conn.execute("SELECT 1 FROM regions WHERE id = ?", (region_id,)).fetchone() is None
        audit = conn.execute(
            "SELECT target_id, approved_by FROM workflow_deletion_audit WHERE id = ?",
            (result["audit_id"],),
        ).fetchone()
    assert tuple(audit) == (region_id, "operator")


def test_hard_delete_refuses_target_changed_after_operator_review(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    region = list_regions(path)[0]
    arguments = {"target_type": "region", "target_id": region.id}
    reviewed = _hard_delete_impact(path, arguments)
    details = {
        "dependencies": reviewed["dependencies"],
        "snapshot_hash": __import__("hashlib").sha256(
            __import__("json").dumps(reviewed["snapshot"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    with connect(path) as conn, conn:
        conn.execute("UPDATE regions SET name = ? WHERE id = ?", ("Changed after review", region.id))

    with pytest.raises(ValueError, match="changed after specialist review"):
        _execute_hard_delete(path, "workflow-id", "operation-id", "delete", arguments, details, "operator")
    assert any(item.id == region.id for item in list_regions(path))


def test_tool_argument_schema_checks_array_items_and_nested_numbers():
    _validate_arguments("add_synthetic_signal", {
        "region_id": "cedar", "topics": ["festival"], "confidence": 0.7,
        "evidence": "Synthetic evidence with sufficient length.", "source_type": "event",
    })
    with pytest.raises(ValueError, match="must be a string"):
        _validate_arguments("add_synthetic_signal", {
            "region_id": "cedar", "topics": [4], "confidence": 0.7,
            "evidence": "Synthetic evidence with sufficient length.", "source_type": "event",
        })
    with pytest.raises(ValueError, match="must be finite"):
        _validate_arguments("preview_recommendation_change", {
            "region_id": "cedar", "topic": "festival", "confidence": float("nan"),
        })


def test_planner_discards_undeclared_cycle_metadata_and_records_normalization(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = "Run Cedar Valley's regional cycle and report the product rankings."
    workflow_id = create_workflow(path, request)
    action = ProposedOperation(
        agent_role="recommendation", tool_name="run_region_cycle",
        arguments={
            "region_id": "cedar",
            "cycle_config": {"confidence": 0.91},
            "note": "The model added planning metadata not supported by the tool.",
        },
        rationale="Run the explicitly requested cycle.",
        impact="Consumes eligible signals and evaluates recommendation changes.",
    )

    normalized = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Cedar cycle", summary="Run the requested cycle.", actions=[action]),
    )

    assert normalized.actions[0].arguments == {"region_id": "cedar"}
    workflow = get_workflow(path, workflow_id)
    event = next(item for item in workflow["events"] if item["event_type"] == "planner_arguments_normalized")
    assert event["detail"] == {
        "operations": [{"tool_name": "run_region_cycle", "fields": ["cycle_config", "note"]}],
    }


def test_planner_uses_finance_defaults_unless_operator_supplies_values(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = "Create a candidate region called Test Beach."
    workflow_id = create_workflow(path, request)
    action = ProposedOperation(
        agent_role="finance", tool_name="create_candidate_region",
        arguments={"region_name": "Test Beach", "monthly_traffic": 0, "campaign_cost": 1},
        rationale="Create the requested candidate.", impact="Assess the candidate.",
    )

    normalized = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Create Test Beach", summary="Use Atlas finance defaults.", actions=[action]),
    )

    assert normalized.actions[0].arguments == {"region_name": "Test Beach"}
    event = next(item for item in get_workflow(path, workflow_id)["events"]
                 if item["event_type"] == "planner_arguments_normalized")
    assert event["detail"]["operations"] == [{
        "tool_name": "create_candidate_region", "fields": ["campaign_cost", "monthly_traffic"],
    }]


def test_failed_finance_gate_marks_downstream_operations_skipped(tmp_path, monkeypatch):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = (
        "Create a candidate region called Test Reject Bay with monthly traffic 0, "
        "add an event festival signal with confidence 0.8 "
        "and evidence that festival demand is rising, then run its regional cycle."
    )
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="finance", tool_name="create_candidate_region",
            arguments={"region_name": "Test Reject Bay", "monthly_traffic": 0},
            rationale="Create the requested candidate.", impact="Assess the candidate with the stated traffic.",
        ),
        ProposedOperation(
            agent_role="finance", tool_name="set_region_lifecycle",
            arguments={"region_id": "Test Reject Bay", "action": "approve"},
            rationale="Activate before the requested cycle.", impact="Subject to Atlas's finance gate.",
        ),
        ProposedOperation(
            agent_role="research", tool_name="add_synthetic_signal",
            arguments={"region_id": "Test Reject Bay", "topics": ["festival"], "confidence": 0.8,
                       "evidence": "Synthetic event evidence reports rising festival demand in the region.",
                       "source_type": "event"},
            rationale="Add the requested evidence.", impact="Creates a pending signal.",
        ),
        ProposedOperation(
            agent_role="recommendation", tool_name="run_region_cycle",
            arguments={"region_id": "Test Reject Bay"}, rationale="Run the requested cycle.",
            impact="Consumes eligible signals and evaluates recommendations.",
        ),
    ]
    plan = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Reject-gated cycle", summary="Finance must approve activation.", actions=actions),
    )
    proposal = _proposal(plan.actions[0]).model_copy(update={"plan": plan})
    plan_hash = save_proposal(path, workflow_id, proposal, None)
    approve_workflow(path, workflow_id, plan_hash, "operator_prompt")
    service = WorkflowService(path, worker_urls={})

    def dispatch_to_atlas_worker(_url, payload, timeout=100):
        return service.execute_operation(payload)

    monkeypatch.setattr(orchestration, "_post_json", dispatch_to_atlas_worker)
    service._execute_plan(workflow_id)

    result = get_workflow(path, workflow_id)
    assert result["status"] == "partially_failed"
    assert [action["status"] for action in result["actions"]] == [
        "complete", "failed", "skipped", "skipped",
    ]
    assert all("finance assessment" in action["error"] for action in result["actions"][2:])


def test_chained_retry_reassesses_existing_rejected_local_candidate(tmp_path, monkeypatch):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    existing = submit_candidate(
        path,
        CandidateRequest(name="Repeat Bay", assumptions=FinanceInputs(monthly_traffic=0)),
    )
    assert existing.finance.recommendation == "reject"
    request = (
        "Create a candidate region named Repeat Bay, add an event festival signal with confidence 0.8 "
        "and evidence that Synthetic festival demand reports rising interest in Repeat Bay, "
        "then run Repeat Bay's regional cycle."
    )
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="finance", tool_name="create_candidate_region",
            arguments={"region_name": "Repeat Bay"}, rationale="Set up the requested candidate.",
            impact="Use stated assumptions or documented defaults.",
        ),
        ProposedOperation(
            agent_role="finance", tool_name="set_region_lifecycle",
            arguments={"region_id": "Repeat Bay", "action": "approve"},
            rationale="Activate before the requested cycle.", impact="Subject to the finance gate.",
        ),
        ProposedOperation(
            agent_role="research", tool_name="add_synthetic_signal",
            arguments={"region_id": "Repeat Bay", "topics": ["festival"], "confidence": 0.8,
                       "evidence": "Synthetic festival demand reports rising interest in Repeat Bay.",
                       "source_type": "event"},
            rationale="Add the requested signal.", impact="Adds one pending event signal.",
        ),
        ProposedOperation(
            agent_role="recommendation", tool_name="run_region_cycle",
            arguments={"region_id": "Repeat Bay"}, rationale="Run the requested cycle.",
            impact="Evaluates recommendation changes from the signal.",
        ),
    ]
    plan = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Retry Repeat Bay", summary="Reassess and run the requested cycle.", actions=actions),
    )
    plan_hash = save_proposal(path, workflow_id, _proposal(plan.actions[0]).model_copy(update={"plan": plan}), None)
    approve_workflow(path, workflow_id, plan_hash, "operator_prompt")
    service = WorkflowService(path, worker_urls={})

    def dispatch_to_atlas_worker(_url, payload, timeout=100):
        return service.execute_operation(payload)

    monkeypatch.setattr(orchestration, "_post_json", dispatch_to_atlas_worker)
    service._execute_plan(workflow_id)

    workflow = get_workflow(path, workflow_id)
    assert workflow["status"] == "completed"
    assert workflow["actions"][0]["result"]["status"] == "reassessed_existing"
    assert workflow["actions"][0]["result"]["finance_decision"] == "approve"
    assert [action["status"] for action in workflow["actions"]] == ["complete"] * 4
    assert any(event["event_type"] == "existing_candidate_finance_reassessed" for event in workflow["events"])
    assert next(region for region in list_regions(path) if region.id == existing.region.id).state.value == "active"


def test_planner_cannot_turn_negated_user_intent_into_a_write(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    workflow_id = create_workflow(path, "Don't run a regional cycle for Cedar; just tell me its pending signals.")
    action = ProposedOperation(
        agent_role="recommendation", tool_name="run_region_cycle",
        arguments={"region_id": "cedar"}, rationale="The planner decided to run the pipeline.",
        impact="Consumes eligible signals and may update recommendations.",
    )

    with pytest.raises(ValueError, match="not explicitly authorized"):
        WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
            workflow_id, WorkflowPlan(title="Unsafe", summary="Negated task", actions=[action]),
        )


def test_requested_create_and_cycle_can_include_visible_finance_gated_activation(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = (
        'Do a full chained workflow, set up a new custom region called "Test Beach", '
        'add the description as "festive sales predictions" and products for festivals only, '
        "then add different confidence inputs, cycle them, and show a comparison result between cycles."
    )
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="planner", tool_name="create_candidate_region",
            arguments={"region_name": "Test Beach"}, rationale="Create the requested candidate.",
            impact="Creates a candidate and records its finance assessment.",
        ),
        ProposedOperation(
            agent_role="planner", tool_name="add_synthetic_signal",
            arguments={"region_id": "Test Beach", "topics": ["festival"], "confidence": 0.8,
                       "evidence": "Synthetic festival demand evidence for the Test Beach market."},
            rationale="Add the requested festival evidence.", impact="Adds a pending synthetic signal.",
        ),
        ProposedOperation(
            agent_role="planner", tool_name="run_region_cycle",
            arguments={"region_id": "Test Beach"}, rationale="Run the requested regional cycle.",
            impact="Consumes eligible signals and evaluates recommendation changes.",
        ),
    ]
    plan = WorkflowPlan(title="Test Beach evaluation", summary="Create, activate, signal, and cycle.", actions=actions)

    enriched = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(workflow_id, plan)

    assert [action.tool_name for action in enriched.actions] == [
        "create_candidate_region", "set_region_lifecycle", "add_synthetic_signal", "run_region_cycle",
    ]
    assert enriched.actions[1].arguments["action"] == "approve"
    assert "finance gate" in enriched.actions[1].impact
    assert enriched.actions[2].arguments["source_type"] == "event"
    assert "Region descriptions are not a persisted field" in enriched.summary
    assert "product catalog is shared" in enriched.summary


def test_candidate_activation_is_not_inferred_when_operator_negates_it(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = (
        'Create a new region called "Test Beach", do not activate it, '
        "and run its regional cycle."
    )
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="finance", tool_name="create_candidate_region",
            arguments={"region_name": "Test Beach"}, rationale="Create requested candidate.", impact="Candidate only.",
        ),
        ProposedOperation(
            agent_role="finance", tool_name="set_region_lifecycle",
            arguments={"region_id": "Test Beach", "action": "approve"},
            rationale="Activate before cycle.", impact="Activates candidate.",
        ),
        ProposedOperation(
            agent_role="recommendation", tool_name="run_region_cycle",
            arguments={"region_id": "Test Beach"}, rationale="Run requested cycle.", impact="Consumes signals.",
        ),
    ]

    with pytest.raises(ValueError, match="not explicitly authorized"):
        WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
            workflow_id,
            WorkflowPlan(title="Unsafe", summary="Must honor the explicit no-activation constraint.", actions=actions),
        )


def test_candidate_activation_is_inserted_before_cycle_even_if_planner_puts_it_after(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = "Create a new region called Test Beach, add a festival signal, and run its regional cycle."
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="finance", tool_name="create_candidate_region",
            arguments={"region_name": "Test Beach"}, rationale="Create requested candidate.", impact="Candidate only.",
        ),
        ProposedOperation(
            agent_role="research", tool_name="add_synthetic_signal",
            arguments={"region_id": "Test Beach", "topics": ["festival"], "confidence": 0.8,
                       "evidence": "Synthetic festival demand evidence for the Test Beach market."},
            rationale="Add requested evidence.", impact="Adds pending signal.",
        ),
        ProposedOperation(
            agent_role="recommendation", tool_name="run_region_cycle",
            arguments={"region_id": "Test Beach"}, rationale="Run requested cycle.", impact="Consumes signals.",
        ),
        ProposedOperation(
            agent_role="finance", tool_name="set_region_lifecycle",
            arguments={"region_id": "Test Beach", "action": "approve"},
            rationale="Activate candidate.", impact="Activates candidate.",
        ),
    ]

    enriched = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Misordered", summary="Candidate dependencies are normalized before review.", actions=actions),
    )
    assert [action.tool_name for action in enriched.actions] == [
        "create_candidate_region", "set_region_lifecycle", "add_synthetic_signal", "run_region_cycle",
    ]


def test_approved_candidate_signal_cycle_executes_in_order_through_atlas_services(tmp_path, monkeypatch):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = "Create a new region called Test Workflow Bay, add an event festival and electronics signal, then run its regional cycle."
    workflow_id = create_workflow(path, request)
    actions = [
        ProposedOperation(
            agent_role="finance", tool_name="create_candidate_region",
            arguments={"region_name": "Test Workflow Bay"}, rationale="Create the requested region candidate.",
            impact="Stores the candidate and simulated finance assessment.",
        ),
        ProposedOperation(
            agent_role="finance", tool_name="set_region_lifecycle",
            arguments={"region_id": "Test Workflow Bay", "action": "approve"},
            rationale="Activate before the requested cycle.", impact="Uses the finance gate.",
        ),
        ProposedOperation(
            agent_role="research", tool_name="add_synthetic_signal",
            arguments={"region_id": "Test Workflow Bay", "topics": ["festival", "electronics"],
                       "confidence": 0.91,
                       "evidence": "Local event reports rising festival electronics demand across Test Workflow Bay."},
            rationale="Record requested event evidence.", impact="Creates one pending synthetic signal.",
        ),
        ProposedOperation(
            agent_role="recommendation", tool_name="run_region_cycle",
            arguments={"region_id": "Test Workflow Bay"}, rationale="Run requested regional cycle.",
            impact="Evaluates the candidate ranking against the active ranking.",
        ),
    ]
    plan = WorkflowService(path, worker_urls={})._validate_and_enrich_plan(
        workflow_id,
        WorkflowPlan(title="Regional signal test", summary="Run candidate through Atlas services.", actions=actions),
    )
    plan_hash = save_proposal(path, workflow_id, _proposal(actions[0]).model_copy(update={"plan": plan}), None)
    approve_workflow(path, workflow_id, plan_hash, "dashboard_operator")
    service = WorkflowService(path, worker_urls={})

    def dispatch_to_atlas_worker(_url, payload, timeout=100):
        return service.execute_operation(payload)

    monkeypatch.setattr(orchestration, "_post_json", dispatch_to_atlas_worker)
    service._execute_plan(workflow_id)

    workflow = get_workflow(path, workflow_id)
    assert workflow["status"] == "completed"
    assert [action["status"] for action in workflow["actions"]] == ["complete"] * 4
    assert workflow["actions"][2]["result"]["status"] == "signal_added"
    cycle_result = workflow["actions"][3]["result"]
    assert cycle_result["status"] == "cycle_completed"
    assert cycle_result["before_rankings"]
    assert cycle_result["after_rankings"]


def test_directly_requested_workflow_authorizes_after_validation_without_operator_click(tmp_path, monkeypatch):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    request = (
        "Add a synthetic event signal to Cedar with topic festival, confidence 0.8, "
        "and evidence: local festival coverage shows demand in Cedar Valley."
    )
    workflow_id = create_workflow(path, request)
    plan = WorkflowPlan(
        title="Add Cedar festival signal",
        summary="Record the explicitly requested synthetic signal.",
        actions=[ProposedOperation(
            agent_role="research", tool_name="add_synthetic_signal",
            arguments={
                "region_id": "cedar", "topics": ["festival"], "confidence": 0.8,
                "evidence": "Local festival coverage shows rising demand in Cedar Valley.",
                "source_type": "event",
            },
            rationale="The operator explicitly requested this signal.",
            impact="Adds one pending synthetic event signal.",
        )],
    )
    reports = {
        role: {"report": SpecialistReport(
            role=role, status="complete", label="Reviewed", summary="The request is in scope."
        ).model_dump(mode="json")}
        for role in ("research", "finance", "recommendation", "review")
    }
    reports["planner"] = {"plan": plan.model_dump(mode="json")}

    def fake_post_json(url, payload, timeout=100):
        role = payload.get("role", "")
        return reports[role]

    scheduled = []
    monkeypatch.setattr(orchestration, "_post_json", fake_post_json)
    monkeypatch.setattr(orchestration, "_COORDINATOR_POOL", SimpleNamespace(
        submit=lambda function, *args: scheduled.append((function, args)),
    ))
    service = WorkflowService(path, worker_urls={role: f"http://{role}" for role in orchestration._ROLES})

    service._plan(workflow_id, request)

    workflow = get_workflow(path, workflow_id)
    assert workflow["status"] == "approved"
    assert workflow["approved_by"] == "operator_prompt"
    assert len(scheduled) == 1
    assert scheduled[0][0].__self__ is service
    assert scheduled[0][0].__name__ == "_execute_plan"
    assert any(event["actor"] == "operator_prompt" for event in workflow["events"])


def test_hard_delete_impact_counts_pipeline_cascades_and_preserved_audits(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    region_id = list_regions(path)[0].id
    impact = _hard_delete_impact(path, {"target_type": "region", "target_id": region_id})
    assert "recommendation_items" in impact["dependencies"]
    assert "consumed_signals" in impact["dependencies"]
    assert "evidence_references" in impact["dependencies"]
    assert "retained_signal_deletion_audits" in impact["dependencies"]


def test_specialist_uses_the_configured_shared_model_and_role_specific_schema(monkeypatch):
    captured = {}
    report_payload = {
        "role": "finance", "status": "degraded", "label": "Finance integration degraded",
        "summary": "Atlas is using simulated financial estimates; cfo.ai is not verified.",
        "findings": ["Do not present local estimates as live cfo.ai output."],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            call = SimpleNamespace(function=SimpleNamespace(
                name="submit_specialist_report", arguments=__import__("json").dumps(report_payload),
            ))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])

    monkeypatch.setattr(subagent, "get_llm_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    ))
    monkeypatch.setattr(subagent, "configured_model", lambda: "configured-shared-model")
    result = subagent.handle_task({
        "workflow_id": "workflow-test", "role": "finance", "request": "Review finances.",
        "context": {"finance": {"source": "local_simulation"}},
        "plan": {"title": "Test", "actions": []},
    })

    assert captured["model"] == "configured-shared-model"
    assert captured["tool_choice"]["function"]["name"] == "submit_specialist_report"
    assert captured["tools"][0]["function"]["parameters"]["properties"]["role"]["enum"] == ["finance"]
    assert result["report"]["role"] == "finance"
    assert "Finance integration degraded" == result["report"]["label"]


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        ("Cedar Valley has one pending signal.", ["Cedar Valley has one pending signal."]),
        ('["One finding", "A second finding"]', ["One finding", "A second finding"]),
        (["Already structured"], ["Already structured"]),
    ],
)
def test_specialist_normalizes_provider_findings_shapes(findings, expected):
    report = subagent._normalize_specialist_report("research", {
        "role": "finance",
        "status": "complete",
        "label": "Research review",
        "summary": "Evidence was reviewed.",
        "findings": findings,
        "provider_extra": "ignored safely",
    })

    assert report.role == "research"
    assert report.findings == expected
    assert report.status == "complete"


def test_specialist_normalizes_invalid_report_status_and_empty_text():
    report = subagent._normalize_specialist_report("review", {
        "role": "review",
        "status": "finished",
        "label": " ",
        "summary": None,
        "findings": {"unexpected": "shape"},
    })

    assert report.status == "degraded"
    assert report.label == "Review specialist report"
    assert report.findings == []
