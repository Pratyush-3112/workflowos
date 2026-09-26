"""Realistic stateful mock CRM connector."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class MockCRMConnector:
    """Stateful mock for CRM operations (SEARCH_CUSTOMER, UPDATE_CUSTOMER)."""

    def __init__(self):
        self._customers: Dict[str, Dict[str, Any]] = {
            "cust_acme_corp": {
                "id": "cust_acme_corp",
                "name": "Acme Corporation",
                "email": "billing@acme.corp",
                "plan": "Enterprise",
                "invoice_status": "PENDING",
                "last_updated": "2026-09-01T00:00:00Z",
            },
            "cust_globex": {
                "id": "cust_globex",
                "name": "Globex Industries",
                "email": "finance@globex.org",
                "plan": "Growth",
                "invoice_status": "PENDING",
                "last_updated": "2026-09-01T00:00:00Z",
            },
        }

    def seed_customer(self, customer_id: str, name: str, email: str, invoice_status: str = "PENDING") -> None:
        """Seed a custom customer record."""
        self._customers[customer_id] = {
            "id": customer_id,
            "name": name,
            "email": email,
            "plan": "Standard",
            "invoice_status": invoice_status,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def search_customer(self, customer_id_or_query: str) -> Optional[Dict[str, Any]]:
        """Search customer by ID or name/email substring."""
        query_clean = customer_id_or_query.strip().lower()
        if query_clean in self._customers:
            return dict(self._customers[query_clean])

        for c in self._customers.values():
            if query_clean in c["name"].lower() or query_clean in c["email"].lower():
                return dict(c)

        # Dynamic fallback: if customer does not exist, provision stub so testing remains fluid
        new_cust = {
            "id": customer_id_or_query,
            "name": f"Customer {customer_id_or_query}",
            "email": f"{customer_id_or_query}@example.com",
            "plan": "Standard",
            "invoice_status": "PENDING",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        self._customers[customer_id_or_query] = new_cust
        return dict(new_cust)

    def update_customer(self, customer_id: str, fields_to_update: Dict[str, Any]) -> Dict[str, Any]:
        """Update specific customer fields and record timestamp."""
        customer = self._customers.get(customer_id)
        if not customer:
            customer = self.search_customer(customer_id)

        assert customer is not None
        for k, v in fields_to_update.items():
            customer[k] = v
        customer["last_updated"] = datetime.now(timezone.utc).isoformat()
        return dict(customer)

    def get_customer(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """Fetch current real CRM state for verification."""
        return self._customers.get(customer_id)
