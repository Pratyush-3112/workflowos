"""Unit tests for Event schema and append-only EventStore."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import pytest
from pydantic import ValidationError

from backend.events.schema import Action, ActivityEvent, Application
from backend.events.store import EventStore, IntegrityError, StoredEvent


def test_activity_event_valid_creation():
    event = ActivityEvent(
        application=Application.GMAIL,
        action=Action.READ_EMAIL,
        target="msg_12345",
        metadata={"subject": "Invoice Inquiry", "sender": "customer@example.com"},
    )
    assert event.event_id.startswith("evt_")
    assert event.application == Application.GMAIL
    assert event.action == Action.READ_EMAIL
    assert event.target == "msg_12345"
    assert event.metadata["subject"] == "Invoice Inquiry"
    assert event.timestamp.tzinfo is not None


def test_activity_event_rejects_empty_target():
    with pytest.raises(ValidationError):
        ActivityEvent(
            application=Application.CRM,
            action=Action.SEARCH_CUSTOMER,
            target="   ",  # whitespace only should fail validator
        )


def test_activity_event_rejects_invalid_enums():
    with pytest.raises(ValidationError):
        ActivityEvent(
            application="NOT_AN_APP",  # type: ignore
            action=Action.READ_EMAIL,
            target="target_1",
        )

    with pytest.raises(ValidationError):
        ActivityEvent(
            application=Application.GMAIL,
            action="UNAUTHORIZED_ACTION",  # type: ignore
            target="target_1",
        )


def test_activity_event_immutability():
    event = ActivityEvent(
        application=Application.SLACK,
        action=Action.SEND_SLACK_MESSAGE,
        target="channel_ops",
    )
    with pytest.raises(ValidationError):
        event.target = "different_channel"  # type: ignore


def test_event_store_append_and_chain():
    store = EventStore()
    now = datetime.now(timezone.utc)

    e1 = ActivityEvent(
        application=Application.GMAIL,
        action=Action.READ_EMAIL,
        target="email_1",
        timestamp=now,
    )
    stored1 = store.append(e1)

    assert stored1.index == 0
    assert stored1.prev_hash == EventStore.GENESIS_HASH
    assert len(stored1.hash) == 64

    e2 = ActivityEvent(
        application=Application.GMAIL,
        action=Action.DOWNLOAD_ATTACHMENT,
        target="attachment_1",
        timestamp=now + timedelta(seconds=1),
    )
    stored2 = store.append(e2)

    assert stored2.index == 1
    assert stored2.prev_hash == stored1.hash
    assert stored2.hash != stored1.hash
    assert len(store) == 2

    # Verification passes
    valid, message = store.verify_integrity()
    assert valid is True
    assert "Integrity verified across 2 events" in message


def test_event_store_get_by_id_and_slice():
    store = EventStore()
    e1 = ActivityEvent(application=Application.GMAIL, action=Action.READ_EMAIL, target="e1")
    e2 = ActivityEvent(
        application=Application.CRM,
        action=Action.SEARCH_CUSTOMER,
        target="c1",
        timestamp=e1.timestamp + timedelta(seconds=1),
    )
    store.append(e1)
    store.append(e2)

    found = store.get_by_id(e1.event_id)
    assert found is not None
    assert found.event.target == "e1"

    missing = store.get_by_id("non_existent_id")
    assert missing is None

    sliced = store.get_slice(0, 1)
    assert len(sliced) == 1
    assert sliced[0].index == 0


def test_event_store_rejects_duplicate_event_id():
    store = EventStore()
    e1 = ActivityEvent(
        event_id="duplicate_id",
        application=Application.GMAIL,
        action=Action.READ_EMAIL,
        target="e1",
    )
    store.append(e1)

    e2 = ActivityEvent(
        event_id="duplicate_id",
        application=Application.GMAIL,
        action=Action.DOWNLOAD_ATTACHMENT,
        target="att1",
        timestamp=e1.timestamp + timedelta(seconds=1),
    )
    with pytest.raises(IntegrityError) as exc_info:
        store.append(e2)
    assert "Duplicate event_id rejected" in str(exc_info.value)


def test_event_store_rejects_non_monotonic_timestamps():
    store = EventStore()
    t0 = datetime.now(timezone.utc)
    e1 = ActivityEvent(
        application=Application.GMAIL,
        action=Action.READ_EMAIL,
        target="e1",
        timestamp=t0,
    )
    store.append(e1)

    # Event with earlier timestamp
    e2 = ActivityEvent(
        application=Application.CRM,
        action=Action.SEARCH_CUSTOMER,
        target="c1",
        timestamp=t0 - timedelta(seconds=10),
    )
    with pytest.raises(IntegrityError) as exc_info:
        store.append(e2)
    assert "Timestamp non-monotonic" in str(exc_info.value)


def test_event_store_persistence_roundtrip(tmp_path: Path):
    persist_file = tmp_path / "events.jsonl"
    store1 = EventStore(persistence_path=persist_file)

    t0 = datetime.now(timezone.utc)
    for i in range(3):
        store1.append(
            ActivityEvent(
                application=Application.GMAIL,
                action=Action.READ_EMAIL,
                target=f"target_{i}",
                timestamp=t0 + timedelta(seconds=i),
            )
        )

    assert persist_file.exists()

    # Load store from the same file in a fresh instance
    store2 = EventStore(persistence_path=persist_file)
    assert len(store2) == 3
    valid, _ = store2.verify_integrity()
    assert valid is True
    assert store2.get_all()[1].event.target == "target_1"


def test_event_store_detects_tampered_persistence_data(tmp_path: Path):
    persist_file = tmp_path / "events.jsonl"
    store = EventStore(persistence_path=persist_file)
    t0 = datetime.now(timezone.utc)
    store.append(ActivityEvent(application=Application.GMAIL, action=Action.READ_EMAIL, target="target_0", timestamp=t0))
    store.append(ActivityEvent(application=Application.GMAIL, action=Action.DOWNLOAD_ATTACHMENT, target="target_1", timestamp=t0 + timedelta(seconds=1)))

    # Tamper with file directly
    lines = persist_file.read_text(encoding="utf-8").strip().split("\n")
    second_record = json.loads(lines[1])
    second_record["event"]["target"] = "tampered_target"
    lines[1] = json.dumps(second_record)
    persist_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Loading the tampered file should fail integrity checks immediately
    with pytest.raises(IntegrityError) as exc_info:
        EventStore(persistence_path=persist_file)
    assert "Checksum mismatch" in str(exc_info.value)
