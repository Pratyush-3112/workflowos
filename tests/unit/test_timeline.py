"""Unit tests for TimelineStore execution recording and filtering."""

from datetime import datetime, timezone
import pytest

from backend.execution.schema import (
    StepExecutionStatus,
    VerificationResult,
    WorkflowExecutionResult,
    WorkflowRunStatus,
)
from backend.execution.timeline import TimelineStore
from backend.workflows.schema import ControlledActionType


def test_timeline_store_record_and_query():
    store = TimelineStore()
    assert len(store) == 0

    res1 = WorkflowExecutionResult(
        execution_id="exec_1",
        workflow_id="wf_1",
        status=WorkflowRunStatus.SUCCESS,
        started_at=datetime.now(timezone.utc),
        step_results=[
            VerificationResult(
                step=1,
                action_type=ControlledActionType.READ_EMAIL,
                status=StepExecutionStatus.SUCCESS,
                expected_state={"opened": True},
                actual_state={"opened": True},
                verified=True,
                detail="Email opened",
            )
        ],
    )

    res2 = WorkflowExecutionResult(
        execution_id="exec_2",
        workflow_id="wf_2",
        status=WorkflowRunStatus.PAUSED,
        started_at=datetime.now(timezone.utc),
        step_results=[],
    )

    store.record_execution(res1)
    store.record_execution(res2)

    assert len(store) == 2
    assert store.get_execution("exec_1") == res1
    assert store.get_execution("exec_2") == res2

    # Query in reverse order
    all_runs = store.list_executions()
    assert all_runs[0].execution_id == "exec_2"
    assert all_runs[1].execution_id == "exec_1"

    # Filter by workflow_id
    filtered = store.list_executions(workflow_id="wf_1")
    assert len(filtered) == 1
    assert filtered[0].execution_id == "exec_1"

    # Filter by status
    paused = store.list_executions(status=WorkflowRunStatus.PAUSED)
    assert len(paused) == 1
    assert paused[0].execution_id == "exec_2"
