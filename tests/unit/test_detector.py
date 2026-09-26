"""Unit tests for deterministic repetition detector."""

from datetime import datetime, timedelta, timezone
import pytest

from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector, compute_confidence
from backend.discovery.schema import WorkflowCandidate
from backend.events.schema import Action, Application


def test_compute_confidence_scores():
    # Base: 2 occurrences, 2 steps
    assert compute_confidence(occurrences=2, pattern_length=2) == 0.50
    # 3 occurrences, 5 steps
    # 0.50 + (0.15 * 1) + (0.05 * 3) = 0.80
    assert compute_confidence(occurrences=3, pattern_length=5) == 0.80
    # Max bound
    assert compute_confidence(occurrences=10, pattern_length=10) <= 0.99


def test_detect_golden_workflow_repetition():
    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    # 3 complete repetitions of Golden Workflow
    for run in range(3):
        rt = t0 + timedelta(minutes=run * 10)
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_{run}", "timestamp": rt},
            {"app": "gmail", "action": "download", "target": f"att_{run}", "timestamp": rt + timedelta(seconds=1)},
            {"app": "crm", "action": "search_customer", "target": f"c_{run}", "timestamp": rt + timedelta(seconds=2)},
            {"app": "crm", "action": "update_customer", "target": f"c_{run}", "timestamp": rt + timedelta(seconds=3)},
            {"app": "slack", "action": "send_slack", "target": "#general", "timestamp": rt + timedelta(seconds=4)},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)

    assert len(candidates) >= 1
    top = candidates[0]
    expected_sig = (
        "GMAIL:READ_EMAIL -> GMAIL:DOWNLOAD_ATTACHMENT -> "
        "CRM:SEARCH_CUSTOMER -> CRM:UPDATE_CUSTOMER -> SLACK:SEND_SLACK_MESSAGE"
    )
    assert top.signature == expected_sig
    assert top.occurrences == 3
    assert top.confidence == 0.80
    assert len(top.action_pattern) == 5
    assert len(top.event_sequence) == 5

    # Candidate verification must pass
    valid, msg = RepetitionDetector.verify_candidate_in_store(top, service.store)
    assert valid is True
    assert "Verified all 3 occurrences exist" in msg


def test_no_candidates_when_no_repetition():
    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    # Random distinct actions
    service.record_batch([
        {"app": "gmail", "action": "open_email", "target": "e1", "timestamp": t0},
        {"app": "crm", "action": "update_customer", "target": "c1", "timestamp": t0 + timedelta(seconds=1)},
        {"app": "slack", "action": "send_slack", "target": "s1", "timestamp": t0 + timedelta(seconds=2)},
        {"app": "browser", "action": "navigate", "target": "https://google.com", "timestamp": t0 + timedelta(seconds=3)},
    ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    assert len(candidates) == 0


def test_detector_prunes_subsumed_patterns():
    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    # Sequence A -> B -> C repeated 2 times
    for r in range(2):
        rt = t0 + timedelta(minutes=r * 5)
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"e{r}", "timestamp": rt},
            {"app": "gmail", "action": "download", "target": f"a{r}", "timestamp": rt + timedelta(seconds=1)},
            {"app": "slack", "action": "send_slack", "target": f"s{r}", "timestamp": rt + timedelta(seconds=2)},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)

    # Should only return the maximal 3-step sequence, not redundant 2-step sub-slices
    assert len(candidates) == 1
    assert len(candidates[0].action_pattern) == 3


def test_verify_candidate_rejects_hallucinated_event_id():
    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    service.record_batch([
        {"app": "gmail", "action": "open_email", "target": "e1", "timestamp": t0},
        {"app": "gmail", "action": "download", "target": "a1", "timestamp": t0 + timedelta(seconds=1)},
        {"app": "gmail", "action": "open_email", "target": "e2", "timestamp": t0 + timedelta(seconds=2)},
        {"app": "gmail", "action": "download", "target": "a2", "timestamp": t0 + timedelta(seconds=3)},
    ])

    # Craft candidate with hallucinated event id
    fake_candidate = WorkflowCandidate(
        signature="GMAIL:READ_EMAIL -> GMAIL:DOWNLOAD_ATTACHMENT",
        action_pattern=["GMAIL:READ_EMAIL", "GMAIL:DOWNLOAD_ATTACHMENT"],
        event_sequence=["evt_hallucinated_1", "evt_hallucinated_2"],
        occurrences=2,
        confidence=0.5,
        instance_sequences=[
            ["evt_hallucinated_1", "evt_hallucinated_2"],
            ["evt_hallucinated_3", "evt_hallucinated_4"],
        ],
    )

    valid, reason = RepetitionDetector.verify_candidate_in_store(fake_candidate, service.store)
    assert valid is False
    assert "does not exist in event store" in reason
