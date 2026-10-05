---
description: Audit your most recent completion claim against evidence that actually ran this session
---

Audit your own most recent completion claim. Do not trust your summary — check
the session.

1. State the last claim you made about the work being done, fixed, passing,
   building, or verified. Quote it.
2. Identify your last edit to the code this session.
3. Find whether a matching check (test / build / type-check / lint) ran **after**
   that edit, and whether its output showed success or failure.
4. Give one verdict:
   - **PASS** — the claim is backed: name the command and its result.
   - **UNVERIFIED** — no matching check ran after the last edit, or one ran and
     failed. Say which, then either run the check now or correct the claim.
   - **N/A** — no code changed, or no completion claim was made.

Be blunt. If the claim is not backed, say so plainly and fix it rather than
defending it.
