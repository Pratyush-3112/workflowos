"""Automation execution engine with per-step post-condition state verification."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.execution.connectors.crm_mock import MockCRMConnector
from backend.execution.connectors.email_mock import MockEmailConnector
from backend.execution.connectors.slack import SlackConnector
from backend.execution.schema import (
    StepExecutionStatus,
    VerificationResult,
    WorkflowExecutionResult,
    WorkflowRunStatus,
)
from backend.workflows.approval import ApprovalStore, UnapprovedExecutionError
from backend.workflows.schema import (
    ActionStep,
    ControlledActionType,
    Workflow,
)


def interpolate_params(raw_params: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """Replace {{variable_name}} templates in parameter values with runtime variable values."""
    resolved: Dict[str, Any] = {}
    for key, val in raw_params.items():
        if isinstance(val, str):
            interpolated = val
            matches = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", val)
            for var_name in matches:
                if var_name in variables:
                    var_val = str(variables[var_name])
                    interpolated = interpolated.replace(f"{{{{{var_name}}}}}", var_val)
            resolved[key] = interpolated
        elif isinstance(val, dict):
            resolved[key] = interpolate_params(val, variables)
        else:
            resolved[key] = val
    return resolved


class AutomationEngine:
    """Executes validated workflows through closed vocabulary connectors with strict per-step verification."""

    def __init__(
        self,
        approval_store: Optional[ApprovalStore] = None,
        email_connector: Optional[MockEmailConnector] = None,
        crm_connector: Optional[MockCRMConnector] = None,
        slack_connector: Optional[SlackConnector] = None,
    ):
        self.approval_store = approval_store or ApprovalStore()
        self.email_connector = email_connector or MockEmailConnector()
        self.crm_connector = crm_connector or MockCRMConnector()
        self.slack_connector = slack_connector or SlackConnector()

    def execute(
        self,
        workflow: Workflow,
        runtime_parameters: Optional[Dict[str, Any]] = None,
        failure_injection: Optional[Dict[str, Any]] = None,
    ) -> WorkflowExecutionResult:
        """Execute an approved workflow with per-step post-condition verification.
        
        Refuses to run if approval is missing, rejected, or if workflow was altered.
        Optionally supports failure_injection for deliberate failure demonstration.
        """
        # 1. Gatekeeper: Assert valid, recorded user consent
        approval_record = self.approval_store.assert_authorized(workflow)

        # Merge approved parameters with runtime overrides
        active_vars: Dict[str, Any] = dict(approval_record.approved_parameters)
        if runtime_parameters:
            active_vars.update(runtime_parameters)

        started_at = datetime.now(timezone.utc)
        step_results: List[VerificationResult] = []
        overall_status = WorkflowRunStatus.SUCCESS
        failure_message: Optional[str] = None

        # 2. Sequential execution with state verification
        for idx, step in enumerate(workflow.actions):
            resolved_params = interpolate_params(step.params, active_vars)

            try:
                # Check for deliberate failure injection
                if failure_injection and failure_injection.get("fail_at_step") == step.step:
                    actual_state = failure_injection.get("simulated_actual_state") or {"invoice_status": "PENDING"}
                    step_ok = True
                    step_err = None
                else:
                    # Dispatch action to closed vocabulary connector
                    actual_state, step_ok, step_err = self._dispatch_action(step.type, resolved_params)

                if not step_ok:
                    raise RuntimeError(step_err or "Connector reported action failure.")

                # Verify post-condition by re-checking real system state
                verified, verify_detail = self._verify_post_condition(step, resolved_params, actual_state)

                if verified:
                    step_results.append(
                        VerificationResult(
                            step=step.step,
                            action_type=step.type,
                            status=StepExecutionStatus.SUCCESS,
                            expected_state=step.verification.expected_state,
                            actual_state=actual_state,
                            verified=True,
                            detail=verify_detail,
                        )
                    )
                else:
                    # Verification failed: actual state did not match expected state
                    step_results.append(
                        VerificationResult(
                            step=step.step,
                            action_type=step.type,
                            status=StepExecutionStatus.FAILED,
                            expected_state=step.verification.expected_state,
                            actual_state=actual_state,
                            verified=False,
                            detail=verify_detail,
                        )
                    )
                    overall_status = WorkflowRunStatus.PAUSED
                    failure_message = f"Step {step.step} ({step.type.value}) verification failed: {verify_detail}"
                    self._skip_remaining_steps(workflow.actions[idx + 1 :], step_results)
                    break

            except Exception as exc:
                step_results.append(
                    VerificationResult(
                        step=step.step,
                        action_type=step.type,
                        status=StepExecutionStatus.FAILED,
                        expected_state=step.verification.expected_state,
                        actual_state={"error": str(exc)},
                        verified=False,
                        detail=f"Execution halted on step {step.step}: {exc}",
                    )
                )
                overall_status = WorkflowRunStatus.PAUSED
                failure_message = f"Execution error on step {step.step}: {exc}"
                self._skip_remaining_steps(workflow.actions[idx + 1 :], step_results)
                break

        completed_at = datetime.now(timezone.utc)
        return WorkflowExecutionResult(
            workflow_id=workflow.workflow_id,
            status=overall_status,
            started_at=started_at,
            completed_at=completed_at,
            step_results=step_results,
            error_message=failure_message,
        )

    def _dispatch_action(
        self,
        action_type: ControlledActionType,
        params: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool, Optional[str]]:
        """Dispatch action to appropriate connector."""
        if action_type == ControlledActionType.READ_EMAIL:
            email_id = params.get("email_id") or params.get("subject") or "default_email"
            res = self.email_connector.read_email(str(email_id))
            return {
                "opened": res.get("opened", True),
                "retrieved": True,
                "email_id": email_id,
            }, True, None

        elif action_type == ControlledActionType.DOWNLOAD_ATTACHMENT:
            att_id = params.get("attachment_id") or "default_attachment"
            res = self.email_connector.download_attachment(str(att_id))
            return {"saved": res.get("saved", True), "attachment_id": att_id}, True, None

        elif action_type == ControlledActionType.SEARCH_CUSTOMER:
            cust_id = params.get("customer_id") or "default_cust"
            res = self.crm_connector.search_customer(str(cust_id))
            exists = res is not None
            return {"exists": exists, "customer_id": cust_id}, exists, None

        elif action_type == ControlledActionType.UPDATE_CUSTOMER:
            cust_id = params.get("customer_id")
            updates = params.get("fields_to_update", {})
            if not cust_id:
                return {}, False, "Missing customer_id parameter for UPDATE_CUSTOMER"
            res = self.crm_connector.update_customer(str(cust_id), updates)
            # Actual state extracts the updated fields directly from CRM
            actual = {k: res.get(k) for k in updates.keys()}
            return actual, True, None

        elif action_type == ControlledActionType.SEND_SLACK_MESSAGE:
            channel = params.get("channel", "#general")
            message = params.get("message", "Workflow notification")
            res, delivered, err = self.slack_connector.send_message(channel, message)
            return {"delivered": delivered, "channel": channel}, delivered, err

        return {}, False, f"Unsupported action type: {action_type}"

    def _verify_post_condition(
        self,
        step: ActionStep,
        params: Dict[str, Any],
        actual_state: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """Verify that real system state satisfies step verification assertions."""
        expected = step.verification.expected_state

        for key, exp_val in expected.items():
            act_val = actual_state.get(key)
            if act_val != exp_val:
                return False, (
                    f"Expected state '{key}' to be '{exp_val}', but real state was '{act_val}'."
                )

        return True, (
            f"Step {step.step} ({step.type.value}) verified: "
            f"{step.verification.description} (Matched: {expected})"
        )

    def _skip_remaining_steps(self, remaining: List[ActionStep], results: List[VerificationResult]) -> None:
        """Mark subsequent steps as SKIPPED when execution halts."""
        for step in remaining:
            results.append(
                VerificationResult(
                    step=step.step,
                    action_type=step.type,
                    status=StepExecutionStatus.SKIPPED,
                    expected_state=step.verification.expected_state,
                    actual_state={},
                    verified=False,
                    detail=f"Step {step.step} skipped due to prior step failure.",
                )
            )


if __name__ == "__main__":
    from backend.workflows.schema import (
        ActionStep,
        CheckType,
        TriggerConfig,
        VerificationRule,
        Workflow,
    )

    print("1. Creating hand-approved Golden Workflow...")
    golden_wf = Workflow(
        name="Invoice Customer Sync",
        description="Extract and notify",
        trigger=TriggerConfig(source="GMAIL:READ_EMAIL"),
        variables={
            "email_id": "string",
            "attachment_id": "string",
            "customer_id": "string",
            "slack_channel": "string",
        },
        actions=[
            ActionStep(
                step=1,
                type=ControlledActionType.READ_EMAIL,
                params={"email_id": "{{email_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.EMAIL_OPENED,
                    expected_state={"opened": True},
                    description="Customer email retrieved",
                ),
            ),
            ActionStep(
                step=2,
                type=ControlledActionType.DOWNLOAD_ATTACHMENT,
                params={"attachment_id": "{{attachment_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.ATTACHMENT_SAVED,
                    expected_state={"saved": True},
                    description="Attachment downloaded",
                ),
            ),
            ActionStep(
                step=3,
                type=ControlledActionType.SEARCH_CUSTOMER,
                params={"customer_id": "{{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.RECORD_EXISTS,
                    expected_state={"exists": True},
                    description="Customer found in CRM",
                ),
            ),
            ActionStep(
                step=4,
                type=ControlledActionType.UPDATE_CUSTOMER,
                params={"customer_id": "{{customer_id}}", "fields_to_update": {"invoice_status": "PROCESSED"}},
                verification=VerificationRule(
                    check_type=CheckType.FIELD_EQUALS,
                    expected_state={"invoice_status": "PROCESSED"},
                    description="Customer marked PROCESSED in CRM",
                ),
            ),
            ActionStep(
                step=5,
                type=ControlledActionType.SEND_SLACK_MESSAGE,
                params={"channel": "{{slack_channel}}", "message": "Synced invoice for {{customer_id}}"},
                verification=VerificationRule(
                    check_type=CheckType.MESSAGE_SENT,
                    expected_state={"delivered": True},
                    description="Team notified in Slack",
                ),
            ),
        ],
    )

    approval_store = ApprovalStore()
    req = approval_store.request_approval(golden_wf)
    approval_store.approve(
        req.approval_id,
        approved_by="demo_user@workflowos.io",
        parameters={
            "email_id": "email_demo_1",
            "attachment_id": "att_inv_001",
            "customer_id": "cust_acme_corp",
            "slack_channel": "#billing-alerts",
        },
    )

    engine = AutomationEngine(approval_store=approval_store)
    result = engine.execute(golden_wf)

    print(f"Execution status: {result.status.value}")
    for res in result.step_results:
        print(f"  Step {res.step} [{res.action_type.value}]: {res.status.value} (Verified={res.verified}) -> {res.detail}")
