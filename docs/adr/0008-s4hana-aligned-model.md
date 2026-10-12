# ADR-008: Align the data model with the S/4HANA universal journal

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

The product targets finance teams running SAP S/4HANA. A credible, familiar structure makes the demo convincing and eases mapping to real ERP extracts later (§7.1).

## Decision

The finance schema mirrors ACDOCA/BKPF (journal), ACDOCP (plan) and the master-data tables (T001, CSKS, CEPC, SKA1, …) with readable English column names; each column's S/4HANA counterpart is noted in DDL comments. Deliberate simplifications are listed in §7.1.

## Consequences

**Positive**

- Finance users recognise the structure; mapping real extracts is mostly renaming.
- Proven dimensional model (company code, profit center, functional area, …).

**Negative / risks**

- Some S/4HANA complexity (time-dependent master data, multiple ledgers) is simplified away and would need work for real data.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Generic star schema | Less credible to SAP users; harder mapping to real extracts |
| Exact S/4HANA table and field names | Cryptic for non-SAP readers and developers |
