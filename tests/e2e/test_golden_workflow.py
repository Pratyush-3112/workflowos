"""End-to-End integration test of the complete Golden Workflow loop."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from backend.ai.generator import WorkflowGenerator
from backend.ai.understanding import IntentInferer
from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector
from backend.execution.connectors.crm_mock import MockCRMConnector
from backend.execution.connectors.email_mock import MockEmailConnector
from backend.execution.connectors.slack import SlackConnector
from backend.execution.engine import AutomationEngine
from backend.execution.schema import StepExecutionStatus, WorkflowRunStatus
from backend.execution.timeline import TimelineStore
from backend.workflows.approval import ApprovalStore, UnapprovedExecutionError
from backend.workflows.schema import ControlledActionType
from backend.workflows.validator import WorkflowValidator


def test_e2e_complete_golden_workflow_success_loop(tmp_path: Path):
    """E2E Test: Observe (3 runs) -> Detect Repetition -> Infer Intent -> Generate -> Approve -> Automate -> Verify."""
    storage_path = tmp_path / "e2e_events.jsonl"
    capture_service = CaptureService(persistence_path=storage_path)
    t0 = datetime.now(timezone.utc)

    # 1. OBSERVE: Simulate 3 runs of digital activity (15 raw actions)
    for r in range(1, 4):
        run_time = t0 + timedelta(minutes=r * 5)
        raw_actions = [
            {"app": "Gmail", "action": "open_email", "target": f"email_{r}", "metadata": {"subject": f"Invoice #{r}"}, "timestamp": run_time},
            {"app": "Gmail", "action": "download", "target": f"invoice_{r}.pdf", "timestamp": run_time + timedelta(seconds=1)},
            {"app": "CRM", "action": "search_customer", "target": f"cust_{r}", "timestamp": run_time + timedelta(seconds=2)},
            {"app": "CRM", "action": "update_customer", "target": f"cust_{r}", "metadata": {"invoice_status": "PROCESSED"}, "timestamp": run_time + timedelta(seconds=3)},
            {"app": "Slack", "action": "send_slack", "target": "#billing-alerts", "metadata": {"message": f"Processed #{r}"}, "timestamp": run_time + timedelta(seconds=4)},
        ]
        capture_service.record_batch(raw_actions)

    assert len(capture_service.store) == 15
    is_valid_store, store_msg = capture_service.verify_integrity()
    assert is_valid_store is True
    assert "Integrity verified across 15 events" in store_msg

    # 2. DETECT REPETITION: Deterministic n-gram discovery
    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(capture_service.store)
    assert len(candidates) >= 1

    top_candidate = candidates[0]
    expected_sig = (
        "GMAIL:READ_EMAIL -> GMAIL:DOWNLOAD_ATTACHMENT -> "
        "CRM:SEARCH_CUSTOMER -> CRM:UPDATE_CUSTOMER -> SLACK:SEND_SLACK_MESSAGE"
    )
    assert top_candidate.signature == expected_sig
    assert top_candidate.occurrences == 3
    assert top_candidate.confidence == 0.80

    # Prove 0 hallucinated history
    is_cand_valid, cand_msg = RepetitionDetector.verify_candidate_in_store(top_candidate, capture_service.store)
    assert is_cand_valid is True

    # 3. UNDERSTAND: AI Intent Inferer
    inferer = IntentInferer()
    intent, was_fallback_intent, _ = inferer.infer_intent(top_candidate, capture_service.store)
    assert len(intent.intent_label) >= 3
    assert intent.trigger_event_type == "GMAIL:READ_EMAIL"

    # 4. GENERATE: AI Workflow Generator
    generator = WorkflowGenerator()
    workflow, was_fallback_wf, _ = generator.generate_workflow(top_candidate, intent, capture_service.store)
    assert len(workflow.actions) == 5

    # 5. VALIDATE: WorkflowValidator assertion
    validated_wf = WorkflowValidator.assert_valid(workflow)
    assert validated_wf.workflow_id == workflow.workflow_id

    # 6. APPROVAL GATE: Consent Store
    approval_store = ApprovalStore()
    appr_req = approval_store.request_approval(workflow)
    assert appr_req.status.value == "PENDING"

    # Shared connectors
    email_conn = MockEmailConnector()
    crm_conn = MockCRMConnector()
    slack_conn = SlackConnector()
    timeline_store = TimelineStore()
    engine = AutomationEngine(
        approval_store=approval_store,
        email_connector=email_conn,
        crm_connector=crm_conn,
        slack_connector=slack_conn,
    )

    # Engine MUST refuse to execute without approval
    with pytest.raises(UnapprovedExecutionError):
        engine.execute(workflow)

    # User explicitly grants approval with live 4th run parameters
    approval_store.approve(
        appr_req.approval_id,
        approved_by="director_of_ops@company.com",
        parameters={
            "email_id": "email_demo_1",
            "attachment_id": "att_inv_001",
            "customer_id": "cust_acme_corp",
            "slack_channel": "#billing-alerts",
        },
    )

    # 7. AUTOMATE & VERIFY: Live 4th execution with computed state verification
    exec_result = engine.execute(workflow)
    timeline_store.record_execution(exec_result)

    assert exec_result.status == WorkflowRunStatus.SUCCESS
    assert len(exec_result.step_results) == 5

    # Assert every single step was verified against ground truth state
    for step_res in exec_result.step_results:
        assert step_res.status == StepExecutionStatus.SUCCESS
        assert step_res.verified is True
        assert len(step_res.detail) > 0

    # 8. AUDIT: Ground truth external state confirmation
    assert email_conn.get_email_state("email_demo_1")["opened"] is True
    assert email_conn.is_attachment_saved("att_inv_001") is True
    assert crm_conn.get_customer("cust_acme_corp")["invoice_status"] == "PROCESSED"
    assert len(slack_conn.get_delivered_messages("#billing-alerts")) == 1

    # Timeline store has complete verifiable audit
    assert len(timeline_store) == 1
    assert timeline_store.get_execution(exec_result.execution_id) is not None


def test_e2e_deliberate_failure_and_safe_pause(tmp_path: Path):
    """E2E Test: Mid-workflow verification failure safely pauses execution and skips subsequent steps."""
    storage_path = tmp_path / "e2e_events_fail.jsonl"
    capture_service = CaptureService(persistence_path=storage_path)

    # Seed repetitions
    for r in range(2):
        capture_service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"e{r}"},
            {"app": "crm", "action": "update_customer", "target": f"c{r}"},
            {"app": "slack", "action": "send_slack", "target": f"s{r}"},
        ])

    candidates = RepetitionDetector(min_sequence_length=2, min_occurrences=2).detect_candidates(capture_service.store)
    intent, _, _ = IntentInferer().infer_intent(candidates[0], capture_service.store)
    workflow, _, _ = WorkflowGenerator().generate_workflow(candidates[0], intent, capture_service.store)

    approval_store = ApprovalStore()
    req = approval_store.request_approval(workflow)
    approval_store.approve(req.approval_id, approved_by="user")

    slack_conn = SlackConnector()
    engine = AutomationEngine(approval_store=approval_store, slack_connector=slack_conn)

    # Inject failure at CRM update step (Step 2)
    result = engine.execute(
        workflow,
        failure_injection={
            "fail_at_step": 2,
            "simulated_actual_state": {"invoice_status": "PENDING"},
        },
    )

    # 1. State must be PAUSED
    assert result.status == WorkflowRunStatus.PAUSED
    assert "verification failed" in (result.error_message or "")

    # 2. Step 1 succeeded, Step 2 failed, Step 3 skipped
    assert result.step_results[0].status == StepExecutionStatus.SUCCESS
    assert result.step_results[0].verified is True

    assert result.step_results[1].status == StepExecutionStatus.FAILED
    assert result.step_results[1].verified is False

    assert result.step_results[2].status == StepExecutionStatus.SKIPPED
    assert result.step_results[2].verified is False

    # 3. Critical verification: Step 3 Slack message was NEVER delivered
    assert len(slack_conn.get_delivered_messages()) == 0
