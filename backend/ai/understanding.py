"""Intent inferer module analyzing WorkflowCandidate to infer user business intent."""

from typing import Dict, List, Optional, Tuple

from backend.ai.client import LLMClient
from backend.ai.schema import WorkflowIntent
from backend.discovery.schema import WorkflowCandidate
from backend.events.store import EventStore, StoredEvent


SYSTEM_INTENT_PROMPT = """You are WorkFlowOS AI Intent Engine.
Your job is to analyze repeated user digital activity patterns and infer the underlying business intent.
You MUST output valid JSON conforming exactly to the WorkflowIntent schema:
{
  "source_candidate_id": "<candidate_id>",
  "intent_label": "<short human title, max 6 words>",
  "summary": "<2-3 sentence business explanation of why the user repeats this sequence>",
  "trigger_event_type": "<the first step e.g. GMAIL:READ_EMAIL>",
  "suggested_variables": {
    "<variable_name>": "<description of dynamic value observed across occurrences>"
  },
  "confidence_rationale": "<reasoning explaining why this sequence is intentional>"
}
"""


def generate_fallback_intent(candidate: WorkflowCandidate, exemplar_events: List[StoredEvent]) -> WorkflowIntent:
    """Deterministic, robust fallback intent generator when LLM is unavailable or errors."""
    first_step = candidate.action_pattern[0]
    last_step = candidate.action_pattern[-1]

    # Infer variables from targets observed across exemplars
    suggested_vars: Dict[str, str] = {}
    for item in exemplar_events:
        app = item.event.application.value.lower()
        act = item.event.action.value.lower()
        if "customer" in act:
            suggested_vars["customer_id"] = "Identifier of target customer account"
        elif "email" in act:
            suggested_vars["email_id"] = "Identifier of customer email message"
        elif "attachment" in act:
            suggested_vars["attachment_name"] = "Name or ID of email file attachment"
        elif "slack" in act:
            suggested_vars["slack_channel"] = "Destination team notification channel"

    if not suggested_vars:
        suggested_vars = {"target_resource": "Dynamic target resource identifier"}

    # Generate title based on pattern
    apps = list(dict.fromkeys(p.split(":")[0].capitalize() for p in candidate.action_pattern))
    label = f"Automated {' ➔ '.join(apps)} Workflow"
    if "Gmail" in apps and "Crm" in apps and "Slack" in apps:
        label = "Sync Customer Invoices to CRM & Slack"

    summary = (
        f"Automates repeated {len(candidate.action_pattern)}-step sequence starting with {first_step} "
        f"and completing with {last_step}. Observed {candidate.occurrences} times with {candidate.confidence*100:.0f}% confidence."
    )

    return WorkflowIntent(
        source_candidate_id=candidate.candidate_id,
        intent_label=label,
        summary=summary,
        trigger_event_type=first_step,
        suggested_variables=suggested_vars,
        confidence_rationale=f"Observed exact contiguous repetition across {candidate.occurrences} instances in event history.",
    )


class IntentInferer:
    """Analyzes workflow candidates and produces structured WorkflowIntent."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.client = llm_client or LLMClient()

    def infer_intent(
        self,
        candidate: WorkflowCandidate,
        store: EventStore,
    ) -> Tuple[WorkflowIntent, bool, Optional[str]]:
        """Infer business intent for a detected WorkflowCandidate.
        
        Returns:
            (intent, was_fallback, audit_note)
        """
        # Fetch exemplar events to provide concrete context
        exemplar_events: List[StoredEvent] = []
        for event_id in candidate.event_sequence:
            entry = store.get_by_id(event_id)
            if entry:
                exemplar_events.append(entry)

        user_prompt = f"""Candidate Pattern:
Signature: {candidate.signature}
Occurrences: {candidate.occurrences}
Confidence: {candidate.confidence}
Candidate ID: {candidate.candidate_id}

Exemplar Events Context:
"""
        for item in exemplar_events:
            user_prompt += f"- [{item.event.application.value}] {item.event.action.value} -> target: '{item.event.target}', metadata: {item.event.metadata}\n"

        fallback_fn = lambda: generate_fallback_intent(candidate, exemplar_events)

        return self.client.call_structured(
            system_prompt=SYSTEM_INTENT_PROMPT,
            user_prompt=user_prompt,
            response_schema=WorkflowIntent,
            fallback_fn=fallback_fn,
        )


if __name__ == "__main__":
    from datetime import datetime, timezone
    from backend.capture.service import CaptureService
    from backend.discovery.detector import RepetitionDetector

    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    for r in range(2):
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"msg_{r}", "params": {"subject": "Invoice"}},
            {"app": "crm", "action": "update_customer", "target": f"cust_{r}", "params": {"paid": True}},
            {"app": "slack", "action": "send_slack", "target": "#ops", "params": {"text": "Done"}},
        ])

    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    assert len(candidates) > 0, "Expected candidate"

    inferer = IntentInferer()
    intent, was_fallback, note = inferer.infer_intent(candidates[0], service.store)

    print(f"1. Inferred Intent Label: {intent.intent_label}")
    print(f"2. Business Summary:     {intent.summary}")
    print(f"3. Inferred Variables:   {intent.suggested_variables}")
    print(f"4. Execution Mode:       {'Safe Fallback' if was_fallback else 'Real OpenAI'}")
    if note:
        print(f"   Audit Note:           {note}")
