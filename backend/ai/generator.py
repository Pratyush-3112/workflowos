"""AI Workflow Generator producing validated executable workflows from candidates and intents."""

import json
from typing import Any, Dict, List, Optional, Tuple

from backend.ai.client import LLMClient
from backend.ai.schema import WorkflowIntent
from backend.discovery.schema import WorkflowCandidate
from backend.events.store import EventStore, StoredEvent
from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)
from backend.workflows.validator import WorkflowValidator


SYSTEM_WORKFLOW_GEN_PROMPT = """You are WorkFlowOS AI Workflow Generator.
Your job is to convert a detected repetitive activity candidate and its inferred business intent into a verified, executable Workflow JSON specification.

CRITICAL RULES:
1. Closed Action Vocabulary: Step action types must ONLY be one of:
   - "READ_EMAIL"
   - "DOWNLOAD_ATTACHMENT"
   - "SEARCH_CUSTOMER"
   - "UPDATE_CUSTOMER"
   - "SEND_SLACK_MESSAGE"
   Any invented or unknown action type is strictly prohibited.
2. Required Parameters:
   - READ_EMAIL requires "email_id"
   - DOWNLOAD_ATTACHMENT requires "attachment_id"
   - SEARCH_CUSTOMER requires "customer_id"
   - UPDATE_CUSTOMER requires "customer_id" and "fields_to_update"
   - SEND_SLACK_MESSAGE requires "channel" and "message"
3. Template Variables:
   - Parameter values can contain templates like "{{email_id}}", "{{customer_id}}", "{{slack_channel}}".
   - Every variable used in step params MUST be declared in the top-level "variables" dictionary.
4. Mandatory Post-Condition Verification:
   - Every step MUST have a "verification" object with:
     - "check_type": one of ["EMAIL_OPENED", "ATTACHMENT_SAVED", "RECORD_EXISTS", "FIELD_EQUALS", "MESSAGE_SENT"]
     - "expected_state": non-empty dictionary of expected state assertions
     - "description": non-blank explanation of what state change is verified.
5. Steps sequence must be strictly 1, 2, 3...
"""


def generate_fallback_workflow(
    candidate: WorkflowCandidate,
    intent: WorkflowIntent,
    exemplar_events: List[StoredEvent],
) -> Workflow:
    """Deterministic, verified fallback workflow generator guaranteeing zero schema or validation errors."""
    actions: List[ActionStep] = []
    variables: Dict[str, str] = {
        "email_id": "string",
        "attachment_id": "string",
        "customer_id": "string",
        "slack_channel": "string",
    }

    step_counter = 1
    for token in candidate.action_pattern:
        action_name = token.split(":")[-1]

        if action_name == "READ_EMAIL":
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.READ_EMAIL,
                params={"email_id": "{{email_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.EMAIL_OPENED,
                    expected_state={"retrieved": True},
                    description="Verify customer email message retrieved",
                ),
            )
        elif action_name == "DOWNLOAD_ATTACHMENT":
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.DOWNLOAD_ATTACHMENT,
                params={"attachment_id": "{{attachment_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.ATTACHMENT_SAVED,
                    expected_state={"saved": True},
                    description="Verify attachment was downloaded and saved locally",
                ),
            )
        elif action_name == "SEARCH_CUSTOMER":
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.SEARCH_CUSTOMER,
                params={"customer_id": "{{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.RECORD_EXISTS,
                    expected_state={"exists": True},
                    description="Verify customer record exists in CRM",
                ),
            )
        elif action_name == "UPDATE_CUSTOMER":
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.UPDATE_CUSTOMER,
                params={
                    "customer_id": "{{customer_id}}",
                    "fields_to_update": {"invoice_status": "PROCESSED"},
                },
                verification=VerificationRule(
                    check_type=CheckType.FIELD_EQUALS,
                    expected_state={"invoice_status": "PROCESSED"},
                    description="Verify customer record field was updated in CRM",
                ),
            )
        elif action_name == "SEND_SLACK_MESSAGE":
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.SEND_SLACK_MESSAGE,
                params={
                    "channel": "{{slack_channel}}",
                    "message": "Processed invoice for customer {{customer_id}}",
                },
                verification=VerificationRule(
                    check_type=CheckType.MESSAGE_SENT,
                    expected_state={"delivered": True},
                    description="Verify alert message was posted to Slack team channel",
                ),
            )
        else:
            # Safe default fallback action step
            step = ActionStep(
                step=step_counter,
                type=ControlledActionType.READ_EMAIL,
                params={"email_id": "{{email_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.EMAIL_OPENED,
                    expected_state={"retrieved": True},
                    description="Assert email action completed",
                ),
            )

        actions.append(step)
        step_counter += 1

    workflow = Workflow(
        name=intent.intent_label,
        description=intent.summary,
        trigger=TriggerConfig(
            type="EVENT_TRIGGER",
            source=intent.trigger_event_type,
            filter_criteria={"confidence_threshold": candidate.confidence},
        ),
        variables=variables,
        actions=actions,
        conditions=[
            "Target email must have matching invoice attachment",
            "Customer record must exist in CRM before update",
        ],
    )

    # Self-verify via WorkflowValidator before returning
    return WorkflowValidator.assert_valid(workflow)


class WorkflowGenerator:
    """Generates structured, validated Workflow JSON from WorkflowCandidate and WorkflowIntent."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.client = llm_client or LLMClient()

    def generate_workflow(
        self,
        candidate: WorkflowCandidate,
        intent: WorkflowIntent,
        store: EventStore,
    ) -> Tuple[Workflow, bool, Optional[str]]:
        """Generate verified Workflow instance.
        
        Returns:
            (workflow, was_fallback, audit_note)
        """
        exemplar_events: List[StoredEvent] = []
        for eid in candidate.event_sequence:
            item = store.get_by_id(eid)
            if item:
                exemplar_events.append(item)

        user_prompt = f"""Inferred Intent:
Label: {intent.intent_label}
Summary: {intent.summary}
Trigger Event: {intent.trigger_event_type}
Suggested Variables: {intent.suggested_variables}

Candidate Signature: {candidate.signature}
Pattern Steps: {candidate.action_pattern}
Candidate ID: {candidate.candidate_id}

Exemplar Events Context:
"""
        for e in exemplar_events:
            user_prompt += f"- Step [{e.event.application.value}:{e.event.action.value}] target='{e.event.target}', metadata={e.event.metadata}\n"

        fallback_fn = lambda: generate_fallback_workflow(candidate, intent, exemplar_events)

        raw_workflow, was_fallback, note = self.client.call_structured(
            system_prompt=SYSTEM_WORKFLOW_GEN_PROMPT,
            user_prompt=user_prompt,
            response_schema=Workflow,
            fallback_fn=fallback_fn,
        )

        # Gatekeeper: run through WorkflowValidator
        validation_res = WorkflowValidator.validate(raw_workflow)
        if not validation_res.valid:
            # Fall back safely rather than propagating an invalid workflow
            safe_wf = fallback_fn()
            return (
                safe_wf,
                True,
                f"Generated workflow failed strict validation ({'; '.join(validation_res.errors)}). Engaged safe fallback.",
            )

        return raw_workflow, was_fallback, note


if __name__ == "__main__":
    from datetime import datetime, timezone
    from backend.capture.service import CaptureService
    from backend.discovery.detector import RepetitionDetector
    from backend.ai.understanding import IntentInferer

    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    # 2 runs of golden workflow
    for r in range(2):
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_{r}"},
            {"app": "gmail", "action": "download", "target": f"att_{r}.pdf"},
            {"app": "crm", "action": "search_customer", "target": f"cust_{r}"},
            {"app": "crm", "action": "update_customer", "target": f"cust_{r}"},
            {"app": "slack", "action": "send_slack", "target": "#alerts"},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    inferer = IntentInferer()
    intent, _, _ = inferer.infer_intent(candidates[0], service.store)

    generator = WorkflowGenerator()
    wf, was_fallback, note = generator.generate_workflow(candidates[0], intent, service.store)

    print(f"1. Workflow Name:        {wf.name}")
    print(f"2. Steps Count:          {len(wf.actions)}")
    print(f"3. Execution Mode:        {'Safe Fallback' if was_fallback else 'Real OpenAI'}")
    print(f"4. First Step Action:    {wf.actions[0].type.value}")
    print(f"5. Final Step Action:    {wf.actions[-1].type.value}")
    print(f"6. Validator Assertion:  Passed with 0 errors!")
