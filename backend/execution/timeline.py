"""Execution timeline and observability store tracking workflow runs and verification results."""

from typing import Dict, List, Optional

from backend.execution.schema import WorkflowExecutionResult, WorkflowRunStatus


class TimelineStore:
    """In-memory and persistent observability store recording workflow execution timelines."""

    def __init__(self):
        self._executions: Dict[str, WorkflowExecutionResult] = {}
        self._history_order: List[str] = []

    def record_execution(self, result: WorkflowExecutionResult) -> None:
        """Record an execution result in the timeline."""
        if not isinstance(result, WorkflowExecutionResult):
            raise TypeError(f"Expected WorkflowExecutionResult, got {type(result).__name__}")
        self._executions[result.execution_id] = result
        if result.execution_id not in self._history_order:
            self._history_order.append(result.execution_id)

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecutionResult]:
        """Fetch execution result by execution ID."""
        return self._executions.get(execution_id)

    def list_executions(
        self,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowRunStatus] = None,
    ) -> List[WorkflowExecutionResult]:
        """List execution records in reverse chronological order."""
        results: List[WorkflowExecutionResult] = []
        for eid in reversed(self._history_order):
            item = self._executions[eid]
            if workflow_id and item.workflow_id != workflow_id:
                continue
            if status and item.status != status:
                continue
            results.append(item)
        return results

    def __len__(self) -> int:
        return len(self._executions)
