#!/usr/bin/env python3
"""Strict fake `codex` binary for codebase-migrator tests.

Accepts only the two argument shapes `sandbox.py` actually issues:

  <bin> --version
  <bin> exec -s MODE -C DIR --skip-git-repo-check [--ephemeral] -o FILE -

Any other invocation is rejected with a non-zero exit. What the fake DOES is
controlled only by the FAKE_CODEX_SCENARIO environment variable (plus a few
scenario-specific FAKE_CODEX_* variables) -- never by the prompt text it
reads from stdin, exactly like the real binary's behaviour is bounded by its
sandbox mode and the files under -C, not by what the prompt asks for. Set
FAKE_CODEX_PROMPT_LOG to a file path to have this fake write the received
prompt there verbatim, for a test that wants to inspect it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

VERSION_STRING = "fake-codex 0.0.0-test"

DEFAULT_PORT_PY = (
    "def apply_discount(price, pct):\n"
    "    return price\n"
)


def fail(message: str) -> None:
    sys.stderr.write(f"fake_codex: {message}\n")
    sys.exit(2)


def parse_exec_args(argv: list) -> dict:
    if len(argv) not in (9, 10):
        fail(f"unexpected argument count for exec: {argv!r}")
    if argv[0] != "exec":
        fail("first argument must be exec")
    if argv[1] != "-s":
        fail("expected -s MODE as the second argument")
    mode = argv[2]
    if mode not in ("workspace-write", "read-only"):
        fail(f"unsupported sandbox mode: {mode!r}")
    if argv[3] != "-C":
        fail("expected -C DIR")
    stage = argv[4]
    idx = 5
    if argv[idx] != "--skip-git-repo-check":
        fail("expected --skip-git-repo-check")
    idx += 1
    if idx < len(argv) and argv[idx] == "--ephemeral":
        idx += 1
    if idx >= len(argv) or argv[idx] != "-o":
        fail("expected -o FILE")
    idx += 1
    if idx >= len(argv):
        fail("missing FILE after -o")
    out_file = argv[idx]
    idx += 1
    if idx >= len(argv) or argv[idx] != "-":
        fail("expected a trailing - (read the prompt from stdin)")
    idx += 1
    if idx != len(argv):
        fail(f"unexpected trailing arguments: {argv[idx:]!r}")
    return {"mode": mode, "stage": Path(stage), "out_file": Path(out_file)}


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def parse_probe_canaries(stage: Path):
    text = read_text(stage / "probe.sh")
    if text is None:
        return []
    canaries = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# CANARY "):
            _, _, rest = line.partition("# CANARY ")
            index_str, _, path_str = rest.partition(" ")
            canaries.append((int(index_str), path_str))
    return canaries


def run_probe_scenario(scenario: str, stage: Path) -> None:
    canaries = parse_probe_canaries(stage)
    probe_dir = stage / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    inside = probe_dir / "inside.txt"

    if scenario == "probe_not_exercised":
        # Never runs the script at all: no .rc/.err files, canaries untouched.
        inside.write_text("ok", encoding="utf-8")
        return

    if scenario == "probe_denied":
        # Genuinely run probe.sh, with every canary made read-only for the
        # duration, so each write attempt fails for real and probe.sh writes
        # its own rc/err files and its own inside.txt -- a probe.sh with no
        # write commands in it (a bug in sandbox.py's own generation) then
        # produces no rc files and this fake reports NOT_EXERCISED, instead
        # of always reporting "denied" regardless of what was generated.
        original_modes = {}
        for _i, canary_path in canaries:
            path = Path(canary_path)
            try:
                original_modes[path] = os.stat(path).st_mode
                os.chmod(path, 0o444)
            except OSError:
                pass
        try:
            subprocess.run(["sh", str(stage / "probe.sh")], capture_output=True)
        finally:
            for path, mode in original_modes.items():
                try:
                    os.chmod(path, mode)
                except OSError:
                    pass
        return

    if scenario == "probe_not_denied":
        for i, canary_path in canaries:
            (probe_dir / f"{i}.rc").write_text("0", encoding="utf-8")
            (probe_dir / f"{i}.err").write_text("", encoding="utf-8")
            try:
                Path(canary_path).write_text("tampered", encoding="utf-8")
            except OSError:
                pass
        inside.write_text("ok", encoding="utf-8")
        return

    fail(f"unknown probe scenario: {scenario!r}")


def run_dispatch_scenario(scenario: str, stage: Path, out_file: Path) -> None:
    out_dir = stage / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    if scenario in ("port_ok", "fix_ok"):
        content = os.environ.get("FAKE_CODEX_TARGET_PY", DEFAULT_PORT_PY)
        (out_dir / "target.py").write_text(content, encoding="utf-8")
        out_file.write_text("done", encoding="utf-8")
        return

    if scenario == "port_extra_ignored":
        content = os.environ.get("FAKE_CODEX_TARGET_PY", DEFAULT_PORT_PY)
        (out_dir / "target.py").write_text(content, encoding="utf-8")
        (out_dir / "notes.txt").write_text("scratch, never promoted", encoding="utf-8")
        out_file.write_text("done", encoding="utf-8")
        return

    if scenario == "port_symlink":
        content = os.environ.get("FAKE_CODEX_TARGET_PY", DEFAULT_PORT_PY)
        real = out_dir / "real_target.py"
        real.write_text(content, encoding="utf-8")
        os.symlink(real, out_dir / "target.py")
        out_file.write_text("done", encoding="utf-8")
        return

    if scenario == "port_no_output":
        out_file.write_text("done, but forgot the file", encoding="utf-8")
        return

    if scenario == "tamper_outside":
        tamper_path = os.environ.get("FAKE_CODEX_TAMPER_PATH")
        if not tamper_path:
            fail("tamper_outside scenario needs FAKE_CODEX_TAMPER_PATH")
        Path(tamper_path).write_text("tampered", encoding="utf-8")
        content = os.environ.get("FAKE_CODEX_TARGET_PY", DEFAULT_PORT_PY)
        (out_dir / "target.py").write_text(content, encoding="utf-8")
        out_file.write_text("done", encoding="utf-8")
        return

    if scenario == "review_empty":
        out_file.write_text(json.dumps({"findings": []}), encoding="utf-8")
        return

    if scenario == "review_json":
        payload = os.environ.get("FAKE_CODEX_REVIEW_JSON")
        if payload is None:
            fail("review_json scenario needs FAKE_CODEX_REVIEW_JSON")
        out_file.write_text(payload, encoding="utf-8")
        return

    if scenario == "review_prose":
        payload = os.environ.get("FAKE_CODEX_REVIEW_JSON", '{"findings": []}')
        out_file.write_text(f"Here is my review.\n{payload}\nThanks!\n", encoding="utf-8")
        return

    if scenario == "cases_json":
        payload = os.environ.get("FAKE_CODEX_CASES_JSON")
        if payload is None:
            fail("cases_json scenario needs FAKE_CODEX_CASES_JSON")
        (out_dir / "cases.json").write_text(payload, encoding="utf-8")
        out_file.write_text("done", encoding="utf-8")
        return

    fail(f"unknown dispatch scenario: {scenario!r}")


def main() -> int:
    argv = sys.argv[1:]

    if argv == ["--version"]:
        print(VERSION_STRING)
        return 0

    parsed = parse_exec_args(argv)
    stage = parsed["stage"]

    # Read the prompt exactly like a real turn would -- its content never
    # selects behaviour here, but a test may still want to inspect it.
    prompt_text = sys.stdin.read()

    scenario = os.environ.get("FAKE_CODEX_SCENARIO")
    if not scenario:
        fail("FAKE_CODEX_SCENARIO is not set")

    argv_log = os.environ.get("FAKE_CODEX_ARGV_LOG")
    if argv_log:
        Path(argv_log).write_text(json.dumps(argv), encoding="utf-8")

    prompt_log = os.environ.get("FAKE_CODEX_PROMPT_LOG")
    if prompt_log:
        Path(prompt_log).write_text(prompt_text, encoding="utf-8")

    if (stage / "probe.sh").exists():
        run_probe_scenario(scenario, stage)
    else:
        run_dispatch_scenario(scenario, stage, parsed["out_file"])

    # Deliberately misleading stdout: the caller must judge ground truth from
    # the files it inspects afterward, never from what this message claims.
    print("codex turn complete (this message proves nothing on its own)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
