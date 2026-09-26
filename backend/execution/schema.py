"""Execution and post-condition verification schemas."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field

from backend.workflows.schema import ControlledActionType


class StepExecutionStatus(str, Enum):
    """Status of an individual step execution."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class WorkflowRunStatus(str, Enum):
    """Overall status of a workflow run."""
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


def default_execution_id() -> str:
    """Generate unique execution identifier."""
    return f"exec_{uuid.uuid4().hex[:12]}"


def default_utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class VerificationResult(BaseModel):
    """Computed post-condition verification result for a single action step."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int = Field(..., ge=1, description="Step number")
    action_type: ControlledActionType = Field(..., description="Action executed from closed vocabulary")
    status: StepExecutionStatus = Field(..., description="Execution status (SUCCESS, FAILED, SKIPPED)")
    expected_state: Dict[str, Any] = Field(..., description="State attributes expected after execution")
    actual_state: Dict[str, Any] = Field(..., description="Real, computed state inspected after execution")
    verified: bool = Field(..., description="True only if actual state satisfies expected state")
    detail: str = Field(..., min_length=3, description="Human-readable verification narrative")


class WorkflowExecutionResult(BaseModel):
    """Audit report of a complete workflow execution timeline."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(default_factory=default_execution_id, description="Unique execution run ID")
    workflow_id: str = Field(..., description="Executed workflow ID")
    status: WorkflowRunStatus = Field(..., description="Final run status (SUCCESS, FAILED, PAUSED)")
    started_at: datetime = Field(default_factory=default_utc_now, description="Run start timestamp")
    completed_at: Optional[datetime] = Field(default=None, description="Run completion timestamp")
    step_results: List[VerificationResult] = Field(default_factory=list, description="Per-step verification timeline")
    error_message: Optional[str] = Field(default=None, description="Clear failure diagnostic if execution halted")
