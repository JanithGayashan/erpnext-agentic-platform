# ERPNext Agentic Platform

A separate, approval-gated workflow service that integrates with ERPNext only
through authenticated ERPNext APIs. See [the project brief](docs/project-brief.md)
and [the first milestone](docs/first-milestone.md) for the current scope.

## Current implementation

The first foundation contains only:

- validated application configuration;
- a FastAPI application factory;
- `GET /health`;
- an automated health-endpoint test.

It does not yet run LangGraph, read stock/reorder documents, or perform writes.

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
calculation only. The request ID is not persisted yet, so this is not the later
resumable or idempotent LangGraph workflow.

Proposal dates use the timezone returned by Frappe's supported
`frappe.client.get_time_zone` method. `transaction_date` is the current date in
that timezone, and `schedule_date` adds the Item's non-negative lead time.

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
