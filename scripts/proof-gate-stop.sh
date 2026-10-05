#!/usr/bin/env bash
# Proof Gate Stop-hook entrypoint.
# Robust by design: if anything here goes wrong, we exit 0 and emit empty
# context so a plumbing failure can NEVER block you from finishing a turn.
set +e

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCRIPT="$ROOT/scripts/proof_gate.py"

# Pass stdin straight through to the verifier. Prefer python3, fall back to python.
if command -v python3 >/dev/null 2>&1; then
  python3 "$SCRIPT" "$@"
  exit $?
elif command -v python >/dev/null 2>&1; then
  python "$SCRIPT" "$@"
  exit $?
else
  # No Python available: fail open.
  printf '{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":""}}'
  exit 0
fi
