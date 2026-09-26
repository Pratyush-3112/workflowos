"""Realistic stateful mock Gmail connector."""

from typing import Any, Dict, List, Optional


class MockEmailConnector:
    """Stateful mock for Gmail operations (READ_EMAIL, DOWNLOAD_ATTACHMENT)."""

    def __init__(self):
        self._emails: Dict[str, Dict[str, Any]] = {
            "email_demo_1": {
                "id": "email_demo_1",
                "sender": "billing@acme.corp",
                "subject": "Invoice INV-2026-001 for Acme Corp",
                "body": "Hi, please find attached your monthly invoice.",
                "attachments": [
                    {"id": "att_inv_001", "filename": "INV-2026-001.pdf", "size_kb": 142}
                ],
                "opened": False,
            },
            "email_demo_2": {
                "id": "email_demo_2",
                "sender": "finance@globex.org",
                "subject": "Globex Monthly Statement",
                "body": "Invoice attached.",
                "attachments": [
                    {"id": "att_inv_002", "filename": "Globex-Statement.pdf", "size_kb": 89}
                ],
                "opened": False,
            },
        }
        self._downloaded_attachments: Dict[str, Dict[str, Any]] = {}

    def seed_email(self, email_id: str, sender: str, subject: str, attachments: List[Dict[str, Any]]) -> None:
        """Seed a custom email into the mock inbox."""
        self._emails[email_id] = {
            "id": email_id,
            "sender": sender,
            "subject": subject,
            "body": "Seeded email content",
            "attachments": attachments,
            "opened": False,
        }

    def read_email(self, email_id: str) -> Dict[str, Any]:
        """Read and mark an email as opened."""
        email = self._emails.get(email_id)
        if not email:
            # Fallback to creating a dynamic email if user specifies an arbitrary ID
            email = {
                "id": email_id,
                "sender": "client@example.com",
                "subject": f"Invoice for {email_id}",
                "body": "Automatic invoice payload",
                "attachments": [{"id": f"att_{email_id}", "filename": f"{email_id}.pdf", "size_kb": 120}],
                "opened": False,
            }
            self._emails[email_id] = email

        email["opened"] = True
        return dict(email)

    def download_attachment(self, attachment_id: str) -> Dict[str, Any]:
        """Download an attachment and record it in local storage."""
        # Find attachment across emails
        target_att = None
        for email in self._emails.values():
            for att in email.get("attachments", []):
                if att["id"] == attachment_id or att["filename"] == attachment_id:
                    target_att = att
                    break
            if target_att:
                break

        if not target_att:
            # Create dynamic attachment record
            target_att = {"id": attachment_id, "filename": f"{attachment_id}.pdf", "size_kb": 120}

        saved_record = {
            "attachment_id": target_att["id"],
            "filename": target_att["filename"],
            "saved": True,
            "local_path": f"/downloads/{target_att['filename']}",
        }
        self._downloaded_attachments[attachment_id] = saved_record
        return saved_record

    def get_email_state(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Query real state of an email."""
        return self._emails.get(email_id)

    def is_attachment_saved(self, attachment_id: str) -> bool:
        """Check if an attachment was saved."""
        return attachment_id in self._downloaded_attachments
