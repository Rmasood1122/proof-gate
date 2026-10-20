"""v0.2: what the USER sees. Before this, warn mode wrote the verdict to stderr
with exit 0 — which Claude Code sends only to its debug log — so the default
install showed nothing, ever. These run the real entrypoint over stdin the way
Claude Code does and assert on stdout, the only channel that reaches the user.

Run: python3 -m pytest tests/ -q   (or: python3 scripts/proof_gate.py --selftest
for the verdict logic alone)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "proof_gate.py"
FIX = ROOT / "tests" / "fixtures"


def run(fixture, data_dir, mode="warn", stop_hook_active=False, quiet=False):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "PROOF_GATE_"))}
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    env["PROOF_GATE_MODE"] = mode
    if quiet:
        env["PROOF_GATE_QUIET"] = "1"
    payload = {"hook_event_name": "Stop", "transcript_path": str(FIX / fixture),
               "stop_hook_active": stop_hook_active}
    return subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def out_json(r):
    s = r.stdout.strip()
    return json.loads(s) if s else None


@pytest.fixture()
def data(tmp_path):
    d = tmp_path / "data"
    # first-run notice is consumed here so the per-verdict tests see steady state
    run("na_no_claim.jsonl", d)
    return d


# ---------------------------------------------------------------- warn mode
def test_warn_unverified_reaches_the_user_via_system_message(data):
    r = run("unverified_unbacked.jsonl", data)
    assert r.returncode == 0
    j = out_json(r)
    assert j is not None, "UNVERIFIED must produce stdout JSON — stderr on exit 0 is invisible"
    assert j["systemMessage"].startswith("Proof Gate: UNVERIFIED")
    assert "decision" not in j                      # warn never blocks
    assert "hookSpecificOutput" not in j            # never feed Claude: Stop-loop risk


def test_warn_pass_and_na_are_silent(data):
    for f in ("pass.jsonl", "na_no_claim.jsonl", "na_no_edits.jsonl"):
        r = run(f, data)
        assert r.returncode == 0 and r.stdout.strip() == "", f


def test_warn_unverified_names_the_reason_and_the_block_switch(data):
    j = out_json(run("unverified_contradicted.jsonl", data))
    assert "•" in j["systemMessage"] or "-" in j["systemMessage"]
    assert "PROOF_GATE_MODE=block" in j["systemMessage"]


# ---------------------------------------------------------------- block mode
def test_block_unverified_blocks_with_reason(data):
    j = out_json(run("unverified_unbacked.jsonl", data, mode="block"))
    assert j["decision"] == "block" and j["reason"].startswith("Proof Gate: UNVERIFIED")


def test_block_mode_loop_breaker_allows_stop(data):
    r = run("unverified_unbacked.jsonl", data, mode="block", stop_hook_active=True)
    assert r.returncode == 0 and r.stdout.strip() == ""


# ---------------------------------------------------------------- first run
def test_first_stop_ever_shows_active_notice_once(tmp_path):
    d = tmp_path / "fresh"
    first = out_json(run("na_no_claim.jsonl", d))
    assert first is not None and "Proof Gate is active" in first["systemMessage"]
    assert "hookSpecificOutput" not in first
    assert (d / "notified").exists()
    assert run("na_no_claim.jsonl", d).stdout.strip() == ""


def test_first_run_notice_does_not_swallow_a_real_verdict(tmp_path):
    d = tmp_path / "fresh"
    j = out_json(run("unverified_unbacked.jsonl", d))
    assert "Proof Gate is active" in j["systemMessage"]
    assert "UNVERIFIED" in j["systemMessage"]


def test_quiet_env_suppresses_the_notice_only(tmp_path):
    d = tmp_path / "fresh"
    assert run("na_no_claim.jsonl", d, quiet=True).stdout.strip() == ""
    j = out_json(run("unverified_unbacked.jsonl", d, quiet=True))
    assert j and "UNVERIFIED" in j["systemMessage"] and "is active" not in j["systemMessage"]


# ---------------------------------------------------------------- log location
def test_verdict_log_lives_in_plugin_data_not_install_dir(data):
    run("unverified_unbacked.jsonl", data)
    assert (data / "verdicts.log").exists()
    assert not (ROOT / ".proof-gate-verdicts.log").exists()
