import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "cc-usage-coach", "scripts"))
import arc


# ---------------------------------------------------------------------------
# Helpers (synthetic identities only — these files are PUBLIC)
# ---------------------------------------------------------------------------
def _write_jsonl(path, entries):
    with open(path, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _user(content, **extra):
    e = {"type": "user", "message": {"role": "user", "content": content}}
    e.update(extra)
    return e


# ---------------------------------------------------------------------------
# redact_paths
# ---------------------------------------------------------------------------
def test_redact_paths():
    cases = [
        "/Users/x/a/b.py",
        "/home/x/c",
        "-Users-x-a-b",
        "%2FUsers%2Fx%2Fsecret",
    ]
    for original in cases:
        result = arc.redact_paths(f"look at {original} now")
        assert "<path>" in result, result
        assert original not in result, result

    # a no-path string is returned unchanged
    plain = "just some plain text with no paths"
    assert arc.redact_paths(plain) == plain
    # empty / falsy passes through
    assert arc.redact_paths("") == ""
    assert arc.redact_paths(None) is None


def test_redact_paths_spaces_and_windows():
    # codex F1: a POSIX path WITH SPACES must be fully redacted (no surviving suffix),
    # plus the Windows form. A bare `~/...` is intentionally left alone.
    r = arc.redact_paths('paste "/Users/x/My Proj/secret.txt" here')
    assert "<path>" in r
    for leaked in ("My", "Proj", "secret"):
        assert leaked not in r, r

    r3 = arc.redact_paths(r"on C:\Users\x\My Files\thing")
    assert "<path>" in r3
    for leaked in ("My", "Files", "thing"):
        assert leaked not in r3, r3

    # a bare home-relative tilde is NOT a machine-path leak -> left untouched
    assert arc.redact_paths("open ~/notes next") == "open ~/notes next"


# ---------------------------------------------------------------------------
# iter_human_prompts
# ---------------------------------------------------------------------------
def test_iter_human_prompts():
    entries = [
        _user("a normal user prompt"),
        _user([{"type": "tool_result", "content": "stuff"}]),        # excluded (no text)
        _user("meta text", isMeta=True),                              # excluded
        _user("compact summary text", isCompactSummary=True),         # excluded
        _user("<command-name>/foo</command-name><command-message>x</command-message>"),
        {"type": "assistant", "message": {"role": "assistant", "content": "hi"}},  # not user
    ]
    prompts = arc.iter_human_prompts(entries)
    # only the normal prompt and the slash-command prompt survive
    assert len(prompts) == 2
    assert prompts[0]["text"] == "a normal user prompt"
    assert prompts[0]["cmd"] is None
    assert prompts[1]["cmd"] == "/foo"
    # the command tags are stripped out of the visible text
    assert "<command-name>" not in prompts[1]["text"]


def test_command_name_path_redacted():
    # codex F4: a path embedded in a <command-name> must be redacted in the captured cmd.
    entries = [
        _user("<command-name>/run /Users/x/secret/script.sh</command-name>"
              "<command-message>go</command-message>"),
    ]
    prompts = arc.iter_human_prompts(entries)
    assert len(prompts) == 1
    cmd = prompts[0]["cmd"]
    assert "<path>" in cmd
    for leaked in ("/Users/", "secret", "script.sh"):
        assert leaked not in cmd, cmd


# ---------------------------------------------------------------------------
# arc_markers
# ---------------------------------------------------------------------------
def test_arc_markers():
    entries = [
        _user("kick things off"),
        {"type": "user", "message": {"role": "user", "content": "compacted"},
         "isCompactSummary": True, "compactMetadata": {"trigger": "auto"}},
        _user("This session is being continued from a previous conversation here"),
        _user("doing a <<autonomous-loop iteration"),
        _user("<command-name>/loop</command-name><command-message>go</command-message>"),
    ]
    markers = arc.arc_markers(entries)
    assert markers["compactions"] >= 1
    assert markers["continuation_resumes"] == 1
    assert markers["autonomous_loop"] is True
    assert "/loop" in markers["slash_cmds"]


# ---------------------------------------------------------------------------
# format_arc elision
# ---------------------------------------------------------------------------
def test_format_arc_elision():
    meta = {"ref": "REF", "project": None, "span": (None, None), "models": [], "turns": 60}
    prompts = [{"idx": i, "ts": None, "cmd": None, "text": f"prompt {i}"} for i in range(60)]
    out = arc.format_arc(meta, prompts, {"compactions": 0, "continuation_resumes": 0,
                                         "autonomous_loop": False, "slash_cmds": {}})
    # head 40 + tail 10 -> 10 elided
    assert "[+10 earlier prompts elided]" in out

    # a short list prints all with no elision line
    short = [{"idx": i, "ts": None, "cmd": None, "text": f"p{i}"} for i in range(5)]
    out5 = arc.format_arc({"ref": "R", "span": (None, None), "models": [], "turns": 5},
                          short, {"compactions": 0, "continuation_resumes": 0,
                                  "autonomous_loop": False, "slash_cmds": {}})
    assert "elided" not in out5
    for i in range(5):
        assert f"p{i}" in out5


# ---------------------------------------------------------------------------
# resolve_ref
# ---------------------------------------------------------------------------
def test_resolve_ref():
    index = {"REF": "/some/path.jsonl"}
    assert arc.resolve_ref("REF", index) == "/some/path.jsonl"
    assert arc.resolve_ref("NOPE", index) is None


# ---------------------------------------------------------------------------
# main — the critical security test
# ---------------------------------------------------------------------------
def test_main_security(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CC_COACH_OUT", str(tmp_path))

    sess = tmp_path / "sess.jsonl"
    entries = [
        {"type": "user", "cwd": "/Users/x/secretproj/foo",
         "timestamp": "2026-06-18T10:00:00Z",
         "message": {"role": "user",
                     "content": "please open /Users/x/hidden/file.txt"}},
        {"type": "user", "cwd": "/Users/x/secretproj/foo",
         "timestamp": "2026-06-18T10:05:00Z",
         "message": {"role": "user", "content": "also check %2FUsers%2Fx%2Fhidden"}},
        {"type": "assistant", "timestamp": "2026-06-18T10:06:00Z",
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "ok"}]}},
    ]
    _write_jsonl(sess, entries)
    (tmp_path / "source_index.json").write_text(json.dumps({"REF": str(sess)}))

    rc = arc.main(["arc.py", "REF"])
    assert rc == 0
    out = capsys.readouterr().out
    for forbidden in ("/Users/", "/home/", "-Users-", "secretproj", "hidden", ".jsonl"):
        assert forbidden not in out, f"leaked {forbidden!r} in:\n{out}"
    # the safe leaf of the cwd IS allowed (it is just a basename, no path)
    assert "foo" in out

    # --- missing index ---------------------------------------------------
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("CC_COACH_OUT", str(empty))
    rc = arc.main(["arc.py", "REF"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "run signals.py" in err
    for forbidden in ("/Users/", "-Users-"):
        assert forbidden not in err

    # --- unknown ref (index present) ------------------------------------
    monkeypatch.setenv("CC_COACH_OUT", str(tmp_path))
    rc = arc.main(["arc.py", "NOPE"])
    assert rc == 1


# ---------------------------------------------------------------------------
# main — a home-directory cwd must NOT leak the username as the "project"
# ---------------------------------------------------------------------------
def test_main_home_dir_cwd_no_username(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CC_COACH_OUT", str(tmp_path))
    sess = tmp_path / "home.jsonl"
    entries = [
        {"type": "user", "cwd": "/Users/alice", "timestamp": "2026-06-18T10:00:00Z",
         "message": {"role": "user", "content": "do a thing"}},
        {"type": "user", "cwd": "/Users/alice", "timestamp": "2026-06-18T10:01:00Z",
         "message": {"role": "user", "content": "do another thing"}},
    ]
    _write_jsonl(sess, entries)
    (tmp_path / "source_index.json").write_text(json.dumps({"H": str(sess)}))

    rc = arc.main(["arc.py", "H"])
    assert rc == 0
    out = capsys.readouterr().out
    # the username (cwd basename) must NOT appear
    assert "alice" not in out
    # the header still renders with ref + turn count (project field omitted)
    header = out.splitlines()[0]
    assert header.startswith("H |")
    assert "2 turns" in header

    # a /home/<user> cwd is suppressed too
    sess2 = tmp_path / "home2.jsonl"
    _write_jsonl(sess2, [
        {"type": "user", "cwd": "/home/bob", "timestamp": "2026-06-18T10:00:00Z",
         "message": {"role": "user", "content": "hi"}},
    ])
    (tmp_path / "source_index.json").write_text(json.dumps({"H2": str(sess2)}))
    rc = arc.main(["arc.py", "H2"])
    assert rc == 0
    assert "bob" not in capsys.readouterr().out

    # but a NORMAL project cwd still shows its leaf
    sess3 = tmp_path / "proj.jsonl"
    _write_jsonl(sess3, [
        {"type": "user", "cwd": "/Users/alice/myrepo", "timestamp": "2026-06-18T10:00:00Z",
         "message": {"role": "user", "content": "hi"}},
    ])
    (tmp_path / "source_index.json").write_text(json.dumps({"P": str(sess3)}))
    rc = arc.main(["arc.py", "P"])
    assert rc == 0
    out3 = capsys.readouterr().out
    assert "myrepo" in out3
    assert "alice" not in out3


# ---------------------------------------------------------------------------
# main — opener regression: a trivial first prompt must not dominate the digest
# ---------------------------------------------------------------------------
def test_main_opener_regression(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CC_COACH_OUT", str(tmp_path))

    sess = tmp_path / "sess2.jsonl"
    entries = [
        _user(".mcp", timestamp="2026-06-18T09:00:00Z", cwd="/Users/x/project-a"),
        _user("now refactor the parser to handle nested tags",
              timestamp="2026-06-18T09:10:00Z", cwd="/Users/x/project-a"),
        _user("and add tests for the elision path",
              timestamp="2026-06-18T09:20:00Z", cwd="/Users/x/project-a"),
        _user("<command-name>/loop</command-name><command-message>go</command-message>",
              timestamp="2026-06-18T09:30:00Z", cwd="/Users/x/project-a"),
    ]
    _write_jsonl(sess, entries)
    (tmp_path / "source_index.json").write_text(json.dumps({"REF2": str(sess)}))

    rc = arc.main(["arc.py", "REF2"])
    assert rc == 0
    out = capsys.readouterr().out
    # later substantive prompts are present, not just the trivial opener
    assert "refactor the parser" in out
    assert "add tests for the elision" in out
    # the markers line is always present
    assert "markers:" in out
    assert "autonomous_loop=True" in out
