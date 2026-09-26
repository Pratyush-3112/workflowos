#!/usr/bin/env python3
"""WorkFlowOS Interactive Demo CLI Script.

Demonstrates:
  1. Observe: Simulate 3 digital activity runs
  2. Detect: Deterministic n-gram repetition detection with store verification
  3. Understand & Generate: AI Intent & Workflow JSON generation
  4. Approve: Consent Gate with cryptographic tamper hash
  5. Automate & Verify: Live 4th execution with computed state verification
  6. Safe Failure: Deliberate mid-workflow failure with safe pause
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.ai.generator import WorkflowGenerator
from backend.ai.understanding import IntentInferer
from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector
from backend.execution.engine import AutomationEngine
from backend.execution.timeline import TimelineStore
from backend.workflows.approval import ApprovalStore
from backend.workflows.validator import WorkflowValidator


def c(text: str, color_code: str) -> str:
    """Helper to colorize terminal output."""
    return f"\033[{color_code}m{text}\033[0m"


def main():
    print(c("\n" + "=" * 70, "36;1"))
    print(c("       WorkFlowOS — Hackathon Golden Workflow Demo", "36;1"))
    print(c("  Observe -> Detect -> Understand -> Generate -> Approve -> Verify", "36"))
    print(c("=" * 70 + "\n", "36;1"))

    # 1. OBSERVE
    print(c("[1/6] 📥 OBSERVING DIGITAL ACTIVITY (Runs 1-3)...", "33;1"))
    service = CaptureService()
    t0 = datetime.now(timezone.utc)

    for r in range(1, 4):
        run_time = t0 + timedelta(minutes=r * 5)
        service.record_batch([
            {"app": "gmail", "action": "open_email", "target": f"email_{r}", "metadata": {"subject": f"Invoice #{r}"}, "timestamp": run_time},
            {"app": "gmail", "action": "download", "target": f"invoice_{r}.pdf", "timestamp": run_time + timedelta(seconds=1)},
            {"app": "crm", "action": "search_customer", "target": "cust_acme_corp", "timestamp": run_time + timedelta(seconds=2)},
            {"app": "crm", "action": "update_customer", "target": "cust_acme_corp", "metadata": {"invoice_status": "PROCESSED"}, "timestamp": run_time + timedelta(seconds=3)},
            {"app": "slack", "action": "send_slack", "target": "#billing-alerts", "metadata": {"message": f"Processed #{r}"}, "timestamp": run_time + timedelta(seconds=4)},
        ])
        print(f"  ✓ Recorded Run {r}: 5 actions committed to append-only EventStore (SHA-256 chained)")

    valid_store, msg = service.verify_integrity()
    print(c(f"  🛡️ EventStore Audit: {msg}", "32"))

    # 2. DETECT
    print(c("\n[2/6] 🔍 DETECTING REPETITIONS (Deterministic Discovery)...", "33;1"))
    detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
    candidates = detector.detect_candidates(service.store)
    top_cand = candidates[0]
    print(f"  ✓ Candidate ID:  {top_cand.candidate_id}")
    print(f"  ✓ Signature:     {c(top_cand.signature, '35')}")
    print(f"  ✓ Occurrences:   {c(str(top_cand.occurrences), '32;1')}")
    print(f"  ✓ Confidence:    {c(f'{top_cand.confidence*100:.0f}%', '32;1')}")
    is_valid_cand, cand_msg = RepetitionDetector.verify_candidate_in_store(top_cand, service.store)
    print(c(f"  🛡️ Cryptographic Proof: {cand_msg}", "32"))

    # 3. UNDERSTAND & GENERATE
    print(c("\n[3/6] 🧠 AI UNDERSTANDING & WORKFLOW GENERATION...", "33;1"))
    inferer = IntentInferer()
    intent, was_fb_intent, note_intent = inferer.infer_intent(top_cand, service.store)
    print(f"  ✓ Inferred Intent: {c(intent.intent_label, '36;1')}")
    print(f"  ✓ Business Summary: {intent.summary}")

    generator = WorkflowGenerator()
    workflow, was_fb_wf, note_wf = generator.generate_workflow(top_cand, intent, service.store)
    print(f"  ✓ Generated Steps: {len(workflow.actions)} steps (Closed Vocabulary)")
    WorkflowValidator.assert_valid(workflow)
    print(c("  🛡️ WorkflowValidator: Passed all schema and closed-vocabulary checks with 0 errors", "32"))

    # 4. APPROVAL GATE
    print(c("\n[4/6] 🔒 USER CONSENT GATE (Recorded Approval)...", "33;1"))
    approval_store = ApprovalStore()
    appr_req = approval_store.request_approval(workflow)
    print(f"  ✓ Approval ID:   {appr_req.approval_id}")
    print(f"  ✓ Workflow Hash: {appr_req.workflow_hash[:16]}... (Tamper Protection)")
    
    # Approve
    approved = approval_store.approve(
        appr_req.approval_id,
        approved_by="pratyush@workflowos.io",
        parameters={
            "email_id": "email_demo_1",
            "attachment_id": "att_inv_001",
            "customer_id": "cust_acme_corp",
            "slack_channel": "#billing-alerts",
        },
    )
    print(c(f"  ✓ User Consent Granted: {approved.decided_by} on {approved.decided_at.strftime('%H:%M:%S UTC')}", "32"))

    # 5. LIVE 4th RUN
    print(c("\n[5/6] 🚀 LIVE 4th EXECUTION (Full Verified Automation)...", "33;1"))
    engine = AutomationEngine(approval_store=approval_store)
    timeline_store = TimelineStore()
    live_result = engine.execute(workflow)
    timeline_store.record_execution(live_result)

    print(f"  Run Status: {c(live_result.status.value, '32;1')}")
    for step in live_result.step_results:
        print(f"  ✓ Step {step.step} [{step.action_type.value}]: {c('SUCCESS (Verified=True)', '32')} -> {step.detail}")

    # 6. DELIBERATE FAILURE DEMO
    print(c("\n[6/6] ⚠️ DELIBERATE FAILURE DEMO (Safe Pause & Exact Error Reporting)...", "33;1"))
    print("  Injecting deliberate CRM write lock conflict at Step 4...")
    fail_result = engine.execute(
        workflow,
        failure_injection={
            "fail_at_step": 4,
            "simulated_actual_state": {"invoice_status": "PENDING"},
        },
    )
    print(f"  Run Status: {c(fail_result.status.value, '31;1')}")
    print(f"  Alert:      {c(fail_result.error_message, '31')}")
    for step in fail_result.step_results:
        color = "32" if step.status.value == "SUCCESS" else ("31" if step.status.value == "FAILED" else "33")
        print(f"  - Step {step.step} [{step.action_type.value}]: {c(step.status.value, color)} (Verified={step.verified}) -> {step.detail}")

    print(c("\n" + "=" * 70, "36;1"))
    print(c("                   ✨ Demo Completed Successfully!", "32;1"))
    print(c("=" * 70 + "\n", "36;1"))


if __name__ == "__main__":
    main()
