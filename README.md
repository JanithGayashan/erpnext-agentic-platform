# ERPNext Agentic Platform

A separate, approval-gated workflow service that integrates with ERPNext only
through authenticated ERPNext APIs. See [the project brief](docs/project-brief.md)
and [the first milestone](docs/first-milestone.md) for the current scope.

## Current implementation

The current foundation contains:

- validated application configuration;
- a FastAPI application factory with `GET /health`;
- an authenticated, read-only `POST /replenishment/proposals` endpoint;
- a typed ERPNext API client and deterministic replenishment calculation;
- a typed LangGraph approval workflow with plan hashing, expiry, rejection,
  self-approval protection, and request-ID reuse checks;
- authenticated FastAPI routes to start, inspect, approve, or reject that
  workflow using separate requester and approver credentials;
- a persistent SQLite checkpoint option for local development;
- automated unit, API, workflow-interrupt, and restart-persistence tests.

An approved workflow ends as `APPROVED_NO_WRITE`; it cannot create, submit, or
otherwise mutate an ERPNext document.

The first read-only connection check is available after configuring the
dedicated API credentials in `.env`:

```powershell
python -m erpnext_agentic.check_erpnext
```

This calls Frappe's `get_logged_user` method, verifies that the token belongs to
the expected integration user, reads the configured Company, and validates that
the configured Warehouse is enabled, non-group, and belongs to that Company.
It also reads the configured Item with its reorder child rows and validates the
one applicable Purchase rule. List reads use explicit field allowlists; the
exact Item resource is required to obtain its child table and is reduced to a
typed model on receipt. Finally, it reads the exact Item/Warehouse Bin and uses
ERPNext's stored projected quantity; a missing Bin is normalized to zero for the
documented first-milestone rule. A pure deterministic function then calculates
`PROPOSAL` or `NO_ACTION`. The Item's enabled UOM is also validated, and proposed
quantities are rounded upward when ERPNext marks that UOM as whole-number-only.
This result does not authorize a write. The service does not perform any mutation.

## Read-only proposal endpoint

`POST /replenishment/proposals` accepts a typed Company, Item, Warehouse, and
caller-generated request UUID. It authenticates the caller through a separate
`X-API-Key`; requester identity comes from that authentication and is not
accepted in the request body. The endpoint performs ERPNext reads and returns a
calculation only. This endpoint is still separate from the new checkpointed
LangGraph workflow.

Proposal dates use the timezone returned by Frappe's supported
`frappe.client.get_time_zone` method. `transaction_date` is the current date in
that timezone, and `schedule_date` adds the Item's non-negative lead time.

## Current LangGraph step

`erpnext_agentic.workflow` now supplies the first resumable orchestration layer:

```text
PLANNING -> NO_ACTION
         -> AWAITING_APPROVAL -> REJECTED
                              -> APPROVED_NO_WRITE
```

The graph calls the existing read-only proposal builder, hashes its canonical
typed result, and pauses with a LangGraph interrupt. The same `request_id` is the
checkpoint thread ID. Approval must reference the exact hash, comes from a
different identity by default, and expires after 30 minutes by default. Local
SQLite checkpoints are ignored by Git. No LLM is needed for this deterministic
workflow.

The workflow HTTP surface is:

| Route | Authentication | Purpose |
|---|---|---|
| `POST /replenishment/workflows` | Requester key | Build or return the checkpointed proposal. |
| `GET /replenishment/workflows/{request_id}` | Requester or approver key | Inspect the current state and exact plan hash. |
| `POST /replenishment/workflows/{request_id}/decision` | Approver key | Resume with `approve` or `reject`. |

The decision body contains only `plan_hash`, `decision`, and an optional
`reason`. Approver identity comes from authentication and decision time comes
from the service; callers cannot supply either field. Configure a distinct
`APP_PLATFORM_APPROVER_ID` and `APP_PLATFORM_APPROVER_API_KEY` only in the
ignored `.env` before a live HTTP test.

The next step is to exercise these routes against the live development ERPNext
fixture and verify SQLite resume through an application restart. ERPNext draft
creation remains a later, separately tested step.

## Local setup

From this repository in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The local `.env` is ignored by Git. It contains the ERPNext URL and site host.
API credentials must be added only to `.env` and are represented as secret types
inside the application. Do not put credentials in `.env.example`, documentation,
tests, source code, commits, terminal screenshots, or chat.

Run the service on port 8001 because the development ERPNext bench uses 8000:

```powershell
python -m uvicorn erpnext_agentic.main:app --reload --port 8001
```

Then open <http://127.0.0.1:8001/health>. The expected response is:

```json
{
  "status": "ok",
  "service": "ERPNext Agentic Platform",
  "environment": "development"
}
```

Run the tests with:

```powershell
python -m pytest
```
