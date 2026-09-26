"""Unit tests for Workflow schema and WorkflowValidator."""

import pytest

from backend.workflows.schema import (
    ActionStep,
    CheckType,
    ControlledActionType,
    TriggerConfig,
    VerificationRule,
    Workflow,
)
from backend.workflows.validator import (
    ValidationResult,
    WorkflowValidationError,
    WorkflowValidator,
)


@pytest.fixture
def golden_workflow_fixture():
    return {
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


def test_golden_workflow_fixture_valid(golden_workflow_fixture):
    result = WorkflowValidator.validate(golden_workflow_fixture)
    assert result.valid is True
    assert len(result.errors) == 0

    # assert_valid returns typed Workflow instance
    wf = WorkflowValidator.assert_valid(golden_workflow_fixture)
    assert isinstance(wf, Workflow)
    assert len(wf.actions) == 5
    assert wf.actions[4].type == ControlledActionType.SEND_SLACK_MESSAGE


def test_validator_rejects_unauthorized_action_type(golden_workflow_fixture):
    # Alter step 2 to arbitrary invented action
    golden_workflow_fixture["actions"][1]["type"] = "EXECUTE_ARBITRARY_CODE"
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("prohibited action type" in err for err in result.errors)


def test_validator_rejects_non_sequential_step_indices(golden_workflow_fixture):
    # Skip step 2, jump from 1 to 3
    golden_workflow_fixture["actions"][1]["step"] = 3
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("Step numbers must be strictly sequential" in err for err in result.errors)


def test_validator_rejects_missing_required_params(golden_workflow_fixture):
    # Omit message in Slack step
    golden_workflow_fixture["actions"][4]["params"] = {"channel": "#general"}
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("Missing required parameter" in err for err in result.errors)


def test_validator_rejects_undeclared_template_variable(golden_workflow_fixture):
    # Reference {{unregistered_token}} in Slack message
    golden_workflow_fixture["actions"][4]["params"]["message"] = "Hello {{unregistered_token}}"
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("undeclared template variables" in err for err in result.errors)


def test_validator_rejects_empty_verification_state(golden_workflow_fixture):
    # Empty expected_state dictionary
    golden_workflow_fixture["actions"][0]["verification"]["expected_state"] = {}
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("expected_state must not be empty" in err for err in result.errors)


def test_validator_blocks_hazardous_content(golden_workflow_fixture):
    golden_workflow_fixture["actions"][3]["params"]["fields_to_update"] = {"danger": "rm -rf /data"}
    result = WorkflowValidator.validate(golden_workflow_fixture)

    assert result.valid is False
    assert any("hazardous pattern" in err for err in result.errors)


def test_assert_valid_raises_custom_exception(golden_workflow_fixture):
    golden_workflow_fixture["actions"][0]["type"] = "INVALID_ACTION"
    with pytest.raises(WorkflowValidationError) as exc_info:
        WorkflowValidator.assert_valid(golden_workflow_fixture)
    assert len(exc_info.value.errors) > 0
