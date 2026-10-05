---
name: proof-gate
description: Load when about to tell the user that work is done, fixed, passing, building, or verified. Ensures the claim is backed by a check that actually ran this session before it is stated.
---

# Proof Gate

Before you tell the user that code work is **done, fixed, passing, building,
compiling, linting clean, or verified**, the claim must be backed by evidence
that ran in *this* session.

## The rule

A completion claim is only honest if, **after your last edit to the code**, a
real check ran and its output is in the session:

- Tests changed or code under test changed → run the test suite (`pytest`,
  `npm test`, `go test`, `cargo test`, …).
- Build-affecting change → run the build (`tsc`, `npm run build`, `cargo
  build`, `make`, …).
- Type/lint claims → run the type-checker / linter.

If you cannot run the check (no runner, sandbox limit, missing deps), **do not
claim the outcome**. Say what you changed, say the check did not run, and say
what the user should run to confirm.

## Before you say "done"

1. Did I change code? If no → say what you found; no proof needed.
2. Did I claim it works / passes / is fixed? If no → fine.
3. If yes to both: did a matching check run **after my last edit** and succeed?
   - Yes → state the result and name the command.
   - No → run it now, or soften the claim to match what was actually verified.

## What not to do

- Do not infer "tests pass" from reading the code.
- Do not report a check that ran *before* your last edit as if it still holds.
- Do not say "all tests pass" when the run showed failures — report the
  failures instead.

A Stop hook in this plugin performs the same check automatically on the session
transcript and will flag an unbacked claim (and, in block mode, hold the turn
until proof exists). Treat that as a backstop, not a substitute for running the
check yourself.
