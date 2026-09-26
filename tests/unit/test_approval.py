"""Unit tests for approval lifecycle, consent store, and tamper protection."""

import pytest

from backend.workflows.approval import (
    ApprovalRecord,
    ApprovalStatus,
    ApprovalStore,
    UnapprovedExecutionError,
    compute_workflow_hash,
)
from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)


@pytest.fixture
def sample_workflow():
    return Workflow(
        name="Invoice Processing",
        description="Extract and notify",
        trigger=TriggerConfig(source="GMAIL:READ_EMAIL"),
        variables={"customer_id": "string"},
        actions=[
            ActionStep(
                step=1,
                type=ControlledActionType.SEARCH_CUSTOMER,
                params={"customer_id": "{{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.RECORD_EXISTS,
                    expected_state={"exists": True},
                    description="Customer verified in CRM",
                ),
            )
        ],
    )


def test_approval_request_lifecycle(sample_workflow):
    store = ApprovalStore()
    req = store.request_approval(sample_workflow, requested_by="discovery_engine")

    assert req.status == ApprovalStatus.PENDING
    assert req.workflow_id == sample_workflow.workflow_id
    assert len(req.workflow_hash) == 64

    # Execution is blocked while PENDING
    is_auth, msg, _ = store.verify_authorization(sample_workflow)
    assert is_auth is False
    assert "cannot run: approval status is 'PENDING'" in msg


def test_approval_grant(sample_workflow):
    store = ApprovalStore()
    req = store.request_approval(sample_workflow)

    approved = store.approve(
        req.approval_id,
        approved_by="pratyush@company.com",
        parameters={"customer_id": "cust_456"},
    )
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.decided_by == "pratyush@company.com"
    assert approved.approved_parameters["customer_id"] == "cust_456"

    # Execution is authorized
    is_auth, msg, record = store.verify_authorization(sample_workflow)
    assert is_auth is True
    assert "Workflow approved by 'pratyush@company.com'" in msg
    assert record.approval_id == req.approval_id


def test_approval_reject(sample_workflow):
    store = ApprovalStore()
    req = store.request_approval(sample_workflow)

    rejected = store.reject(
        req.approval_id,
        rejected_by="admin@company.com",
        reason="Target CRM field requires manual review first.",
    )
    assert rejected.status == ApprovalStatus.REJECTED
    assert "requires manual review" in (rejected.notes or "")

    # Execution is blocked
    is_auth, msg, _ = store.verify_authorization(sample_workflow)
    assert is_auth is False
    assert "approval status is 'REJECTED'" in msg


def test_approval_revoke(sample_workflow):
    store = ApprovalStore()
    req = store.request_approval(sample_workflow)
    store.approve(req.approval_id, approved_by="user@company.com")

    # Now revoke
    revoked = store.revoke(
        req.approval_id,
        revoked_by="security_team",
        reason="Security audit ongoing",
    )
    assert revoked.status == ApprovalStatus.REVOKED

    # Execution is blocked
    is_auth, msg, _ = store.verify_authorization(sample_workflow)
    assert is_auth is False
    assert "approval status is 'REVOKED'" in msg


def test_tamper_detection_blocks_execution(sample_workflow):
    store = ApprovalStore()
    req = store.request_approval(sample_workflow)
    store.approve(req.approval_id, approved_by="user@company.com")

    # Tamper with the workflow object (e.g. inject an altered condition or action)
    tampered_workflow = sample_workflow.model_copy(
        update={"description": "Altered description after approval granted!"}
    )

    is_auth, msg, _ = store.verify_authorization(tampered_workflow)
    assert is_auth is False
    assert "was altered after user approval was granted" in msg
    assert "Execution strictly blocked" in msg


def test_assert_authorized_raises_unapproved_error(sample_workflow):
    store = ApprovalStore()
    with pytest.raises(UnapprovedExecutionError) as exc_info:
        store.assert_authorized(sample_workflow)
    assert "has no recorded approval request" in str(exc_info.value)
