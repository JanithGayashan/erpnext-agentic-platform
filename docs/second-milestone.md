# Second Milestone — Read-Only Purchase Lead-Time Exception Review

**Status:** Paused as a standalone learning experiment; verified fixtures and
typed read-only Material Request retrieval are implemented. The proposed
lead-time workflow is not being attached to ERPNext automatic reorder.
**Depends on:** [`project-brief.md`](./project-brief.md),
[`first-milestone.md`](./first-milestone.md), and
[`next-use-case.md`](./next-use-case.md)

**Last reviewed:** 2026-09-03

## 1. Provisional objective

Given the exact name of one existing **draft Purchase Material Request**, read it
through authenticated ERPNext APIs and flag an Item row when its required-by date
is earlier than the Item's configured lead time permits.

This is a learning policy selected to avoid duplicating automatic reorder. It is
not an agreed JG Investors production rule. No implementation should be described
as business-ready until an owner confirms how purchasing lead time is actually
defined and used.

ERPNext-generated reorder requests already derive their schedule date from the
same Item `lead_time_days` value. Applying this check unchanged to those requests
would therefore duplicate the native calculation and normally always return
`PASS`. See [`builtin-reorder-integration.md`](./builtin-reorder-integration.md)
for the replacement integration direction.

## 2. Confirmed gap and boundary

The installed ERPNext v16 buying controller's server-side
`validate_schedule_date` method checks that a row's `schedule_date` is not before
the Material Request `transaction_date`; that method does not compare the date
with the Item's `lead_time_days`.

Relevant installed source:

- `D:\ERPNext\frappe_docker\development\frappe-bench\apps\erpnext\erpnext\controllers\buying_controller.py`
- `D:\ERPNext\frappe_docker\development\frappe-bench\apps\erpnext\erpnext\stock\doctype\material_request\material_request.json`
- `D:\ERPNext\frappe_docker\development\frappe-bench\apps\erpnext\erpnext\stock\doctype\material_request_item\material_request_item.json`

ERPNext remains responsible for document lifecycle and its normal validation.
The service supplies an additional external assessment only; it does not override
ERPNext or change the Material Request.

## 3. Smallest scope

Included:

- one caller-supplied Material Request name;
- `material_request_type = Purchase`;
- `docstatus = 0` (Draft);
- company `JG Investors (Demo)`;
- exactly one Item row for the first test;
- Item `AGENTIC-TEST-ITEM` and Warehouse
  `Agentic Test Warehouse - JGD` for the initial fixture;
- authenticated read-only ERPNext API calls;
- deterministic assessment, checkpoint, reviewer decision, and stale-document
  revalidation;
- no LLM.

Excluded:

- creating, editing, submitting, cancelling, or deleting the Material Request;
- selecting suppliers or predicting their actual delivery performance;
- working-day calendars, holidays, shipping routes, and supplier-specific lead
  times;
- multi-row and multi-warehouse requests;
- automatic notifications or scheduled scans;
- direct ERPNext database access or ERPNext core changes.

## 4. Typed request and assessment

The API request will contain:

| Field | Rule |
|---|---|
| `review_id` | Caller-generated stable UUID and LangGraph thread ID. |
| `material_request_name` | Required exact ERPNext document name. |

Requester identity comes from platform authentication.

For the one Item row:

```text
earliest_feasible_date = transaction_date + lead_time_days

if schedule_date < earliest_feasible_date:
    outcome = LEAD_TIME_RISK
else:
    outcome = PASS
```

The first version uses calendar days because that matches the existing
replenishment experiment. Negative lead time, missing required fields, wrong
scope, or unexpected document structure fails closed.

The typed assessment must show:

- Material Request name, company, type, and document status;
- ERPNext modification timestamp;
- Item code, Warehouse, quantity, stock UOM, and required-by date;
- Item lead-time days and calculated earliest feasible date;
- outcome code and a deterministic explanation;
- rule version and canonical snapshot hash.

## 5. Proposed workflow

```text
Receive typed review request
→ authenticate requester and validate configured scope
→ GET the exact Material Request through the ERPNext API
→ validate draft Purchase header and the single Item row
→ GET and validate Item and Warehouse through the API
→ calculate PASS or LEAD_TIME_RISK deterministically
→ persist snapshot and hash
→ pause for a separate reviewer
→ reject or acknowledge the exact assessment
→ re-read Material Request, Item, and Warehouse
→ changed hash: STALE_REVIEW_REQUIRED
→ unchanged hash: REVIEWED_NO_WRITE
```

Reviewer acknowledgement means only “I reviewed this assessment.” It does not
approve, submit, or authorize any ERPNext transaction.

## 6. Candidate API reads

| Purpose | Method and path |
|---|---|
| Read exact Material Request | `GET /api/resource/Material%20Request/{name}` |
| Read exact Item | `GET /api/resource/Item/{item_code}` |
| Read exact Warehouse | `GET /api/resource/Warehouse/{warehouse}` |

Path components must be encoded by the HTTP client. ERPNext responses must be
reduced to explicit typed fields before checkpointing. The existing integration
role already has Material Request, Item, Warehouse, Company, and UOM read access;
this must be verified again before implementation.

## 7. Verified test dataset

Two records were created on 2026-09-03 through the dedicated ERPNext test API
user and the supported DocType REST endpoint. This was fixture setup performed
outside the platform workflow; no ERPNext database or core code was touched.

| Expected result | Material Request | Transaction date | Required by | Lead time | Status |
|---|---|---|---|---:|---|
| `LEAD_TIME_RISK` | `MAT-MR-2026-00001` | 2026-09-03 | 2026-09-04 | 3 days | Draft / `docstatus = 0` |
| `PASS` | `MAT-MR-2026-00002` | 2026-09-03 | 2026-09-06 | 3 days | Draft / `docstatus = 0` |

Authenticated read-back verified that each record:

- belongs to `JG Investors (Demo)` and has purpose `Purchase`;
- contains exactly one `AGENTIC-TEST-ITEM` row;
- targets `Agentic Test Warehouse - JGD`;
- requests 20 `Nos`;
- remains Draft and unsubmitted.

Do not submit these fixtures. ERPNext automatic reorder must remain disabled for
the existing test Item.

## 8. Acceptance scenarios

Before this milestone is considered complete, tests must prove:

1. The urgent draft returns `LEAD_TIME_RISK` with the expected dates.
2. A sufficiently late draft returns `PASS`.
3. Submitted, cancelled, non-Purchase, wrong-company, multi-row, and out-of-scope
   documents fail closed.
4. Missing/disabled Items and invalid/group/wrong-company Warehouses fail closed.
5. Requester and reviewer identities come from authentication.
6. The reviewer decision is bound to the exact snapshot hash and expires.
7. Changing the draft, Item lead time, or Warehouse after review invalidates the
   decision.
8. Replaying `review_id` cannot change its bound Material Request.
9. A service restart restores the pending review.
10. Source and network evidence show zero ERPNext mutations and no database access.

## 9. Decisions still requiring a business owner

- Is Item `lead_time_days` authoritative, or should the rule use supplier/item,
  shipping, or historical lead time?
- Are lead times calendar days or working days, and which holiday calendar applies?
- Does an early required-by date only warn, require escalation, or block a later
  action?
- Are quantities, Item Groups, Projects, urgency reasons, or purchase values also
  part of the policy?
- Should ERPNext's native Workflow remain the actual approval mechanism?
- Does this exception happen often enough to justify maintaining a separate
  service?

Until those questions are answered, the milestone remains read-only and
educational.

## 10. Implementation checkpoint — typed read boundary

The ERPNext client now implements
`get_material_request(name) -> MaterialRequestRecord` using the exact encoded
`GET /api/resource/Material%20Request/{name}` route. The response is reduced to
typed header and Item-row models; unrelated ERPNext response fields are not
retained.

Automated tests cover the HTTP method and path, document-name encoding, typed
dates and decimal quantities, ignored extra fields, and mismatched-document
rejection. Live calls through the new client method validated both
`MAT-MR-2026-00001` and `MAT-MR-2026-00002` as the expected one-row drafts.

The client method deliberately does not decide `PASS` or `LEAD_TIME_RISK` and
does not contain a write operation. The next implementation step is a pure
deterministic assessment function, separate from HTTP and LangGraph.
