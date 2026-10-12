# ADR-010: Correct three defects in the reference DDL

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-13 |
| **Deciders** | Albert Navarro |
| **Supersedes** | None (amends SPEC §7.10) |

## Context

Turning the SPEC §7.10 DDL into migrations (M1-01..M1-05) exposed three defects:

1. **Cost center keys are too short.** `cost_center` is `varchar(10)` (the SAP `KOSTL` length), but the naming rule in §7.6 (`<CC>-<FUNCTION>-<SUFFIX>`) produces keys up to 14 characters, such as `ES10-RD-MEDAFF` and `US10-SALES-IMM`. The seed data from §7.6 cannot be loaded.
2. **`rpt.unbalanced_documents` bypasses row-level security.** Unlike the other `rpt` views it is not `security_invoker`, so it runs as the view owner. `vc_tool_reader` can select it (it is in schema `rpt`) and would see document numbers and sums for every company code.
3. **Grants are missing.** The section 4 statement `GRANT SELECT ON ALL TABLES IN SCHEMA ... doc` runs before the `doc` tables exist in section 5, so `vc_tool_reader` cannot read documents. `vc_app` has no privileges on `app` or `review`, although §7.2 says the API owns them.

## Decision

1. Every `cost_center` column (`md.cost_center`, `md.wbs_element`, `fin.journal_line`, `fin.plan_line`, `fin.stat_key_figure`) is `varchar(20)`.
2. `rpt.unbalanced_documents` is created `WITH (security_invoker = true)`.
3. After the `doc` tables are created, `vc_tool_reader` gets `SELECT` on `doc.document` and `doc.chunk`. `vc_app` gets `USAGE` on `app` and `review` and `SELECT, INSERT, UPDATE` on their tables. It gets no `DELETE`: runs and commentary revisions are kept.

## Consequences

**Positive**

- The §7.6 cost center names load unchanged.
- Every `rpt` view is subject to RLS. A test checks that a broken US10 document is invisible under ES10 scope.
- Retrieval tools and the API have the privileges their design docs assume.

**Negative / risks**

- Cost center keys no longer match the SAP field length. A loader for real extracts maps 10-character `KOSTL` values into a wider column, which needs no conversion.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Shorten cost center names to 10 characters | Loses the readable `<CC>-<FUNCTION>-<SUFFIX>` scheme that §7.6, the scenario events and the corpus use |
| Revoke `vc_tool_reader` access to `rpt.unbalanced_documents` | Validation and tests already query it as owner; the view is harmless once it respects RLS |
