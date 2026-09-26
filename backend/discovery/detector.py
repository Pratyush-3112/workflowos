"""Deterministic repetition detector discovering candidate workflows from stored events."""

from typing import Dict, List, Optional, Tuple

from backend.discovery.schema import WorkflowCandidate
from backend.events.store import EventStore, StoredEvent


def compute_confidence(occurrences: int, pattern_length: int) -> float:
    """Compute deterministic confidence score based on repetition frequency and sequence depth.
    
    Formula:
      Base confidence: 0.50 for min threshold (2 occurrences, length 2).
      Frequency bonus: +0.15 per additional occurrence.
      Depth bonus: +0.05 per additional step beyond 2 (capped at +0.20).
    Max bounded to 0.99.
    """
    freq_bonus = 0.15 * max(0, occurrences - 2)
    depth_bonus = 0.05 * min(4, max(0, pattern_length - 2))
    raw_score = 0.50 + freq_bonus + depth_bonus
    return min(0.99, round(raw_score, 2))


class RepetitionDetector:
    """Detects deterministic repeating action sequences within an EventStore."""

    def __init__(
        self,
        min_sequence_length: int = 2,
        min_occurrences: int = 2,
        max_step_gap_seconds: Optional[float] = 300.0,
    ):
        if min_sequence_length < 2:
            raise ValueError("min_sequence_length must be at least 2")
        if min_occurrences < 2:
            raise ValueError("min_occurrences must be at least 2")
        self.min_sequence_length = min_sequence_length
        self.min_occurrences = min_occurrences
        self.max_step_gap_seconds = max_step_gap_seconds

    def _is_contiguous_in_time(self, events: List[StoredEvent]) -> bool:
        """Check if all consecutive events occur within max_step_gap_seconds."""
        if self.max_step_gap_seconds is None or len(events) <= 1:
            return True
        for k in range(len(events) - 1):
            gap = (events[k + 1].event.timestamp - events[k].event.timestamp).total_seconds()
            if gap > self.max_step_gap_seconds:
                return False
        return True

    def detect_candidates(self, store: EventStore) -> List[WorkflowCandidate]:
        """Scan event log and discover repeated action sequence candidates."""
        stored_events = store.get_all()
        if len(stored_events) < self.min_sequence_length * self.min_occurrences:
            return []

        # Canonical tokens: e.g. "GMAIL:READ_EMAIL", "CRM:SEARCH_CUSTOMER"
        tokens = [f"{e.event.application.value}:{e.event.action.value}" for e in stored_events]
        total_len = len(tokens)

        # Dictionary mapping n-gram tuple -> list of starting indices in stored_events
        candidates_by_pattern: Dict[Tuple[str, ...], List[int]] = {}

        # Search for pattern lengths from min_sequence_length up to total_len // min_occurrences
        max_search_length = min(15, total_len // self.min_occurrences)
        for length in range(self.min_sequence_length, max_search_length + 1):
            pattern_starts: Dict[Tuple[str, ...], List[int]] = {}
            for i in range(total_len - length + 1):
                window_events = stored_events[i : i + length]
                if not self._is_contiguous_in_time(window_events):
                    continue
                pat = tuple(tokens[i : i + length])
                pattern_starts.setdefault(pat, []).append(i)


            # Filter for non-overlapping occurrences
            for pat, indices in pattern_starts.items():
                non_overlapping: List[int] = []
                last_end = -1
                for idx in indices:
                    if idx >= last_end:
                        non_overlapping.append(idx)
                        last_end = idx + length

                if len(non_overlapping) >= self.min_occurrences:
                    candidates_by_pattern[pat] = non_overlapping

        # Prune subsumed patterns: if pattern A is a strict sub-slice of a longer pattern B
        # with identical occurrences, prefer the longer maximal pattern
        sorted_patterns = sorted(candidates_by_pattern.keys(), key=lambda p: len(p), reverse=True)
        maximal_patterns: List[Tuple[Tuple[str, ...], List[int]]] = []

        for pat in sorted_patterns:
            pat_indices = candidates_by_pattern[pat]
            is_subsumed = False
            for parent_pat, parent_indices in maximal_patterns:
                # Check if pat is subpattern of parent_pat and matches same occurrence positions
                pat_str = " -> ".join(pat)
                parent_str = " -> ".join(parent_pat)
                if pat_str in parent_str and len(pat_indices) == len(parent_indices):
                    is_subsumed = True
                    break
            if not is_subsumed:
                maximal_patterns.append((pat, pat_indices))

        candidates: List[WorkflowCandidate] = []
        for pat, start_indices in maximal_patterns:
            instances: List[List[str]] = []
            for start_idx in start_indices:
                instance_ids = [stored_events[start_idx + k].event.event_id for k in range(len(pat))]
                instances.append(instance_ids)

            signature = " -> ".join(pat)
            confidence = compute_confidence(len(instances), len(pat))

            candidate = WorkflowCandidate(
                signature=signature,
                action_pattern=list(pat),
                event_sequence=instances[-1],  # latest instance as exemplar
                occurrences=len(instances),
                confidence=confidence,
                instance_sequences=instances,
            )

            # Explicit verification: prove candidate exists in store
            is_valid, reason = self.verify_candidate_in_store(candidate, store)
            if not is_valid:
                raise RuntimeError(f"Detector verification failure: {reason}")

            candidates.append(candidate)

        # Sort by confidence descending, then occurrences descending
        candidates.sort(key=lambda c: (c.confidence, c.occurrences), reverse=True)
        return candidates

    @classmethod
    def verify_candidate_in_store(
        cls, candidate: WorkflowCandidate, store: EventStore
    ) -> Tuple[bool, str]:
        """Verify that every instance in candidate corresponds to an actual,
        contiguous sequence in the store matching the pattern exactly.
        """
        for inst_idx, seq in enumerate(candidate.instance_sequences):
            if len(seq) != len(candidate.action_pattern):
                return False, f"Instance {inst_idx} length {len(seq)} does not match pattern length {len(candidate.action_pattern)}"

            stored_items: List[StoredEvent] = []
            for event_id in seq:
                item = store.get_by_id(event_id)
                if item is None:
                    return False, f"Event ID '{event_id}' in candidate does not exist in event store"
                stored_items.append(item)

            # Check contiguity
            for step_k in range(len(stored_items) - 1):
                if stored_items[step_k + 1].index != stored_items[step_k].index + 1:
                    return False, (
                        f"Instance {inst_idx} is not contiguous in store: "
                        f"index {stored_items[step_k].index} followed by {stored_items[step_k + 1].index}"
                    )

            # Check pattern match
            for step_k, stored in enumerate(stored_items):
                actual_token = f"{stored.event.application.value}:{stored.event.action.value}"
                expected_token = candidate.action_pattern[step_k]
                if actual_token != expected_token:
                    return False, (
                        f"Instance {inst_idx} step {step_k} token mismatch: "
                        f"expected '{expected_token}', got '{actual_token}'"
                    )

        return True, f"Verified all {len(candidate.instance_sequences)} occurrences exist contiguously in store"


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone
    from backend.capture.service import CaptureService

    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    print("Simulating 3 runs of the Golden Workflow...")
    # Repeat the 5-step golden workflow 3 times with different customer targets
    for run in range(1, 4):
        run_time = t0 + timedelta(minutes=run * 10)
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_run_{run}", "timestamp": run_time},
            {"app": "gmail", "action": "download", "target": f"invoice_{run}.pdf", "timestamp": run_time + timedelta(seconds=1)},
            {"app": "crm", "action": "search_customer", "target": f"cust_{run}", "timestamp": run_time + timedelta(seconds=2)},
            {"app": "crm", "action": "update_customer", "target": f"cust_{run}", "timestamp": run_time + timedelta(seconds=3)},
            {"app": "slack", "action": "send_slack", "target": "#billing-alerts", "timestamp": run_time + timedelta(seconds=4)},
        ])

    print(f"Total events recorded in store: {len(service.store)}")

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    discovered = detector.detect_candidates(service.store)

    print(f"\nDiscovered {len(discovered)} candidate workflow(s):")
    for cand in discovered:
        print(f"Candidate ID: {cand.candidate_id}")
        print(f"Signature:    {cand.signature}")
        print(f"Occurrences:  {cand.occurrences}")
        print(f"Confidence:   {cand.confidence}")
        print(f"Verified:     {RepetitionDetector.verify_candidate_in_store(cand, service.store)[1]}")
