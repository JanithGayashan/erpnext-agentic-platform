# Built-In Reorder Integration

**Status:** Superseded by the secured event-driven sourcing workflow described in
[`auto-reorder-event-workflow.md`](./auto-reorder-event-workflow.md).
**Last reviewed:** 2026-09-03

## Decision

ERPNext owns reorder detection and automatic Material Request creation. This
service must not run a second reorder scheduler or independently create the same
requests.

The supported integration boundary is the Material Request created by ERPNext.
ERPNext marks these documents with `auto_created_via_reorder = 1` and, in the
installed version, submits them during the same scheduled operation.

```text
ERPNext projected stock and Item Reorder settings
-> ERPNext creates and submits Material Request
-> this service discovers it through authenticated REST reads
-> deterministic exception review
-> human acknowledgement
-> any later RFQ or Purchase Order action remains separately authorized
```

## Implemented slice

`ERPNextClient.get_recent_auto_reorder_material_requests()` performs an
authenticated `GET /api/resource/Material Request` with explicit filters for:

- the configured Company;
- Purchase request type;
- `auto_created_via_reorder = 1`;
- submitted documents (`docstatus = 1`).

It requests only an allowlisted summary, orders by the most recently modified
document, limits the result to 1-100 rows, validates the response again locally,
and performs no write. An empty result is valid while automatic reorder remains
disabled or no item has triggered it.

The exact-document read now also retains the typed
`auto_created_via_reorder` flag. Discovery returns document names; a later step
can use the existing exact-document read when a justified policy needs Item rows.

## Important limitation

The current experimental lead-time check should not be attached unchanged to an
automatic reorder request. ERPNext itself calculates that request's schedule
date from the Item's `lead_time_days`, so the check would normally repeat the
same calculation and always pass.

Before adding a new LangGraph workflow, the business must identify an exception
ERPNext does not already decide, such as an unusual quantity threshold, an
external supplier disruption, a budget escalation not covered by native
controls, or a policy involving multiple records.

## Deliberately not implemented

- enabling ERPNext automatic reorder;
- a polling scheduler or webhook;
- creating test Material Requests;
- changing or submitting ERPNext documents;
- RFQ or Purchase Order creation;
- an LLM call;
- a LangGraph workflow without a distinct business decision.

## Implemented continuation

The service now accepts a signed ERPNext `on_submit` Webhook, re-reads the exact
auto-created request, checks Item Supplier links, and starts an idempotent,
checkpointed LangGraph review. The earlier list discovery method remains useful
for reconciliation, but it is no longer the primary event path.
