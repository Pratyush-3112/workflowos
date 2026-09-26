"""Approval record schema, consent store, and execution gatekeeper."""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from pydantic import BaseModel, ConfigDict, Field

from backend.workflows.schema import Workflow


class ApprovalStatus(str, Enum):
    """Lifecycle status of a workflow approval request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class UnapprovedExecutionError(PermissionError):
    """Raised when an attempt is made to execute a workflow without recorded user consent."""

    def __init__(self, message: str, workflow_id: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.workflow_id = workflow_id
        self.details = details or {}


def compute_workflow_hash(workflow: Workflow) -> str:
    """Compute deterministic SHA-256 hash of a Workflow to detect any post-approval tampering."""
    canonical_json = json.dumps(
        json.loads(workflow.model_dump_json()),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def default_approval_id() -> str:
    """Generate unique approval request identifier."""
    return f"appr_{uuid.uuid4().hex[:12]}"


def default_utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class ApprovalRecord(BaseModel):
    """Immutable audit record of user consent for an automated workflow."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(default_factory=default_approval_id, description="Unique approval identifier")
    workflow_id: str = Field(..., description="Target workflow ID")
    workflow_hash: str = Field(..., description="SHA-256 hash of workflow at approval time (tamper protection)")
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING, description="Current approval status")
    requested_by: str = Field(default="system", description="Entity requesting execution approval")
    requested_at: datetime = Field(default_factory=default_utc_now, description="Timestamp of approval request")
    decided_by: Optional[str] = Field(default=None, description="User who approved or rejected the workflow")
    decided_at: Optional[datetime] = Field(default=None, description="Timestamp of decision")
    approved_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="User-approved execution parameter values or overrides",
    )
    notes: Optional[str] = Field(default=None, description="Reason for decision or rejection explanation")


class ApprovalStore:
    """Store managing user approval lifecycle and enforcing execution authorization."""

    def __init__(self):
        self._records: Dict[str, ApprovalRecord] = {}
        self._workflow_index: Dict[str, str] = {}  # workflow_id -> latest approval_id

    def request_approval(self, workflow: Workflow, requested_by: str = "system") -> ApprovalRecord:
        """Create a pending approval request for a generated Workflow."""
        if not isinstance(workflow, Workflow):
            raise TypeError(f"Expected Workflow instance, got {type(workflow).__name__}")

        wf_hash = compute_workflow_hash(workflow)
        record = ApprovalRecord(
            workflow_id=workflow.workflow_id,
            workflow_hash=wf_hash,
            status=ApprovalStatus.PENDING,
            requested_by=requested_by,
        )
        self._records[record.approval_id] = record
        self._workflow_index[workflow.workflow_id] = record.approval_id
        return record

    def approve(
        self,
        approval_id: str,
        approved_by: str,
        parameters: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> ApprovalRecord:
        """Record explicit user approval for a workflow."""
        existing = self.get(approval_id)
        if not existing:
            raise KeyError(f"Approval request '{approval_id}' not found.")
        if existing.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot approve request with status '{existing.status.value}'.")

        updated = ApprovalRecord(
            approval_id=existing.approval_id,
            workflow_id=existing.workflow_id,
            workflow_hash=existing.workflow_hash,
            status=ApprovalStatus.APPROVED,
            requested_by=existing.requested_by,
            requested_at=existing.requested_at,
            decided_by=approved_by,
            decided_at=datetime.now(timezone.utc),
            approved_parameters=parameters or {},
            notes=notes or "Approved by user.",
        )
        self._records[approval_id] = updated
        return updated

    def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str,
    ) -> ApprovalRecord:
        """Record explicit user rejection for a workflow."""
        existing = self.get(approval_id)
        if not existing:
            raise KeyError(f"Approval request '{approval_id}' not found.")
        if existing.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot reject request with status '{existing.status.value}'.")

        updated = ApprovalRecord(
            approval_id=existing.approval_id,
            workflow_id=existing.workflow_id,
            workflow_hash=existing.workflow_hash,
            status=ApprovalStatus.REJECTED,
            requested_by=existing.requested_by,
            requested_at=existing.requested_at,
            decided_by=rejected_by,
            decided_at=datetime.now(timezone.utc),
            approved_parameters={},
            notes=reason,
        )
        self._records[approval_id] = updated
        return updated

    def revoke(
        self,
        approval_id: str,
        revoked_by: str,
        reason: str,
    ) -> ApprovalRecord:
        """Revoke a previously granted approval."""
        existing = self.get(approval_id)
        if not existing:
            raise KeyError(f"Approval request '{approval_id}' not found.")
        if existing.status != ApprovalStatus.APPROVED:
            raise ValueError(f"Cannot revoke request with status '{existing.status.value}'.")

        updated = ApprovalRecord(
            approval_id=existing.approval_id,
            workflow_id=existing.workflow_id,
            workflow_hash=existing.workflow_hash,
            status=ApprovalStatus.REVOKED,
            requested_by=existing.requested_by,
            requested_at=existing.requested_at,
            decided_by=revoked_by,
            decided_at=datetime.now(timezone.utc),
            approved_parameters=existing.approved_parameters,
            notes=f"Revoked: {reason}",
        )
        self._records[approval_id] = updated
        return updated

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        """Fetch approval record by ID."""
        return self._records.get(approval_id)

    def get_by_workflow_id(self, workflow_id: str) -> Optional[ApprovalRecord]:
        """Fetch latest approval record for a workflow."""
        appr_id = self._workflow_index.get(workflow_id)
        if not appr_id:
            return None
        return self._records.get(appr_id)

    def verify_authorization(
        self,
        workflow: Workflow,
    ) -> Tuple[bool, str, Optional[ApprovalRecord]]:
        """Verify whether a workflow has explicit, un-tampered user approval.
        
        Returns:
            (is_authorized, detail_message, approval_record)
        """
        record = self.get_by_workflow_id(workflow.workflow_id)
        if not record:
            return False, f"Workflow '{workflow.workflow_id}' has no recorded approval request.", None

        if record.status != ApprovalStatus.APPROVED:
            return False, (
                f"Workflow '{workflow.workflow_id}' cannot run: approval status is '{record.status.value}' "
                f"(notes: {record.notes or 'None'})."
            ), record

        # Tamper detection: verify hash matches current workflow state
        current_hash = compute_workflow_hash(workflow)
        if current_hash != record.workflow_hash:
            return False, (
                f"Workflow '{workflow.workflow_id}' was altered after user approval was granted! "
                f"Recorded hash '{record.workflow_hash[:12]}...', current hash '{current_hash[:12]}...'. "
                f"Execution strictly blocked."
            ), record

        return True, f"Workflow approved by '{record.decided_by}' on {record.decided_at.isoformat()}.", record

    def assert_authorized(self, workflow: Workflow) -> ApprovalRecord:
        """Assert workflow has valid recorded approval, or raise UnapprovedExecutionError."""
        authorized, message, record = self.verify_authorization(workflow)
        if not authorized:
            raise UnapprovedExecutionError(
                message=message,
                workflow_id=workflow.workflow_id,
                details={"record": record.model_dump() if record else None},
            )
        return record  # type: ignore


if __name__ == "__main__":
    from backend.workflows.schema import (
        ActionStep,
        CheckType,
        ControlledActionType,
        TriggerConfig,
        VerificationRule,
    )

    wf = Workflow(
        name="Sync Customer Data",
        description="Syncs Gmail to CRM and Slack",
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

    store = ApprovalStore()
    req = store.request_approval(wf, requested_by="ai_generator")
    print(f"1. Request created: ID={req.approval_id}, Status={req.status.value}")

    # Check execution before approval
    auth, msg, _ = store.verify_authorization(wf)
    print(f"2. Execution before approval: Authorized={auth} -> {msg}")

    # Approve
    store.approve(req.approval_id, approved_by="alice@company.com", parameters={"customer_id": "cust_999"})
    auth, msg, _ = store.verify_authorization(wf)
    print(f"3. Execution after approval:  Authorized={auth} -> {msg}")
