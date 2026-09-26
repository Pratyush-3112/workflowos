"""Unit tests for raw action normalization."""

from datetime import datetime, timezone
import pytest

from backend.capture.normalizer import NormalizationError, normalize_raw_action
from backend.events.schema import Action, Application


def test_normalize_raw_action_flexible_keys():
    raw = {
        "app": "Gmail",
        "action": "open_email",
        "resource": "msg_999",
        "params": {"subject": "Hello World"},
    }
    event = normalize_raw_action(raw)
    assert event.application == Application.GMAIL
    assert event.action == Action.READ_EMAIL
    assert event.target == "msg_999"
    assert event.metadata["subject"] == "Hello World"
    assert event.timestamp.tzinfo is not None


def test_normalize_raw_action_crm_aliases():
    raw = {
        "application": "hubspot",
        "type": "find_customer",
        "entity": "acme_inc",
        "metadata": {"domain": "acme.com"},
    }
    event = normalize_raw_action(raw)
    assert event.application == Application.CRM
    assert event.action == Action.SEARCH_CUSTOMER
    assert event.target == "acme_inc"


def test_normalize_raw_action_slack_aliases():
    raw = {
        "app": "slack",
        "event": "notify_slack",
        "channel": "#general",
        "data": {"text": "Workflow completed"},
    }
    event = normalize_raw_action(raw)
    assert event.application == Application.SLACK
    assert event.action == Action.SEND_SLACK_MESSAGE
    assert event.target == "#general"
    assert event.metadata["text"] == "Workflow completed"


def test_normalize_raw_action_iso_timestamp():
    iso_time = "2026-09-26T10:00:00Z"
    raw = {
        "application": "crm",
        "action": "update_customer",
        "target": "c_123",
        "timestamp": iso_time,
    }
    event = normalize_raw_action(raw)
    assert event.timestamp == datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone.utc)


def test_normalize_rejects_missing_application():
    raw = {"action": "read", "target": "t1"}
    with pytest.raises(NormalizationError) as exc_info:
        normalize_raw_action(raw)
    assert exc_info.value.field == "application"


def test_normalize_rejects_invalid_application():
    raw = {"application": "unsupported_tool", "action": "read", "target": "t1"}
    with pytest.raises(NormalizationError) as exc_info:
        normalize_raw_action(raw)
    assert "Unrecognized application" in str(exc_info.value)


def test_normalize_rejects_missing_target():
    raw = {"application": "gmail", "action": "open_email", "target": "  "}
    with pytest.raises(NormalizationError) as exc_info:
        normalize_raw_action(raw)
    assert exc_info.value.field == "target"


def test_normalize_rejects_invalid_payload_type():
    with pytest.raises(NormalizationError):
        normalize_raw_action("not_a_dict")  # type: ignore
