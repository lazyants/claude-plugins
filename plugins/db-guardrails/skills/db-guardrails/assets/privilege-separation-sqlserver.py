#!/usr/bin/env python3
"""Install SQL Server logins, database users and a runtime DML role.

Run with an administrative SQL login against an existing application database.
Required environment: SQLSERVER_SERVER, SQLSERVER_DATABASE, SQLSERVER_APP_USER,
SQLSERVER_APP_PASSWORD, SQLSERVER_MIGRATOR_PASSWORD, SQLSERVER_ADMIN_PASSWORD.
Optional: SQLSERVER_ADMIN_USER (sa), SQLSERVER_MIGRATOR_USER (<app>_migrator),
SQLSERVER_SCHEMA (dbo), SQLSERVER_SQLCMD (sqlcmd executable path), and
SQLSERVER_TRUST_SERVER_CERTIFICATE=true (only for a known local/test server).

The adjacent SQL template is rendered once, then sent on stdin with sqlcmd
substitution disabled. Passwords never enter argv or an intermediate file.
Existing app elevation/ownership is rejected, not silently removed. Successful
reruns reset both login passwords and verify effective direct DDL privileges.
Azure SQL contained users and Windows/Entra identities need a separate recipe.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


def required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def identifier(name: str, value: str, limit: int = 128) -> str:
    if len(value) > limit or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value):
        raise ValueError(f"{name} must be an identifier of at most {limit} characters")
    return value


def literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def install(env: dict[str, str]) -> None:
    server = required(env, "SQLSERVER_SERVER")
    database = identifier("SQLSERVER_DATABASE", required(env, "SQLSERVER_DATABASE"))
    app = identifier("SQLSERVER_APP_USER", required(env, "SQLSERVER_APP_USER"), 100)
    migrator = identifier("SQLSERVER_MIGRATOR_USER", env.get("SQLSERVER_MIGRATOR_USER", app + "_migrator"))
    schema = identifier("SQLSERVER_SCHEMA", env.get("SQLSERVER_SCHEMA", "dbo"))
    runtime_role = app + "_runtime"
    reserved = {"sa", "dbo", "guest", "public", "sys", "information_schema"}
    if app.casefold() in reserved or migrator.casefold() in reserved:
        raise ValueError("Choose dedicated, non-system app and migrator names")
    if len({app.casefold(), migrator.casefold(), runtime_role.casefold()}) != 3:
        raise ValueError("App, migrator and runtime role names must be distinct")
    if database.casefold() in {"master", "model", "msdb", "tempdb"}:
        raise ValueError("SQLSERVER_DATABASE must be an application database")
    passwords = {}
    for name in ["SQLSERVER_APP_PASSWORD", "SQLSERVER_MIGRATOR_PASSWORD"]:
        value = required(env, name)
        if len(value.encode("utf-16-le")) // 2 > 128 or any(c in value for c in "\r\n\x00"):
            raise ValueError(f"{name} must have 1-128 UTF-16 code units without line breaks or NUL")
        passwords[name] = value
    admin_password = required(env, "SQLSERVER_ADMIN_PASSWORD")
    values = {
        "APP_USER": app,
        "MIGRATOR_USER": migrator,
        "RUNTIME_ROLE": runtime_role,
        "SCHEMA": schema,
        "APP_PASSWORD": passwords["SQLSERVER_APP_PASSWORD"],
        "MIGRATOR_PASSWORD": passwords["SQLSERVER_MIGRATOR_PASSWORD"],
    }
    template = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    # A single substitution pass prevents a password containing a template
    # marker from being interpreted as another variable.
    sql = re.sub(r"__([A-Z_]+)__", lambda match: literal(values[match[1]]), template)
    command = [env.get("SQLSERVER_SQLCMD", "sqlcmd"), "-S", server,
               "-U", env.get("SQLSERVER_ADMIN_USER", "sa"), "-d", database,
               "-b", "-r", "1", "-x", "-l", "30"]
    if env.get("SQLSERVER_TRUST_SERVER_CERTIFICATE") == "true":
        command.append("-C")
    child_env = {key: value for key, value in env.items()
                 if key not in {"SQLSERVER_APP_PASSWORD", "SQLSERVER_MIGRATOR_PASSWORD", "SQLSERVER_ADMIN_PASSWORD"}}
    child_env["SQLCMDPASSWORD"] = admin_password
    result = subprocess.run(command, input=sql, text=True, encoding="utf-8",
                            capture_output=True, env=child_env, timeout=120, check=False)
    if result.returncode != 0:
        # Client diagnostics can contain portions of submitted SQL. Do not
        # echo them, since the batch contains login passwords.
        codes = re.findall(r"\bMsg (\d+)\b", result.stdout + result.stderr)
        suffix = f"; SQL Server error(s) {', '.join(dict.fromkeys(codes))}" if codes else ""
        raise RuntimeError(f"SQL Server did not confirm installation (sqlcmd exit {result.returncode}{suffix}). "
                           "Review account privileges/ownership and server diagnostics before retrying.")
    if "DB_GUARDRAILS_SQLSERVER_OK" not in result.stdout:
        raise RuntimeError("SQL Server did not return the installation verification marker")
    print(f"[db-guardrails] verified schema {schema}: {app} has DML; {migrator} is the database migrator")


def main() -> int:
    try:
        install(dict(os.environ))
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        # OSError/TimeoutExpired can contain argv, but argv never has secrets.
        print(f"[db-guardrails] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
