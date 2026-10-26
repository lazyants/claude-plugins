# Exhaustive coverage inventory classification

The coverage question is still about the whole project, including files the adapter never
declared. This procedure changes how the reviewer sees a large inventory, not its scope.
Use ordinary Python 3 and file/shell tools; a context-processing MCP is optional.

1. Read the current packet and the project's localization configuration, catalog loaders and
   scope agreement. Use these to identify possible groups and evidence to inspect. A folder
   name, suffix, or adapter declaration alone is not evidence that all strings are covered.
2. Save the Python recipe below as `classify_inventory.py` in a **new evidence directory beside
   `inventory.txt`**. Run it with the current packet and that evidence directory as arguments.
   It reads every path, checks the count/hash and uniqueness, and initially leaves every path
   unresolved. It writes the complete mapping, group index and complete candidate list; only
   the small totals enter tool output. Keep the script with its outputs for traceability.
3. Read every entry in `groups.json`. A directory is just an index, never an exclusion.
   Inspect the relevant content, loaders and explicit scope decisions. You may edit `classify`
   to apply decisions established by that review to exact paths or a demonstrably homogeneous
   group. Each decision needs a non-empty evidence reference and rationale. Default all other
   paths to `unresolved`; new/hidden catalogs and inline language maps must remain candidates.
   Do not infer "prose is irrelevant" from `.md` or exclude a whole directory because most of
   its files are prose. Group evidence must support the decision for **every** member, including
   exceptions; otherwise split the group or inspect its members.
4. Rerun the recipe after editing `classify`. Retain the original inventory unchanged, its
   SHA-256/count, the packet's run ID, and `classification.jsonl` with exactly one row per path.
   `groups.json` indexes every group/decision/evidence combination with its count.
   `candidates.jsonl` contains **all** unresolved paths, not a sample. Read it in bounded
   portions if necessary and inspect every candidate's contents. Within adapter-read files,
   compare user-visible strings with the packet's collected IDs too.
5. Record inspected decisions and their evidence in `classify`, rerun, and verify the final
   totals and group index. `reviewed` means inspected against collected IDs, with no missing
   string; `out_of_scope` means a recorded scope decision supported by the project's evidence.
   Neither label may be assigned just to make the unresolved count reach zero. A real miss
   remains in the coverage answer even if you have finished inspecting its file.
6. Return the existing `{"run_id": "...", "missing": [...]}` answer. Include every miss, and
   every unreadable or still-uncertain candidate as a whole-file gap (`key: null`, reason in
   `why_user_visible`). Do not return `missing: []` while anything remains unresolved. The
   operator still fixes/rechecks the adapter or explicitly accepts a named scope exception.
   If the inventory or accounting checks fail, stop and report the failure to the caller.

Grouping and its evidence are reviewer judgments. The code below performs exact I/O,
hashing and accounting only; it does not discover or approve semantic exclusions. It never
reads project contents or changes acceptance state. A successful run proves accounting,
not localization coverage. If ordinary Python/shell tools are unavailable, read the complete
inventory directly; if neither route can finish, report the incomplete review.

```python
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path, PurePosixPath


def classify(path):
    # Replace only with decisions supported by the review described above.
    # Grouping by parent helps navigation; it makes no scope decision.
    return str(PurePosixPath(path).parent), "unresolved", "Not yet inspected"


def main(argv):
    packet_path, evidence_dir = map(Path, argv)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    inventory = packet["inventory"]
    raw = Path(inventory["path"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != inventory["sha256"]:
        raise ValueError("Inventory hash mismatch; use the current packet")
    # The inventory producer appends LF to each path; other characters belong to the name.
    paths = raw.decode("utf-8").split("\n")[:-1]
    if len(paths) != inventory["line_count"] or len(set(paths)) != len(paths):
        raise ValueError("Inventory count mismatch or duplicate paths")
    if any(not path.strip() for path in paths):
        raise ValueError("Inventory contains a blank path")
    rows = []
    groups = Counter()
    for path in paths:
        group, decision, evidence = classify(path)
        if decision not in {"unresolved", "reviewed", "out_of_scope"}:
            raise ValueError(f"Invalid decision for {path}")
        if not isinstance(group, str) or not group.strip():
            raise ValueError(f"Missing group for {path}")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"Missing evidence for {path}")
        rows.append(dict(path=path, group=group, decision=decision, evidence=evidence))
        groups[group, decision, evidence] += 1
    candidates = [row for row in rows if row["decision"] == "unresolved"]
    index = {
        "run_id": packet["run_id"],
        "inventory": inventory,
        "classified_count": len(rows),
        "unresolved_count": len(candidates),
        "groups": [dict(group=g, decision=d, evidence=e, count=n)
                   for (g, d, e), n in sorted(groups.items())],
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for name, entries in [("classification.jsonl", rows), ("candidates.jsonl", candidates)]:
        (evidence_dir / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in entries),
            encoding="utf-8",
        )
    (evidence_dir / "groups.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: index[key] for key in
                      ("run_id", "classified_count", "unresolved_count")}))


if __name__ == "__main__":
    main(sys.argv[1:])
```

For example, from the evidence directory (replace the packet path):

```sh
python3 classify_inventory.py /absolute/path/to/R/runs/_coverage/packet.json .
```

Treat file paths as data: pass them as script arguments and use quoted shell variables or
Python file APIs for content reads. Keep the inventory, script and evidence files together
until the audit closes. Rebuild them for a new run ID; never reuse an older run's decisions
without checking their evidence against the current project.
