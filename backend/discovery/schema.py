"""Workflow Candidate schema for discovered repetitive sequences."""

from typing import List
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def default_candidate_id() -> str:
    """Generate unique candidate workflow identifier."""
    return f"cand_{uuid.uuid4().hex[:12]}"


class WorkflowCandidate(BaseModel):
    """Candidate workflow discovered from repeating sequences in the event store."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(default_factory=default_candidate_id, description="Unique candidate ID")
    signature: str = Field(..., min_length=3, description="Canonical action chain signature")
    action_pattern: List[str] = Field(..., min_length=2, description="List of (Application:Action) keys")
    event_sequence: List[str] = Field(..., min_length=2, description="Event IDs of the latest exemplar run")
    occurrences: int = Field(..., ge=2, description="Number of times this sequence repeated in history")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score [0.0, 1.0]")
    instance_sequences: List[List[str]] = Field(
        ...,
        min_length=2,
        description="All recorded event ID sequences matching this candidate",
    )

    @field_validator("instance_sequences")
    @classmethod
    def validate_occurrences_match(cls, instances: List[List[str]], info) -> List[List[str]]:
        occurrences = info.data.get("occurrences")
        if occurrences is not None and len(instances) != occurrences:
            raise ValueError(f"instance_sequences count ({len(instances)}) must equal occurrences ({occurrences})")
        return instances
