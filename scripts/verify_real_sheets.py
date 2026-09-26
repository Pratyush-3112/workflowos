"""Manual verification script for real Google Sheets CRM connector.

Run this script after configuring credentials.json and setting GOOGLE_SHEET_ID:
    GOOGLE_SHEET_ID="your_sheet_id" PYTHONPATH=. .venv/bin/python scripts/verify_real_sheets.py
"""

import os
import sys
from backend.execution.connectors.crm_sheets import RealSheetsCRMConnector


def main():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        print("❌ Error: Set GOOGLE_SHEET_ID environment variable before running this script.", file=sys.stderr)
        print("Example: GOOGLE_SHEET_ID=\"1AbCdEfGhIjKlMn\" python scripts/verify_real_sheets.py", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to Google Sheets (ID: {sheet_id})...")
    try:
        connector = RealSheetsCRMConnector(spreadsheet_id=sheet_id)
        print("1. Searching for customer 'cust_acme_corp' in sheet...")
        search_res = connector.search_customer("cust_acme_corp")
        if not search_res.get("exists"):
            print("❌ Customer 'cust_acme_corp' not found in sheet. Ensure the sheet has a 'Customers' tab with customer_id header.", file=sys.stderr)
            sys.exit(1)

        print(f"✓ Found customer at row {search_res.get('row_index')}: {search_res.get('customer')}")

        print("\n2. Updating invoice_status to 'PROCESSED' and reading back cell...")
        update_res = connector.update_customer("cust_acme_corp", {"invoice_status": "PROCESSED"})
        actual_val = update_res.get("fields_updated", {}).get("invoice_status")
        print(f"✓ Post-condition verification: invoice_status read back as '{actual_val}'")

        if actual_val == "PROCESSED":
            print("\n✨ Google Sheets CRM connector verified successfully!")
        else:
            print(f"❌ Verification mismatch: expected 'PROCESSED', got '{actual_val}'", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"\n❌ Google Sheets verification error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
