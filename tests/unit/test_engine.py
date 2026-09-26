"""Unit tests for AutomationEngine, connectors, and per-step post-condition verification."""

import pytest

from backend.execution.connectors.crm_mock import MockCRMConnector
from backend.execution.connectors.email_mock import MockEmailConnector
from backend.execution.connectors.slack import SlackConnector
from backend.execution.engine import AutomationEngine
from backend.execution.schema import StepExecutionStatus, WorkflowRunStatus
from backend.workflows.approval import ApprovalStore, UnapprovedExecutionError
from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)


@pytest.fixture
def golden_workflow():
    return Workflow(
        name="Sync Invoices to CRM & Slack",
        description="Extract invoice from email, update customer in CRM, notify Slack",
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
                    description="Assert email message was opened",
                ),
            ),
            ActionStep(
                step=2,
                type=ControlledActionType.DOWNLOAD_ATTACHMENT,
                params={"attachment_id": "{{attachment_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.ATTACHMENT_SAVED,
                    expected_state={"saved": True},
                    description="Assert attachment was saved",
                ),
            ),
            ActionStep(
                step=3,
                type=ControlledActionType.SEARCH_CUSTOMER,
                params={"customer_id": "{{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.RECORD_EXISTS,
                    expected_state={"exists": True},
                    description="Assert customer exists in CRM",
                ),
            ),
            ActionStep(
                step=4,
                type=ControlledActionType.UPDATE_CUSTOMER,
                params={
                    "customer_id": "{{customer_id}}",
                    "fields_to_update": {"invoice_status": "PROCESSED"},
                },
                verification=VerificationRule(
                    check_type=CheckType.FIELD_EQUALS,
                    expected_state={"invoice_status": "PROCESSED"},
                    description="Assert customer status changed to PROCESSED",
                ),
            ),
            ActionStep(
                step=5,
                type=ControlledActionType.SEND_SLACK_MESSAGE,
                params={
                    "channel": "{{slack_channel}}",
                    "message": "Processed invoice for customer {{customer_id}}",
                },
                verification=VerificationRule(
                    check_type=CheckType.MESSAGE_SENT,
                    expected_state={"delivered": True},
                    description="Assert notification was delivered to Slack",
                ),
            ),
        ],
    )


def test_engine_golden_workflow_success_and_verification(golden_workflow):
    crm = MockCRMConnector()
    email = MockEmailConnector()
    slack = SlackConnector()
    approval_store = ApprovalStore()

    req = approval_store.request_approval(golden_workflow)
    approval_store.approve(
        req.approval_id,
        approved_by="demo_tester",
        parameters={
            "email_id": "email_demo_1",
            "attachment_id": "att_inv_001",
            "customer_id": "cust_acme_corp",
            "slack_channel": "#billing-alerts",
        },
    )

    engine = AutomationEngine(
        approval_store=approval_store,
        email_connector=email,
        crm_connector=crm,
        slack_connector=slack,
    )

    result = engine.execute(golden_workflow)

    # 1. Assert overall status
    assert result.status == WorkflowRunStatus.SUCCESS
    assert len(result.step_results) == 5

    # 2. Assert every step was verified
    for step_res in result.step_results:
        assert step_res.status == StepExecutionStatus.SUCCESS
        assert step_res.verified is True
        assert len(step_res.detail) > 0

    # 3. Assert real state mutations in connectors
    assert email.get_email_state("email_demo_1")["opened"] is True
    assert email.is_attachment_saved("att_inv_001") is True
    assert crm.get_customer("cust_acme_corp")["invoice_status"] == "PROCESSED"
    assert len(slack.get_delivered_messages("#billing-alerts")) == 1


def test_engine_blocks_unapproved_workflow(golden_workflow):
    engine = AutomationEngine()  # Empty approval store
    with pytest.raises(UnapprovedExecutionError) as exc_info:
        engine.execute(golden_workflow)
    assert "no recorded approval request" in str(exc_info.value)


def test_engine_blocks_tampered_workflow(golden_workflow):
    store = ApprovalStore()
    req = store.request_approval(golden_workflow)
    store.approve(req.approval_id, approved_by="admin")

    # Tamper with action params after approval
    tampered = golden_workflow.model_copy(
        update={"description": "Tampered description after approval"}
    )
    engine = AutomationEngine(approval_store=store)

    with pytest.raises(UnapprovedExecutionError) as exc_info:
        engine.execute(tampered)
    assert "was altered after user approval was granted" in str(exc_info.value)


def test_engine_detects_step_failure_and_skips_remaining(golden_workflow):
    crm = MockCRMConnector()
    approval_store = ApprovalStore()

    # Pre-seed a customer who is locked or mock a verification failure
    # If the step expects invoice_status to be 'PROCESSED', but CRM returns 'REJECTED'
    crm.update_customer = lambda cust_id, fields: {"invoice_status": "REJECTED"}  # type: ignore

    req = approval_store.request_approval(golden_workflow)
    approval_store.approve(
        req.approval_id,
        approved_by="admin",
        parameters={"email_id": "e1", "attachment_id": "a1", "customer_id": "c1", "slack_channel": "#dev"},
    )

    engine = AutomationEngine(approval_store=approval_store, crm_connector=crm)
    result = engine.execute(golden_workflow)

    # Overall execution must PAUSE safely
    assert result.status == WorkflowRunStatus.PAUSED
    assert "verification failed" in (result.error_message or "")

    # Steps 1, 2, 3 should have succeeded
    assert result.step_results[0].status == StepExecutionStatus.SUCCESS
    assert result.step_results[1].status == StepExecutionStatus.SUCCESS
    assert result.step_results[2].status == StepExecutionStatus.SUCCESS

    # Step 4 failed verification
    assert result.step_results[3].status == StepExecutionStatus.FAILED
    assert result.step_results[3].verified is False
    assert "Expected state 'invoice_status' to be 'PROCESSED', but real state was 'REJECTED'" in result.step_results[3].detail

    # Step 5 was skipped
    assert result.step_results[4].status == StepExecutionStatus.SKIPPED
    assert result.step_results[4].verified is False
    assert "skipped due to prior step failure" in result.step_results[4].detail
