"""Unit tests for RealGmailConnector using mocked Google API service."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from backend.execution.connectors.gmail_real import RealGmailConnector, GmailConnectorError


@pytest.fixture
def mock_gmail_service():
    service = MagicMock()
    
    # Mock messages.list
    list_mock = MagicMock()
    list_mock.execute.return_value = {"messages": [{"id": "msg_real_123"}]}
    service.users().messages().list.return_value = list_mock
    
    # Mock messages.get
    get_mock = MagicMock()
    get_mock.execute.return_value = {
        "id": "msg_real_123",
        "payload": {
            "headers": [{"name": "Subject", "value": "Invoice Acme Corp #1001"}],
            "parts": []
        }
    }
    service.users().messages().get.return_value = get_mock
    
    # Mock messages.modify (removing UNREAD)
    mod_mock = MagicMock()
    mod_mock.execute.return_value = {"id": "msg_real_123", "labelIds": ["INBOX"]}
    service.users().messages().modify.return_value = mod_mock
    
    return service


def test_real_gmail_connector_read_email_success(mock_gmail_service, tmp_path):
    connector = RealGmailConnector(service=mock_gmail_service, download_dir=tmp_path)
    
    res = connector.read_email(email_id="msg_real_123")
    assert res["opened"] is True
    assert res["retrieved"] is True
    assert res["email_id"] == "msg_real_123"
    assert res["state_verified"] is True
    
    state = connector.verify_state("READ_EMAIL", res)
    assert state["opened"] is True
    assert state["retrieved"] is True


def test_real_gmail_connector_download_attachment(mock_gmail_service, tmp_path):
    connector = RealGmailConnector(service=mock_gmail_service, download_dir=tmp_path)
    
    res = connector.download_attachment(attachment_id="att_real_99", email_id="msg_real_123", filename="test_inv.pdf")
    assert res["saved"] is True
    assert res["attachment_id"] == "att_real_99"
    assert Path(res["file_path"]).exists()
    assert res["file_size"] > 0
    
    state = connector.verify_state("DOWNLOAD_ATTACHMENT", res)
    assert state["saved"] is True
    assert state["attachment_id"] == "att_real_99"
