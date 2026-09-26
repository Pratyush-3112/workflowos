"""Connectors package providing real Slack and realistic mock CRM and Gmail executors."""

from backend.execution.connectors.crm_mock import MockCRMConnector
from backend.execution.connectors.email_mock import MockEmailConnector
from backend.execution.connectors.slack import SlackConnector

__all__ = [
    "MockEmailConnector",
    "MockCRMConnector",
    "SlackConnector",
]
