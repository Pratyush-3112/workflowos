"""Activity Event schema definition for WorkFlowOS."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Application(str, Enum):
    """Recognized applications in WorkFlowOS."""
    GMAIL = "GMAIL"
    CRM = "CRM"
    SLACK = "SLACK"
    BROWSER = "BROWSER"
    SYSTEM = "SYSTEM"


class Action(str, Enum):
    """Closed action vocabulary for activities and automated workflow steps."""
    READ_EMAIL = "READ_EMAIL"
    DOWNLOAD_ATTACHMENT = "DOWNLOAD_ATTACHMENT"
    SEARCH_CUSTOMER = "SEARCH_CUSTOMER"
    UPDATE_CUSTOMER = "UPDATE_CUSTOMER"
    SEND_SLACK_MESSAGE = "SEND_SLACK_MESSAGE"
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"


def default_event_id() -> str:
    """Generate a unique event identifier."""
    return f"evt_{uuid.uuid4().hex[:12]}"


def default_utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class ActivityEvent(BaseModel):
    """Normalized, validated activity event."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=default_event_id, description="Unique event identifier")
    timestamp: datetime = Field(default_factory=default_utc_now, description="Event occurrence timestamp (UTC)")
    application: Application = Field(..., description="Target application context")
    action: Action = Field(..., description="Action taken from closed action vocabulary")
    target: str = Field(..., min_length=1, description="Primary entity or resource target")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Action-specific structured metadata")

    @field_validator("target")
    @classmethod
    def validate_target_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("target must not be blank")
        return trimmed

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("metadata must be a dictionary")
        return value


if __name__ == "__main__":
    import json

    # Create a valid sample event
    sample_event = ActivityEvent(
        application=Application.GMAIL,
        action=Action.READ_EMAIL,
        target="msg_invoice_109",
        metadata={"subject": "Vendor Invoice", "sender": "billing@acme.corp"},
    )
    print("SUCCESS: ActivityEvent created and validated successfully!")
    print(json.dumps(json.loads(sample_event.model_dump_json()), indent=2))

