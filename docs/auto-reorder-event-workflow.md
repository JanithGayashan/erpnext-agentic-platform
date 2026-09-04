# Auto-Reorder Event Sourcing Workflow

**Status:** Implemented and automated-test verified; live ERPNext Webhook activation
still requires a shared secret and one controlled non-production reorder event.
**Last reviewed:** 2026-09-04

## Objective

Use ERPNext's native reorder system as the autonomous trigger and add a separate,
non-duplicative sourcing-readiness review in the FastAPI/LangGraph service.

ERPNext remains responsible for projected stock, reorder thresholds, quantity,
lead time, Material Request creation, and Material Request submission. The
external service does not recalculate or replace those decisions in this flow.

```text
ERPNext daily reorder scheduler
-> submitted Purchase Material Request (`auto_created_via_reorder = 1`)
-> secured ERPNext `on_submit` Webhook
-> FastAPI verifies HMAC signature
-> FastAPI re-reads the exact Material Request from ERPNext
-> deterministic Supplier-link readiness assessment
-> LangGraph checkpoint and human review
-> re-read and stale-data check
-> REVIEWED_NO_WRITE or STALE_REVIEW_REQUIRED
```

## Value added in version 1

For every Material Request Item, the service reads the corresponding ERPNext
Item master and checks its `supplier_items` child table.

- at least one linked Supplier: `LINKED_SUPPLIER_PRESENT`;
- no linked Supplier: `NO_LINKED_SUPPLIER`;
- every row has a link: `READY_FOR_SOURCING_REVIEW`;
- one or more rows have no link: `SUPPLIER_LINK_REVIEW_REQUIRED`.

A Supplier link is only master-data readiness. It does not prove that a Supplier
is enabled, approved, available, competitive, or best. Supplier validation and
ranking require a later policy, permissions, and test dataset.

## Security model

The Webhook request is untrusted until verified. Frappe signs the exact raw HTTP
body with HMAC-SHA256 and sends the base64 signature in
`X-Frappe-Webhook-Signature`. FastAPI calculates the expected signature with
`APP_ERPNEXT_WEBHOOK_SECRET` and performs a constant-time comparison before
parsing the event.

The body is limited to 16 KiB and accepts only:

```json
{
  "doctype": "Material Request",
  "name": "MAT-MR-YYYY-NNNNN",
  "auto_created_via_reorder": true
}
```

The payload is only a notification. FastAPI never trusts it as business data.
After signature validation, the service uses its least-privilege ERPNext token
to read the exact Material Request and verifies again that it is:

- in the configured Company;
- a Purchase Material Request;
- submitted;
- marked as created by ERPNext automatic reorder.

It also validates every target Warehouse and Item through authenticated reads.

## Idempotency and human review

The service derives a stable UUID from the Material Request name. Repeated
Webhook deliveries therefore return the same LangGraph checkpoint instead of
creating duplicate reviews. Frappe queues the Webhook only after the database
transaction commits and retries failed deliveries. If the first assessment
fails before reaching the review pause, a repeated delivery resumes that same
incomplete checkpoint and retries the assessment safely.

The graph saves a canonical SHA-256 assessment hash and pauses at
`AWAITING_REVIEW`. Only the configured platform approver can approve or reject.
After approval, the graph re-reads ERPNext and recomputes the assessment:

- unchanged hash: `REVIEWED_NO_WRITE`;
- changed Material Request or Item Supplier links: `STALE_REVIEW_REQUIRED`.

Approval does not create an RFQ, Purchase Order, stock entry, or any other
ERPNext document.

## HTTP endpoints

| Endpoint | Authentication | Purpose |
|---|---|---|
| `POST /erpnext/webhooks/material-request-reorder` | Frappe HMAC signature | Receive one native reorder event and start or return its review. |
| `GET /sourcing-reviews/by-material-request/{name}` | Requester or approver key | Find the review using the ERPNext Material Request name. |
| `GET /sourcing-reviews/{review_id}` | Requester or approver key | Inspect the exact checkpoint. |
| `POST /sourcing-reviews/{review_id}/decision` | Approver key | Approve or reject the exact assessment hash. |

## Local activation

### 1. Configure a private shared secret

Generate a strong random value locally. Store the same value in two places only:

1. ignored `.env` as `APP_ERPNEXT_WEBHOOK_SECRET`;
2. the ERPNext Webhook's **Webhook Secret** field.

Do not commit, document, screenshot, or paste the value into chat.

### 2. Make FastAPI reachable from Docker

Run the service on all host interfaces for local Docker access:

```powershell
python -m uvicorn erpnext_agentic.main:app --reload --host 0.0.0.0 --port 8001
```

From the ERPNext container, verify that
`http://host.docker.internal:8001/health` is reachable before enabling the
Webhook. Production must use an authenticated HTTPS endpoint; the Docker-host
URL is development-only.

### 3. Configure the standard ERPNext Webhook

Create a Webhook through ERPNext Desk as a System Manager:

| Field | Value |
|---|---|
| Name | `Agentic Auto Reorder Review` |
| DocType | `Material Request` |
| Doc Event | `on_submit` |
| Enabled | Yes |
| Condition | `doc.auto_created_via_reorder == 1 and doc.material_request_type == "Purchase"` |
| Request URL | `http://host.docker.internal:8001/erpnext/webhooks/material-request-reorder` |
| Request Method | `POST` |
| Request Structure | leave empty and use Webhook Data rows |
| Enable Security | Yes |
| Webhook Secret | same private value as `.env` |
| Timeout | `10` seconds for the local experiment |

Add the header:

| Key | Value |
|---|---|
| `Content-Type` | `application/json` |

Add these Webhook Data rows:

| Key | Fieldname |
|---|---|
| `doctype` | `doctype` |
| `name` | `name` |
| `auto_created_via_reorder` | `auto_created_via_reorder` |

Using Webhook Data rows avoids hand-built JSON escaping. Frappe signs the same
serialized body that it transmits.

### 4. Controlled non-production proof

Only after FastAPI and the Webhook are ready:

1. enable ERPNext automatic Material Requests in Stock Settings;
2. confirm the test Item is below its reorder level;
3. allow the daily scheduler to run, or deliberately invoke the installed
   reorder job once in the development bench;
4. find the submitted auto-created Material Request;
5. inspect the associated sourcing review;
6. inspect ERPNext **Webhook Request Log** if delivery failed;
7. disable automatic reorder again if continuous operation is not wanted.

Invoking the reorder job is a mutating test: it can create and submit a Material
Request. Never run it casually against production.

## Current activation blocker

The local `.env` does not yet contain `APP_ERPNEXT_WEBHOOK_SECRET`, and no live
ERPNext Webhook has been configured by this repository. The code path is complete
and tested, but ERPNext cannot deliver a signed event until those two matching
secret settings exist and FastAPI is reachable from its container.
