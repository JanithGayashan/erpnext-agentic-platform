# Next Use Case — Selection Before Implementation

**Status:** Provisional learning selection; production business value is not yet confirmed.
**Last reviewed:** 2026-09-03

## Why selection comes before more code

The replenishment experiment proved the reusable technical boundary: FastAPI
authentication, typed ERPNext API reads, deterministic calculation, LangGraph
checkpoints, approval, restart recovery, exact-plan hashing, and post-approval
revalidation. It also exposed that standard reorder-driven Material Request
creation already belongs to ERPNext.

The platform should not replace a feature that ERPNext can safely provide through
configuration. A new workflow qualifies only when it requires at least one of:

- a company-specific decision spanning multiple ERPNext document types;
- data from an approved external system;
- interpretation of unstructured text or documents;
- recovery or human coordination that cannot be met by ERPNext Workflow;
- a separately justified audit or policy boundary.

Convenience alone is not enough to justify another transaction engine.

## Native capabilities we should not duplicate

| Area | ERPNext already provides | Consequence for this project |
|---|---|---|
| Stock reorder | Reorder levels can generate Material Requests from projected stock. | Do not build another ordinary reorder scheduler. |
| Approvals | Workflow supports multiple states, roles, conditions, and approval levels. | Do not add an external approval graph merely to rename native states. |
| Budget control | Budgets can warn or stop Material Requests and Purchase Orders. | Do not duplicate standard budget enforcement. |
| Procurement visibility | Buying reports cover purchase analytics, items to order/receive, and procurement tracking. | Do not rebuild a report without a distinct decision or action. |
| Supplier evaluation | Supplier Quotations and Supplier Scorecards record prices and performance criteria. | Add decision support only if company policy combines factors ERPNext does not already evaluate. |

Official references:

- [ERPNext automatic reordering](https://docs.frappe.io/erpnext/item#34-automatic-reordering)
- [ERPNext Workflows](https://docs.frappe.io/erpnext/workflows)
- [ERPNext Budget controls](https://docs.frappe.io/erpnext/budget)
- [ERPNext Buying Reports](https://docs.frappe.io/erpnext/buying_reports)
- [ERPNext Supplier Quotation](https://docs.frappe.io/erpnext/supplier-quotation)
- [ERPNext Supplier Scorecard](https://docs.frappe.io/erpnext/supplier-scorecard)

## Candidate comparison

| Candidate | Distinct potential value | LLM needed initially | Setup/risk | Decision |
|---|---|---:|---|---|
| Standard reorder to Material Request | None beyond the native feature for the current rules. | No | Low, but duplicative | Stop after the architecture experiment. |
| Purchase-request exception review | Combine a draft request with open demand, stock, lead time, and company-specific thresholds before a human acts. | No | Low-to-medium; can remain read-only | **Recommended candidate**, only after real policies are supplied. |
| Supplier selection recommendation | Combine quotations, landed cost, lead time, quality, and company-specific supplier-risk policy. | Optional later for terms text | Requires multiple suppliers and quotations | Later candidate after procurement data exists. |
| Email/PDF purchasing intake | Convert unstructured requests or quotations into a reviewable typed draft. | Yes | Higher privacy, evaluation, and prompt-injection risk | Defer until the deterministic platform is mature. |
| Cross-company inventory optimization | Recommend transfers or purchases across several companies and warehouses. | No | High data and business-rule complexity | Defer; outside the single-company milestone. |

## Recommended second milestone, if the business confirms the gap

Build a **read-only Purchase Request Exception Review** for one existing draft
Purchase Material Request.

The service would:

1. Accept the exact Material Request name from an authenticated requester.
2. Read the header and item rows through allowlisted ERPNext APIs.
3. Read only the additional ERPNext records required by agreed company policy.
4. Produce typed facts, deterministic exception codes, and human-readable
   explanations derived from those codes.
5. Checkpoint the reviewed document version and its canonical hash.
6. Allow a reviewer to acknowledge or reject the assessment.
7. Re-read the document and invalidate the review if it changed.
8. Perform no ERPNext write in the first slice.

Possible policies below are examples, not requirements:

- flag overlapping open demand for the same Item and Warehouse;
- flag a required-by date earlier than the Item's lead time permits;
- flag quantities outside a company-approved threshold;
- require additional review for selected Item Groups, Projects, or Warehouses;
- combine an approved external risk signal with ERPNext facts.

At least one genuine policy must be confirmed before implementation. Otherwise,
this candidate also becomes only a different presentation of native ERPNext data.

For the next learning increment, the provisional policy is **required-by date
versus Item lead time**. The detailed, deliberately read-only specification is
in [`second-milestone.md`](./second-milestone.md). This is an engineering
exercise, not a claim that JG Investors needs this production policy.

## Business input required

Before writing the second workflow, record answers to these questions:

1. What recurring decision currently requires manual comparison or judgment?
2. Which ERPNext document starts that decision, and at what document status?
3. Which exact facts influence the decision?
4. What company-specific rule cannot be handled adequately by ERPNext Workflow,
   Budget, reports, or permissions?
5. What should the service return or do, and what must it never do?
6. Who requests, reviews, and approves it?
7. What real or synthetic non-production dataset can prove the rule?

## Implementation gate

Do not add another ERPNext client method, graph, LLM, or write endpoint until the
business input above identifies one testable policy and a non-production dataset.
Once confirmed, create a dedicated milestone document with acceptance scenarios
before changing application code.
