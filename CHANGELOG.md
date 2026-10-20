# Changelog

## 0.2.0 — 2026-10-20

Warn mode was invisible. The verdict went to stderr with exit 0, which Claude
Code routes to its debug log, not the screen; the fallback log file lived in
the plugin's install directory, which is wiped on every update. A default
install therefore never showed anyone a result.

- **Fixed: UNVERIFIED now reaches the user** as a `systemMessage` (user-only,
  never fed to the model, so it cannot cause the Stop loop the code guards
  against). PASS and N/A stay silent.
- **New: one-time activation line** on the first Stop after install, so you
  know it's running. `PROOF_GATE_QUIET=1` hides it.
- **Changed: verdict log** moved to `CLAUDE_PLUGIN_DATA/verdicts.log`
  (fallback `~/.proof-gate/`), which survives updates.
- Tests: `tests/test_hook_output.py` runs the real entrypoint over stdin and
  asserts on stdout; CI added (Ubuntu + Windows). Removed a committed `.pyc`.
