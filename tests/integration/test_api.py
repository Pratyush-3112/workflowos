"""Integration tests for FastAPI endpoints and end-to-end API flows."""

from fastapi.testclient import TestClient
import pytest

from backend.api.main import app

client = TestClient(app)


def test_api_system_status():
    res = client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert "repetition_detection" in data["architecture"]
    assert "post_condition_verification" in data["architecture"]
    assert "REAL" in data["architecture"]["repetition_detection"]


def test_api_golden_workflow_lifecycle():
    # 1. Simulate 3 runs
    sim_res = client.post("/api/simulate-golden-runs?runs_count=3")
    assert sim_res.status_code == 200
    assert sim_res.json()["candidates_discovered"] >= 1

    # 2. Get candidates
    cand_res = client.get("/api/candidates")
    assert cand_res.status_code == 200
    candidates = cand_res.json()
    assert len(candidates) >= 1
    top_cand = candidates[0]
    cand_id = top_cand["candidate_id"]

    # 3. Infer intent
    intent_res = client.post(f"/api/candidates/{cand_id}/infer-intent")
    assert intent_res.status_code == 200
    intent = intent_res.json()["intent"]
    assert len(intent["intent_label"]) > 0

    # 4. Generate workflow
    gen_res = client.post(f"/api/candidates/{cand_id}/generate-workflow")
    assert gen_res.status_code == 200
    wf_data = gen_res.json()
    wf_id = wf_data["workflow"]["workflow_id"]
    assert len(wf_data["workflow"]["actions"]) == 5

    # 5. Attempt execution BEFORE approval -> must be rejected with 403
    unauth_exec = client.post(
        f"/api/workflows/{wf_id}/execute",
        json={"runtime_parameters": {}},
    )
    assert unauth_exec.status_code == 403

    # 6. Approve workflow
    appr_res = client.post(
        f"/api/workflows/{wf_id}/approve",
        json={
            "approved_by": "api_test_user@workflowos.io",
            "parameters": {
                "email_id": "email_demo_1",
                "attachment_id": "att_inv_001",
                "customer_id": "cust_acme_corp",
                "slack_channel": "#billing-alerts",
            },
            "notes": "Approved for testing",
        },
    )
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "APPROVED"

    # 7. Execute approved workflow
    exec_res = client.post(
        f"/api/workflows/{wf_id}/execute",
        json={"runtime_parameters": {}},
    )
    assert exec_res.status_code == 200
    exec_body = exec_res.json()
    assert exec_body["status"] == "SUCCESS"
    assert len(exec_body["execution"]["step_results"]) == 5
    assert all(r["verified"] is True for r in exec_body["execution"]["step_results"])

    # 8. Query timeline
    timeline_res = client.get("/api/executions")
    assert timeline_res.status_code == 200
    assert len(timeline_res.json()) >= 1


def test_api_serves_dashboard_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "WorkFlowOS" in res.text
