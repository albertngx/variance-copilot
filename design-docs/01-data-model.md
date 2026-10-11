# 01 Data model

**Status:** Design · **Spec:** §7 · **Backlog:** M1-01..M1-06

## Accounting primer

The minimum finance vocabulary needed to read the rest of the docs.

| Concept | Meaning | Table |
| --- | --- | --- |
| **Journal entry (document)** | One business event, e.g. an invoice. Has a header and two or more lines | `fin.journal_header` |
| **Journal line** | One debit or credit to one account, with all the analysis dimensions | `fin.journal_line` |
| **Double entry** | Lines of a document always sum to zero: what is credited somewhere is debited elsewhere | rule I-01 |
| **GL account** | What the money is (sales, salaries, CRO costs...). Chart of accounts `AUR1` | `md.gl_account` |
| **Company code** | A legal entity with its own books (CH10, US10...) | `md.company_code` |
| **Cost center / profit center** | Who is responsible (a department / a business line) | `md.cost_center`, `md.profit_center` |
| **Functional area** | Which P&L line a cost belongs to (COGS, S&M, R&D, G&A) | `md.functional_area` |
| **Plan version** | A budget or forecast for a year (`BUD26`, `FC126`, `FC226`) | `fin.plan_version`, `fin.plan_line` |
| **Period** | Fiscal month 1–12, plus special periods 13–16 for year-end adjustments | `md.fiscal_period` |
| **Local / group currency** | The entity's own currency (EUR, CHF) vs the group's (USD) | columns `amount_lc`, `amount_gc` |
| **Trading partner** | Marks a line as intercompany (between two group entities) | `trading_partner` |

The tables mirror **SAP S/4HANA** (journal lines ≈ `ACDOCA`, plan ≈ `ACDOCP`) with readable English column names, so a real SAP extract can later be loaded into the same structure.

## Example: one billing document

US10 sells CARDIVEX to a wholesaler for USD 1,000,000 with a 2% cash discount:

| Line | Account | Debit/credit | Stored amount | Presented in P&L |
| --- | --- | --- | --- | --- |
| 1 | 11000000 Trade receivables | S (debit) | +980,000 | — (balance sheet) |
| 2 | 40130000 Cash discounts | S (debit) | +20,000 | −20,000 |
| 3 | 40000000 Gross sales | H (credit) | −1,000,000 | +1,000,000 |
| | **Sum** | | **0** | |

## Sign convention

Two conventions exist, and confusing them is a classic source of errors:

- **Stored** (ledger convention): debit positive, credit negative. Revenue is therefore stored negative.
- **Presented** (in `rpt` views and every tool output): income positive, expense negative. `presented = −stored`.

With the presented sign, a variance `Δ = Actual − Comparison` is **favourable when positive for every P&L line**, revenue or cost alike. Tools also return an explicit `favorable` flag, so the model never has to reason about signs.

## Schemas

```mermaid
flowchart LR
  subgraph Finance data
    MD[("md<br/>master data")]
    FIN[("fin<br/>journal + plan")]
    RPT[("rpt<br/>P&L views")]
  end
  DOC[("doc<br/>documents, chunks, vectors")]
  APP[("app<br/>runs, idempotency")]
  AUD[("audit<br/>events, facts")]
  REV[("review<br/>commentaries")]
  TR[("truth<br/>answer key")]
  FIN --> RPT
  MD --> RPT
  TOOLS["Tools (vc_tool_reader)"] --> RPT
  TOOLS --> DOC
  API["API (vc_app)"] --> APP
  API --> AUD
  API --> REV
  AUDITOR["vc_auditor"] --> AUD
  EVAL["Evaluation (vc_eval)"] --> TR
```

| Schema | Contents | Who reads / writes |
| --- | --- | --- |
| `md` | Company codes, accounts, cost centers, brands, FX rates, calendar | Tools read; generator writes |
| `fin` | Journal headers and lines, plan lines, FTEs, period status | Tools read (with RLS); generator writes |
| `rpt` | Views with presentation sign and joined master data | Tools read |
| `doc` | Documents, chunks, embeddings | Retrieval reads; ingestion writes |
| `app` | Run records, idempotency keys | API |
| `audit` | Append-only events and fact ledger | API inserts only; auditors read |
| `review` | Commentaries and revisions | API |
| `truth` | Ground truth of planted events | Evaluation only, never the agent |

## Core entities

```mermaid
erDiagram
  COMPANY_CODE ||--o{ JOURNAL_HEADER : books
  JOURNAL_HEADER ||--|{ JOURNAL_LINE : contains
  GL_ACCOUNT ||--o{ JOURNAL_LINE : "posted to"
  COST_CENTER ||--o{ JOURNAL_LINE : "charged to"
  PROFIT_CENTER ||--o{ COST_CENTER : groups
  FUNCTIONAL_AREA ||--o{ COST_CENTER : classifies
  MATERIAL ||--o{ JOURNAL_LINE : "sold / produced"
  BRAND ||--|{ MATERIAL : has
  CUSTOMER ||--o{ JOURNAL_LINE : "billed"
  VENDOR ||--o{ JOURNAL_LINE : "invoiced by"
  WBS_ELEMENT ||--o{ JOURNAL_LINE : "project / trial"
  PLAN_VERSION ||--|{ PLAN_LINE : contains
  GL_ACCOUNT ||--o{ PLAN_LINE : planned
  COMPANY_CODE ||--o{ PLAN_LINE : plans
```

## Row-level security

Access control is enforced by **PostgreSQL itself**, not by the prompt. Every finance table has a policy that only shows rows whose `company_code` is in the current transaction's scope. If no scope is set, no rows are returned.

```mermaid
sequenceDiagram
  participant T as Tool
  participant DB as PostgreSQL
  T->>DB: BEGIN
  T->>DB: SET LOCAL ROLE vc_tool_reader
  T->>DB: SET LOCAL statement_timeout = '5s'
  T->>DB: set_config('app.company_scope', 'ES10,DE10', true)
  Note over DB: scope comes from the verified user token, never from the question
  T->>DB: SELECT ... FROM rpt.pnl_actual
  DB-->>T: only ES10 and DE10 rows
  T->>DB: COMMIT (scope disappears with the transaction)
```

Why transaction-local: connections are pooled and reused, so a setting that outlived the transaction could leak one user's scope to another. Views use `security_invoker = true` so the policy applies to the caller, not to the view owner.

## Integrity rules

| Rule | Meaning |
| --- | --- |
| I-01 | Every document balances in local and group currency |
| I-02 | No postings into a period after it was closed |
| I-03 | Posting date falls inside its period |
| I-04 | Reversals point to their original |
| I-05 | Intercompany sales = intercompany purchases, per pair and period |
| I-06 | Group totals exclude intercompany lines |
| I-07 | No intercompany inventory build-up (simplification) |
| I-08 | Functional area follows the derivation rule |

## Implementation notes

_Nothing built yet._
