"""Connector factory for resolving real vs. mocked connectors based on environment variables."""

import os
from typing import Any

from backend.execution.connectors.crm_mock import MockCRMConnector
from backend.execution.connectors.crm_sheets import RealSheetsCRMConnector
from backend.execution.connectors.email_mock import MockEmailConnector
from backend.execution.connectors.gmail_real import RealGmailConnector
from backend.execution.connectors.slack import SlackConnector


def get_email_connector() -> Any:
    """Resolve email connector according to GMAIL_MODE env var.
    
    Defaults to MockEmailConnector.
    If GMAIL_MODE='real', loads RealGmailConnector with automatic fallback to mock on error.
    """
    mode = os.environ.get("GMAIL_MODE", "mock").strip().lower()
    if mode == "real":
        try:
            return RealGmailConnector()
        except Exception as e:
            # Safe kill-switch fallback
            return MockEmailConnector()
    return MockEmailConnector()


def get_crm_connector() -> Any:
    """Resolve CRM connector according to CRM_MODE env var.
    
    Defaults to MockCRMConnector.
    If CRM_MODE='real', loads RealSheetsCRMConnector with automatic fallback to mock on error.
    """
    mode = os.environ.get("CRM_MODE", "mock").strip().lower()
    if mode == "real":
        try:
            sheet_id = os.environ.get("GOOGLE_SHEET_ID")
            return RealSheetsCRMConnector(spreadsheet_id=sheet_id)
        except Exception as e:
            # Safe kill-switch fallback
            return MockCRMConnector()
    return MockCRMConnector()


def get_slack_connector() -> SlackConnector:
    """Resolve Slack connector."""
    return SlackConnector()
