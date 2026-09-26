"""Integration tests for CaptureService and EventStore session persistence."""

from pathlib import Path
from backend.capture.service import CaptureService
from backend.events.schema import Action, Application


def test_capture_service_record_and_persist(tmp_path: Path):
    storage_file = tmp_path / "captured_events.jsonl"
    service = CaptureService(persistence_path=storage_file)

    golden_actions = [
        {"app": "gmail", "action": "open_email", "target": "msg_001", "metadata": {"subject": "New Invoice"}},
        {"app": "gmail", "action": "download", "target": "invoice_001.pdf"},
        {"app": "crm", "action": "find_customer", "target": "cust_456"},
        {"app": "crm", "action": "update_customer", "target": "cust_456", "metadata": {"status": "paid"}},
        {"app": "slack", "action": "notify_slack", "target": "#billing-alerts", "metadata": {"msg": "Updated"}},
    ]

    stored = service.record_batch(golden_actions)
    assert len(stored) == 5
    assert len(service) == 5

    # Check order & types
    assert stored[0].event.application == Application.GMAIL
    assert stored[0].event.action == Action.READ_EMAIL
    assert stored[4].event.application == Application.SLACK
    assert stored[4].event.action == Action.SEND_SLACK_MESSAGE

    # Verify cryptographic integrity
    valid, message = service.verify_integrity()
    assert valid is True
    assert "Integrity verified across 5 events" in message

    # Now simulate process restart by initializing a new CaptureService from the same file
    restored_service = CaptureService(persistence_path=storage_file)
    assert len(restored_service) == 5

    valid_restored, msg_restored = restored_service.verify_integrity()
    assert valid_restored is True
    assert "Integrity verified across 5 events" in msg_restored

    # Ensure events match precisely
    restored_events = restored_service.get_events()
    assert restored_events[0].event.target == "msg_001"
    assert restored_events[4].event.target == "#billing-alerts"
