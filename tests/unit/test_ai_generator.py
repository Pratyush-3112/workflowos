"""Unit tests for AI Workflow Generator, mocked LLM calls, and validation gatekeeping."""

from unittest.mock import MagicMock
import pytest

from backend.ai.client import LLMClient
from backend.ai.generator import WorkflowGenerator, generate_fallback_workflow
from backend.ai.schema import WorkflowIntent
from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector
from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)
from backend.workflows.validator import WorkflowValidator


@pytest.fixture
def golden_candidate_and_intent():
    service = CaptureService()
    for r in range(2):
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_{r}"},
            {"app": "gmail", "action": "download", "target": f"att_{r}.pdf"},
            {"app": "crm", "action": "search_customer", "target": f"c_{r}"},
            {"app": "crm", "action": "update_customer", "target": f"c_{r}"},
            {"app": "slack", "action": "send_slack", "target": "#ops"},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    candidate = candidates[0]

    intent = WorkflowIntent(
        source_candidate_id=candidate.candidate_id,
        intent_label="Sync Customer Invoices to CRM & Slack",
        summary="Automates customer invoice processing across Gmail, CRM, and Slack alerts.",
        trigger_event_type="GMAIL:READ_EMAIL",
        suggested_variables={"email_id": "email ID", "customer_id": "customer ID"},
        confidence_rationale="High frequency repetition observed in logs.",
    )

    return candidate, intent, service.store


def test_generate_fallback_workflow_validity(golden_candidate_and_intent):
    candidate, intent, store = golden_candidate_and_intent
    exemplars = [store.get_by_id(eid) for eid in candidate.event_sequence if store.get_by_id(eid)]

    wf = generate_fallback_workflow(candidate, intent, exemplars)
    assert isinstance(wf, Workflow)
    assert len(wf.actions) == 5

    # Crucial rule: Output MUST be 100% valid under WorkflowValidator
    val_result = WorkflowValidator.validate(wf)
    assert val_result.valid is True
    assert len(val_result.errors) == 0

    # Assert closed vocabulary compliance
    expected_types = [
        ControlledActionType.READ_EMAIL,
        ControlledActionType.DOWNLOAD_ATTACHMENT,
        ControlledActionType.SEARCH_CUSTOMER,
        ControlledActionType.UPDATE_CUSTOMER,
        ControlledActionType.SEND_SLACK_MESSAGE,
    ]
    actual_types = [a.type for a in wf.actions]
    assert actual_types == expected_types


def test_workflow_generator_unconfigured_uses_safe_fallback(golden_candidate_and_intent):
    candidate, intent, store = golden_candidate_and_intent
    unconfigured_client = LLMClient(api_key="")
    generator = WorkflowGenerator(llm_client=unconfigured_client)

    wf, was_fallback, note = generator.generate_workflow(candidate, intent, store)
    assert was_fallback is True
    assert isinstance(wf, Workflow)
    assert len(wf.actions) == 5
    assert "Engaged deterministic safe fallback" in (note or "")


def test_workflow_generator_mocked_clean_response(golden_candidate_and_intent):
    candidate, intent, store = golden_candidate_and_intent
    exemplars = [store.get_by_id(eid) for eid in candidate.event_sequence if store.get_by_id(eid)]
    clean_wf = generate_fallback_workflow(candidate, intent, exemplars)

    mock_client = MagicMock(spec=LLMClient)
    mock_client.call_structured.return_value = (clean_wf, False, None)

    generator = WorkflowGenerator(llm_client=mock_client)
    wf, was_fallback, note = generator.generate_workflow(candidate, intent, store)

    assert was_fallback is False
    assert wf.name == clean_wf.name
    assert len(wf.actions) == 5


def test_workflow_generator_rejects_invalid_llm_output_and_falls_back(golden_candidate_and_intent):
    candidate, intent, store = golden_candidate_and_intent

    # Create an invalid workflow (step numbers not starting from 1)
    bad_step = ActionStep(
        step=99,
        type=ControlledActionType.READ_EMAIL,
        params={"email_id": "123"},
        verification=VerificationRule(
            check_type=CheckType.EMAIL_OPENED,
            expected_state={"ok": True},
            description="desc",
        ),
    )
    # Using construct/dict bypass to produce invalid Workflow
    bad_wf = Workflow.model_construct(
        workflow_id="wf_bad",
        name="Bad WF",
        description="Invalid sequence",
        trigger=TriggerConfig(source="GMAIL"),
        variables={},
        actions=[bad_step],
        conditions=[],
    )

    mock_client = MagicMock(spec=LLMClient)
    mock_client.call_structured.return_value = (bad_wf, False, None)

    generator = WorkflowGenerator(llm_client=mock_client)
    wf, was_fallback, note = generator.generate_workflow(candidate, intent, store)

    # Must engage safe fallback and log audit note
    assert was_fallback is True
    assert "failed strict validation" in (note or "")
    assert len(wf.actions) == 5  # Returns the valid fallback workflow
