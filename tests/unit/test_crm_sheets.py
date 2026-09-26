"""Unit tests for RealSheetsCRMConnector using mocked Google Sheets API service."""

import pytest
from unittest.mock import MagicMock

from backend.execution.connectors.crm_sheets import RealSheetsCRMConnector, SheetsCRMError


def make_mock_sheets_service():
    service = MagicMock()
    
    sheet_data = {
        "headers": ["customer_id", "name", "email", "invoice_status"],
        "rows": [
            ["cust_acme_corp", "Acme Corp", "billing@acme.com", "PENDING"]
        ]
    }
    
    def mock_get(**kwargs):
        range_str = kwargs.get("range", "")
        mock_req = MagicMock()
        # Check specific range first!
        if "A1:Z100" in range_str:
            mock_req.execute.return_value = {"values": [sheet_data["headers"]] + sheet_data["rows"]}
        elif "A1:Z1" in range_str:
            mock_req.execute.return_value = {"values": [sheet_data["headers"]]}
        elif "D2" in range_str:
            mock_req.execute.return_value = {"values": [[sheet_data["rows"][0][3]]]}
        else:
            mock_req.execute.return_value = {"values": [["PROCESSED"]]}
        return mock_req

    def mock_update(**kwargs):
        range_str = kwargs.get("range", "")
        body = kwargs.get("body", {})
        val = body.get("values", [[]])[0][0]
        if "D2" in range_str:
            sheet_data["rows"][0][3] = str(val)
        mock_req = MagicMock()
        mock_req.execute.return_value = {"updatedCells": 1}
        return mock_req

    service.spreadsheets().values().get.side_effect = mock_get
    service.spreadsheets().values().update.side_effect = mock_update
    return service


def test_sheets_crm_search_customer_found():
    service = make_mock_sheets_service()
    connector = RealSheetsCRMConnector(service=service, spreadsheet_id="test_sheet_id_123")
    res = connector.search_customer("cust_acme_corp")
    assert res["exists"] is True
    assert res["customer_id"] == "cust_acme_corp"
    assert res["row_index"] == 2
    assert res["customer"]["invoice_status"] == "PENDING"

    state = connector.verify_state("SEARCH_CUSTOMER", res)
    assert state["exists"] is True


def test_sheets_crm_search_customer_not_found():
    service = make_mock_sheets_service()
    connector = RealSheetsCRMConnector(service=service, spreadsheet_id="test_sheet_id_123")
    res = connector.search_customer("non_existent_cust")
    assert res["exists"] is False
    assert res["customer"] is None


def test_sheets_crm_update_customer_verified():
    service = make_mock_sheets_service()
    connector = RealSheetsCRMConnector(service=service, spreadsheet_id="test_sheet_id_123")
    res = connector.update_customer("cust_acme_corp", {"invoice_status": "PROCESSED"})
    assert res["updated"] is True
    assert res["fields_updated"]["invoice_status"] == "PROCESSED"

    state = connector.verify_state("UPDATE_CUSTOMER", res)
    assert state["invoice_status"] == "PROCESSED"


def test_sheets_crm_raises_when_no_sheet_id():
    connector = RealSheetsCRMConnector(spreadsheet_id="")
    with pytest.raises(SheetsCRMError, match="No Google Sheet ID provided"):
        connector.search_customer("cust_acme_corp")
