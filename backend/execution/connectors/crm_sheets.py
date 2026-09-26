"""Real Google Sheets CRM connector for WorkFlowOS.

Treats a Google Sheet ('Customers' tab) as a live relational customer database
with search and update operations verified by reading cells directly back from
the spreadsheet to compute post-condition truth.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.integrations.google_auth import get_google_credentials

DEFAULT_SHEET_RANGE = "Customers!A1:Z100"


class SheetsCRMError(Exception):
    """Raised when an operation against Google Sheets CRM fails."""


class RealSheetsCRMConnector:
    """Production CRM connector executing customer operations on Google Sheets."""

    def __init__(self, service: Optional[Any] = None, spreadsheet_id: Optional[str] = None) -> None:
        self.spreadsheet_id = spreadsheet_id if spreadsheet_id is not None else os.environ.get("GOOGLE_SHEET_ID", "")
        self._service = service

    def _ensure_spreadsheet_id(self) -> str:
        if not self.spreadsheet_id:
            raise SheetsCRMError(
                "No Google Sheet ID provided. Set GOOGLE_SHEET_ID environment variable "
                "or pass spreadsheet_id to RealSheetsCRMConnector."
            )
        return self.spreadsheet_id

    def _get_service(self) -> Any:
        if self._service is None:
            creds = get_google_credentials()
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def search_customer(self, customer_id: str) -> dict[str, Any]:
        """Search the Google Sheet for a customer row matching customer_id."""
        sheet_id = self._ensure_spreadsheet_id()
        service = self._get_service()

        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=DEFAULT_SHEET_RANGE
            ).execute()
        except HttpError as e:
            raise SheetsCRMError(f"Google Sheets API error during search: {e}") from e

        rows = result.get("values", [])
        if not rows:
            return {"exists": False, "customer_id": customer_id, "customer": None}

        headers = [h.strip().lower() for h in rows[0]]
        cust_id_col = headers.index("customer_id") if "customer_id" in headers else 0

        for row_idx, row in enumerate(rows[1:], start=2):
            if len(row) > cust_id_col and row[cust_id_col].strip() == customer_id.strip():
                customer_data = {}
                for col_idx, header in enumerate(headers):
                    val = row[col_idx] if col_idx < len(row) else ""
                    customer_data[header] = val
                return {
                    "exists": True,
                    "customer_id": customer_id,
                    "customer": customer_data,
                    "row_index": row_idx
                }

        return {"exists": False, "customer_id": customer_id, "customer": None}

    def update_customer(self, customer_id: str, fields_to_update: dict[str, Any]) -> dict[str, Any]:
        """Update customer fields in the Google Sheet and verify by reading cell back immediately."""
        sheet_id = self._ensure_spreadsheet_id()
        service = self._get_service()

        # 1. Search to find target row
        search_res = self.search_customer(customer_id)
        if not search_res.get("exists"):
            raise SheetsCRMError(f"Cannot update customer: customer_id '{customer_id}' not found in sheet.")

        row_idx = search_res["row_index"]

        # Fetch headers to find target column letters
        header_res = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range="Customers!A1:Z1"
        ).execute()
        headers = [h.strip().lower() for h in header_res.get("values", [[]])[0]]

        updated_values = {}
        for field, new_value in fields_to_update.items():
            field_name = field.strip().lower()
            if field_name not in headers:
                raise SheetsCRMError(f"Field '{field}' does not exist as a column in the Customers sheet.")

            col_idx = headers.index(field_name)
            # Convert 0-indexed column to A1 notation column letter (0->A, 1->B, etc.)
            col_letter = chr(ord('A') + col_idx)
            cell_range = f"Customers!{col_letter}{row_idx}"

            # 2. Write cell update
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=cell_range,
                valueInputOption="USER_ENTERED",
                body={"values": [[str(new_value)]]}
            ).execute()

            # 3. Read back immediately for post-condition verification
            verify_res = service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=cell_range
            ).execute()
            read_back_rows = verify_res.get("values", [[]])
            actual_cell_val = read_back_rows[0][0] if read_back_rows and read_back_rows[0] else ""
            updated_values[field] = actual_cell_val

        return {
            "updated": True,
            "customer_id": customer_id,
            "fields_updated": updated_values
        }

    def verify_state(self, step_type: str, result_state: dict[str, Any]) -> dict[str, Any]:
        """Return actual observable state for engine post-condition comparison."""
        if step_type == "SEARCH_CUSTOMER":
            return {
                "exists": result_state.get("exists", False),
                "customer_id": result_state.get("customer_id")
            }
        elif step_type == "UPDATE_CUSTOMER":
            fields = result_state.get("fields_updated", {})
            return {
                "invoice_status": fields.get("invoice_status")
            }
        return result_state
