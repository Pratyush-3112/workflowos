# WorkFlowOS

**Self-synthesizing workflow automation with cryptographic auditability and per-step post-condition state verification.**

[![Tests](https://img.shields.io/badge/pytest-66%20passed-34D399?style=flat-square&logo=pytest)](https://github.com/Pratyush-3112/workflowos)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-38BDF8?style=flat-square&logo=python)](https://python.org)

---

## Overview

WorkFlowOS passively observes multi-app user activity, detects repeating sequences using deterministic n-gram analysis, infers business intent via an LLM, generates a structured executable workflow from a closed action vocabulary, gates execution behind explicit user consent with cryptographic tamper protection, and then executes with per-step post-condition state verification. If any step's actual system state diverges from what was expected, execution halts and surfaces the exact mismatch — it never fabricates success.

The core design principle is that users should not have to program automations. The system observes, asks for permission, and verifies its own actions in reality.

**Limitations to be aware of upfront:** there is no persistent database (all state is in-memory and resets on server restart), no authentication on the dashboard, and the Slack integration requires a real bot token to deliver messages (it falls back to simulated delivery otherwise).

---

## Architecture

```
Raw Actions
    │
    ▼
Activity Capture          normalize & validate into immutable ActivityEvent models
    │
    ▼
Event Store               append-only; SHA-256 hash chain prevents history forgery
    │
    ▼
Repetition Detector       deterministic n-gram sliding window; temporal gating (max 300s gap)
    │
    ▼
AI Intent Inferer         OpenAI gpt-4o-mini with JSON Mode; deterministic fallback if no key
    │
    ▼
Workflow Generator        generates closed-vocabulary JSON; WorkflowValidator rejects any
    │                     hallucinated action types before the workflow reaches the engine
    ▼
User Approval Gate        SHA-256 workflow fingerprint; engine refuses to run if workflow
    │                     was altered after approval was recorded
    ▼
Automation Engine         sequential step dispatch → real connector → re-query state →
    │                     compare actual vs expected → PAUSED on any mismatch
    ▼
Timeline Store            immutable execution record with per-step verification results
```

---

## Component Status

| Component | Status | Details |
|---|---|---|
| Repetition Detection | **Real** | Deterministic n-gram analysis with SHA-256-verified event history |
| Event History Integrity | **Real** | Append-only SHA-256 hash chain; forged history is detected |
| AI Intent Understanding | **Real** | OpenAI API (`gpt-4o-mini`) with JSON Mode and single-retry self-repair; falls back to deterministic inference if `OPENAI_API_KEY` is not set |
| Workflow Generation | **Real** | Closed vocabulary enforced by `WorkflowValidator`; LLM cannot invent action types |
| User Approval & Tamper Gate | **Real** | SHA-256 workflow fingerprint; altered workflows are rejected at execution time |
| Post-Condition Verification | **Real** | Engine re-queries connector state after every step; surfaces exact diffs on mismatch |
| Gmail Connector | **Switchable** | Defaults to `MockEmailConnector` (stateful in-memory fixture). Set `GMAIL_MODE=real` with valid `credentials.json` + `token.json` to use `RealGmailConnector` via the Gmail API. See [Enabling Real Connectors](#enabling-real-connectors). |
| CRM Connector | **Switchable** | Defaults to `MockCRMConnector` (stateful in-memory fixture). Set `CRM_MODE=real` + `GOOGLE_SHEET_ID` to use `RealSheetsCRMConnector` against a real Google Sheet. See [Enabling Real Connectors](#enabling-real-connectors). |
| Slack Integration | **Switchable** | Set `SLACK_BOT_TOKEN` for real `chat.postMessage` delivery; simulates delivery otherwise |

---

## Setup

### Prerequisites

- Python 3.10+
- No external services required for mock mode (default)

### Install

```bash
git clone https://github.com/Pratyush-3112/workflowos.git
cd workflowos
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run tests

```bash
.venv/bin/pytest -v
```

All 66 tests pass with zero external network calls in under 1 second.

---

## Running Locally

### Mock mode (default — no credentials required)

```bash
./run.sh
# or equivalently:
.venv/bin/uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The header strip shows which connectors are active. In mock mode, Gmail and CRM are served by stateful in-memory fixtures — no OAuth, no network calls.

### Real connector mode

```bash
GMAIL_MODE=real \
CRM_MODE=real \
GOOGLE_SHEET_ID=<your-sheet-id> \
./run.sh
```

The dashboard header badges update immediately to reflect the live connector mode. See [Enabling Real Connectors](#enabling-real-connectors) for credential setup.

---

## Demo Walkthrough

The dashboard presents the full pipeline as four sequential steps.

**1. Ingest Events**
Click "Simulate 3 Prior Runs". Seeds 15 activity events (5 actions × 3 runs) into the append-only event store and runs the repetition detector. The detected candidate — `GMAIL:READ_EMAIL → GMAIL:DOWNLOAD_ATTACHMENT → CRM:SEARCH_CUSTOMER → CRM:UPDATE_CUSTOMER → SLACK:SEND_SLACK_MESSAGE` — appears with occurrence count and confidence score.

**2. Synthesize Workflow**
Click "AI Infer & Generate". Runs intent inference (LLM or deterministic fallback if no API key), then generates a parameter-templated workflow from the closed vocabulary. Result is a validated `Workflow` object with a SHA-256 fingerprint and a pending approval request.

**3. Authorize & Execute**
Click "Authorize & Execute". Records explicit user consent with the workflow hash and dispatches execution. All 5 steps run sequentially; each step's actual post-execution state is verified against the declared expected state. All steps show `SUCCESS / Verified: true`.

**4. Simulate Assertion Mismatch**
Click "Simulate Assertion Mismatch". Re-runs the workflow but injects a fabricated `actual_state` of `{"invoice_status": "PENDING"}` at Step 4, simulating a CRM write that left the field in the wrong state. Expected result:

- Steps 1–3: `SUCCESS`
- Step 4: `FAILED` — `Expected 'invoice_status' = 'PROCESSED', actual = 'PENDING'`
- Step 5: `SKIPPED` — downstream step suppressed to prevent a false Slack notification

> **Note:** Steps 1–3 of the failure simulation always run against mock connectors, even when `GMAIL_MODE=real`. This is intentional — the demo proves that the verification halt mechanism works, not that real connectors are functional (that is already demonstrated by step 3). Using mock connectors makes the demo deterministic and environment-independent. See [Design Decisions](#design-decisions).

---

## Enabling Real Connectors

### Prerequisites

1. A **Google Cloud project** with the Gmail API and Google Sheets API enabled.
2. An **OAuth 2.0 Desktop client** credential downloaded as `credentials.json` placed at the repo root.
3. A **Google Sheet** with a tab named exactly `Customers` containing at minimum these column headers (case-insensitive, any order): `customer_id`, `invoice_status`. Add a data row: `cust_acme_corp | OPEN`.

> `credentials.json` and `token.json` are in `.gitignore` and will never be committed.

### First-time OAuth authorization

Run once to generate `token.json` (opens a browser window for Google login):

```bash
.venv/bin/python scripts/verify_real_gmail.py
```

### Start with real connectors

```bash
GMAIL_MODE=real \
CRM_MODE=real \
GOOGLE_SHEET_ID=<your-sheet-id> \
./run.sh
```

The factory (`backend/execution/factory.py`) resolves the connector at startup. If credentials are missing or the API call fails, it automatically falls back to mock.

### Verify connectors independently

```bash
# Gmail
PYTHONPATH=. .venv/bin/python scripts/verify_real_gmail.py

# Sheets CRM
GOOGLE_SHEET_ID=<your-sheet-id> PYTHONPATH=. .venv/bin/python scripts/verify_real_sheets.py
```

---

## Project Structure

```
workflowos/
├── backend/
│   ├── events/               # ActivityEvent schema & SHA-256 append-only EventStore
│   ├── capture/              # Raw action normalization & CaptureService
│   ├── discovery/            # WorkflowCandidate schema & RepetitionDetector
│   ├── ai/                   # LLMClient, IntentInferer, WorkflowGenerator
│   ├── workflows/            # Workflow schema, WorkflowValidator, ApprovalStore
│   ├── execution/
│   │   ├── engine.py         # AutomationEngine — sequential dispatch & verification
│   │   ├── factory.py        # Connector resolver (reads GMAIL_MODE / CRM_MODE)
│   │   ├── schema.py         # WorkflowExecutionResult, VerificationResult
│   │   ├── timeline.py       # TimelineStore — immutable execution history
│   │   └── connectors/
│   │       ├── email_mock.py     # MockEmailConnector — stateful in-memory inbox
│   │       ├── gmail_real.py     # RealGmailConnector — Gmail API
│   │       ├── crm_mock.py       # MockCRMConnector — stateful in-memory customer DB
│   │       ├── crm_sheets.py     # RealSheetsCRMConnector — Google Sheets API
│   │       └── slack.py          # SlackConnector — real or simulated delivery
│   ├── integrations/
│   │   └── google_auth.py    # Centralized OAuth2 credential management with token caching
│   └── api/
│       └── main.py           # FastAPI application — all endpoints
├── frontend/
│   └── index.html            # Single-page dashboard (vanilla JS, no framework)
├── scripts/
│   ├── demo_cli.py           # Terminal demo runner
│   ├── verify_real_gmail.py  # Manual Gmail connector verification
│   └── verify_real_sheets.py # Manual Sheets CRM connector verification
├── tests/
│   ├── unit/                 # Schema, detector, engine, connectors, failure paths
│   ├── integration/          # API endpoint tests
│   └── e2e/                  # Full 5-step golden workflow end-to-end tests
├── requirements.txt
├── pytest.ini
└── run.sh
```

---

## Design Decisions

**Why is the failure-simulation demo always mocked, even in `GMAIL_MODE=real`?**
The `POST /simulate-failure` endpoint proves that the verification mechanism correctly detects a state mismatch and halts safely. It is not re-testing real connectors — that is already done by the normal Execute path. Using mock connectors makes the demo deterministic: it does not require a specific invoice email to exist in a real inbox, it will not mutate real external state, and it will not fail due to network conditions. This is documented as an explicit architectural choice in the endpoint, not a workaround.

**Why a closed action vocabulary?**
Allowing an LLM to generate arbitrary code or API calls eliminates the human's ability to audit what will actually happen before approving. A closed vocabulary (`READ_EMAIL`, `DOWNLOAD_ATTACHMENT`, `SEARCH_CUSTOMER`, `UPDATE_CUSTOMER`, `SEND_SLACK_MESSAGE`) means every action the system can take is known at design time and validated by `WorkflowValidator` before the user sees the workflow.

**Why SHA-256 workflow fingerprinting?**
The approval gate records a hash of the workflow at consent time. If anything in the workflow is modified between approval and execution — even a single parameter value — the hash mismatch is detected and execution is blocked. The user's consent is bound to the exact workflow they reviewed.

**Why no persistent database?**
All state is held in memory and resets on server restart. This was a deliberate scope decision to keep the system self-contained. Replacing the in-memory stores with a real database does not require changes to the engine or connectors — only the store implementations.
