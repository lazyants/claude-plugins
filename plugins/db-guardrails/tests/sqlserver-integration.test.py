#!/usr/bin/env python3
"""Real SQL Server 2022 assertions, run in CI against its disposable service.

Required: SQLSERVER_CONTAINER and SQLSERVER_ADMIN_PASSWORD. The service image
must provide /opt/mssql-tools18/bin/sqlcmd. Missing setup is a failure, not a
skip. All test databases/logins are unique and are removed in finally blocks.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

ASSET = Path(__file__).resolve().parents[1] / "skills/db-guardrails/assets/privilege-separation-sqlserver.py"


def main():
    container = os.environ["SQLSERVER_CONTAINER"]
    admin_password = os.environ["SQLSERVER_ADMIN_PASSWORD"]
    suffix = uuid.uuid4().hex[:12]
    database = "guardrails_" + suffix
    app = "app_" + suffix
    migrator = app + "_migrator"
    extra_app = "rollback_" + suffix
    extra_migrator = extra_app + "_migrator"
    old_app_password = "Runtime_Original_493!"
    old_migrator_password = "Migration_Original_782!"
    assertions = 0

    def sql(batch, user="sa", password=None, db=database, expected=True):
        nonlocal assertions
        env = dict(os.environ, SQLCMDPASSWORD=password or admin_password)
        result = subprocess.run(["docker", "exec", "-i", "-e", "SQLCMDPASSWORD", container,
                                 "/opt/mssql-tools18/bin/sqlcmd", "-S", "localhost", "-U", user,
                                 "-d", db, "-C", "-b", "-x", "-r", "1", "-l", "5"],
                                input="SET NOCOUNT ON;\nGO\n" + batch, text=True, capture_output=True,
                                env=env, timeout=30)
        if (result.returncode == 0) != expected:
            # Batch text may contain secrets. Do not echo client diagnostics.
            codes = list(dict.fromkeys(re.findall(r"\bMsg (\d+)\b", result.stdout + result.stderr)))
            raise AssertionError(f"Unexpected SQL decision for user {user} "
                                 f"(exit {result.returncode}, expected success={expected}, "
                                 f"SQL Server errors={','.join(codes) or 'none'})")
        assertions += 1
        return result.stdout

    # Service startup can outlast checkout. Poll that exact container, do not
    # start/restart another process when an observation fails.
    for attempt in range(60):
        try:
            sql("SELECT 1;", db="master")
            break
        except (AssertionError, subprocess.TimeoutExpired):
            if attempt == 59:
                raise AssertionError("SQL Server service did not become ready")
            time.sleep(2)

    with tempfile.TemporaryDirectory() as temporary:
        adapter = Path(temporary) / "sqlcmd"
        adapter.write_text('#!/bin/sh\nexec docker exec -i -e SQLCMDPASSWORD "$SQLSERVER_CONTAINER" /opt/mssql-tools18/bin/sqlcmd "$@"\n')
        adapter.chmod(0o700)
        env = dict(os.environ, SQLSERVER_SQLCMD=str(adapter), SQLSERVER_SERVER="localhost",
                   SQLSERVER_DATABASE=database, SQLSERVER_APP_USER=app,
                   SQLSERVER_MIGRATOR_USER=migrator, SQLSERVER_APP_PASSWORD=old_app_password,
                   SQLSERVER_MIGRATOR_PASSWORD=old_migrator_password,
                   SQLSERVER_TRUST_SERVER_CERTIFICATE="true")

        def install(expected=True, changes=None):
            nonlocal assertions
            result = subprocess.run([sys.executable, str(ASSET)], env=dict(env, **(changes or {})),
                                    capture_output=True, text=True, timeout=150)
            if (result.returncode == 0) != expected:
                raise AssertionError(f"Unexpected installation result: exit={result.returncode}; {result.stderr}")
            if expected and "[db-guardrails] verified" not in result.stdout:
                raise AssertionError("Installer omitted verification result")
            assertions += 1

        sql(f"CREATE DATABASE [{database}];", db="master")
        try:
            sql("CREATE TABLE dbo.Existing (id int NOT NULL); INSERT dbo.Existing VALUES (1);")
            install()
            sql("SELECT * FROM dbo.Existing; INSERT dbo.Existing VALUES (2); UPDATE dbo.Existing SET id=3 WHERE id=2; DELETE dbo.Existing WHERE id=3;", app, old_app_password)
            for command in ["DROP TABLE dbo.Existing;", "TRUNCATE TABLE dbo.Existing;",
                            "CREATE TABLE dbo.Forbidden (id int);", f"DROP DATABASE [{database}];"]:
                sql(command, app, old_app_password, expected=False)
            sql("IF OBJECT_ID(N'dbo.Existing') IS NULL OR (SELECT COUNT(*) FROM dbo.Existing) <> 1 THROW 52000, 'App DDL changed existing data.', 1;")
            sql("CREATE TABLE dbo.Future (id int); INSERT dbo.Future VALUES (1); ALTER TABLE dbo.Future ADD note int NULL;", migrator, old_migrator_password)
            sql("SELECT * FROM dbo.Future; INSERT dbo.Future (id) VALUES (2);", app, old_app_password)
            sql("TRUNCATE TABLE dbo.Future; DROP TABLE dbo.Future;", migrator, old_migrator_password)
            sql("IF OBJECT_ID(N'dbo.Future') IS NOT NULL THROW 52001, 'Migrator could not drop table.', 1;")

            # A successful rerun really resets credentials, rather than only
            # accepting an existing account whose password cannot be used.
            env["SQLSERVER_APP_PASSWORD"] = "Runtime_Changed_493!"
            env["SQLSERVER_MIGRATOR_PASSWORD"] = "Migration_Changed_782!"
            install()
            sql("SELECT 1;", app, old_app_password, expected=False)
            sql("SELECT 1;", app, env["SQLSERVER_APP_PASSWORD"])
            sql("SELECT 1;", migrator, old_migrator_password, expected=False)
            sql("SELECT 1;", migrator, env["SQLSERVER_MIGRATOR_PASSWORD"])

            sql(f"ALTER ROLE db_owner ADD MEMBER [{app}];")
            install(False)
            sql(f"IF IS_ROLEMEMBER(N'db_owner',N'{app}') <> 1 THROW 52002, 'Rejected installation changed app membership.', 1; ALTER ROLE db_owner DROP MEMBER [{app}];")
            sql(f"ALTER SERVER ROLE sysadmin ADD MEMBER [{app}];", db="master")
            install(False)
            sql(f"ALTER SERVER ROLE sysadmin DROP MEMBER [{app}];", db="master")

            sql(f"CREATE SCHEMA AppOwned AUTHORIZATION [{app}];")
            install(False)
            sql("ALTER AUTHORIZATION ON SCHEMA::AppOwned TO dbo; DROP SCHEMA AppOwned;")
            sql(f"CREATE TABLE dbo.AppOwned (id int); ALTER AUTHORIZATION ON OBJECT::dbo.AppOwned TO [{app}];")
            install(False)
            sql("ALTER AUTHORIZATION ON OBJECT::dbo.AppOwned TO dbo; DROP TABLE dbo.AppOwned;")

            # Public grants are inherited despite clean explicit memberships.
            # Audit failure after password resets must roll those resets back.
            sql("GRANT ALTER ANY USER TO public;")
            install(False, {"SQLSERVER_APP_PASSWORD": "Runtime_Rollback_949!"})
            sql("SELECT 1;", app, "Runtime_Rollback_949!", expected=False)
            sql("SELECT 1;", app, env["SQLSERVER_APP_PASSWORD"])
            sql("REVOKE ALTER ANY USER FROM public;")

            # A grant on one privileged identity need not imply IMPERSONATE
            # ANY LOGIN/USER. The app must be rejected even if its restricted
            # metadata view would hide that login from sys.server_principals.
            for kind in ["LOGIN", "USER"]:
                # LOGIN is a server securable; GRANT/REVOKE server privileges
                # require master. USER is scoped to the application database.
                grant_database = "master" if kind == "LOGIN" else database
                sql(f"GRANT IMPERSONATE ON {kind}::[{migrator}] TO public;", db=grant_database)
                try:
                    sql(f"EXECUTE AS {kind} = '{migrator}'; REVERT;", app, env["SQLSERVER_APP_PASSWORD"])
                    install(False, {"SQLSERVER_APP_PASSWORD": "Runtime_Rollback_949!"})
                    sql("SELECT 1;", app, "Runtime_Rollback_949!", expected=False)
                    sql("SELECT 1;", app, env["SQLSERVER_APP_PASSWORD"])
                finally:
                    sql(f"REVOKE IMPERSONATE ON {kind}::[{migrator}] FROM public;", db=grant_database)

            # Fail after creating the first login: no login/user/role from
            # that attempt may survive the transaction's rollback.
            install(False, {"SQLSERVER_APP_USER": extra_app, "SQLSERVER_MIGRATOR_USER": extra_migrator,
                            "SQLSERVER_APP_PASSWORD": "Runtime_Strong_428!", "SQLSERVER_MIGRATOR_PASSWORD": "a"})
            sql(f"IF SUSER_ID(N'{extra_app}') IS NOT NULL OR SUSER_ID(N'{extra_migrator}') IS NOT NULL OR USER_ID(N'{extra_app}') IS NOT NULL OR USER_ID(N'{extra_app}_runtime') IS NOT NULL THROW 52003, 'Failed installation left principals behind.', 1;")
            install()
        finally:
            sql(f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{database}];", db="master")
            for login in [app, migrator, extra_app, extra_migrator]:
                sql(f"IF SUSER_ID(N'{login}') IS NOT NULL DROP LOGIN [{login}];", db="master")
    print(f"PASS: SQL Server engine ({assertions} assertions; app DDL denied, migrator DDL succeeds, rerun and rollback verified)")


if __name__ == "__main__":
    main()
