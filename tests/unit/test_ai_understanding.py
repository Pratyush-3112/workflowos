"""Unit tests for AI intent inference, mock responses, retry, and safe fallback."""

import json
from unittest.mock import MagicMock, patch
import pytest

from backend.ai.client import LLMClient
from backend.ai.schema import WorkflowIntent
from backend.ai.understanding import IntentInferer, generate_fallback_intent
from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector


@pytest.fixture
def sample_candidate_and_store():
    service = CaptureService()
    for r in range(2):
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_{r}", "params": {"subject": "New Invoice"}},
            {"app": "crm", "action": "update_customer", "target": f"c_{r}", "params": {"status": "paid"}},
            {"app": "slack", "action": "send_slack", "target": "#ops", "params": {"text": "Updated"}},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    return candidates[0], service.store


def test_fallback_intent_generation(sample_candidate_and_store):
    candidate, store = sample_candidate_and_store
    exemplar_events = [store.get_by_id(eid) for eid in candidate.event_sequence if store.get_by_id(eid)]

    intent = generate_fallback_intent(candidate, exemplar_events)
    assert isinstance(intent, WorkflowIntent)
    assert intent.source_candidate_id == candidate.candidate_id
    assert intent.trigger_event_type == "GMAIL:READ_EMAIL"
    assert "customer_id" in intent.suggested_variables
    assert "slack_channel" in intent.suggested_variables
    assert len(intent.intent_label) >= 3


def test_intent_inferer_unconfigured_uses_safe_fallback(sample_candidate_and_store):
    candidate, store = sample_candidate_and_store
    client = LLMClient(api_key="")  # Unconfigured
    inferer = IntentInferer(llm_client=client)

    intent, was_fallback, note = inferer.infer_intent(candidate, store)
    assert was_fallback is True
    assert "Engaged deterministic safe fallback" in (note or "")
    assert intent.source_candidate_id == candidate.candidate_id


def test_intent_inferer_mocked_clean_response(sample_candidate_and_store):
    candidate, store = sample_candidate_and_store
    mock_client = MagicMock(spec=LLMClient)
    expected_intent = WorkflowIntent(
        source_candidate_id=candidate.candidate_id,
        intent_label="Customer Invoice Settlement",
        summary="Automates customer invoice extraction and team notifications.",
        trigger_event_type="GMAIL:READ_EMAIL",
        suggested_variables={"invoice_id": "Invoice number"},
        confidence_rationale="Observed high-frequency repetitions.",
    )
    mock_client.call_structured.return_value = (expected_intent, False, None)

    inferer = IntentInferer(llm_client=mock_client)
    intent, was_fallback, note = inferer.infer_intent(candidate, store)

    assert was_fallback is False
    assert intent.intent_label == "Customer Invoice Settlement"
    assert note is None


def test_llm_client_retry_and_recovery():
    # Simulate first attempt returning bad JSON, retry returning valid JSON
    client = LLMClient(api_key="sk-test-key")

    mock_openai_module = MagicMock()
    mock_openai_instance = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_openai_instance

    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content="INVALID_JSON{"))]

    good_resp = MagicMock()
    good_payload = {
        "source_candidate_id": "cand_123",
        "intent_label": "Valid Recovered Title",
        "summary": "This is a valid business summary explaining the workflow intent.",
        "trigger_event_type": "GMAIL:READ_EMAIL",
        "suggested_variables": {"v1": "description"},
        "confidence_rationale": "High repetition rate.",
    }
    good_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(good_payload)))]

    mock_openai_instance.chat.completions.create.side_effect = [bad_resp, good_resp]

    with patch.dict("sys.modules", {"openai": mock_openai_module}):
        result, was_fallback, note = client.call_structured(
            system_prompt="sys",
            user_prompt="user",
            response_schema=WorkflowIntent,
            fallback_fn=lambda: None,
        )

    assert was_fallback is False
    assert result.intent_label == "Valid Recovered Title"
    assert "single-retry" in (note or "")
