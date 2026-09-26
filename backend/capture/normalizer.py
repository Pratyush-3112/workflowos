"""Normalizer converting arbitrary raw action dictionaries into verified ActivityEvent instances."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.events.schema import Action, ActivityEvent, Application


class NormalizationError(ValueError):
    """Raised when raw activity data cannot be normalized into a valid ActivityEvent."""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        received_value: Any = None,
        allowed_values: Optional[List[str]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.field = field
        self.received_value = received_value
        self.allowed_values = allowed_values or []


APPLICATION_MAP: Dict[str, Application] = {
    "gmail": Application.GMAIL,
    "email": Application.GMAIL,
    "mail": Application.GMAIL,
    "crm": Application.CRM,
    "hubspot": Application.CRM,
    "salesforce": Application.CRM,
    "slack": Application.SLACK,
    "browser": Application.BROWSER,
    "chrome": Application.BROWSER,
    "system": Application.SYSTEM,
}

ACTION_MAP: Dict[str, Action] = {
    "read_email": Action.READ_EMAIL,
    "open_email": Action.READ_EMAIL,
    "email_read": Action.READ_EMAIL,
    "read": Action.READ_EMAIL,
    "download_attachment": Action.DOWNLOAD_ATTACHMENT,
    "download": Action.DOWNLOAD_ATTACHMENT,
    "save_attachment": Action.DOWNLOAD_ATTACHMENT,
    "search_customer": Action.SEARCH_CUSTOMER,
    "find_customer": Action.SEARCH_CUSTOMER,
    "search_crm": Action.SEARCH_CUSTOMER,
    "search": Action.SEARCH_CUSTOMER,
    "update_customer": Action.UPDATE_CUSTOMER,
    "edit_customer": Action.UPDATE_CUSTOMER,
    "modify_customer": Action.UPDATE_CUSTOMER,
    "update_crm": Action.UPDATE_CUSTOMER,
    "send_slack_message": Action.SEND_SLACK_MESSAGE,
    "send_slack": Action.SEND_SLACK_MESSAGE,
    "slack_message": Action.SEND_SLACK_MESSAGE,
    "notify_slack": Action.SEND_SLACK_MESSAGE,
    "slack": Action.SEND_SLACK_MESSAGE,
    "navigate": Action.NAVIGATE,
    "open_url": Action.NAVIGATE,
    "go_to": Action.NAVIGATE,
    "click": Action.CLICK,
    "mouse_click": Action.CLICK,
}


def _parse_timestamp(raw_ts: Any) -> datetime:
    """Parse various timestamp representations into timezone-aware UTC datetime."""
    if raw_ts is None:
        return datetime.now(timezone.utc)
    if isinstance(raw_ts, datetime):
        if raw_ts.tzinfo is None:
            return raw_ts.replace(tzinfo=timezone.utc)
        return raw_ts.astimezone(timezone.utc)
    if isinstance(raw_ts, (int, float)):
        return datetime.fromtimestamp(raw_ts, tz=timezone.utc)
    if isinstance(raw_ts, str):
        cleaned = raw_ts.strip()
        try:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError as exc:
            raise NormalizationError(
                f"Invalid timestamp format '{raw_ts}'. Must be ISO-8601 or UNIX epoch timestamp.",
                field="timestamp",
                received_value=raw_ts,
            ) from exc
    raise NormalizationError(
        f"Unsupported timestamp type {type(raw_ts).__name__}",
        field="timestamp",
        received_value=raw_ts,
    )


def normalize_raw_action(raw_data: Dict[str, Any]) -> ActivityEvent:
    """Normalize and validate a raw action dictionary into a verified ActivityEvent.
    
    Accepts raw payloads with flexible key casing, aliases, and string representations,
    guaranteeing that downstream modules only ever receive strictly typed ActivityEvents.
    """
    if not isinstance(raw_data, dict):
        raise NormalizationError(
            f"Expected dictionary for raw action, got {type(raw_data).__name__}",
            field="payload",
            received_value=raw_data,
        )

    # 1. Resolve Application
    raw_app = raw_data.get("application") or raw_data.get("app")
    if not raw_app:
        raise NormalizationError(
            "Missing 'application' in raw action payload",
            field="application",
            received_value=None,
            allowed_values=list(APPLICATION_MAP.keys()),
        )
    app_key = str(raw_app).strip().lower()
    if app_key not in APPLICATION_MAP:
        raise NormalizationError(
            f"Unrecognized application '{raw_app}'.",
            field="application",
            received_value=raw_app,
            allowed_values=sorted(list(set(a.value for a in Application))),
        )
    application = APPLICATION_MAP[app_key]

    # 2. Resolve Action
    raw_act = raw_data.get("action") or raw_data.get("type") or raw_data.get("event")
    if not raw_act:
        raise NormalizationError(
            "Missing 'action' in raw action payload",
            field="action",
            received_value=None,
            allowed_values=list(ACTION_MAP.keys()),
        )
    act_key = str(raw_act).strip().lower().replace("-", "_")
    if act_key not in ACTION_MAP:
        raise NormalizationError(
            f"Unrecognized action '{raw_act}'.",
            field="action",
            received_value=raw_act,
            allowed_values=sorted(list(set(a.value for a in Action))),
        )
    action = ACTION_MAP[act_key]

    # 3. Resolve Target
    raw_target = (
        raw_data.get("target")
        or raw_data.get("resource")
        or raw_data.get("entity")
        or raw_data.get("url")
        or raw_data.get("channel")
        or raw_data.get("id")
    )
    if not raw_target or not str(raw_target).strip():
        raise NormalizationError(
            "Missing or empty 'target' in raw action payload (checked target, resource, entity, url, channel, id)",
            field="target",
            received_value=raw_target,
        )
    target = str(raw_target).strip()

    # 4. Resolve Timestamp
    timestamp = _parse_timestamp(raw_data.get("timestamp") or raw_data.get("ts"))

    # 5. Resolve Metadata
    raw_meta = raw_data.get("metadata") or raw_data.get("data") or raw_data.get("params") or {}
    if not isinstance(raw_meta, dict):
        raise NormalizationError(
            f"Metadata must be a dictionary, got {type(raw_meta).__name__}",
            field="metadata",
            received_value=raw_meta,
        )

    # Optional event_id pass-through if provided, otherwise schema defaults
    optional_kwargs: Dict[str, Any] = {}
    if "event_id" in raw_data and raw_data["event_id"]:
        optional_kwargs["event_id"] = str(raw_data["event_id"]).strip()

    try:
        return ActivityEvent(
            application=application,
            action=action,
            target=target,
            timestamp=timestamp,
            metadata=raw_meta,
            **optional_kwargs,
        )
    except Exception as exc:
        raise NormalizationError(
            f"Failed to instantiate ActivityEvent: {exc}",
            field="schema_validation",
            received_value=raw_data,
        ) from exc


if __name__ == "__main__":
    import json

    raw_sample = {
        "app": "Gmail",
        "action": "open_email",
        "resource": "msg_lead_441",
        "params": {"subject": "URGENT: Contract terms"},
    }
    normalized = normalize_raw_action(raw_sample)
    print("SUCCESS: Raw action normalized to validated ActivityEvent!")
    print(json.dumps(json.loads(normalized.model_dump_json()), indent=2))
