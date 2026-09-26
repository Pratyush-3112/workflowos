"""Unit and integration tests for deliberate mid-workflow failure and safe pause behavior."""

from fastapi.testclient import TestClient
import pytest

from backend.api.main import app
from backend.execution.engine import AutomationEngine
from backend.execution.schema import StepExecutionStatus, WorkflowRunStatus
from backend.workflows.approval import ApprovalStore
from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)


@pytest.fixture
def test_workflow():
    return Workflow(
        name="Invoice Customer Sync",
        description="Extract and notify",
        trigger=TriggerConfig(source="GMAIL:READ_EMAIL"),
        variables={
            "email_id": "string",
            "attachment_id": "string",
            "customer_id": "string",
            "slack_channel": "string",
        },
        actions=[
            ActionStep(
                step=1,
                type=ControlledActionType.READ_EMAIL,
                params={"email_id": "{{email_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.EMAIL_OPENED,
                    expected_state={"opened": True},
                    description="Customer email retrieved",
                ),
            ),
            ActionStep(
                step=2,
                type=ControlledActionType.DOWNLOAD_ATTACHMENT,
                params={"attachment_id": "{{attachment_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.ATTACHMENT_SAVED,
                    expected_state={"saved": True},
                    description="Attachment downloaded",
                ),
            ),
            ActionStep(
                step=3,
                type=ControlledActionType.SEARCH_CUSTOMER,
                params={"customer_id": "{{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.RECORD_EXISTS,
                    expected_state={"exists": True},
                    description="Customer found in CRM",
                ),
            ),
            ActionStep(
                step=4,
                type=ControlledActionType.UPDATE_CUSTOMER,
                params={"customer_id": "{{customer_id}}", "fields_to_update": {"invoice_status": "PROCESSED"}},
                verification=VerificationRule(
                    check_type=CheckType.FIELD_EQUALS,
                    expected_state={"invoice_status": "PROCESSED"},
                    description="Customer marked PROCESSED in CRM",
                ),
            ),
            ActionStep(
                step=5,
                type=ControlledActionType.SEND_SLACK_MESSAGE,
                params={"channel": "{{slack_channel}}", "message": "Synced invoice"},
                verification=VerificationRule(
                    check_type=CheckType.MESSAGE_SENT,
                    expected_state={"delivered": True},
                    description="Team notified in Slack",
                ),
            ),
        ],
    )


def test_deliberate_failure_at_step_4(test_workflow):
    store = ApprovalStore()
    req = store.request_approval(test_workflow)
    store.approve(
        req.approval_id,
        approved_by="tester",
        parameters={
            "email_id": "email_demo_1",
            "attachment_id": "att_inv_001",
            "customer_id": "cust_acme_corp",
            "slack_channel": "#billing-alerts",
        },
    )

    engine = AutomationEngine(approval_store=store)

    # Inject failure where CRM fails to update and field remains PENDING
    result = engine.execute(
        test_workflow,
        failure_injection={
            "fail_at_step": 4,
            "simulated_actual_state": {"invoice_status": "PENDING"},
        },
    )

    # 1. Overall status must be PAUSED
    assert result.status == WorkflowRunStatus.PAUSED
    assert "verification failed" in (result.error_message or "")

    # 2. Steps 1-3 succeeded
    for k in range(3):
        assert result.step_results[k].status == StepExecutionStatus.SUCCESS
        assert result.step_results[k].verified is True

    # 3. Step 4 failed with exact computed mismatch
    step4 = result.step_results[3]
    assert step4.step == 4
    assert step4.status == StepExecutionStatus.FAILED
    assert step4.verified is False
    assert "Expected state 'invoice_status' to be 'PROCESSED', but real state was 'PENDING'" in step4.detail

    # 4. Step 5 skipped
    step5 = result.step_results[4]
    assert step5.step == 5
    assert step5.status == StepExecutionStatus.SKIPPED
    assert step5.verified is False
    assert "skipped due to prior step failure" in step5.detail


def test_deliberate_failure_at_step_2_attachment(test_workflow):
    store = ApprovalStore()
    req = store.request_approval(test_workflow)
    store.approve(req.approval_id, approved_by="tester")

    engine = AutomationEngine(approval_store=store)
    result = engine.execute(
        test_workflow,
        failure_injection={
            "fail_at_step": 2,
            "simulated_actual_state": {"saved": False},
        },
    )

    assert result.status == WorkflowRunStatus.PAUSED
    assert result.step_results[0].status == StepExecutionStatus.SUCCESS
    assert result.step_results[1].status == StepExecutionStatus.FAILED
    assert result.step_results[1].verified is False
    assert result.step_results[2].status == StepExecutionStatus.SKIPPED
    assert result.step_results[3].status == StepExecutionStatus.SKIPPED
    assert result.step_results[4].status == StepExecutionStatus.SKIPPED


def test_api_simulate_failure_endpoint():
    client = TestClient(app)

    # 1. Seed runs and generate workflow
    client.post("/api/simulate-golden-runs?runs_count=3")
    cand = client.get("/api/candidates").json()[0]
    wf_data = client.post(f"/api/candidates/{cand['candidate_id']}/generate-workflow").json()
    wf_id = wf_data["workflow"]["workflow_id"]

    # 2. Call simulate-failure endpoint
    res = client.post(
        f"/api/workflows/{wf_id}/simulate-failure",
        json={"fail_at_step": 4, "simulated_actual_state": {"invoice_status": "PENDING"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "PAUSED"
    exec_data = body["execution"]
    assert exec_data["step_results"][3]["status"] == "FAILED"
    assert exec_data["step_results"][4]["status"] == "SKIPPED"
