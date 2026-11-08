---
id: repair
version: 1
owner: Albert Navarro
changelog:
  - version: 1
    date: 2026-11-08
    change: First version; one repair turn listing the exact violations (SPEC §13.7).
---

Your previous answer failed validation and was not shown to the user.

Violations:
$violations

Return a corrected answer in the required JSON schema. Fix every violation listed above and keep
the rest of the answer unchanged. Refer to numbers only with placeholders for fact IDs that tools
have returned; if you need a number no tool has returned, call a tool. Never type digits for
amounts, percentages, rates or quantities.
