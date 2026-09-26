"""Schema definitions for AI intent inference."""

from typing import Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


def default_intent_id() -> str:
    """Generate a unique intent identifier."""
    return f"intent_{uuid.uuid4().hex[:12]}"


class WorkflowIntent(BaseModel):
    """Structured understanding of user intent inferred from repeated candidate actions."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: str = Field(default_factory=default_intent_id, description="Unique intent identifier")
    source_candidate_id: str = Field(..., description="ID of the source WorkflowCandidate")
    intent_label: str = Field(..., min_length=3, description="Concise human title (e.g. 'Sync Invoices to CRM & Slack')")
    summary: str = Field(..., min_length=10, description="Detailed explanation of the user's business goal")
    trigger_event_type: str = Field(..., min_length=3, description="Event that starts this intent (e.g. 'GMAIL:READ_EMAIL')")
    suggested_variables: Dict[str, str] = Field(
        default_factory=dict,
        description="Variables identified as dynamic across repetitions (e.g. {'customer_id': 'CRM customer ID'})",
    )
    confidence_rationale: str = Field(..., min_length=5, description="Explanation for inferred confidence score")
