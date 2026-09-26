"""Capture service bridging raw actions, normalization, and persistent EventStore."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.capture.normalizer import normalize_raw_action
from backend.events.schema import ActivityEvent
from backend.events.store import EventStore, StoredEvent


class CaptureService:
    """Service responsible for ingesting raw action data, normalizing it,
    and committing it safely to the append-only EventStore.
    """

    def __init__(self, persistence_path: Optional[Path] = None):
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self._store = EventStore(persistence_path=self.persistence_path)

    @property
    def store(self) -> EventStore:
        """Access underlying EventStore."""
        return self._store

    def record_action(self, raw_action: Dict[str, Any]) -> StoredEvent:
        """Normalize raw action dictionary and append to the store."""
        event: ActivityEvent = normalize_raw_action(raw_action)
        return self._store.append(event)

    def record_batch(self, raw_actions: List[Dict[str, Any]]) -> List[StoredEvent]:
        """Normalize and append a sequence of raw actions atomically in order."""
        stored_entries: List[StoredEvent] = []
        for raw in raw_actions:
            stored_entries.append(self.record_action(raw))
        return stored_entries

    def get_events(self) -> List[StoredEvent]:
        """Retrieve all captured events in monotonic order."""
        return self._store.get_all()

    def get_by_id(self, event_id: str) -> Optional[StoredEvent]:
        """Retrieve event by its unique event_id."""
        return self._store.get_by_id(event_id)

    def verify_integrity(self) -> Tuple[bool, str]:
        """Verify the cryptographic sequence continuity of all captured events."""
        return self._store.verify_integrity()

    def __len__(self) -> int:
        return len(self._store)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_file = Path(tmpdir) / "session_events.jsonl"
        service = CaptureService(persistence_path=storage_file)

        print("1. Recording raw actions via CaptureService...")
        s1 = service.record_action({
            "app": "Gmail",
            "action": "open_email",
            "target": "msg_901",
            "metadata": {"subject": "Q3 Contract"},
        })
        s2 = service.record_action({
            "app": "CRM",
            "action": "update_customer",
            "entity": "cust_acme_corp",
            "params": {"status": "Active"},
        })

        print(f"   Recorded Event 0: ID={s1.event.event_id}, App={s1.event.application.value}")
        print(f"   Recorded Event 1: ID={s2.event.event_id}, App={s2.event.application.value}")

        # Check persistence restart
        print("2. Re-opening CaptureService from persisted log...")
        restarted = CaptureService(persistence_path=storage_file)
        valid, msg = restarted.verify_integrity()
        print(f"   Restarted service event count: {len(restarted)}")
        print(f"   Integrity check: valid={valid} ({msg})")
