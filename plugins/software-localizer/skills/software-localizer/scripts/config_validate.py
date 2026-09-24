"""Validate `localize.json` against schema 1.

CLI: `config_validate.py --root R`. Importable: `lz_common.validate_config`
is where the rules actually live; this script only wires it to argv and to
the plugin's CLI contract (one JSON line, exit 0/1/2).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lz_common  # noqa: E402


def main() -> int:
    parser = lz_common.make_parser(
        prog="config_validate.py", description="Validate localize.json against schema 1."
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root = lz_common.resolve_root(args.root)
    cfg = lz_common.read_json(root / "localize.json", "localize.json")
    problems = lz_common.validate_config(cfg, root)

    for p in problems:
        print(f"{p['field']}: {p['message']}", file=sys.stderr)

    ok = not problems
    lz_common.emit({"ok": ok, "problems": problems})
    return lz_common.EXIT_OK if ok else lz_common.EXIT_FAIL


if __name__ == "__main__":
    lz_common.run_main(main)
