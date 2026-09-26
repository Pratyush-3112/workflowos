"""Workflow schema and closed action vocabulary definitions."""

from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ControlledActionType(str, Enum):
    """Closed action vocabulary allowed for execution in the golden workflow."""
    READ_EMAIL = "READ_EMAIL"
    DOWNLOAD_ATTACHMENT = "DOWNLOAD_ATTACHMENT"
    SEARCH_CUSTOMER = "SEARCH_CUSTOMER"
    UPDATE_CUSTOMER = "UPDATE_CUSTOMER"
    SEND_SLACK_MESSAGE = "SEND_SLACK_MESSAGE"


class CheckType(str, Enum):
    """Post-condition verification check types."""
    EMAIL_OPENED = "EMAIL_OPENED"
    ATTACHMENT_SAVED = "ATTACHMENT_SAVED"
    RECORD_EXISTS = "RECORD_EXISTS"
    FIELD_EQUALS = "FIELD_EQUALS"
    MESSAGE_SENT = "MESSAGE_SENT"


class VerificationRule(BaseModel):
    """Post-condition verification rule attached to an individual action step."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_type: CheckType = Field(..., description="Classification of the state assertion")
    expected_state: Dict[str, Any] = Field(..., description="Expected key-value state attributes")
    description: str = Field(..., min_length=3, description="Human-readable verification assertion statement")


class ActionStep(BaseModel):
    """A discrete, validated action step in a generated workflow."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int = Field(..., ge=1, description="1-indexed sequential step position")
    type: ControlledActionType = Field(..., description="Action type strictly restricted to closed vocabulary")
    params: Dict[str, Any] = Field(default_factory=dict, description="Execution parameters or templates")
    verification: VerificationRule = Field(..., description="Mandatory post-condition verification rule")


class TriggerConfig(BaseModel):
    """Trigger conditions for initiating the automated workflow."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(default="EVENT_TRIGGER", description="Trigger mechanism (e.g. EVENT_TRIGGER, MANUAL)")
    source: str = Field(..., min_length=1, description="Source event signature or application trigger")
    filter_criteria: Dict[str, Any] = Field(default_factory=dict, description="Criteria for triggering execution")


def default_workflow_id() -> str:
    """Generate a unique workflow identifier."""
    return f"wf_{uuid.uuid4().hex[:12]}"


class Workflow(BaseModel):
    """Structured, verified workflow specification."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(default_factory=default_workflow_id, description="Unique workflow identifier")
    name: str = Field(..., min_length=3, description="Human-readable workflow name")
    description: str = Field(..., min_length=5, description="Clear summary of workflow purpose")
    trigger: TriggerConfig = Field(..., description="Workflow trigger definition")
    variables: Dict[str, str] = Field(default_factory=dict, description="Template variables and expected types")
    actions: List[ActionStep] = Field(..., min_length=1, description="Ordered list of action steps")
    conditions: List[str] = Field(default_factory=list, description="Pre-condition guard rails")

    @field_validator("actions")
    @classmethod
    def validate_steps_sequence(cls, steps: List[ActionStep]) -> List[ActionStep]:
        """Validate step numbers are strictly 1, 2, 3... without gaps or duplicate indices."""
        indices = [s.step for s in steps]
        expected = list(range(1, len(steps) + 1))
        if indices != expected:
            raise ValueError(f"Step numbers must be strictly sequential starting at 1. Expected {expected}, got {indices}")
        return steps


if __name__ == "__main__":
    import json

    sample_step = ActionStep(
        step=1,
        type=ControlledActionType.READ_EMAIL,
        params={"email_id": "{{email_id}}"},
        verification=VerificationRule(
            check_type=CheckType.EMAIL_OPENED,
            expected_state={"opened": True},
            description="Assert email message was retrieved and opened",
        ),
    )

    sample_wf = Workflow(
        name="Invoice Customer Sync",
        description="Extract invoices and update CRM customer records",
        trigger=TriggerConfig(source="GMAIL:READ_EMAIL"),
        variables={"email_id": "string", "customer_id": "string"},
        actions=[sample_step],
    )

    print("SUCCESS: Valid Workflow instantiated!")
    print(json.dumps(json.loads(sample_wf.model_dump_json()), indent=2))
