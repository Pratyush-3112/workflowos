"""Manual verification script for real Gmail API connector.

Run this script after placing credentials.json in the project root:
    PYTHONPATH=. .venv/bin/python scripts/verify_real_gmail.py
"""

import sys
from backend.execution.connectors.gmail_real import RealGmailConnector


def main():
    print("Connecting to Real Gmail API...")
    try:
        connector = RealGmailConnector()
        print("Reading email (searching for invoice)...")
        read_res = connector.read_email(email_id="invoice")
        print(f"✓ Retrieved message: {read_res.get('email_id')} (Subject: {read_res.get('subject')})")
        print(f"✓ Post-condition verification: is_unread={read_res.get('is_unread')}")

        print("\nDownloading attachment...")
        att_res = connector.download_attachment(attachment_id="demo_att", email_id=read_res.get("email_id"))
        print(f"✓ Attachment saved to: {att_res.get('file_path')} ({att_res.get('file_size')} bytes)")
        print("\n✨ Real Gmail connector verified successfully!")
    except Exception as e:
        print(f"\n❌ Gmail verification error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
