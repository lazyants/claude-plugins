#!/usr/bin/env python3
"""Credential assets: fake clients locally, opt-in live engines in CI.

python3 tests/credential-assets.test.py
DB_GUARDRAILS_LIVE_MYSQL=1 DB_GUARDRAILS_MYSQL_PORT=3306 \
  MYSQL_ROOT_PASSWORD=fixture-root python3 tests/credential-assets.test.py
DB_GUARDRAILS_LIVE_POSTGRES=1 PGHOST=127.0.0.1 PGPORT=5432 \
  PGUSER=postgres PGPASSWORD=fixture-root python3 tests/credential-assets.test.py

Live engine accounts/databases are isolated by a random identifier and removed
afterward. MySQL needs a mysql/mariadb client; PostgreSQL needs psql 15+.
"""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import uuid


ASSETS = Path(__file__).resolve().parents[1] / "skills/db-guardrails/assets"
MYSQL = ASSETS / "privilege-separation-mysql.sh"
POSTGRES = ASSETS / "privilege-separation-postgres.sql"
LARAVEL = ASSETS / "laravel-run-as-migrator.sh"
SAFE_GRANTS = (
    "GRANT USAGE ON *.* TO `app`@`%`\n"
    "GRANT SELECT, INSERT, UPDATE, DELETE, EXECUTE, CREATE, ALTER, INDEX, "
    "REFERENCES, LOCK TABLES, CREATE TEMPORARY TABLES ON `appdb`.* TO `app`@`%`\n"
)
FAKE_CLIENT = r'''#!/usr/bin/env python3
import json, os, pathlib, stat, sys
args = sys.argv[1:]
sql = sys.stdin.read()
entry = {"argv": args, "sql": sql}
for arg in args:
    if arg.startswith("--defaults-extra-file="):
        path = pathlib.Path(arg.split("=", 1)[1])
        entry.update(defaults=str(path), content=path.read_text(),
                     mode=stat.S_IMODE(path.stat().st_mode))
entry.update(DB_USERNAME=os.environ.get("DB_USERNAME"),
             DB_PASSWORD=os.environ.get("DB_PASSWORD"))
with open(os.environ["GUARD_CAPTURE"], "a") as capture:
    capture.write(json.dumps(entry) + "\n")
if "SHOW GLOBAL VARIABLES" in sql:
    print(os.environ.get("GUARD_MANDATORY_ROLES", ""))
elif "SHOW GRANTS" in sql:
    print(os.environ.get("GUARD_APP_GRANTS", ""))
elif os.environ.get("GUARD_APPLY_FAIL"):
    sys.exit(1)
'''

FAKE_PSQL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
source = pathlib.Path(args[args.index("-f") + 1]).read_text()
entry = {"argv": args, "secret": os.environ.get("MIGRATOR_PASSWORD"),
         "reads_env": "\\getenv migrator_pw MIGRATOR_PASSWORD" in source}
pathlib.Path(os.environ["GUARD_CAPTURE"]).write_text(json.dumps(entry))
time.sleep(2)
'''


def sanitized_env():
    result = os.environ.copy()
    for key in list(result):
        if key.startswith(("MIGRATOR_", "MYSQL_", "DB_GUARD", "GUARD_")):
            result.pop(key)
    result.pop("ALLOW_DESTRUCTIVE", None)
    return result


def run(args, *, env, cwd=None, sql=None):
    return subprocess.run(args, env=env, cwd=cwd, input=sql, text=True,
                          capture_output=True, timeout=30)


def quoted_sql_value(sql, prefix):
    """Decode the single literal under NO_BACKSLASH_ESCAPES, with its end."""
    start = sql.index(prefix) + len(prefix)
    if sql[start] != "'":
        raise AssertionError("expected a single-quoted value")
    i, value = start + 1, []
    while i < len(sql):
        if sql[i] == "'":
            if sql[i:i + 2] == "''":
                value.append("'")
                i += 2
                continue
            return "".join(value), i + 1
        value.append(sql[i])
        i += 1
    raise AssertionError("unterminated literal")


class CredentialAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        self.capture = self.root / "capture.jsonl"
        for name in ("mysql", "mariadb", "docker"):
            fixture = self.bin / name
            fixture.write_text(FAKE_CLIENT)
            fixture.chmod(0o700)
        self.env = sanitized_env()
        self.env.update(PATH=str(self.bin) + os.pathsep + self.env["PATH"],
                        GUARD_CAPTURE=str(self.capture),
                        GUARD_APP_GRANTS=SAFE_GRANTS,
                        MYSQL_DATABASE="appdb", MYSQL_USER="app",
                        MYSQL_ROOT_PASSWORD="root-password",
                        MIGRATOR_PASSWORD="migrator-password")

    def entries(self):
        if not self.capture.exists():
            return []
        return [json.loads(line) for line in self.capture.read_text().splitlines()]

    def mysql(self, **values):
        env = self.env | values
        return run(["sh", str(MYSQL)], env=env)

    def laravel(self, dotenv=None, **values):
        project = self.root / "project"
        (project / "bin").mkdir(parents=True, exist_ok=True)
        wrapper = project / "bin/artisan-as-migrator.sh"
        shutil.copy2(LARAVEL, wrapper)
        if dotenv is not None:
            (project / ".env").write_text(dotenv)
        env = self.env.copy()
        env.pop("MIGRATOR_PASSWORD", None)
        env.pop("MIGRATOR_USER", None)
        env.update(values)
        return run(["bash", str(wrapper), "migrate", "--force"], env=env)

    def test_mysql_success_requires_both_grant_checks(self):
        result = self.mysql()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("applied:", result.stdout)
        calls = self.entries()
        self.assertEqual(len(calls), 3)
        self.assertIn("SHOW GLOBAL VARIABLES LIKE 'mandatory_roles'", calls[1]["sql"])
        self.assertIn("SHOW GRANTS FOR 'app'@'%'", calls[2]["sql"])

    def test_mysql_root_secret_is_quoted_private_and_removed(self):
        secret = ' leading # quote" backslash\\ trailing '
        result = self.mysql(MYSQL_ROOT_PASSWORD=secret)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = secret.replace("\\", "\\\\").replace('"', '\\"')
        for entry in self.entries():
            self.assertEqual(entry["content"], '[client]\npassword="' + expected + '"\n')
            self.assertEqual(entry["mode"], 0o600)
            self.assertNotIn(secret, " ".join(entry["argv"]))
            self.assertFalse(Path(entry["defaults"]).exists())

    def test_mysql_quote_backslash_password_roundtrip(self):
        secrets = ["x\\'; DROP USER 'victim'@'%'; -- ", "one\\two'quote", "\\\\'", "spaces # ; \"$"]
        for secret in secrets:
            with self.subTest(secret=secret):
                result = self.mysql(MIGRATOR_PASSWORD=secret)
                self.assertEqual(result.returncode, 0, result.stderr)
                sql = self.entries()[-3]["sql"]
                mode = "SET SESSION sql_mode = CONCAT_WS(',', NULLIF(@@SESSION.sql_mode, ''), 'NO_BACKSLASH_ESCAPES');"
                self.assertTrue(sql.startswith(mode))
                self.assertLess(sql.index(mode), sql.index("IDENTIFIED BY"))
                # Both CREATE and ALTER carry exactly one correctly quoted
                # password; injection-looking text must remain in the literal.
                for suffix in sql.split("IDENTIFIED BY ")[1:]:
                    actual, end = quoted_sql_value(suffix, "")
                    self.assertEqual(actual, secret)
                    self.assertTrue(suffix[end:].startswith(";\n"))
                self.assertNotIn(secret, result.stdout + result.stderr)
                for call in self.entries():
                    self.assertNotIn(secret, " ".join(call["argv"]))

    def test_mysql_rejects_multiline_credentials_before_client(self):
        for key in ("MYSQL_ROOT_PASSWORD", "MIGRATOR_PASSWORD"):
            for secret in ("one\ntwo", "one\rtwo"):
                with self.subTest(key=key, secret=secret):
                    result = self.mysql(**{key: secret})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(key + " must not contain CR or LF", result.stderr)
                    self.assertNotIn(secret, result.stderr)
        self.assertEqual(self.entries(), [])

    def test_mysql_identical_app_and_migrator_names_never_call_client(self):
        for host in ("%", "localhost"):
            with self.subTest(host=host):
                result = self.mysql(MIGRATOR_USER="app", DB_APP_HOST=host)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be distinct usernames", result.stderr)
                self.assertNotIn("applied:", result.stdout)
        self.assertEqual(self.entries(), [], "identity collision must not reset passwords or apply grants")

    def test_mysql_root_cannot_be_app_or_migrator_and_never_calls_client(self):
        for key in ("MYSQL_USER", "MIGRATOR_USER"):
            for account in ("root", "ROOT"):
                with self.subTest(key=key, account=account):
                    result = self.mysql(**{key: account})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("dedicated non-root accounts", result.stderr)
                    self.assertNotIn("applied:", result.stdout)
        self.assertEqual(self.entries(), [], "administrative accounts must not be altered")

    def test_mysql_rejects_dangerous_or_unverifiable_effective_grants(self):
        extras = [
            "GRANT ALL PRIVILEGES ON *.* TO `app`@`%`",
            "GRANT SELECT, DROP ON *.* TO `app`@`%`",
            "GRANT SELECT ON *.* TO `app`@`%`",
            "GRANT DROP ON `other`.* TO `app`@`%`",
            "GRANT ALL PRIVILEGES ON `appdb`.* TO `app`@`%`",
            "GRANT SELECT ON `appdb`.* TO `app`@`%` WITH GRANT OPTION",
            "GRANT `dangerous_role`@`%` TO `app`@`%`",
            "GRANT PROXY ON 'root'@'localhost' TO 'app'@'%'",
            "GRANT SYSTEM_USER ON *.* TO `app`@`%`",
            "REVOKE DROP ON `other`.* FROM `app`@`%`",
            "unrecognized server output",
        ]
        for extra in extras:
            with self.subTest(grant=extra):
                result = self.mysql(GUARD_APP_GRANTS=SAFE_GRANTS + extra + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("verification failed", result.stderr)
                self.assertNotIn("applied:", result.stdout)
                self.assertNotIn(extra, result.stderr)

    def test_mysql_empty_grants_fail_closed(self):
        for grants in ("", "GRANT USAGE ON *.* TO `app`@`%`\n"):
            with self.subTest(grants=grants):
                result = self.mysql(GUARD_APP_GRANTS=grants)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("applied:", result.stdout)

    def test_mysql_mandatory_roles_fail_closed(self):
        result = self.mysql(GUARD_MANDATORY_ROLES="mandatory_roles\t`dangerous_role`@`%`")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mandatory roles", result.stderr)
        self.assertNotIn("applied:", result.stdout)
        self.assertEqual(len(self.entries()), 2)

    def test_mysql_empty_mandatory_roles_are_safe(self):
        result = self.mysql(GUARD_MANDATORY_ROLES="mandatory_roles\t")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mysql_failed_apply_does_not_claim_success(self):
        result = self.mysql(GUARD_APPLY_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("applied:", result.stdout)
        self.assertEqual(len(self.entries()), 1)

    def test_laravel_missing_dotenv_keys_report_required_variable(self):
        cases = [("DB_DATABASE=app\n", "MIGRATOR_USER"),
                 ("MIGRATOR_USER=migrator\n", "MIGRATOR_PASSWORD"),
                 ("MIGRATOR_PASSWORD=secret\n", "MIGRATOR_USER"),
                 (None, "MIGRATOR_USER")]
        for dotenv, variable in cases:
            with self.subTest(dotenv=dotenv):
                if (self.root / "project/.env").exists():
                    (self.root / "project/.env").unlink()
                result = self.laravel(dotenv)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("set " + variable + " in your shell env or in .env", result.stderr)
        self.assertEqual(self.entries(), [])

    def test_laravel_dotenv_values_reach_docker_environment_only(self):
        result = self.laravel("MIGRATOR_USER='my_migrator'\r\nMIGRATOR_PASSWORD=\"secret#value\"\r\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries()[0]
        self.assertEqual(entry["DB_USERNAME"], "my_migrator")
        self.assertEqual(entry["DB_PASSWORD"], "secret#value")
        self.assertNotIn("secret#value", " ".join(entry["argv"]))
        self.assertEqual(entry["argv"][-4:], ["php", "artisan", "migrate", "--force"])

    def test_laravel_shell_credentials_override_dotenv(self):
        result = self.laravel("MIGRATOR_USER=unused\n", MIGRATOR_USER="shell_user",
                              MIGRATOR_PASSWORD="shell-secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.entries()[0]["DB_PASSWORD"], "shell-secret")

    def test_postgres_secret_input_and_echo_controls(self):
        sql = POSTGRES.read_text()
        self.assertNotRegex(sql, r"-v\s+migrator_pw=")
        self.assertIn("\\getenv migrator_pw MIGRATOR_PASSWORD", sql)
        self.assertLess(sql.index("\\set ECHO none"), sql.index("\\getenv"))
        self.assertLess(sql.index("\\set migrator_pw ''"), sql.index("\\getenv migrator_pw"))
        self.assertIn("MIGRATOR_PASSWORD must be set to a nonempty value", sql)
        self.assertIn("\\unset migrator_pw", sql)

    def test_postgres_invocation_process_has_no_secret_on_argv(self):
        client = self.bin / "psql"
        client.write_text(FAKE_PSQL)
        client.chmod(0o700)
        secret = "process-probe\\' secret"
        env = self.env | {"MIGRATOR_PASSWORD": secret}
        process = subprocess.Popen([str(client), "-X", "-d", "appdb", "-v", "app_user=app",
                                    "-v", "migrator_user=migrator", "-f", str(POSTGRES)],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            observed = run(["ps", "-p", str(process.pid), "-o", "args="], env=self.env)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            self.assertNotIn(secret, observed.stdout)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn(secret, stdout + stderr)
            record = json.loads(self.capture.read_text())
            self.assertEqual(record["secret"], secret)
            self.assertTrue(record["reads_env"])
            self.assertNotIn(secret, " ".join(record["argv"]))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


@unittest.skipUnless(os.environ.get("DB_GUARDRAILS_LIVE_MYSQL") == "1", "live MySQL/MariaDB is CI-only")
class MySQLLiveTest(unittest.TestCase):
    def setUp(self):
        self.client = shutil.which("mariadb") or shutil.which("mysql")
        self.assertIsNotNone(self.client, "live client must be installed")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env["MYSQL_PWD"] = self.env["MYSQL_ROOT_PASSWORD"]
        self.args = [self.client, "--protocol=TCP", "--host=127.0.0.1",
                     "--port=" + os.environ.get("DB_GUARDRAILS_MYSQL_PORT", "3306"),
                     "-uroot", "--batch", "--skip-column-names", "--raw"]
        self.identifier = "guard" + uuid.uuid4().hex[:12]
        self.app = self.identifier + "app"
        self.migrator = self.identifier + "m"
        self.addCleanup(self.cleanup_database)
        initial_mode = self.admin("SELECT @@GLOBAL.sql_mode;").stdout.strip()
        self.addCleanup(lambda: self.admin("SET GLOBAL sql_mode = '" + initial_mode + "';", check=False))
        self.admin(f"CREATE DATABASE `{self.identifier}`; CREATE USER '{self.app}'@'%' IDENTIFIED BY 'app-password'; "
                   f"GRANT ALL PRIVILEGES ON `{self.identifier}`.* TO '{self.app}'@'%';")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Route the unmodified asset to the service TCP endpoint, while its
        # root secret still comes exclusively from its private option file.
        wrapper = self.bin / "mariadb"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(self.client) +
                           ' "$@" --protocol=TCP --host=127.0.0.1 --port=' +
                           shlex.quote(os.environ.get("DB_GUARDRAILS_MYSQL_PORT", "3306")) + "\n")
        wrapper.chmod(0o700)

    def admin(self, sql, check=True):
        result = run(self.args, env=self.env, sql=sql)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def cleanup_database(self):
        self.admin(f"DROP DATABASE IF EXISTS `{self.identifier}`; DROP USER IF EXISTS '{self.app}'@'%'; "
                   f"DROP USER IF EXISTS '{self.migrator}'@'%';", check=False)

    def provision(self, password):
        env = sanitized_env() | {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
              "MYSQL_DATABASE": self.identifier, "MYSQL_USER": self.app,
              "MYSQL_ROOT_PASSWORD": os.environ["MYSQL_ROOT_PASSWORD"],
              "MIGRATOR_USER": self.migrator, "MIGRATOR_PASSWORD": password}
        return run(["sh", str(MYSQL)], env=env)

    def as_user(self, user, password, sql):
        args = [a for a in self.args if a != "-uroot"] + ["-u" + user, self.identifier]
        return run(args, env=self.env | {"MYSQL_PWD": password}, sql=sql)

    def test_password_roundtrip_and_app_destructive_denial(self):
        for initial_mode in ("", "NO_BACKSLASH_ESCAPES"):
            for password in ("x\\'; DROP USER 'victim'@'%'; -- ", "two\\slashes'quote"):
                with self.subTest(mode=initial_mode, password=password):
                    self.admin("SET GLOBAL sql_mode = '" + initial_mode + "';")
                    result = self.provision(password)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(password, result.stdout + result.stderr)
                    self.assertEqual(self.as_user(self.migrator, password, "SELECT 1").returncode, 0)
        self.admin(f"CREATE TABLE `{self.identifier}`.keep_data (id INT); INSERT INTO `{self.identifier}`.keep_data VALUES (1);")
        for sql in ("DROP TABLE keep_data", "TRUNCATE TABLE keep_data", "DROP DATABASE `" + self.identifier + "`"):
            result = self.as_user(self.app, "app-password", sql)
            self.assertNotEqual(result.returncode, 0, sql)
        self.assertEqual(self.as_user(self.app, "app-password", "SELECT count(*) FROM keep_data").stdout.strip(), "1")

    def test_preexisting_global_drop_refuses_success(self):
        self.admin(f"GRANT DROP ON *.* TO '{self.app}'@'%';")
        result = self.provision("migrator-password")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verification failed", result.stderr)
        self.assertNotIn("applied:", result.stdout)


@unittest.skipUnless(os.environ.get("DB_GUARDRAILS_LIVE_POSTGRES") == "1", "live PostgreSQL is CI-only")
class PostgresLiveTest(unittest.TestCase):
    def setUp(self):
        self.client = shutil.which("psql")
        self.assertIsNotNone(self.client, "psql 15+ must be installed")
        self.env = os.environ.copy()
        self.identifier = "guard" + uuid.uuid4().hex[:12]
        self.app = self.identifier + "app"
        self.migrator = self.identifier + "m"
        self.addCleanup(self.cleanup_database)
        self.admin(f"CREATE ROLE {self.app} LOGIN PASSWORD 'app-password'; CREATE DATABASE {self.identifier};")
        self.admin(f"CREATE TABLE keep_data (id INT); INSERT INTO keep_data VALUES (1); ALTER TABLE keep_data OWNER TO {self.app};", database=self.identifier)

    def admin(self, sql, database="postgres", check=True):
        result = run([self.client, "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", database], env=self.env, sql=sql)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def cleanup_database(self):
        self.admin(f"DROP DATABASE IF EXISTS {self.identifier}; DROP ROLE IF EXISTS {self.app}; DROP ROLE IF EXISTS {self.migrator};", check=False)

    def provision(self, password, *extra):
        env = self.env.copy()
        env.pop("MIGRATOR_PASSWORD", None)
        if password is not None:
            env["MIGRATOR_PASSWORD"] = password
        args = [self.client, "-X", *extra, "-d", self.identifier,
                "-v", "app_user=" + self.app, "-v", "migrator_user=" + self.migrator,
                "-f", str(POSTGRES)]
        if password:
            self.assertNotIn(password, " ".join(args))
        return run(args, env=env)

    def as_user(self, user, password, sql):
        return run([self.client, "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", self.identifier],
                   env=self.env | {"PGUSER": user, "PGPASSWORD": password}, sql=sql)

    def test_missing_and_empty_secret_fail_before_provisioning(self):
        for password in (None, ""):
            result = self.provision(password, "-v", "migrator_pw=old-value")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MIGRATOR_PASSWORD must be set", result.stderr)
            self.assertEqual(self.admin(f"SELECT count(*) FROM pg_roles WHERE rolname='{self.migrator}'").stdout.strip(), "0")

    def test_env_secret_roundtrip_echo_suppression_and_schema_protection(self):
        password = "pg\\'\" secret; $ literal"
        for echo_option in ("-a", "-e", "-b", "-E"):
            with self.subTest(echo=echo_option):
                result = self.provision(password, echo_option)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(password, result.stdout + result.stderr)
        self.assertEqual(self.as_user(self.migrator, password, "SELECT 1").returncode, 0)
        self.assertEqual(self.as_user(self.app, "app-password", "SELECT count(*) FROM keep_data").stdout.strip(), "1")
        for sql in ("DROP TABLE keep_data", "TRUNCATE TABLE keep_data", "CREATE TABLE new_table (id INT)"):
            result = self.as_user(self.app, "app-password", sql)
            self.assertNotEqual(result.returncode, 0, sql)
        owners = self.admin("SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='keep_data'", database=self.identifier)
        self.assertEqual(owners.stdout.strip(), self.migrator)

    def test_secret_absent_from_running_psql_process_args(self):
        password = "pg-live-process-secret"
        args = [self.client, "-X", "-d", self.identifier, "-c", "SELECT pg_sleep(2);",
                "-v", "app_user=" + self.app, "-v", "migrator_user=" + self.migrator,
                "-f", str(POSTGRES)]
        process = subprocess.Popen(args, env=self.env | {"MIGRATOR_PASSWORD": password},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            observed = run(["ps", "-p", str(process.pid), "-o", "args="], env=self.env)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            self.assertNotIn(password, observed.stdout)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertNotIn(password, stdout + stderr)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
