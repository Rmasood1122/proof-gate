# Proof Gate

**Make Claude prove its own work before it says "done."**

Agents are confident. They say *"all tests pass,"* *"fixed,"* *"build is
green,"* *"verified"* — and a lot of the time nothing actually ran to back that
up. You merge it, and the claim was vapor. A 304,000-commit study of
AI-authored code found roughly a quarter of AI-introduced issues survive to the
latest revision, with security issues persisting at a higher rate. The gap
isn't capability — it's **unverified claims**.

Proof Gate is a Claude Code plugin that checks, at the moment Claude tries to
finish a turn, whether its completion claims are backed by a command that
**actually ran and succeeded after the last code change** in the session.

It is **not** another code reviewer. It doesn't judge your code. It judges
whether Claude's *claim about* the code is true.

## Why a hook, not a skill

Tools that split an agent's commits or chat summary into claims and check them
are useful — but they run **after the fact**, when you remember to ask, on work
that's already landed. Proof Gate is a **Stop hook**: it fires **automatically
at the end of every turn**, before Claude hands the work back, with no prompt
from you. In warn mode it records a verdict; in block mode (`PROOF_GATE_MODE=block`)
an unbacked "done" **holds the turn** until the proof exists. It's a gate on the
live session, not a post-mortem.

---

## What it checks

When Claude ends a turn, Proof Gate reads the session transcript and returns one
verdict:

| Verdict | Meaning |
|---|---|
| **PASS** | A completion claim was made, and a matching check (test/build/lint/type-check) ran after the last edit and succeeded. |
| **UNVERIFIED** | A claim was made but nothing backs it — no check ran after the last edit, or one ran and **failed**. |
| **N/A** | Nothing to gate: no code changed, or no completion claim was made. Proof Gate stays silent. |

The three things it catches:

1. **Unbacked claims** — "tests pass" with no test run after the edit.
2. **Contradicted claims** — "all tests pass" when the run showed failures.
3. **Stale claims** — a green run from *before* the last edit, cited as if it
   still holds.

---

## Warn by default, block when you want

Proof Gate ships in **warn mode**: it prints a verdict as non-blocking context
and never halts you. Flip one environment variable to make an `UNVERIFIED`
verdict **block** the turn until proof exists:

```bash
export PROOF_GATE_MODE=block   # hard-gate: unbacked "done" is held
# unset, or PROOF_GATE_MODE=warn  → default, advisory only
```

Teams that want a real gate turn on block mode in CI or in a shared
`settings.json`. Solo users usually leave it on warn.

---

## Install

```bash
/plugin marketplace add Rehanrana11/proof-gate
/plugin install proof-gate@proof-gate
```

Then start (or `/reload-plugins`) and work normally. No config, no API key, no
network. When you next say "done," you'll see the verdict.

Run `/proof` any time to audit your most recent completion claim on demand.

---

## How it works

- A **Stop hook** runs `scripts/proof_gate.py` against the session transcript.
- The verifier is **Python 3.8+ stdlib only** — no pip install, no dependencies.
- It is **read-only**: it never edits your repo and makes no network calls.
- It **fails open**: any error in the hook (no Python, unreadable transcript)
  exits cleanly and never blocks you.
- It is **loop-safe**: the hook stays silent on stdout unless it is intentionally
  blocking, and it honors `stop_hook_active`, so it blocks a stop at most once
  and can never trap you in a hook loop. Warn-mode verdicts go to stderr and a
  `.proof-gate-verdicts.log`, never fed back into the model.
- A companion **skill** reminds Claude to run the check itself before claiming
  done — the hook is the backstop.

Detected checks include pytest, unittest, tox, npm/yarn/pnpm test, jest,
vitest, mocha, playwright, go test, cargo test/build/check, maven, gradle,
make, rspec, phpunit, dotnet test, tsc, eslint, ruff, flake8, mypy, and more.

---

## Prove it works (on your machine)

```bash
python3 scripts/proof_gate.py --selftest
```

Expected:

```
[ok] pass.jsonl: expected PASS, got PASS
[ok] unverified_unbacked.jsonl: expected UNVERIFIED, got UNVERIFIED
[ok] unverified_contradicted.jsonl: expected UNVERIFIED, got UNVERIFIED
[ok] unverified_no_tests.jsonl: expected UNVERIFIED, got UNVERIFIED
[ok] na_no_edits.jsonl: expected N/A, got N/A
[ok] na_no_claim.jsonl: expected N/A, got N/A
[ok] na_done_only.jsonl: expected N/A, got N/A

SELFTEST PASSED: all verdicts correct.
```

You can also point it at any transcript:

```bash
python3 scripts/proof_gate.py --transcript path/to/session.jsonl
```

---

## Honest limits

- It verifies that a check **ran and passed**, not that the check is *good*. A
  passing but meaningless test still reads as PASS. Proof Gate fights unbacked
  claims, not weak test suites.
- It matches claims and commands by pattern. An exotic phrasing or a custom test
  runner it doesn't recognize can slip through as N/A. Patterns are in
  `scripts/proof_gate.py` and easy to extend.
- It reasons over the session transcript only. Work done in a previous session,
  or outside Claude, is invisible to it.

It is deliberately biased toward silence: it would rather miss a borderline case
than nag on every turn.

---

## License

MIT — see [LICENSE](LICENSE).
