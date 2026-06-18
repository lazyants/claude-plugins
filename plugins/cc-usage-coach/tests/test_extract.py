"""
Generalization tests for the output-dir resolution helpers in lib_sessions:
out_dir() precedence and _is_writable_dir(). Synthetic tmp paths only.
Run: python3 -m pytest tests/test_extract.py -q

The 0600 mode of source_index.json is set in signals.main() and is covered by the
signals/integration tests (it needs a real dataset), so it is intentionally not
asserted here — this file focuses on out_dir + _is_writable_dir.
"""
import os, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "cc-usage-coach", "scripts"))
import lib_sessions as L


# --- out_dir precedence -------------------------------------------------------
def test_out_dir_env_wins(tmp_path, monkeypatch):
    target = tmp_path / "o"
    monkeypatch.setenv("CC_COACH_OUT", str(target))
    got = L.out_dir()
    # CC_COACH_OUT wins, resolved to an absolute path, and the dir is created.
    assert got == os.path.abspath(str(target))
    assert os.path.isdir(got)


def test_out_dir_env_expands_user(tmp_path, monkeypatch):
    # A ~-prefixed CC_COACH_OUT is expanded; point HOME at tmp so we don't touch real $HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CC_COACH_OUT", "~/out-here")
    got = L.out_dir()
    assert got == os.path.join(str(tmp_path), "out-here")
    assert os.path.isdir(got)


def test_out_dir_returns_writable_dir_without_env(tmp_path, monkeypatch):
    # With CC_COACH_OUT unset, out_dir() returns SOME writable dir and a probe
    # write into it succeeds. We don't assert the exact path (dev-tree vs cache).
    monkeypatch.delenv("CC_COACH_OUT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    got = L.out_dir()
    assert os.path.isdir(got)
    probe = os.path.join(got, ".probe_write")
    with open(probe, "w") as fh:
        fh.write("ok")
    assert os.path.exists(probe)
    os.remove(probe)


# --- _is_writable_dir ---------------------------------------------------------
def test_is_writable_dir_true_for_normal_tmp(tmp_path):
    assert L._is_writable_dir(str(tmp_path)) is True


def test_is_writable_dir_creates_missing_dir(tmp_path):
    # _is_writable_dir makes the dir if absent, then confirms it can be written.
    target = str(tmp_path / "made" / "here")
    assert L._is_writable_dir(target) is True
    assert os.path.isdir(target)


def test_is_writable_dir_false_for_readonly(tmp_path):
    # A read-only (0o500) dir cannot be written -> False. Root bypasses perms, so skip.
    if os.getuid() == 0:
        import pytest
        pytest.skip("running as root bypasses directory permissions")
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(str(ro), 0o500)
    try:
        assert L._is_writable_dir(str(ro)) is False
    finally:
        os.chmod(str(ro), 0o700)  # restore so pytest can clean up tmp_path


# --- proj_of: the shareable pack's project label must be PATH-FREE (codex r2 HIGH) ---
def test_proj_of_is_path_free(monkeypatch):
    import extract as E
    monkeypatch.setattr(E, "_SELF", set())   # isolate the path-SHAPE logic from the test-runner's identity
    # normal project cwd -> its leaf basename (a clean project name)
    assert E.proj_of({"cwd": "/Users/alice/myrepo"}, "/x/p/s.jsonl") == "myrepo"
    assert E.proj_of({"cwd": "/home/bob/work/proj"}, "/x/p/s.jsonl") == "proj"
    # cwd that IS a home dir -> basename would be the USERNAME -> "unknown", never leaked
    assert E.proj_of({"cwd": "/Users/alice"}, "/x/p/s.jsonl") == "unknown"
    assert E.proj_of({"cwd": "/home/bob"}, "/x/p/s.jsonl") == "unknown"
    # no cwd -> the dir-name fallback is the mangled full path -> "unknown"
    assert E.proj_of({}, "/Users/alice/.claude/projects/-Users-alice-myrepo/s.jsonl") == "unknown"
    # a cwd that is itself a mangled CC dir name -> "unknown"
    assert E.proj_of({"cwd": "-Users-alice-myrepo"}, "/x/p/s.jsonl") == "unknown"
    # Windows cwd (recorded on a Windows machine, analyzed on a POSIX host): the project leaf is
    # safe, but a Windows HOME dir must still collapse to "unknown" (ntpath parsing — review).
    assert E.proj_of({"cwd": "C:\\Users\\alice\\myrepo"}, "/x/p/s.jsonl") == "myrepo"
    assert E.proj_of({"cwd": "C:\\Users\\alice"}, "/x/p/s.jsonl") == "unknown"
    assert E.proj_of({"cwd": "C:/Users/alice"}, "/x/p/s.jsonl") == "unknown"
    assert E.proj_of({"cwd": "\\\\server\\Users\\alice"}, "/x/p/s.jsonl") == "unknown"


# --- _safe_leaf: every leaf into the shareable pack (repeat_reads + proj_of) must be PATH-FREE ---
def test_safe_leaf_is_path_free(monkeypatch):
    import extract as E
    monkeypatch.setattr(E, "_SELF", set())   # isolate the path-SHAPE logic from the test-runner's identity
    # a normal file -> its basename (a clean leaf is a legit re-read signal)
    assert E._safe_leaf("/Users/alice/proj/file.py") == "file.py"
    # Read of a home dir -> basename would be the USERNAME -> dropped
    assert E._safe_leaf("/Users/alice") is None
    assert E._safe_leaf("/home/bob") is None
    # Windows home dir, BOTH backslash and forward-slash forms -> username -> dropped.
    # posixpath alone leaked "C:/Users/alice" as "alice" (codex); ntpath parses both correctly.
    assert E._safe_leaf("C:\\Users\\alice") is None
    assert E._safe_leaf("C:/Users/alice") is None
    # WSL / mounted-drive home roots (parent leaf is "Users"/"home", whatever the mount prefix)
    assert E._safe_leaf("/mnt/c/Users/alice") is None
    assert E._safe_leaf("/Volumes/Data/Users/alice") is None
    # UNC Windows roaming/network home: ntpath.basename of the share root is "" -> split manually
    assert E._safe_leaf("\\\\server\\Users\\alice") is None
    # the home-root dir itself, and the Linux root account's home -> dropped
    assert E._safe_leaf("/Users") is None
    assert E._safe_leaf("/root") is None
    # but a real project UNDER root's home keeps its leaf (root is not a multi-user home parent)
    assert E._safe_leaf("/root/myproj") == "myproj"
    # a Windows FILE path is split correctly: username parent dropped, the filename leaf kept & safe
    assert E._safe_leaf("C:\\Users\\alice\\proj\\secret.py") == "secret.py"
    # any leaf still carrying a stray ':' (un-split drive / alternate-data-stream) -> dropped
    assert E._safe_leaf("/weird/with:colon") is None
    # the renamed helper replaced the old name (no divergent second copy can drift)
    assert not hasattr(E, "_safe_read_leaf")


# --- local-identity backstop: a relocated home under a NON-standard parent (path shape can't see
#     it) must still drop the username, because the tool knows the local $HOME/$USER (PR #1 review) ---
def test_safe_leaf_drops_local_identity(monkeypatch):
    import extract as E
    monkeypatch.setattr(E, "_SELF", {"alice"})   # pretend the local user is "alice"
    # bare relocated home roots whose parent leaf is NOT Users/home -> shape misses, identity catches
    assert E._safe_leaf("/Volumes/Data/alice") is None
    assert E._safe_leaf("/export/home2/alice") is None
    assert E.proj_of({"cwd": "D:\\Profiles\\alice"}, "/x/p/s.jsonl") == "unknown"
    # but a real project UNDER the relocated home keeps its leaf (only the username is dropped)
    assert E._safe_leaf("/Volumes/Data/alice/realproj") == "realproj"
    assert E.proj_of({"cwd": "/Volumes/Data/alice/realproj"}, "/x/p/s.jsonl") == "realproj"


# --- _session_id: the SHAREABLE pack's source_ref must be OPAQUE — no filename leak (PR audit F1) ---
def test_session_id_opaque_no_filename_leak():
    # a session file named after a client/project, or carrying a username, must NOT survive into
    # the shareable source_ref — only an opaque sess_<hash>. (Default CC logs are UUIDs, but the
    # contract is unconditional and the code must enforce it.)
    for p in ("/Users/x/projects/p/CLIENTACME-debugging-session.jsonl",
              "/Users/bob/.claude/projects/-Users-bob-secret/bob_the_user.jsonl",
              "C:\\Users\\bob\\proj\\acmecorp-merger.jsonl"):
        sid = L.session_id(p)
        assert re.match(r"^sess_[0-9a-f]{10}$", sid), sid
        for needle in ("CLIENTACME", "debugging", "bob", "secret", "acmecorp", "merger", "Users"):
            assert needle not in sid
    # stable + path-specific (different realpaths -> different ids)
    assert L.session_id("/a/x.jsonl") == L.session_id("/a/x.jsonl")
    assert L.session_id("/a/x.jsonl") != L.session_id("/b/x.jsonl")
