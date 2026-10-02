#!/usr/bin/env python3
"""Host-side SQL Server installer boundary tests; engine assertions are separate."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ASSET = Path(__file__).resolve().parents[1] / "skills/db-guardrails/assets/privilege-separation-sqlserver.py"
SPEC = importlib.util.spec_from_file_location("sqlserver_installer", ASSET)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "PATH": "/usr/bin:/bin", "SQLSERVER_SERVER": "localhost,1433",
            "SQLSERVER_DATABASE": "guardrails_test", "SQLSERVER_APP_USER": "runtime_app",
            "SQLSERVER_APP_PASSWORD": "App_Password_234!",
            "SQLSERVER_MIGRATOR_PASSWORD": "Migration_Password_567!",
            "SQLSERVER_ADMIN_PASSWORD": "Admin_Password_890!",
        }

    def run_install(self, env=None):
        with patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "DB_GUARDRAILS_SQLSERVER_OK\n", "")) as call:
            installer.install(env or self.env)
        return call.call_args

    def test_secrets_use_stdin_and_admin_environment(self):
        args, kwargs = self.run_install()
        argv = args[0]
        self.assertIn("-x", argv)
        self.assertIn("-b", argv)
        self.assertNotIn("-C", argv)
        self.assertEqual(kwargs["env"]["SQLCMDPASSWORD"], self.env["SQLSERVER_ADMIN_PASSWORD"])
        for key in ["SQLSERVER_APP_PASSWORD", "SQLSERVER_MIGRATOR_PASSWORD", "SQLSERVER_ADMIN_PASSWORD"]:
            self.assertNotIn(self.env[key], " ".join(argv))
            self.assertNotIn(key, kwargs["env"])
        self.assertIn(self.env["SQLSERVER_APP_PASSWORD"], kwargs["input"])
        self.assertNotIn(self.env["SQLSERVER_ADMIN_PASSWORD"], kwargs["input"])
        self.assertNotIn("__APP_USER__", kwargs["input"])

    def test_template_substitution_is_single_pass_and_escapes_literals(self):
        self.env["SQLSERVER_APP_PASSWORD"] = "Quoted_'_$(var)__MIGRATOR_USER__!42"
        args, kwargs = self.run_install()
        self.assertIn("N'Quoted_''_$(var)__MIGRATOR_USER__!42'", kwargs["input"])

    def test_explicit_local_certificate_override_and_client_path(self):
        self.env["SQLSERVER_SQLCMD"] = "/opt/test/sqlcmd"
        self.env["SQLSERVER_TRUST_SERVER_CERTIFICATE"] = "true"
        args, _ = self.run_install()
        self.assertEqual(args[0][0], "/opt/test/sqlcmd")
        self.assertIn("-C", args[0])
        self.env["SQLSERVER_TRUST_SERVER_CERTIFICATE"] = "1"
        args, _ = self.run_install()
        self.assertNotIn("-C", args[0])

    def test_every_required_value_fails_before_client_execution(self):
        for key in self.env.keys() - {"PATH"}:
            with self.subTest(key=key), patch.object(installer.subprocess, "run") as call:
                env = dict(self.env)
                del env[key]
                with self.assertRaises(ValueError):
                    installer.install(env)
                call.assert_not_called()

    def test_identifiers_and_identity_collisions_fail_before_client_execution(self):
        for key, value in [("SQLSERVER_DATABASE", "master"), ("SQLSERVER_DATABASE", "bad];DROP DATABASE app;--"),
                           ("SQLSERVER_APP_USER", "dbo"), ("SQLSERVER_MIGRATOR_USER", "RUNTIME_APP"),
                           ("SQLSERVER_MIGRATOR_USER", "runtime_app_runtime"), ("SQLSERVER_SCHEMA", "x.y"),
                           ("SQLSERVER_APP_USER", "a" * 101)]:
            with self.subTest(key=key, value=value), patch.object(installer.subprocess, "run") as call:
                env = dict(self.env, **{key: value})
                with self.assertRaises(ValueError):
                    installer.install(env)
                call.assert_not_called()

    def test_control_characters_in_password_are_rejected(self):
        for value in ["Password\nGO\nDROP DATABASE x", "Password\rGO", "Password\x00", "p" * 129, "Aa1!" + "😀" * 70]:
            with self.subTest(value=repr(value)), patch.object(installer.subprocess, "run") as call:
                with self.assertRaises(ValueError):
                    installer.install(dict(self.env, SQLSERVER_APP_PASSWORD=value))
                call.assert_not_called()

    def test_password_uses_utf16_limit_including_non_bmp_characters(self):
        self.env["SQLSERVER_APP_PASSWORD"] = "Aa1!" + "😀" * 62  # exactly 128 SQL UTF-16 code units
        _, kwargs = self.run_install()
        self.assertIn(self.env["SQLSERVER_APP_PASSWORD"], kwargs["input"])

    def test_server_failure_is_not_success_and_diagnostics_hide_secrets(self):
        with patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 1,
                          "Msg 51003, Level 16 " + self.env["SQLSERVER_APP_PASSWORD"], self.env["SQLSERVER_ADMIN_PASSWORD"])):
            with self.assertRaises(RuntimeError) as error:
                installer.install(self.env)
        self.assertIn("51003", str(error.exception))
        self.assertNotIn(self.env["SQLSERVER_APP_PASSWORD"], str(error.exception))
        self.assertNotIn(self.env["SQLSERVER_ADMIN_PASSWORD"], str(error.exception))

    def test_success_without_server_verification_marker_is_failure(self):
        with patch.object(installer.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaises(RuntimeError):
                installer.install(self.env)


if __name__ == "__main__":
    unittest.main()
