"""tests/canon_link_groups.test.py -- scripts/canon_link_groups.py, the single
runtime-validating loader for the one-entity link-routing sidecar
canon_link_groups.json (#588).

Every assertion drives `load_link_groups` end to end (never a bare
`jsonschema.Draft202012Validator`) against synthetic fixtures in `tmp_path`,
the same discipline `tests/canon_senses.test.py` uses for the other sidecar.
What is locked down here:

  - the strict `canon-link-groups.schema.json` shape: `members` `minItems:2`
    (a one-member "group" asserts nothing and is a hard error, never a
    silent no-op), `uniqueItems`, a required non-blank `note` (the
    provenance of an identity call no script may make -- THE IRON RULE), and
    `additionalProperties:false` at every level;
  - the three procedural rejects the schema cannot express: a `primary`
    outside its own `members`, a member that is not a `canon['entries']`
    key, and one `source_form` appearing in two groups;
  - the path-state policy: only a genuinely absent file is "absent"; a
    directory and a DANGLING SYMLINK both block, because a broken sidecar is
    one the operator meant to have -- treating it as absent would silently
    skip an identity pass they believe is applied;
  - that the returned map is the flat `{member: primary}` projection keyed
    by the LITERAL, byte-exact canon keys (no folding, no NFC
    renormalization), since `build_entity_index` looks up an owner's own raw
    `source_form`.
"""
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts"
    / "canon_link_groups.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("canon_link_groups_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


clg = _load_module()

ENTRIES = {
    "משה לייב": {"canonical_target_form": "Moyshe-Leyb"},
    "משה־לייב": {"canonical_target_form": "Moyshe-Leyb"},
    "Peter": {"canonical_target_form": "Peter"},
}


def _doc(groups=None):
    return {"schema_version": 1, "groups": groups if groups is not None else []}


def _group(primary="משה לייב", members=None, note="two pointings of one man"):
    return {
        "primary": primary,
        "members": members if members is not None else ["משה לייב", "משה־לייב"],
        "note": note,
    }


def _write(tmp_path, doc, name="canon_link_groups.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy paths + the two empty states
# ---------------------------------------------------------------------------

def test_valid_group_returns_flat_member_to_primary_map(tmp_path):
    path = _write(tmp_path, _doc([_group()]))
    assert clg.load_link_groups(path, ENTRIES) == {
        "משה לייב": "משה לייב",
        "משה־לייב": "משה לייב",
    }


def test_primary_maps_to_itself_so_the_projection_is_one_hop(tmp_path):
    """The renderer's own `_validate_link_groups` requires `m[primary] ==
    primary`; the loader must therefore always emit the primary as its own
    member, never as a chain that has to be walked."""
    path = _write(tmp_path, _doc([_group()]))
    result = clg.load_link_groups(path, ENTRIES)
    for primary in set(result.values()):
        assert result[primary] == primary


def test_absent_file_with_allow_absent_is_empty(tmp_path):
    assert clg.load_link_groups(tmp_path / "nope.json", ENTRIES) == {}


def test_absent_file_without_allow_absent_blocks(tmp_path):
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(tmp_path / "nope.json", ENTRIES, allow_absent=False)
    assert "not found" in str(exc.value)


def test_schema_valid_empty_groups_is_the_distinguished_empty_state(tmp_path):
    path = _write(tmp_path, _doc([]))
    assert clg.load_link_groups(path, ENTRIES) == {}


def test_raw_empty_object_is_blocking_not_empty(tmp_path):
    """`{}` is missing both required top-level fields -- a schema error, not
    a silent 'no groups'."""
    path = _write(tmp_path, {})
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "schema validation" in str(exc.value)


# ---------------------------------------------------------------------------
# Schema rejects
# ---------------------------------------------------------------------------

def test_one_member_group_is_a_hard_schema_error(tmp_path):
    path = _write(tmp_path, _doc([_group(members=["Peter"], primary="Peter")]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "schema validation" in str(exc.value)


def test_duplicate_members_rejected(tmp_path):
    path = _write(tmp_path, _doc([_group(members=["משה לייב", "משה לייב"])]))
    with pytest.raises(clg.CanonLinkGroupsLoadError):
        clg.load_link_groups(path, ENTRIES)


@pytest.mark.parametrize("note", ["", "   "])
def test_blank_note_rejected(tmp_path, note):
    """A group with no stated reason is indistinguishable from a mistake."""
    path = _write(tmp_path, _doc([_group(note=note)]))
    with pytest.raises(clg.CanonLinkGroupsLoadError):
        clg.load_link_groups(path, ENTRIES)


def test_missing_note_rejected(tmp_path):
    group = _group()
    del group["note"]
    path = _write(tmp_path, _doc([group]))
    with pytest.raises(clg.CanonLinkGroupsLoadError):
        clg.load_link_groups(path, ENTRIES)


def test_unknown_extra_field_rejected(tmp_path):
    group = _group()
    group["confidence"] = "high"
    path = _write(tmp_path, _doc([group]))
    with pytest.raises(clg.CanonLinkGroupsLoadError):
        clg.load_link_groups(path, ENTRIES)


def test_wrong_schema_version_rejected(tmp_path):
    doc = _doc([_group()])
    doc["schema_version"] = 2
    path = _write(tmp_path, doc)
    with pytest.raises(clg.CanonLinkGroupsLoadError):
        clg.load_link_groups(path, ENTRIES)


def test_non_object_document_rejected(tmp_path):
    path = tmp_path / "canon_link_groups.json"
    path.write_text(json.dumps([_group()]), encoding="utf-8")
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "did not parse to an object" in str(exc.value)


# ---------------------------------------------------------------------------
# The three procedural rejects
# ---------------------------------------------------------------------------

def test_primary_outside_its_own_members_rejected(tmp_path):
    path = _write(tmp_path, _doc([_group(primary="Peter")]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "not one of its own members" in str(exc.value)
    assert exc.value.offending == "Peter"


def test_member_not_a_canon_key_rejected(tmp_path):
    """The failure a silent no-op would hide: a typo'd member never matches
    any owner, so the group quietly does nothing and the operator believes
    their identity pass was applied."""
    path = _write(tmp_path, _doc([_group(members=["משה לייב", "Ghost"])]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "is not a canon.json entries{} key" in str(exc.value)
    assert exc.value.offending == "Ghost"


def test_source_form_in_two_groups_rejected(tmp_path):
    entries = dict(ENTRIES)
    entries["Pyotr"] = {"canonical_target_form": "Peter"}
    doc = _doc([
        _group(),
        _group(primary="Peter", members=["Peter", "משה־לייב"], note="conflicting claim"),
    ])
    path = _write(tmp_path, doc)
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, entries)
    assert "more than one group" in str(exc.value)
    assert exc.value.offending == "משה־לייב"


def test_membership_is_byte_exact_never_nfc_folded(tmp_path):
    """A member spelled in a different normalization form than its canon key
    is a MISS, and must fail loudly: `build_entity_index` looks the owner's
    own raw `source_form` up in this map, so a silently-normalized key would
    never match anything."""
    nfd_key = unicodedata.normalize("NFD", "Adèle")
    nfc_key = unicodedata.normalize("NFC", "Adèle")
    assert nfd_key != nfc_key
    entries = {nfc_key: {"canonical_target_form": "Adele"}, "Peter": {}}
    path = _write(tmp_path, _doc([
        {"primary": nfd_key, "members": [nfd_key, "Peter"], "note": "same woman"}
    ]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, entries)
    assert "is not a canon.json entries{} key" in str(exc.value)


# ---------------------------------------------------------------------------
# Path state + read layer
# ---------------------------------------------------------------------------

def test_directory_path_blocks_regardless_of_allow_absent(tmp_path):
    target = tmp_path / "canon_link_groups.json"
    target.mkdir()
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(target, ENTRIES)
    assert "not a regular file" in str(exc.value)


def test_dangling_symlink_blocks_regardless_of_allow_absent(tmp_path):
    link = tmp_path / "canon_link_groups.json"
    link.symlink_to(tmp_path / "missing-target.json")
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(link, ENTRIES)
    assert "not a regular file" in str(exc.value)


def test_invalid_utf8_raises_load_error(tmp_path):
    path = tmp_path / "canon_link_groups.json"
    path.write_bytes(b'{"schema_version": 1, "groups": [\xff]}')
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "not valid UTF-8" in str(exc.value)


def test_invalid_json_raises_load_error(tmp_path):
    path = tmp_path / "canon_link_groups.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "not valid JSON" in str(exc.value)


def test_deeply_nested_document_rejected_by_the_iterative_preflight(tmp_path):
    depth = clg.MAX_NESTING_DEPTH + 5
    doc = "[" * depth + "]" * depth
    path = tmp_path / "canon_link_groups.json"
    path.write_text(doc, encoding="utf-8")
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "nests deeper than" in str(exc.value)


def test_entries_must_be_a_mapping(tmp_path):
    """A forgotten/renamed `entries` argument must not silently turn
    membership validation off."""
    path = _write(tmp_path, _doc([_group()]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, None)
    assert "must be a mapping" in str(exc.value)


# ---------------------------------------------------------------------------
# Duplicate object keys -- the silent last-one-wins collapse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc,dupe,label", [
    ('{"schema_version": 1, "groups": [{"primary": "משה לייב", '
     '"primary": "משה־לייב", "members": ["משה לייב", "משה־לייב"], '
     '"note": "primary written twice"}]}', "primary", "primary"),
    ('{"schema_version": 1, "groups": [], "groups": [{"primary": "משה לייב", '
     '"members": ["משה לייב", "משה־לייב"], "note": "second groups wins"}]}',
     "groups", "groups"),
    ('{"schema_version": 1, "groups": [{"primary": "משה לייב", '
     '"members": ["משה לייב"], "members": ["משה לייב", "משה־לייב"], '
     '"note": "members widened by the second copy"}]}', "members", "members"),
    ('{"schema_version": 1, "schema_version": 2, "groups": []}',
     "schema_version", "schema_version"),
])
def test_duplicate_object_key_is_rejected_not_collapsed(tmp_path, doc, dupe, label):
    """`json.loads` keeps only the LAST value for a repeated key, and
    jsonschema then validates the already-collapsed dict -- so no amount of
    `additionalProperties:false`/`required[]` strictness can see the
    duplicate. Left alone, an operator reads `primary: "משה לייב"` in their
    own file while the vault links to the other member's note, with every
    gate green: exactly the silent wrong-note routing this loader exists to
    prevent. The `groups` and `members` cases are worse still -- one replaces
    the whole group list, the other can silently WIDEN a group past the
    membership the operator wrote."""
    path = tmp_path / "canon_link_groups.json"
    path.write_text(doc, encoding="utf-8")
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES)
    assert "repeats the object key" in str(exc.value), label
    assert exc.value.offending == dupe


def test_the_guard_covers_the_schema_document_too(tmp_path):
    """`_read_json` is shared, so the SCHEMA path is guarded as well -- and
    that must be proven by INJECTING a duplicate there, not by loading the
    valid shipped schema: a regression to plain `json.loads` on the schema
    read would pass a mere happy-path assertion unchanged."""
    bad_schema = tmp_path / "dupe.schema.json"
    bad_schema.write_text(
        '{"type": "object", "type": "array", "additionalProperties": false}',
        encoding="utf-8",
    )
    path = _write(tmp_path, _doc([_group()]))
    with pytest.raises(clg.CanonLinkGroupsLoadError) as exc:
        clg.load_link_groups(path, ENTRIES, schema_path=bad_schema)
    assert "repeats the object key" in str(exc.value)
    assert exc.value.offending == "type"


def test_the_shipped_schema_file_still_loads_under_the_guard(tmp_path):
    """The other direction: the real schema this plugin ships must contain
    no duplicate key, or every load would now block."""
    path = _write(tmp_path, _doc([_group()]))
    assert clg.DEFAULT_SCHEMA_PATH.is_file()
    assert clg.load_link_groups(path, ENTRIES)   # reads DEFAULT_SCHEMA_PATH


def test_a_well_formed_document_is_unaffected_by_the_guard(tmp_path):
    """The other half of "nothing well-formed is newly rejected": repeated
    VALUES across distinct keys, and the same key in SIBLING objects, are
    both legal and must still load."""
    doc = {"schema_version": 1, "groups": [
        {"primary": "משה לייב", "members": ["משה לייב", "משה־לייב"], "note": "n"},
    ]}
    entries = dict(ENTRIES)
    entries["Pyotr"] = {"canonical_target_form": "Peter"}
    doc["groups"].append({"primary": "Peter", "members": ["Peter", "Pyotr"], "note": "n"})
    path = _write(tmp_path, doc)
    assert clg.load_link_groups(path, entries) == {
        "משה לייב": "משה לייב", "משה־לייב": "משה לייב",
        "Peter": "Peter", "Pyotr": "Peter",
    }


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
