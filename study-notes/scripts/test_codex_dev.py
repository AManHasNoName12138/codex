"""Isolation launcher checks using disposable directories and fake executables."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("codex-dev.py")
SPEC = importlib.util.spec_from_file_location("codex_dev", SCRIPT)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-dev-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "dev with spaces"
        self.root.mkdir()
        self.official = self.base / "official"
        self.official.mkdir()
        self.original = {"auth.json": b"official credential sentinel",
                         "state.sqlite": b"official database sentinel",
                         "config.toml": b"official configuration sentinel"}
        for name, content in self.original.items():
            (self.official / name).write_bytes(content)
        # The real machine may have managed /etc/codex files. Redirect only the
        # disposable script's system-config probe into our fixture directory;
        # never edit host policy or add a production bypass flag.
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count('Path("/etc/codex")'), 1)
        source = source.replace('Path("/etc/codex")',
                                f"Path({str(self.base / 'system-config')!r})")
        (self.root / SCRIPT.name).write_text(source, encoding="utf-8")
        launcher.initialize(self.root, False)

    def make_release(self, windows=False):
        release = self.root / "releases/build-001"
        (release / "bin").mkdir(parents=True)
        (release / "codex-path").mkdir()
        (release / "codex-resources").mkdir()
        suffix = ".exe" if windows else ""
        files = [f"bin/codex{suffix}", f"bin/codex-code-mode-host{suffix}",
                 f"bin/codex-exec-server{suffix}", f"codex-path/rg{suffix}"]
        files += (["codex-resources/codex-command-runner.exe",
                   "codex-resources/codex-windows-sandbox-setup.exe"]
                  if windows else ["codex-resources/bwrap"])
        for name in files:
            (release / name).write_text("fake binary", encoding="utf-8")
        (release / "codex-package.json").write_text(json.dumps({
            "variant": "codex", "entrypoint": f"bin/codex{suffix}"}), encoding="utf-8")
        (self.root / "current.txt").write_text("build-001\n", encoding="utf-8")
        return release

    def test_parent_credentials_and_overrides_are_not_inherited(self):
        parent = {"PATH": os.defpath, "CODEX_HOME": str(self.official),
                  "CODEX_SQLITE_HOME": str(self.official), "OPENAI_API_KEY": "sentinel",
                  "AWS_ACCESS_KEY_ID": "sentinel", "CODEX_REMOTE_URL": "sentinel",
                  "RUSTY_V8_ARCHIVE": "sentinel", "GIT_CONFIG_COUNT": "2",
                  "BASH_ENV": "sentinel", "LD_PRELOAD": "sentinel",
                  "https_proxy": "http://example.test:8080"}
        before = dict(parent)
        env = launcher.environment(self.root, parent, False)
        self.assertEqual(parent, before)
        self.assertEqual(env["CODEX_HOME"], str(self.root / "codex-home"))
        self.assertEqual(env["CODEX_SQLITE_HOME"], str(self.root / "sqlite"))
        self.assertEqual(env["https_proxy"], parent["https_proxy"])
        for key in ("OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "CODEX_REMOTE_URL",
                    "RUSTY_V8_ARCHIVE", "GIT_CONFIG_COUNT", "BASH_ENV", "LD_PRELOAD"):
            self.assertNotIn(key, env)

    def test_windows_environment_keeps_msvc_and_moves_user_paths(self):
        env = launcher.environment(self.root, {"Path": "compiler-path", "SystemRoot": "C:/Windows",
                                  "INCLUDE": "msvc-include", "LIB": "msvc-lib",
                                  "LOCALAPPDATA": str(self.official), "CODEX_HOME": str(self.official)}, True)
        self.assertEqual(env["SYSTEMROOT"], "C:/Windows")
        self.assertEqual(env["INCLUDE"], "msvc-include")
        self.assertEqual(env["LIB"], "msvc-lib")
        self.assertNotIn("Path", env)
        self.assertEqual(env["LOCALAPPDATA"], str(self.root / "home/AppData/Local"))
        self.assertEqual(launcher.fixed_config(self.root, True)["windows.sandbox"], "unelevated")

    def test_init_does_not_overwrite_existing_state(self):
        config = self.root / "codex-home/config.toml"
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            launcher.initialize(self.root, False)
        self.assertEqual(before, config.read_bytes())

    def test_path_and_release_pointer_cannot_escape(self):
        self.make_release()
        with self.assertRaises(ValueError):
            launcher.runtime_command(self.root, False,
                                     ["--project", str(self.official), "tui"], {})
        (self.root / "current.txt").write_text("../official", encoding="utf-8")
        with self.assertRaises(ValueError):
            launcher.selected_release(self.root, False)

    def test_missing_helper_rejected(self):
        release = self.make_release()
        (release / "bin/codex-exec-server").unlink()
        with self.assertRaises(ValueError):
            launcher.selected_release(self.root, False)

    def test_link_to_official_credentials_rejected(self):
        try:
            (self.root / "codex-home/auth.json").symlink_to(self.official / "auth.json")
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(ValueError):
            launcher.check_tree(self.root / "codex-home", self.root)

    def test_hard_link_to_official_database_rejected(self):
        try:
            os.link(self.official / "state.sqlite", self.root / "sqlite/state.sqlite")
        except OSError as error:
            self.skipTest(str(error))
        with self.assertRaises(ValueError):
            launcher.check_tree(self.root / "sqlite", self.root)

    def test_configuration_and_dotenv_fail_closed(self):
        with patch.object(launcher, "system_config_candidates", return_value=[]):
            launcher.check_runtime(self.root, False)
            dotenv = self.root / "codex-home/.env"
            dotenv.write_text("HOME=official", encoding="utf-8")
            with self.assertRaises(ValueError):
                launcher.check_runtime(self.root, False)
            dotenv.unlink()
            config = self.root / "codex-home/config.toml"
            config.write_text('sqlite_home = "official"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                launcher.check_runtime(self.root, False)

    def test_shared_system_configuration_rejected(self):
        with patch.object(launcher, "system_config_candidates",
                          return_value=[self.official / "config.toml"]):
            with self.assertRaises(ValueError):
                launcher.check_runtime(self.root, False)

    def test_service_update_remote_and_config_arguments_rejected(self):
        for args in (["update"], ["app"], ["app-server", "daemon", "start"],
                     ["remote-control", "start"], ["-c", "sqlite_home=official", "tui"],
                     ["tui", "--remote=unix://official"], ["exec", "--config=sqlite_home=official"],
                     ["tui", "-Cofficial"], ["tui", "--profile", "official"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                launcher.runtime_command(self.root, False, args, {})

    @unittest.skipIf(os.name == "nt", "POSIX fake executable integration")
    def test_real_child_is_isolated_and_parent_and_official_files_unchanged(self):
        release = self.make_release()
        executable = release / "bin/codex"
        executable.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
from pathlib import Path
Path(os.environ['CODEX_HOME'], 'auth.json').write_text('development-only')
Path(os.environ['CODEX_SQLITE_HOME'], 'state.sqlite').write_text('development-only')
print(json.dumps({'argv':sys.argv[1:], 'cwd':os.getcwd(), 'env':dict(os.environ)}))
sys.exit(17)
""", encoding="utf-8")
        executable.chmod(0o700)
        # Use the shell entrypoint too, including argument forwarding with spaces/quotes.
        wrapper = self.root / "codex-dev"
        shutil.copyfile(SCRIPT.with_name("codex-dev.sh"), wrapper)
        parent = {**os.environ, "CODEX_HOME": str(self.official),
                  "CODEX_SQLITE_HOME": str(self.official), "OPENAI_API_KEY": "sentinel"}
        before = dict(os.environ)
        result = subprocess.run(["bash", str(wrapper), "exec", 'quoted "prompt" with spaces'],
                                env=parent, cwd=self.official, capture_output=True, text=True)
        self.assertEqual(result.returncode, 17, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["cwd"], str(self.root / "workspaces/scratch"))
        self.assertEqual(report["argv"][-2:], ["exec", 'quoted "prompt" with spaces'])
        self.assertNotIn("OPENAI_API_KEY", report["env"])
        self.assertEqual(report["env"]["CODEX_HOME"], str(self.root / "codex-home"))
        self.assertEqual(dict(os.environ), before)
        for name, content in self.original.items():
            self.assertEqual((self.official / name).read_bytes(), content)
        self.assertEqual((self.root / "codex-home/auth.json").read_text(), "development-only")


if __name__ == "__main__":
    unittest.main()
