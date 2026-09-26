"""Append-only EventStore with cryptographic sequence integrity verification."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from backend.events.schema import ActivityEvent


class IntegrityError(Exception):
    """Raised when event store sequence or hash chain integrity is violated."""

    def __init__(self, message: str, index: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.index = index
        self.details = details or {}


class StoredEvent(BaseModel):
    """Event wrapper recording monotonic sequence index and cryptographic hash chain."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(..., ge=0, description="0-indexed monotonic position in the log")
    event: ActivityEvent = Field(..., description="Immutable event payload")
    prev_hash: str = Field(..., description="Hash of the previous stored event ('GENESIS' for index 0)")
    hash: str = Field(..., description="SHA-256 hash of this entry (prev_hash + canonical serialized event)")


def compute_event_hash(prev_hash: str, index: int, event: ActivityEvent) -> str:
    """Compute deterministic SHA-256 hash across sequence index, prev_hash, and canonical event JSON."""
    canonical_payload = json.dumps(
        {
            "index": index,
            "prev_hash": prev_hash,
            "event": json.loads(event.model_dump_json()),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


class EventStore:
    """Append-only store for activity events with verifiable sequence integrity."""

    GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"

    def __init__(self, persistence_path: Optional[Path] = None):
        self._events: List[StoredEvent] = []
        self._id_index: Dict[str, int] = {}
        self.persistence_path = Path(persistence_path) if persistence_path else None

        if self.persistence_path and self.persistence_path.exists():
            self._load_from_disk()

    def append(self, event: ActivityEvent) -> StoredEvent:
        """Append a validated ActivityEvent to the store, enforcing sequence and timestamp continuity."""
        if not isinstance(event, ActivityEvent):
            raise TypeError(f"Expected ActivityEvent, got {type(event).__name__}")

        if event.event_id in self._id_index:
            raise IntegrityError(
                f"Duplicate event_id rejected: {event.event_id}",
                index=len(self._events),
                details={"event_id": event.event_id},
            )

        new_index = len(self._events)
        if new_index == 0:
            prev_hash = self.GENESIS_HASH
        else:
            prev_entry = self._events[-1]
            prev_hash = prev_entry.hash
            if event.timestamp < prev_entry.event.timestamp:
                raise IntegrityError(
                    f"Timestamp non-monotonic: new event {event.timestamp.isoformat()} is earlier than previous event {prev_entry.event.timestamp.isoformat()}",
                    index=new_index,
                    details={
                        "new_timestamp": event.timestamp.isoformat(),
                        "prev_timestamp": prev_entry.event.timestamp.isoformat(),
                    },
                )

        event_hash = compute_event_hash(prev_hash=prev_hash, index=new_index, event=event)
        stored = StoredEvent(
            index=new_index,
            event=event,
            prev_hash=prev_hash,
            hash=event_hash,
        )

        self._events.append(stored)
        self._id_index[event.event_id] = new_index

        if self.persistence_path:
            self._persist_entry(stored)

        return stored

    def _persist_entry(self, entry: StoredEvent) -> None:
        """Write single entry as JSON line in append mode."""
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.persistence_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _load_from_disk(self) -> None:
        """Load and verify all entries from persistence path."""
        self._events.clear()
        self._id_index.clear()

        with open(self.persistence_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                    stored = StoredEvent.model_validate(data)
                except Exception as exc:
                    raise IntegrityError(
                        f"Failed to parse stored event at line {line_no}: {exc}",
                        index=len(self._events),
                    ) from exc

                expected_index = len(self._events)
                if stored.index != expected_index:
                    raise IntegrityError(
                        f"Index mismatch at line {line_no}: expected {expected_index}, got {stored.index}",
                        index=stored.index,
                    )

                expected_prev = self.GENESIS_HASH if expected_index == 0 else self._events[-1].hash
                if stored.prev_hash != expected_prev:
                    raise IntegrityError(
                        f"Hash chain broken at index {stored.index}: expected prev_hash {expected_prev}, got {stored.prev_hash}",
                        index=stored.index,
                    )

                recomputed = compute_event_hash(
                    prev_hash=stored.prev_hash,
                    index=stored.index,
                    event=stored.event,
                )
                if stored.hash != recomputed:
                    raise IntegrityError(
                        f"Checksum mismatch at index {stored.index}: stored {stored.hash}, recomputed {recomputed}",
                        index=stored.index,
                    )

                self._events.append(stored)
                self._id_index[stored.event.event_id] = stored.index

    def verify_integrity(self) -> Tuple[bool, str]:
        """Verify complete sequence continuity, hash chain integrity, and timestamps.
        
        Returns:
            (True, "Integrity verified across N events") if valid,
            (False, "Detailed failure explanation") otherwise.
        """
        for i, item in enumerate(self._events):
            if item.index != i:
                return False, f"Broken sequence index at position {i}: found index {item.index}"

            expected_prev = self.GENESIS_HASH if i == 0 else self._events[i - 1].hash
            if item.prev_hash != expected_prev:
                return False, (
                    f"Hash chain broken at index {i}: expected prev_hash '{expected_prev}', got '{item.prev_hash}'"
                )

            recomputed = compute_event_hash(
                prev_hash=item.prev_hash,
                index=item.index,
                event=item.event,
            )
            if item.hash != recomputed:
                return False, f"Checksum verification failed at index {i}: hash was altered"

            if i > 0:
                prev_time = self._events[i - 1].event.timestamp
                if item.event.timestamp < prev_time:
                    return False, (
                        f"Non-monotonic timestamp at index {i}: {item.event.timestamp} < {prev_time}"
                    )

        return True, f"Integrity verified across {len(self._events)} events"

    def get_all(self) -> List[StoredEvent]:
        """Return shallow copy of stored events list (elements are immutable)."""
        return list(self._events)

    def get_by_id(self, event_id: str) -> Optional[StoredEvent]:
        """Lookup stored event by event_id in O(1) time."""
        idx = self._id_index.get(event_id)
        if idx is None:
            return None
        return self._events[idx]

    def get_slice(self, start_idx: int, end_idx: Optional[int] = None) -> List[StoredEvent]:
        """Return slice of stored events."""
        if end_idx is None:
            return list(self._events[start_idx:])
        return list(self._events[start_idx:end_idx])

    def __len__(self) -> int:
        return len(self._events)
