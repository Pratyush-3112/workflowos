"""Strict workflow validator enforcing closed vocabulary, parameters, and safety rules."""

import re
from typing import Any, Dict, List, Set, Union

from pydantic import BaseModel, ConfigDict, Field

from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    VerificationRule,
    Workflow,
)


class WorkflowValidationError(ValueError):
    """Raised when a workflow fails strict safety or structural validation."""

    def __init__(self, message: str, errors: List[str]):
        super().__init__(message)
        self.errors = errors


class ValidationResult(BaseModel):
    """Structured report of workflow validation."""
    model_config = ConfigDict(frozen=True)

    valid: bool = Field(..., description="Whether workflow passed all validation checks")
    errors: List[str] = Field(default_factory=list, description="List of blocking validation errors")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking advisories")


REQUIRED_PARAMS_BY_ACTION: Dict[ControlledActionType, List[List[str]]] = {
    ControlledActionType.READ_EMAIL: [["email_id", "subject", "query"]],
    ControlledActionType.DOWNLOAD_ATTACHMENT: [["attachment_id", "filename"]],
    ControlledActionType.SEARCH_CUSTOMER: [["query", "customer_id", "domain"]],
    ControlledActionType.UPDATE_CUSTOMER: [["customer_id"], ["fields_to_update", "updates", "data"]],
    ControlledActionType.SEND_SLACK_MESSAGE: [["channel"], ["message", "text"]],
}

FORBIDDEN_KEYWORD_PATTERNS = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+database\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\beval\(", re.IGNORECASE),
    re.compile(r"\bexec\(", re.IGNORECASE),
]


class WorkflowValidator:
    """Enforces closed action vocabulary, parameter presence, variable bindings, and safety."""

    @classmethod
    def validate(cls, candidate: Union[Dict[str, Any], Workflow]) -> ValidationResult:
        """Validate workflow candidate and return comprehensive ValidationResult."""
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Structural / Pydantic schema validation
        workflow: Workflow
        if isinstance(candidate, Workflow):
            workflow = candidate
        elif isinstance(candidate, dict):
            # Check closed vocabulary first before Pydantic parsing for clear error message
            actions_raw = candidate.get("actions", [])
            if isinstance(actions_raw, list):
                for idx, act in enumerate(actions_raw):
                    if isinstance(act, dict):
                        act_type = act.get("type")
                        allowed_names = {a.value for a in ControlledActionType}
                        if act_type not in allowed_names:
                            errors.append(
                                f"Step {act.get('step', idx + 1)} uses prohibited action type '{act_type}'. "
                                f"Only closed vocabulary is allowed: {sorted(list(allowed_names))}"
                            )
            if errors:
                return ValidationResult(valid=False, errors=errors, warnings=warnings)

            try:
                workflow = Workflow.model_validate(candidate)
            except Exception as exc:
                errors.append(f"Workflow schema validation failed: {exc}")
                return ValidationResult(valid=False, errors=errors, warnings=warnings)
        else:
            errors.append(f"Expected dict or Workflow instance, got {type(candidate).__name__}")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        # 2. Check Action Steps count
        if not workflow.actions:
            errors.append("Workflow must contain at least one action step.")

        # 3. Check parameter requirements per step
        referenced_variables: Set[str] = set()

        for step in workflow.actions:
            # Check action type is strictly from closed vocabulary
            if step.type not in ControlledActionType:
                errors.append(
                    f"Step {step.step}: Action type '{step.type}' is not recognized in closed vocabulary."
                )

            # Check parameter presence requirements
            req_param_groups = REQUIRED_PARAMS_BY_ACTION.get(step.type, [])
            for group in req_param_groups:
                found = any(k in step.params and str(step.params[k]).strip() for k in group)
                if not found:
                    errors.append(
                        f"Step {step.step} ({step.type.value}): Missing required parameter from options {group}."
                    )

            # Scan for variable references like {{var_name}}
            for param_key, param_val in step.params.items():
                val_str = str(param_val)
                matches = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", val_str)
                for var in matches:
                    referenced_variables.add(var)

                # Check for hazardous payload text
                for pattern in FORBIDDEN_KEYWORD_PATTERNS:
                    if pattern.search(val_str):
                        errors.append(
                            f"Step {step.step} parameter '{param_key}' contains hazardous pattern '{pattern.pattern}'."
                        )

            # Check verification rule
            if not step.verification:
                errors.append(f"Step {step.step}: Mandatory verification rule is missing.")
            else:
                if not step.verification.expected_state:
                    errors.append(f"Step {step.step}: Verification expected_state must not be empty.")
                if not step.verification.description.strip():
                    errors.append(f"Step {step.step}: Verification description must not be blank.")

        # 4. Check template variable bindings
        defined_vars = set(workflow.variables.keys())
        missing_vars = referenced_variables - defined_vars
        if missing_vars:
            errors.append(
                f"Workflow references undeclared template variables: {sorted(list(missing_vars))}. "
                f"Declared variables: {sorted(list(defined_vars))}"
            )

        unused_vars = defined_vars - referenced_variables
        if unused_vars:
            warnings.append(f"Workflow defines variables not referenced in any step: {sorted(list(unused_vars))}")

        is_valid = len(errors) == 0
        return ValidationResult(valid=is_valid, errors=errors, warnings=warnings)

    @classmethod
    def assert_valid(cls, candidate: Union[Dict[str, Any], Workflow]) -> Workflow:
        """Validate and return Workflow instance, or raise WorkflowValidationError."""
        res = cls.validate(candidate)
        if not res.valid:
            raise WorkflowValidationError(
                f"Workflow validation failed with {len(res.errors)} error(s): {'; '.join(res.errors)}",
                errors=res.errors,
            )
        if isinstance(candidate, Workflow):
            return candidate
        return Workflow.model_validate(candidate)


if __name__ == "__main__":
    from backend.workflows.schema import CheckType, TriggerConfig

    print("1. Validating hand-written Golden Workflow fixture...")
    golden_workflow_dict = {
        "name": "Sync Invoices to CRM & Slack",
        "description": "Extract invoice from email, update customer record in CRM, and notify team on Slack",
        "trigger": {"type": "EVENT_TRIGGER", "source": "GMAIL:READ_EMAIL"},
        "variables": {
            "email_id": "string",
            "attachment_id": "string",
            "customer_id": "string",
            "slack_channel": "string",
        },
        "actions": [
            {
                "step": 1,
                "type": "READ_EMAIL",
                "params": {"email_id": "{{email_id}}"},
                "verification": {
                    "check_type": "EMAIL_OPENED",
                    "expected_state": {"retrieved": True},
                    "description": "Verify customer email was retrieved",
                },
            },
            {
                "step": 2,
                "type": "DOWNLOAD_ATTACHMENT",
                "params": {"attachment_id": "{{attachment_id}}"},
                "verification": {
                    "check_type": "ATTACHMENT_SAVED",
                    "expected_state": {"saved": True},
                    "description": "Verify attachment was saved locally",
                },
            },
            {
                "step": 3,
                "type": "SEARCH_CUSTOMER",
                "params": {"customer_id": "{{customer_id}}"},
                "verification": {
                    "check_type": "RECORD_EXISTS",
                    "expected_state": {"exists": True},
                    "description": "Verify customer account found in CRM",
                },
            },
            {
                "step": 4,
                "type": "UPDATE_CUSTOMER",
                "params": {
                    "customer_id": "{{customer_id}}",
                    "fields_to_update": {"invoice_status": "PROCESSED"},
                },
                "verification": {
                    "check_type": "FIELD_EQUALS",
                    "expected_state": {"invoice_status": "PROCESSED"},
                    "description": "Verify customer record field was updated",
                },
            },
            {
                "step": 5,
                "type": "SEND_SLACK_MESSAGE",
                "params": {
                    "channel": "{{slack_channel}}",
                    "message": "Processed invoice for customer {{customer_id}}",
                },
                "verification": {
                    "check_type": "MESSAGE_SENT",
                    "expected_state": {"delivered": True},
                    "description": "Verify alert message posted to Slack",
                },
            },
        ],
    }

    result = WorkflowValidator.validate(golden_workflow_dict)
    print(f"   Validation status: valid={result.valid}, errors={result.errors}, warnings={result.warnings}")

    print("\n2. Validating rejected workflow (illegal action + undeclared variable)...")
    invalid_dict = {
        "name": "Malicious Workflow",
        "description": "Tries arbitrary execution",
        "trigger": {"source": "SYSTEM"},
        "actions": [
            {
                "step": 1,
                "type": "RUN_SHELL_COMMAND",
                "params": {"command": "rm -rf /"},
                "verification": {
                    "check_type": "FIELD_EQUALS",
                    "expected_state": {},
                    "description": "Bad check",
                },
            }
        ],
    }
    invalid_result = WorkflowValidator.validate(invalid_dict)
    print(f"   Validation blocked: valid={invalid_result.valid}")
    for err in invalid_result.errors:
        print(f"   - Error: {err}")
