"""Create or resume a software-localizer durable root.

Writes `localize.json` with every answer a `CHOOSE_...` sentinel when it does
not already exist; never overwrites an existing one, so an operator's
in-progress answers are never lost by re-running this.

Also writes a `.gitignore` when the root has none. The root may sit inside the
project's own repository, so the file is an allowlist: it commits the decisions
(config, adapter, canon) and ignores everything else, which keeps every
run-state file the plugin writes, now or later, out of git by default. An
existing `.gitignore` is never overwritten.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lz_common  # noqa: E402

GITIGNORE = """\
# software-localizer workspace: commit the decisions, keep run state local.
# Add a "!/<name>" line to commit another file or directory.
/*
!/.gitignore
!/localize.json
!/adapter/
!/canon.json
!/canon.lock.json
!/canon.history.json
__pycache__/
"""


def _default_config() -> dict:
    cfg = {
        "schema": 1,
        "project_root": "CHOOSE_PROJECT_ROOT",
        "source_locale": "CHOOSE_SOURCE_LOCALE",
        "target_locales": "CHOOSE_TARGET_LOCALES",
        "adapter": {"argv": "CHOOSE_ADAPTER_ARGV", "options": {}},
        "style": {},
    }
    cfg.update({k: (list(v) if isinstance(v, list) else v) for k, v in lz_common.CONFIG_DEFAULTS.items()})
    return cfg


def main() -> int:
    parser = lz_common.make_parser(
        prog="scaffold.py", description="Create or resume a software-localizer durable root."
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root_arg_path = Path(args.root)
    if root_arg_path.exists() and not root_arg_path.is_dir():
        lz_common.fail(f"root exists and is not a directory: {args.root}", lz_common.EXIT_CANNOT)
    root = root_arg_path.resolve()
    root.mkdir(parents=True, exist_ok=True)

    config_path = root / "localize.json"
    if config_path.exists():
        outcome = "resumed"
        created = False
    else:
        lz_common.atomic_write_json(config_path, _default_config())
        outcome = "fresh"
        created = True

    gitignore_path = root / ".gitignore"
    gitignore_created = not os.path.lexists(gitignore_path)
    if gitignore_created:
        lz_common.atomic_write_text(gitignore_path, GITIGNORE)

    lz_common.emit({
        "ok": True, "outcome": outcome, "created": created, "path": str(config_path),
        "gitignore_created": gitignore_created,
    })
    return lz_common.EXIT_OK


if __name__ == "__main__":
    lz_common.run_main(main)
