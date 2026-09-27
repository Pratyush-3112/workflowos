"""FastAPI backend application for WorkFlowOS."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.ai.generator import WorkflowGenerator
from backend.ai.schema import WorkflowIntent
from backend.ai.understanding import IntentInferer
from backend.capture.service import CaptureService
from backend.discovery.detector import RepetitionDetector
from backend.discovery.schema import WorkflowCandidate
from backend.execution.engine import AutomationEngine
from backend.execution.schema import WorkflowExecutionResult, WorkflowRunStatus
from backend.execution.timeline import TimelineStore
from backend.workflows.approval import ApprovalRecord, ApprovalStore, UnapprovedExecutionError
from backend.workflows.schema import Workflow
from backend.workflows.validator import WorkflowValidator


app = FastAPI(
    title="WorkFlowOS API",
    description="Observe -> Detect Repetition -> Infer Intent -> Generate Workflow -> Approve -> Automate & Verify",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared in-memory and persistent system state
capture_service = CaptureService()
detector = RepetitionDetector(min_sequence_length=2, min_occurrences=2)
intent_inferer = IntentInferer()
workflow_generator = WorkflowGenerator()
approval_store = ApprovalStore()
engine = AutomationEngine(approval_store=approval_store)
timeline_store = TimelineStore()

# Global caches
cached_candidates: Dict[str, WorkflowCandidate] = {}
cached_intents: Dict[str, WorkflowIntent] = {}
cached_workflows: Dict[str, Workflow] = {}


# Request Models
class ApproveRequest(BaseModel):
    approved_by: str = "demo_user@workflowos.io"
    parameters: Dict[str, Any] = {}
    notes: Optional[str] = "Approved for execution"


class RejectRequest(BaseModel):
    rejected_by: str = "demo_user@workflowos.io"
    reason: str


class ExecuteRequest(BaseModel):
    runtime_parameters: Dict[str, Any] = {}


class SimulateFailureRequest(BaseModel):
    fail_at_step: int = 4
    simulated_actual_state: Dict[str, Any] = {"invoice_status": "PENDING"}


@app.post("/api/workflows/{workflow_id}/simulate-failure")
def simulate_workflow_failure(workflow_id: str, req: SimulateFailureRequest) -> Dict[str, Any]:
    """Deliberately force a post-condition verification failure mid-workflow to prove safe pause behavior.

    This endpoint always uses mock connectors for Steps 1–3, regardless of GMAIL_MODE/CRM_MODE.

    Rationale: the demo proves the SAFETY MECHANISM — that the engine detects a real-vs-expected
    state mismatch and halts safely. The real connectors are already exercised by the normal
    Execute path. Running real Gmail/Sheets during a simulation demo would require a real invoice
    email in the inbox and a real Sheet row, and would fail unpredictably in any environment.

    The injected step (default Step 4) receives a fabricated actual_state that intentionally
    mismatches expected_state, triggering PAUSED + all downstream steps SKIPPED.
    """
    wf = cached_workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    # Always approve with known-good demo parameters so template interpolation succeeds
    # against the mock connectors for Steps 1–3.
    appr = approval_store.get_by_workflow_id(workflow_id)
    demo_params = {
        "email_id": "email_demo_1",
        "attachment_id": "att_inv_001",
        "customer_id": "cust_acme_corp",
        "slack_channel": "#billing-alerts",
    }
    if not appr:
        appr_id = approval_store.request_approval(wf).approval_id
        approval_store.approve(appr_id, approved_by="failure_demo_user", parameters=demo_params)
    elif appr.status.value != "APPROVED":
        approval_store.approve(appr.approval_id, approved_by="failure_demo_user", parameters=demo_params)
    # If already APPROVED, engine will use whatever parameters were approved — that's fine.

    # Use a dedicated mock engine so the demo never makes live Gmail/Sheets API calls.
    # Real connector behaviour is proven by the normal Execute run; this demo only needs
    # to show the verification halt mechanism.
    from backend.execution.connectors.crm_mock import MockCRMConnector
    from backend.execution.connectors.email_mock import MockEmailConnector
    demo_engine = AutomationEngine(
        approval_store=approval_store,
        email_connector=MockEmailConnector(),
        crm_connector=MockCRMConnector(),
    )

    execution_result = demo_engine.execute(
        wf,
        failure_injection={
            "fail_at_step": req.fail_at_step,
            "simulated_actual_state": req.simulated_actual_state,
        },
    )
    timeline_store.record_execution(execution_result)
    return {
        "status": execution_result.status.value,
        "execution": execution_result.model_dump(),
    }




@app.get("/api/system/status")
def get_system_status() -> Dict[str, Any]:
    """Report real vs mocked integration architecture (reads env at request time)."""
    gmail_mode = os.environ.get("GMAIL_MODE", "mock").strip().lower()
    crm_mode = os.environ.get("CRM_MODE", "mock").strip().lower()

    gmail_label = (
        "REAL (Gmail API — reads live inbox via OAuth)"
        if gmail_mode == "real"
        else "MOCK (Realistic stateful inbox fixture — set GMAIL_MODE=real for live Gmail)"
    )
    crm_label = (
        f"REAL (Google Sheets CRM — Sheet ID {os.environ.get('GOOGLE_SHEET_ID', '?')})"
        if crm_mode == "real"
        else "MOCK (Realistic stateful customer DB fixture — set CRM_MODE=real for live Sheets)"
    )

    return {
        "connector_modes": {
            "gmail_mode": gmail_mode,
            "crm_mode": crm_mode,
        },
        "architecture": {
            "repetition_detection": "REAL (Deterministic n-gram analysis + cryptographic event history verification)",
            "ai_intent_understanding": "REAL (OpenAI API with JSON Mode + structured schema fallback)",
            "ai_workflow_generation": "REAL (OpenAI API with closed action vocabulary validation)",
            "approval_gatekeeper": "REAL (Cryptographic SHA-256 workflow tamper detection)",
            "slack_integration": (
                "REAL (Slack Web API chat.postMessage)"
                if engine.slack_connector.is_configured
                else "SIMULATED (Set SLACK_BOT_TOKEN for real network delivery)"
            ),
            "gmail_integration": gmail_label,
            "crm_integration": crm_label,
            "post_condition_verification": "REAL (Computes ground-truth state diffs per step)",
        },
        "event_count": len(capture_service.store),
        "executions_count": len(timeline_store),
    }


@app.post("/api/simulate-golden-runs")
def simulate_golden_runs(runs_count: int = 3) -> Dict[str, Any]:
    """Simulate runs of the Golden Workflow to trigger repetition detection."""
    existing_events = capture_service.store.get_all()
    if existing_events:
        base_time = max(datetime.now(timezone.utc), existing_events[-1].event.timestamp + timedelta(seconds=5))
    else:
        base_time = datetime.now(timezone.utc)

    seeded_runs = []
    for r in range(1, runs_count + 1):
        run_time = base_time + timedelta(seconds=r * 10)
        actions = [
            {"app": "gmail", "action": "open_email", "target": f"email_run_{r}", "params": {"subject": f"Invoice #{r} - Acme Corp"}, "timestamp": run_time},
            {"app": "gmail", "action": "download", "target": f"invoice_{r}.pdf", "timestamp": run_time + timedelta(seconds=1)},
            {"app": "crm", "action": "search_customer", "target": f"cust_acme_corp", "timestamp": run_time + timedelta(seconds=2)},
            {"app": "crm", "action": "update_customer", "target": f"cust_acme_corp", "params": {"invoice_status": "PROCESSED"}, "timestamp": run_time + timedelta(seconds=3)},
            {"app": "slack", "action": "send_slack", "target": "#billing-alerts", "params": {"message": f"Processed invoice #{r}"}, "timestamp": run_time + timedelta(seconds=4)},
        ]
        capture_service.record_batch(actions)
        seeded_runs.append({"run_number": r, "actions_count": len(actions)})

    # Auto-detect candidates
    candidates = detector.detect_candidates(capture_service.store)
    cached_candidates.clear()
    for c in candidates:
        cached_candidates[c.candidate_id] = c

    return {
        "status": "SUCCESS",
        "seeded_runs": seeded_runs,
        "total_events_in_store": len(capture_service.store),
        "candidates_discovered": len(candidates),
    }


@app.get("/api/candidates")
def list_candidates() -> List[Dict[str, Any]]:
    """List discovered repetitive workflow candidates."""
    candidates = detector.detect_candidates(capture_service.store)
    for c in candidates:
        cached_candidates[c.candidate_id] = c
    return [c.model_dump() for c in candidates]


@app.post("/api/candidates/{candidate_id}/infer-intent")
def infer_intent(candidate_id: str) -> Dict[str, Any]:
    """Run AI understanding on candidate to infer structured business intent."""
    candidate = cached_candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail=f"Candidate '{candidate_id}' not found.")

    intent, was_fallback, note = intent_inferer.infer_intent(candidate, capture_service.store)
    cached_intents[candidate_id] = intent

    return {
        "intent": intent.model_dump(),
        "was_fallback": was_fallback,
        "audit_note": note,
    }


@app.post("/api/candidates/{candidate_id}/generate-workflow")
def generate_workflow(candidate_id: str) -> Dict[str, Any]:
    """Run AI workflow generator to create verified executable Workflow."""
    candidate = cached_candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail=f"Candidate '{candidate_id}' not found.")

    intent = cached_intents.get(candidate_id)
    if not intent:
        intent, _, _ = intent_inferer.infer_intent(candidate, capture_service.store)
        cached_intents[candidate_id] = intent

    workflow, was_fallback, note = workflow_generator.generate_workflow(
        candidate, intent, capture_service.store
    )
    cached_workflows[workflow.workflow_id] = workflow

    # Automatically create pending approval request
    approval_req = approval_store.request_approval(workflow)

    return {
        "workflow": workflow.model_dump(),
        "approval_request": approval_req.model_dump(),
        "was_fallback": was_fallback,
        "audit_note": note,
    }


@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> Dict[str, Any]:
    """Get workflow and current approval status."""
    wf = cached_workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    appr = approval_store.get_by_workflow_id(workflow_id)
    return {
        "workflow": wf.model_dump(),
        "approval": appr.model_dump() if appr else None,
    }


@app.post("/api/workflows/{workflow_id}/approve")
def approve_workflow(workflow_id: str, req: ApproveRequest) -> Dict[str, Any]:
    """Record explicit user approval for a workflow."""
    wf = cached_workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    appr = approval_store.get_by_workflow_id(workflow_id)
    if not appr:
        appr = approval_store.request_approval(wf)

    updated = approval_store.approve(
        approval_id=appr.approval_id,
        approved_by=req.approved_by,
        parameters=req.parameters,
        notes=req.notes,
    )
    return {"status": "APPROVED", "approval_record": updated.model_dump()}


@app.post("/api/workflows/{workflow_id}/reject")
def reject_workflow(workflow_id: str, req: RejectRequest) -> Dict[str, Any]:
    """Record user rejection for a workflow."""
    wf = cached_workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    appr = approval_store.get_by_workflow_id(workflow_id)
    if not appr:
        raise HTTPException(status_code=400, detail="No approval request found.")

    updated = approval_store.reject(
        approval_id=appr.approval_id,
        rejected_by=req.rejected_by,
        reason=req.reason,
    )
    return {"status": "REJECTED", "approval_record": updated.model_dump()}


@app.post("/api/workflows/{workflow_id}/execute")
def execute_workflow(workflow_id: str, req: ExecuteRequest) -> Dict[str, Any]:
    """Execute approved workflow through AutomationEngine and record in TimelineStore."""
    wf = cached_workflows.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")

    try:
        execution_result = engine.execute(wf, runtime_parameters=req.runtime_parameters)
        timeline_store.record_execution(execution_result)
        return {
            "status": execution_result.status.value,
            "execution": execution_result.model_dump(),
        }
    except UnapprovedExecutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Execution error: {exc}")


@app.get("/api/executions")
def list_executions() -> List[Dict[str, Any]]:
    """List execution history."""
    return [e.model_dump() for e in timeline_store.list_executions()]


@app.get("/api/executions/{execution_id}")
def get_execution(execution_id: str) -> Dict[str, Any]:
    """Get single execution result with detailed per-step verification."""
    result = timeline_store.get_execution(execution_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Execution '{execution_id}' not found.")
    return result.model_dump()


# Frontend HTML Route
@app.get("/", response_class=HTMLResponse)
def get_dashboard_html() -> str:
    """Serve single-page WorkFlowOS dashboard UI."""
    html_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>WorkFlowOS Dashboard Loading...</h1>"
