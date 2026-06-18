"""
Generalization tests for lib_sessions config-dir resolution + file discovery.

These cover the public-skill generalization of lib_sessions: env-driven config-dir
resolution (_resolve_config_dirs) and realpath-deduped session-log discovery
(discover_files). Synthetic paths only — no real usernames/projects. PLAN §1.
Run: python3 -m pytest tests/test_lib_sessions.py -q
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "cc-usage-coach", "scripts"))
import lib_sessions as L


# --- _resolve_config_dirs -----------------------------------------------------
def test_resolve_default_is_standard_claude_only():
    # Default scans exactly the standard `.claude` — proves the personal
    # multi-identity extras were dropped (positive assertion, no extra dirs).
    assert L._resolve_config_dirs({}) == [".claude"]


def test_resolve_claude_config_dir_single():
    assert L._resolve_config_dirs({"CLAUDE_CONFIG_DIR": "/abs/c"}) == ["/abs/c"]


def test_resolve_claude_config_dir_multi_comma():
    assert L._resolve_config_dirs({"CLAUDE_CONFIG_DIR": "/a,/b"}) == ["/a", "/b"]


def test_resolve_claude_config_dir_pathsep_not_split():
    # os.pathsep is NOT a separator — on POSIX it is ":", which would corrupt an absolute
    # path. Only comma splits CLAUDE_CONFIG_DIR; a pathsep-joined value stays one token.
    val = "/a" + os.pathsep + "/b"
    assert L._resolve_config_dirs({"CLAUDE_CONFIG_DIR": val}) == [val]


def test_resolve_claude_config_dir_replaces_default():
    # CLAUDE_CONFIG_DIR REPLACES the default `.claude` (not appended).
    out = L._resolve_config_dirs({"CLAUDE_CONFIG_DIR": "/only"})
    assert out == ["/only"]
    assert ".claude" not in out


def test_resolve_coach_config_dirs_appends_to_default():
    # CC_COACH_CONFIG_DIRS appends to whatever resolved — here the default.
    out = L._resolve_config_dirs({"CC_COACH_CONFIG_DIRS": "dir-a,dir-b"})
    assert out == [".claude", "dir-a", "dir-b"]


def test_resolve_coach_config_dirs_appends_to_claude_config_dir():
    # CLAUDE_CONFIG_DIR replaces default, then CC_COACH_CONFIG_DIRS appends.
    out = L._resolve_config_dirs({"CLAUDE_CONFIG_DIR": "/c", "CC_COACH_CONFIG_DIRS": "dir-a"})
    assert out == ["/c", "dir-a"]


# --- discover_files -----------------------------------------------------------
def _seed_log(home, config_dir, project, fname):
    """Create <home>/<config_dir>/projects/<project>/<fname> with a stub line."""
    d = os.path.join(home, config_dir, "projects", project)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, fname)
    with open(p, "w") as fh:
        fh.write('{"type": "stub"}\n')
    return p


def test_discover_files_realpath_dedup_symlinked_dir(tmp_path):
    home = str(tmp_path)
    _seed_log(home, ".claude", "p", "x.jsonl")
    # `dupe` is a symlink to `.claude`, so its projects/p/x.jsonl resolves to the
    # same real path — realpath-dedup must return the file only once.
    os.symlink(os.path.join(home, ".claude"), os.path.join(home, "dupe"))
    files = L.discover_files(config_dirs=[".claude", "dupe"], home=home)
    assert len(files) == 1
    assert files[0] == os.path.realpath(os.path.join(home, ".claude", "projects", "p", "x.jsonl"))


def test_discover_files_missing_dir_yields_nothing(tmp_path):
    home = str(tmp_path)
    # No config dirs exist at all -> empty list, no crash.
    files = L.discover_files(config_dirs=["nope", "also-missing"], home=home)
    assert files == []


def test_discover_files_finds_logs_across_dirs(tmp_path):
    home = str(tmp_path)
    a = _seed_log(home, ".claude", "p1", "a.jsonl")
    b = _seed_log(home, "other", "p2", "b.jsonl")
    files = set(L.discover_files(config_dirs=[".claude", "other"], home=home))
    assert files == {os.path.realpath(a), os.path.realpath(b)}


def test_discover_files_absolute_dir_used_as_is(tmp_path):
    # An absolute config-dir token is used verbatim, NOT joined under home.
    home = str(tmp_path / "home")
    os.makedirs(home, exist_ok=True)
    abs_dir = str(tmp_path / "elsewhere")
    p = _seed_log(str(tmp_path), "elsewhere", "p", "z.jsonl")  # creates tmp_path/elsewhere/...
    files = L.discover_files(config_dirs=[abs_dir], home=home)
    assert files == [os.path.realpath(p)]


def test_discover_files_recurses_nested_projects(tmp_path):
    home = str(tmp_path)
    # Nested project path under projects/ must still be found (recursive glob).
    d = os.path.join(home, ".claude", "projects", "deep", "nest")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "n.jsonl")
    with open(p, "w") as fh:
        fh.write("{}\n")
    files = L.discover_files(config_dirs=[".claude"], home=home)
    assert files == [os.path.realpath(p)]
