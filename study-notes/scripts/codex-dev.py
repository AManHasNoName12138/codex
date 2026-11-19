#!/usr/bin/env python3
"""Launch a separate Codex development installation; this is not an OS sandbox."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib


MARKER = "codex-dev-layout-v1\n"
DIRECTORIES = (
    "codex-home", "sqlite", "logs", "home", "home/AppData/Roaming",
    "home/AppData/Local", "xdg/config", "xdg/cache", "xdg/data", "xdg/state",
    "runtime", "tmp", "tools/cargo/bin", "tools/rustup", "target", "releases",
    "workspaces/scratch", "downloads",
)
# Deliberately exclude parent CODEX_*, API keys, Git credentials, shell startup
# hooks, and compiler/cache overrides. Keep the native MSVC developer environment.
ENV_ALLOW = set("""
PATH SystemRoot WINDIR COMSPEC PATHEXT SYSTEMDRIVE NUMBER_OF_PROCESSORS
PROCESSOR_ARCHITECTURE PROCESSOR_ARCHITEW6432 OS
TERM COLORTERM TERM_PROGRAM TERM_PROGRAM_VERSION TERMINFO TERMINFO_DIRS
LANG LC_ALL LC_CTYPE TZ NO_COLOR FORCE_COLOR WT_SESSION
HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
http_proxy https_proxy all_proxy no_proxy
SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
INCLUDE LIB LIBPATH VCINSTALLDIR VCToolsInstallDir VCToolsVersion
VSINSTALLDIR VisualStudioVersion WindowsSdkDir WindowsSDKVersion
WindowsSDKLibVersion WindowsSdkBinPath WindowsLibPath
UniversalCRTSdkDir UCRTVersion
""".split())
MODES = {"tui", "exec", "login", "logout", "resume", "fork", "doctor",
         "--version", "--help"}
FORBIDDEN_OPTIONS = (
    "--config", "--profile", "--cd", "--remote", "--remote-auth-token-env",
    "--add-dir", "--ignore-user-config", "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
)


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes development root: {path}")
    return resolved


def check_tree(path: Path, root: Path) -> None:
    """Reject redirected state and hard-linked files; not a race-proof sandbox."""
    inside(path, root)
    if not path.exists():
        return
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(parent) / name
            inside(item, root)
            info = item.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise ValueError(f"Hard-linked state file is not allowed: {item}")


def fixed_config(root: Path, windows: bool) -> dict:
    values = {
        "cli_auth_credentials_store": "file",
        "mcp_oauth_credentials_store": "file",
        "sqlite_home": str(root / "sqlite"),
        "log_dir": str(root / "logs"),
        "check_for_update_on_startup": False,
    }
    if windows:
        values["windows.sandbox"] = "unelevated"
        values["windows.allow_mxc"] = False
    return values


def toml_literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def initialize(root: Path, windows: bool) -> None:
    marker = root / ".codex-dev-root"
    if marker.exists():
        raise ValueError("Already initialized; do not reinitialize or overwrite state.")
    for name in DIRECTORIES:
        path = root / name
        inside(path, root)
        if path.exists() and any(path.iterdir()):
            raise ValueError(f"Initialization requires empty development directories: {path}")
    for name in DIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    config = "# Managed isolation settings; use a separate VM for system-level tests.\n"
    config += "\n".join(f"{key} = {toml_literal(value)}"
                        for key, value in fixed_config(root, windows).items()) + "\n"
    (root / "codex-home/config.toml").write_text(config, encoding="utf-8")
    (root / "home/.gitconfig").write_text("", encoding="utf-8")
    marker.write_text(MARKER, encoding="utf-8")


def environment(root: Path, parent: dict[str, str], windows: bool) -> dict[str, str]:
    allowed = {key.upper() for key in ENV_ALLOW}
    env = {key: value for key, value in parent.items() if key.upper() in allowed}
    # Normalize case so Windows cannot retain a second Path/PATH value.
    if windows:
        env = {key.upper(): value for key, value in env.items()}
    for key, relative in {
        "HOME": "home", "USERPROFILE": "home", "APPDATA": "home/AppData/Roaming",
        "LOCALAPPDATA": "home/AppData/Local", "CODEX_HOME": "codex-home",
        "CODEX_SQLITE_HOME": "sqlite", "XDG_CONFIG_HOME": "xdg/config",
        "XDG_CACHE_HOME": "xdg/cache", "XDG_DATA_HOME": "xdg/data",
        "XDG_STATE_HOME": "xdg/state", "XDG_RUNTIME_DIR": "runtime",
        "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
        "CARGO_HOME": "tools/cargo", "RUSTUP_HOME": "tools/rustup",
        "CARGO_TARGET_DIR": "target", "CODEX_REPO_ROOT": "workspaces/codex",
        "GIT_CONFIG_GLOBAL": "home/.gitconfig", "NPM_CONFIG_CACHE": "xdg/cache/npm",
        "UV_CACHE_DIR": "xdg/cache/uv", "PIP_CACHE_DIR": "xdg/cache/pip",
    }.items():
        env[key] = str(root / relative)
    if windows:
        drive, tail = os.path.splitdrive(str(root / "home"))
        env["HOMEDRIVE"], env["HOMEPATH"] = drive, tail
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["CARGO_NET_GIT_FETCH_WITH_CLI"] = "true"
    env["PATH"] = str(root / "tools/cargo/bin") + os.pathsep + env.get("PATH", os.defpath)
    return env


def system_config_candidates(windows: bool) -> list[Path]:
    if not windows:
        return [Path("/etc/codex") / name for name in
                ("config.toml", "requirements.toml", "managed_config.toml")]
    # CSIDL_COMMON_APPDATA resolves the same system location as ProgramData's
    # known folder; changing the ProgramData environment variable does not.
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x23, None, 0, buffer)
    if result != 0:
        raise ValueError("Cannot resolve Windows system configuration location.")
    base = Path(buffer.value) / "OpenAI/Codex"
    paths = [base / "config.toml", base / "requirements.toml"]
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\OpenAI\Codex",
                           0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _ = winreg.QueryValueEx(key, "RequirementsPath")
            raise ValueError("Managed Windows RequirementsPath exists; use an approved separate VM.")
    except FileNotFoundError:
        pass
    return paths


def check_runtime(root: Path, windows: bool) -> None:
    for path in system_config_candidates(windows):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise ValueError(f"Shared system configuration exists; use a separate VM: {path}")
    for name in ("codex-home", "sqlite", "logs", "home", "xdg", "runtime", "tmp"):
        check_tree(root / name, root)
    for name in (".env", "managed_config.toml"):
        if os.path.lexists(root / "codex-home" / name):
            raise ValueError(f"This launcher does not support codex-home/{name}.")
    config = tomllib.loads((root / "codex-home/config.toml").read_text(encoding="utf-8"))
    for key, expected in fixed_config(root, windows).items():
        actual = config
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if actual != expected:
            raise ValueError(f"Isolation setting changed: {key}")


def selected_release(root: Path, windows: bool) -> Path:
    pointer = root / "current.txt"
    inside(pointer, root)
    name = pointer.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("current.txt must contain one release directory name.")
    release = inside(root / "releases" / name, root / "releases")
    suffix = ".exe" if windows else ""
    manifest = json.loads((release / "codex-package.json").read_text(encoding="utf-8"))
    if manifest.get("variant") != "codex" or manifest.get("entrypoint") != f"bin/codex{suffix}":
        raise ValueError("Expected a complete codex package for this OS.")
    required = ["codex-package.json", f"bin/codex{suffix}",
                f"bin/codex-code-mode-host{suffix}", f"bin/codex-exec-server{suffix}",
                f"codex-path/rg{suffix}"]
    required += (["codex-resources/codex-command-runner.exe",
                  "codex-resources/codex-windows-sandbox-setup.exe"]
                 if windows else ["codex-resources/bwrap"])
    for name in required:
        path = inside(release / name, root / "releases")
        if not path.is_file():
            raise ValueError(f"Incomplete package: {path}")
    return release


def runtime_command(root: Path, windows: bool, args: list[str], env: dict[str, str]):
    project = root / "workspaces/scratch"
    if args[:1] == ["--project"]:
        if len(args) < 3:
            raise ValueError("Usage: --project /absolute/development/project MODE [arguments]")
        project = Path(args[1])
        args = args[2:]
    project = inside(project, root / "workspaces")
    if not project.is_dir():
        raise ValueError(f"Project directory is missing: {project}")
    mode = args[0] if args else "tui"
    if mode not in MODES:
        raise ValueError("Allowed modes: " + ", ".join(sorted(MODES)))
    for token in args[1:]:
        if (any(token == opt or token.startswith(opt + "=") for opt in FORBIDDEN_OPTIONS)
                or any(token.startswith(opt) for opt in ("-c", "-C", "-p"))):
            raise ValueError(f"Path/config override is not supported by this launcher: {token}")
    release = selected_release(root, windows)
    executable = release / "bin" / ("codex.exe" if windows else "codex")
    env["PATH"] = os.pathsep.join((str(release / "bin"), str(release / "codex-path"), env["PATH"]))
    command = [str(executable)]
    for key, value in fixed_config(root, windows).items():
        command.extend(["-c", f"{key}={toml_literal(value)}"])
    command.extend(args[1:] if mode == "tui" else args)
    return command, project


def run(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    if os.name != "nt":
        os.chdir(cwd)
        os.execve(command[0], command, env)
    process = subprocess.Popen(command, cwd=cwd, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The attached console delivers Ctrl+C to the child as well.
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
            return 130


def main() -> int:
    if sys.version_info < (3, 12):
        raise ValueError("Use Python 3.12 or newer.")
    if sys.platform not in ("linux", "win32"):
        raise ValueError("This guide supports Linux and native Windows.")
    root = Path(__file__).resolve().parent
    windows = os.name == "nt"
    if not windows:
        os.umask(0o077)
    args = sys.argv[1:]
    if args == ["--init"]:
        initialize(root, windows)
        print(f"Initialized development root: {root}")
        return 0
    if (root / ".codex-dev-root").read_text(encoding="utf-8") != MARKER:
        raise ValueError("Initialize this separate installation with --init first.")
    for name in DIRECTORIES:
        inside(root / name, root)
    env = environment(root, dict(os.environ), windows)
    if args == ["--paths"]:
        keys = ("CODEX_HOME", "CODEX_SQLITE_HOME", "HOME", "USERPROFILE", "CARGO_HOME",
                "RUSTUP_HOME", "CARGO_TARGET_DIR", "TMPDIR", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
        report = {key: env[key] for key in keys}
        report["root"] = str(root)
        report["note"] = "Launcher paths only; not a report of Codex's effective config or OS isolation."
        if (root / "current.txt").exists():
            report["release"] = str(selected_release(root, windows))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args[:1] == ["--exec"]:
        if len(args) < 2 or not Path(args[1]).is_absolute():
            raise ValueError("--exec requires an absolute tool path; it is for build tools, not Codex.")
        source = root / "workspaces/codex"
        git = shutil.which("git", path=env["PATH"])
        if (source / ".git").exists() and git:
            env["STABLE_GIT_COMMIT"] = subprocess.check_output(
                [git, "-C", str(source), "rev-parse", "HEAD"], env=env, text=True).strip()
        return run(args[1:], Path.cwd(), env)
    check_runtime(root, windows)
    command, project = runtime_command(root, windows, args, env)
    return run(command, project, env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"codex-dev: {error}", file=sys.stderr)
        raise SystemExit(2)
