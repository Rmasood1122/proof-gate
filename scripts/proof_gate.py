#!/usr/bin/env python3
"""
Proof Gate — verify that an agent's claims about its own work are backed by
evidence that actually ran in the session.

Zero dependencies (Python 3.8+ stdlib only). Read-only: it never edits your
repo, never phones home, never writes outside its own log.

It answers one question: when Claude said it was done, did the work it
*claimed* (tests pass, build succeeds, "fixed", "verified") actually get
*demonstrated* by a command that ran — and succeeded — after the last code
change in this session?

Verdicts
  PASS        A completion claim was made AND a matching verification command
              ran successfully after the last edit.
  UNVERIFIED  A completion claim was made but nothing backs it — no verifying
              command ran after the last edit, or one ran and FAILED.
  N/A         Nothing to gate: no code was changed, or no completion claim was
              made. Proof Gate stays silent here by design.

Usage
  # As a Stop hook: reads the hook JSON on stdin, finds transcript_path.
  cat hook_input.json | proof_gate.py

  # Direct on a transcript file:
  proof_gate.py --transcript /path/to/session.jsonl

  # Self-test against bundled fixtures:
  proof_gate.py --selftest

Exit codes
  0  verdict produced (PASS or N/A), or warn-mode UNVERIFIED (non-blocking)
  2  block-mode UNVERIFIED (PROOF_GATE_MODE=block) — blocks the Stop

Modes
  Warn (default): prints a verdict as non-blocking context. Never halts.
  Block (PROOF_GATE_MODE=block): an UNVERIFIED verdict blocks the Stop and
  tells Claude to produce proof before finishing.
"""

import json
import os
import re
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# Signal dictionaries. Deliberately conservative: we would rather call
# something N/A than cry wolf, because a gate that nags on every turn gets
# uninstalled (the #1 reason verification plugins die in the directory).
# ----------------------------------------------------------------------------

# Phrases where Claude asserts the work is finished / correct.
CLAIM_PATTERNS = [
    r"\ball tests? (?:are )?(?:now )?pass(?:ing|ed|es)?\b",
    r"\btests? (?:are )?(?:now )?(?:all )?green\b",
    r"\b(?:the )?(?:test )?suite (?:is )?(?:now )?passing\b",
    r"\bbuild (?:is )?(?:now )?(?:succeed|succeeded|successful|passing|green|clean)\b",
    r"\bcompil(?:es|ed) (?:cleanly|successfully|without errors)\b",
    r"\btype[- ]?check(?:s|ing|ed)? (?:now )?pass(?:es|ing|ed)?\b",
    r"\blint(?:ing)? (?:is )?(?:now )?(?:clean|passing|green)\b",
    r"\bno (?:more )?errors?\b",
    r"\b(?:i(?:'ve| have)?|now) ?(?:fixed|resolved|corrected) (?:the|this|that|it|all)\b",
    r"\bthe (?:bug|issue|error|problem) (?:is |has been |was )?(?:now )?(?:fixed|resolved)\b",
    r"\bverified\b",
    r"\bconfirmed (?:working|that it works|the fix)\b",
    r"\beverything (?:is )?(?:now )?working\b",
    r"\bworks? (?:now|as expected|correctly)\b",
    r"\bready (?:to|for) (?:merge|ship|commit|deploy)\b",
]
# NOTE: bare "done" / "complete" / "finished" are deliberately NOT claims.
# They are turn-enders, not assertions about an outcome. Gating on them made
# the hook fire on normal "all done" sign-offs and launder any stray command
# into a PASS. A claim must assert a *verifiable outcome* (pass / fix / build /
# works / verified), not merely that the turn is over.

# Commands that constitute real verification (ran in a Bash/shell tool).
VERIFY_COMMAND_PATTERNS = [
    r"\bpytest\b", r"\bpy\.test\b", r"\bunittest\b", r"\bnose2?\b", r"\btox\b",
    r"\bnpm (?:run )?test\b", r"\byarn (?:run )?test\b", r"\bpnpm (?:run )?test\b",
    r"\bnpx jest\b", r"\bjest\b", r"\bvitest\b", r"\bmocha\b", r"\bplaywright test\b",
    r"\bgo test\b", r"\bcargo test\b", r"\bcargo build\b", r"\bcargo check\b",
    r"\bmvn (?:test|verify|package)\b", r"\bgradle(?:w)? (?:test|build|check)\b",
    r"\bmake (?:test|check|build)\b", r"\bctest\b", r"\brspec\b", r"\bbundle exec rspec\b",
    r"\bphpunit\b", r"\bdotnet test\b", r"\bbazel test\b",
    r"\bnpm run build\b", r"\byarn build\b", r"\bpnpm build\b", r"\btsc\b",
    r"\bnpm run lint\b", r"\beslint\b", r"\bruff\b", r"\bflake8\b", r"\bmypy\b",
    r"\bgo build\b", r"\bgo vet\b", r"\bnpm run typecheck\b",
]

# Tool names (any casing) that mutate code in the repo.
EDIT_TOOL_NAMES = {"edit", "write", "multiedit", "notebookedit", "str_replace", "applypatch", "apply_patch"}

# Tool names that run shell commands (verification happens here).
BASH_TOOL_NAMES = {"bash", "shell", "run", "runcommand", "run_command", "exec", "terminal"}

# Signals, inside a tool result, that a command FAILED even if is_error wasn't set.
FAILURE_PATTERNS = [
    r"\bFAILED\b", r"\bFAIL\b", r"\bfailures?=[1-9]\b", r"\berror[s]?:\b",
    r"\bTraceback \(most recent call last\)", r"\bAssertionError\b",
    r"\b[1-9]\d* (?:failed|error|errors)\b", r"\bexit code [1-9]\b",
    r"\bnon-zero exit\b", r"\bcompilation failed\b", r"\bBUILD FAILED\b",
    r"\bnpm ERR!", r"\bTS\d{3,5}:", r"\bsegmentation fault\b",
]

# Signals a test/build run SUCCEEDED. Must be a POSITIVE outcome, not merely the
# absence of failure — a run that executed nothing is not a success.
SUCCESS_PATTERNS = [
    r"\b[1-9]\d* passed\b",            # "5 passed" — at least one test passed
    r"\bran \d+ tests?\b.*\bok\b",     # unittest "Ran 3 tests ... OK"
    r"\bok\s+[1-9]\d* tests?\b",
    r"\ball tests passed\b", r"\btests? passed\b",
    r"\bBUILD SUCCESS", r"\bbuild succeeded\b", r"\bbuilt? successfully\b",
    r"\bcompiled successfully\b",
]

# A run that produced NO result (collected nothing, built nothing). pytest exits
# 5 here. This must NEVER be read as success — it's the false-PASS trap.
NO_RESULT_PATTERNS = [
    r"\bno tests ran\b", r"\bno tests? (?:were )?collected\b",
    r"\bcollected 0 items\b", r"\b0 passed\b", r"\bempty test suite\b",
    r"\bno test files\b", r"\bexit 5\b", r"\bpytest exit: 5\b",
    r"\bno tests found\b", r"\bnothing to do\b",
]

CLAIM_RE = re.compile("|".join(CLAIM_PATTERNS), re.IGNORECASE)
VERIFY_RE = re.compile("|".join(VERIFY_COMMAND_PATTERNS), re.IGNORECASE)
FAILURE_RE = re.compile("|".join(FAILURE_PATTERNS))
SUCCESS_RE = re.compile("|".join(SUCCESS_PATTERNS), re.IGNORECASE)
NO_RESULT_RE = re.compile("|".join(NO_RESULT_PATTERNS), re.IGNORECASE)


# ----------------------------------------------------------------------------
# Transcript parsing — tolerant of the common Claude Code JSONL shapes.
# ----------------------------------------------------------------------------

def _iter_content_blocks(message):
    """Yield content blocks from a message dict, tolerating string or list content."""
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield {"type": "text", "text": content}
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block
            elif isinstance(block, str):
                yield {"type": "text", "text": block}


def _text_of(block):
    """Extract plain text from a content block of any supported shape."""
    if not isinstance(block, dict):
        return str(block)
    if "text" in block and isinstance(block["text"], str):
        return block["text"]
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for sub in c:
            if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                parts.append(sub["text"])
            elif isinstance(sub, str):
                parts.append(sub)
        return "\n".join(parts)
    return ""


def parse_transcript(path):
    """
    Return an ordered list of events:
      {"kind": "assistant_text", "text": str}
      {"kind": "edit", "tool": str}
      {"kind": "bash", "command": str}
      {"kind": "tool_result", "ok": bool|None, "text": str}
    Unknown / malformed lines are skipped rather than crashing.
    """
    events = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, IOError):
        return events

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue

        # The message may be nested under "message" or be the object itself.
        message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = message.get("role") or obj.get("type") or obj.get("role")

        # Top-level toolUseResult (Claude Code attaches run metadata here).
        tur = obj.get("toolUseResult")

        for block in _iter_content_blocks(message):
            btype = (block.get("type") or "").lower()

            if btype == "text" or (btype == "" and "text" in block):
                txt = _text_of(block)
                if txt and role in ("assistant", None):
                    # Treat untyped/assistant text as assistant_text; user text is ignored.
                    if role == "assistant" or obj.get("type") == "assistant":
                        events.append({"kind": "assistant_text", "text": txt})

            elif btype == "tool_use" or ("name" in block and "input" in block):
                name = str(block.get("name", "")).lower().replace("-", "").replace("_", "")
                inp = block.get("input") or {}
                if name in {n.replace("_", "") for n in EDIT_TOOL_NAMES}:
                    events.append({"kind": "edit", "tool": block.get("name", name)})
                elif name in {n.replace("_", "") for n in BASH_TOOL_NAMES}:
                    cmd = ""
                    if isinstance(inp, dict):
                        cmd = inp.get("command") or inp.get("cmd") or inp.get("script") or ""
                        if isinstance(cmd, list):
                            cmd = " ".join(str(x) for x in cmd)
                    events.append({"kind": "bash", "command": str(cmd)})

            elif btype == "tool_result" or "tool_use_id" in block:
                ok = None
                if isinstance(block.get("is_error"), bool):
                    ok = not block["is_error"]
                rtext = _text_of(block)
                if isinstance(tur, dict):
                    # Prefer explicit exit-code metadata when present.
                    for key in ("exitCode", "exit_code", "returnCode", "code"):
                        if isinstance(tur.get(key), int):
                            ok = (tur[key] == 0)
                            break
                    extra = tur.get("stdout", "") or tur.get("stderr", "") or ""
                    if isinstance(extra, str):
                        rtext = (rtext + "\n" + extra).strip()
                events.append({"kind": "tool_result", "ok": ok, "text": rtext or ""})

    return events


# ----------------------------------------------------------------------------
# The verdict.
# ----------------------------------------------------------------------------

def evaluate(events):
    """
    Return dict: {verdict, reasons:[...], claim_excerpt, checks:{...}}.
    """
    reasons = []

    edited = any(e["kind"] == "edit" for e in events)
    # Index of the last code-mutating action.
    last_edit_idx = max((i for i, e in enumerate(events) if e["kind"] == "edit"), default=-1)

    # Completion claims: scan the FINAL assistant text block (what it said at stop time).
    assistant_texts = [(i, e["text"]) for i, e in enumerate(events) if e["kind"] == "assistant_text"]
    claim_excerpt = ""
    claim_found = False
    for i, txt in reversed(assistant_texts):
        m = CLAIM_RE.search(txt)
        if m:
            claim_found = True
            # Pull a short window around the match for the report.
            start = max(0, m.start() - 40)
            end = min(len(txt), m.end() + 40)
            claim_excerpt = txt[start:end].strip().replace("\n", " ")
            break

    # --- N/A short-circuits (stay silent, don't nag) ---
    if not edited:
        return {
            "verdict": "N/A",
            "reasons": ["No code was changed this session — nothing to verify."],
            "claim_excerpt": "",
            "checks": {"edited": False, "claim": claim_found},
        }
    if not claim_found:
        return {
            "verdict": "N/A",
            "reasons": ["Code changed, but no completion/verification claim was made — not gating."],
            "claim_excerpt": "",
            "checks": {"edited": True, "claim": False},
        }

    # --- A claim exists and code changed. Look for backing evidence. ---
    # Verification = a bash command matching VERIFY_RE that ran AFTER the last edit,
    # with a following tool_result that did not fail.
    verify_ran = False
    verify_succeeded = False
    verify_failed = False
    verify_noresult = False   # a check ran but executed nothing (pytest exit 5, etc.)
    verify_cmd = ""
    success_cmd = ""

    for i, e in enumerate(events):
        if e["kind"] != "bash" or i <= last_edit_idx:
            continue
        if not VERIFY_RE.search(e["command"]):
            continue
        verify_ran = True
        verify_cmd = e["command"].strip()
        # Find the next tool_result after this command.
        result_text = ""
        result_ok = None
        for j in range(i + 1, len(events)):
            if events[j]["kind"] == "tool_result":
                result_ok = events[j]["ok"]
                result_text = events[j]["text"]
                break

        failed = (result_ok is False) or bool(FAILURE_RE.search(result_text))
        no_result = bool(NO_RESULT_RE.search(result_text))
        positive = bool(SUCCESS_RE.search(result_text))

        if failed:
            verify_failed = True
        elif no_result:
            # Ran but verified nothing — NOT a pass. This is the false-PASS trap.
            verify_noresult = True
        elif positive:
            # A genuine positive outcome. Exit-0 alone is NOT enough: a command can
            # exit 0 having done nothing. We require a positive success signal.
            verify_succeeded = True
            success_cmd = verify_cmd
        elif result_ok is True:
            # Exit 0 with no recognizable success or no-result signal. Treat as
            # weak/unconfirmed, not a pass — bias toward UNVERIFIED over false PASS.
            pass
        # keep scanning: a later clean success can still be found

    if verify_succeeded and not verify_failed:
        return {
            "verdict": "PASS",
            "reasons": [
                f'Claim backed: `{success_cmd[:80]}` ran after the last edit and reported a '
                f"passing result.",
            ],
            "claim_excerpt": claim_excerpt,
            "checks": {"edited": True, "claim": True, "verify_ran": True, "verify_succeeded": True},
        }

    if verify_failed and not verify_succeeded:
        reasons.append(
            f'Claim CONTRADICTED: "{claim_excerpt}" — but `{verify_cmd[:80]}` '
            f"reported failure after the last edit."
        )
    elif verify_succeeded and verify_failed:
        reasons.append(
            f'Mixed evidence: one verification command passed and another failed after the '
            f'last edit. Claim "{claim_excerpt}" is not cleanly backed.'
        )
    elif verify_noresult:
        reasons.append(
            f'Claim UNBACKED: "{claim_excerpt}" — a check ran but executed no tests '
            f"(nothing was collected), so nothing was actually verified."
        )
    elif verify_ran:
        reasons.append(
            f'Claim UNBACKED: "{claim_excerpt}" — a check ran after the last edit but gave '
            f"no clear passing result."
        )
    else:
        reasons.append(
            f'Claim UNBACKED: "{claim_excerpt}" — but no test/build/lint command ran after '
            f"the last code change this session."
        )
    reasons.append("Run the relevant check (and let its output into the session) before finishing, "
                   "or soften the claim to match what was actually verified.")

    return {
        "verdict": "UNVERIFIED",
        "reasons": reasons,
        "claim_excerpt": claim_excerpt,
        "checks": {"edited": True, "claim": True, "verify_ran": verify_ran,
                   "verify_succeeded": verify_succeeded, "verify_failed": verify_failed,
                   "verify_noresult": verify_noresult},
    }


# ----------------------------------------------------------------------------
# Rendering + hook I/O.
# ----------------------------------------------------------------------------

def render(result):
    lines = [f"Proof Gate: {result['verdict']}"]
    for r in result["reasons"]:
        lines.append(f"  • {r}")
    return "\n".join(lines)


def _log_verdict(message):
    """Append the verdict to a per-user log so warn-mode results are never lost,
    without feeding anything back to the model."""
    try:
        import time
        logdir = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parent.parent))
        logfile = logdir / ".proof-gate-verdicts.log"
        with logfile.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message.replace("\n", " | ") + "\n")
    except Exception:
        pass  # logging is best-effort, never fatal


def emit_hook_output(result, mode):
    """
    Print the Stop-hook contract and return the exit code.

    CRITICAL loop-safety rule: a Stop hook must stay SILENT on stdout unless it
    is intentionally blocking. Emitting feedback (additionalContext) on every
    stop makes Claude respond to it and stop again — an infinite loop. So:
      • Block (block mode + UNVERIFIED): emit decision:block ONCE. The
        stop_hook_active guard in main() prevents this from looping.
      • Everything else: NOTHING on stdout. The verdict goes to stderr (for the
        user) and a log file. The model is never fed, so it never re-triggers.
    """
    verdict = result["verdict"]
    message = render(result)

    if verdict == "UNVERIFIED" and mode == "block":
        _log_verdict(message)
        print(json.dumps({"decision": "block", "reason": message}))
        return 0  # JSON decision takes precedence

    # Non-blocking for everything else. Do NOT touch stdout.
    if verdict != "N/A":
        _log_verdict(message)
        # stderr is shown to the user by Claude Code but not fed back to Claude.
        sys.stderr.write(message + "\n")
    return 0


def run_on_transcript(path, mode):
    events = parse_transcript(path)
    result = evaluate(events)
    return emit_hook_output(result, mode), result


def normalize_path(p):
    """
    Resolve a transcript path that may arrive in a Unix-shell form on Windows.
    Claude Code normally passes a native path, but some shells (MSYS2, Git Bash,
    Cygwin) hand over '/c/Users/...' or '/cygdrive/c/Users/...'. Windows Python
    can't stat those, so we translate to 'C:\\Users\\...' rather than fail open
    on a path we could have read. Returns the first form that exists, else the
    original.
    """
    if not p:
        return p
    try:
        if Path(p).exists():
            return p
    except (OSError, ValueError):
        pass
    candidates = []
    m = re.match(r"^/cygdrive/([a-zA-Z])/(.*)$", p)
    if m:
        candidates.append(m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\"))
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        candidates.append(m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\"))
    for c in candidates:
        try:
            if Path(c).exists():
                return c
        except (OSError, ValueError):
            continue
    return p


def read_stdin_json():
    try:
        data = sys.stdin.read()
    except (OSError, IOError):
        return {}
    if not data.strip():
        return {}
    try:
        obj = json.loads(data)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def main(argv):
    mode = os.environ.get("PROOF_GATE_MODE", "warn").strip().lower()
    if mode not in ("warn", "block"):
        mode = "warn"

    if "--selftest" in argv:
        return selftest()

    transcript = None
    if "--transcript" in argv:
        idx = argv.index("--transcript")
        if idx + 1 < len(argv):
            transcript = argv[idx + 1]

    hook = {}
    if transcript is None:
        hook = read_stdin_json()
        transcript = hook.get("transcript_path") or hook.get("transcriptPath")

    transcript = normalize_path(transcript)

    # LOOP BREAKER (required by the Stop-hook contract): if we are re-entered
    # because a previous block is still active, allow the stop immediately.
    # Without this, block mode would hold the turn forever.
    if hook.get("stop_hook_active") is True:
        return 0

    if not transcript or not Path(transcript).exists():
        # Nothing to read. Fail OPEN and stay silent (never block on plumbing).
        return 0

    code, _ = run_on_transcript(transcript, mode)
    return code


# ----------------------------------------------------------------------------
# Self-test: proves the gate fires correctly on a clean machine.
# ----------------------------------------------------------------------------

def selftest():
    here = Path(__file__).resolve().parent
    fixtures = here.parent / "tests" / "fixtures"
    cases = [
        ("pass.jsonl", "PASS"),
        ("unverified_unbacked.jsonl", "UNVERIFIED"),
        ("unverified_contradicted.jsonl", "UNVERIFIED"),
        ("unverified_no_tests.jsonl", "UNVERIFIED"),
        ("na_no_edits.jsonl", "N/A"),
        ("na_no_claim.jsonl", "N/A"),
        ("na_done_only.jsonl", "N/A"),
    ]
    failures = 0
    for fname, expected in cases:
        fpath = fixtures / fname
        if not fpath.exists():
            print(f"MISSING fixture: {fpath}")
            failures += 1
            continue
        events = parse_transcript(str(fpath))
        result = evaluate(events)
        got = result["verdict"]
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"[{status}] {fname}: expected {expected}, got {got}")
        if got != expected:
            for r in result["reasons"]:
                print(f"         reason: {r}")
    print()
    if failures:
        print(f"SELFTEST FAILED: {failures} case(s) wrong.")
        return 1
    print("SELFTEST PASSED: all verdicts correct.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
