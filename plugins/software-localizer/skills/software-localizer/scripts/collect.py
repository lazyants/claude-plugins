"""Collect the project's current strings into `R/messages.json`. Refuses
unless the adapter is accepted (`lz_common.require_accepted_adapter`); the
actual adapter invocation and message-format validation are
`adapter_client.collect`'s job."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapter_client  # noqa: E402
import lz_common  # noqa: E402


def main() -> int:
    parser = lz_common.make_parser("collect.py", "Collect the project's current strings into R/messages.json.")
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root = lz_common.resolve_root(args.root)
    cfg = lz_common.load_config(root)
    lz_common.require_accepted_adapter(root, cfg)

    messages = adapter_client.collect(str(root), cfg, cfg["project_root"], str(root / "messages.json"))

    lz_common.emit({"ok": True, "count": len(messages["messages"]), "files": messages["files"]})
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
