#!/usr/bin/env python3
"""Update the local AI tools and the remote Homebrew AI formulas."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.4.0"
COMMAND_TIMEOUT = 30
UPDATE_TIMEOUT = 1800
URL_TIMEOUT = 20
UPDATE_POLL_INTERVAL = 5
BUN_OMP = Path.home() / ".bun" / "bin" / "omp"
REMOTE_BREW = "/opt/homebrew/bin/brew"
REMOTE_HOST = "ai-server"
MLX_FORMULA_URL = (
    "https://raw.githubusercontent.com/ddalcu/mlx-serve/main/Formula/mlx-serve.rb"
)
LLAMA_FORMULA_URL = "https://formulae.brew.sh/api/formula/llama.cpp.json"
OMLX_FORMULA_URL = "https://raw.githubusercontent.com/jundot/omlx/main/Formula/omlx.rb"
HEX_RELEASE_URL = "https://anoma.ly/release/hex/desktop/stable/manifest"

HERDR_SESSION = "default"
AI_MEMORY_UNIT = "ai-memory.service"
# restart id -> systemd/ai-server service name for the remote engines
ENGINES = {"mlx-serve": "mlx-serve", "llama.cpp": "llama-server-small", "omlx": "omlx-stt"}
# omlx only serves the stt services, so it is restarted without the idle check.
ALWAYS_RESTARTABLE = ("omlx",)
REMOTE_FORMULAE = {
    "mlx-serve": "ddalcu/mlx-serve/mlx-serve",
    "llama.cpp": "llama.cpp",
    "omlx": "jundot/omlx/omlx",
}
# name -> (version argv, update argv or None for "<argv[0]> update")
LOCAL_TOOLS = {
    "herdr": (["herdr", "--version"], None),
    "hex": (["hex", "--version"], ["hex", "check-for-updates"]),
    "pi": (["pi", "--version"], None),
    "codex": (["codex", "--version"], None),
    "claude": (["claude", "--version"], None),
    "omp": ([str(BUN_OMP), "--version"], None),
    "ai-memory": (["ai-memory", "--version"], ["ai-memory", "upgrade"]),
}
TOOLS = (*LOCAL_TOOLS, *REMOTE_FORMULAE)
# Stop order matters: herdr first, and it is relaunched last.
RESTARTABLE = ("herdr", "ai-memory", *ENGINES)

# Hex installs its own update, so its update command returns before the new version is on disk.
BACKGROUND_UPDATE_TOOLS = ("hex",)

# feed URL plus the field holding the version: registries use "version",
# GitHub releases use "tag_name".
LOCAL_LATEST = {
    "herdr": ("https://herdr.dev/latest.json", "version"),
    "pi": ("https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent/latest", "version"),
    "codex": ("https://registry.npmjs.org/@openai%2Fcodex/latest", "version"),
    "claude": ("https://registry.npmjs.org/@anthropic-ai%2Fclaude-code/latest", "version"),
    "omp": ("https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent/latest", "version"),
    "ai-memory": (
        "https://api.github.com/repos/akitaonrails/ai-memory/releases/latest",
        "tag_name",
    ),
}

_VERSION_RE = re.compile(
    r"^v?(?P<base>\d+(?:\.\d+)+)"
    r"(?:_(?P<revision>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
_FORMULA_VERSION_RE = re.compile(r'^\s*version\s+["\']([^"\']+)["\']\s*$')
_FORMULA_REVISION_RE = re.compile(r"^\s*revision\s+(\d+)\s*$")
_HEX_STABLE_RE = re.compile(r"hex/desktop/stable (\d+(?:\.\d+)+) has no target")


class UpdateError(Exception):
    """An expected failure that should be reported on one tool row."""


@dataclass(frozen=True)
class ParsedVersion:
    text: str
    base: tuple[int, ...]
    revision: int = 0
    prerelease: tuple[object, ...] = ()

    def key(self) -> tuple[tuple[int, ...], tuple[object, ...], int]:
        width = max(len(self.base), 3)
        padded = self.base + (0,) * (width - len(self.base))
        # A release is newer than every prerelease of the same numeric version.
        if not self.prerelease:
            pre_key: tuple[object, ...] = (1,)
        else:
            components = tuple(
                (0, part) if isinstance(part, int) else (1, part)
                for part in self.prerelease
            )
            pre_key = (0, components)
        return padded, pre_key, self.revision

    @property
    def display(self) -> str:
        result = ".".join(str(part) for part in self.base)
        if self.revision:
            result += f"_{self.revision}"
        if self.prerelease:
            result += "-" + ".".join(str(part) for part in self.prerelease)
        return result


def parse_version(value: object, *, revision: int = 0) -> ParsedVersion:
    if not isinstance(value, str):
        raise UpdateError(f"unsupported version syntax: {value!r}")
    text = value.strip()
    match = _VERSION_RE.fullmatch(text)
    if not match:
        raise UpdateError(f"unsupported version syntax: {text!r}")
    base = tuple(int(part) for part in match.group("base").split("."))
    embedded_revision = int(match.group("revision") or 0)
    if not isinstance(revision, int) or revision < 0:
        raise UpdateError(f"unsupported revision: {revision!r}")
    pre: tuple[object, ...] = ()
    if match.group("pre"):
        parts: list[object] = []
        for part in match.group("pre").split("."):
            parts.append(int(part) if part.isdigit() else part)
        pre = tuple(parts)
    return ParsedVersion(text, base, max(embedded_revision, revision), pre)


def _command_environment(argv: list[str]) -> dict[str, str] | None:
    if not argv or Path(argv[0]).expanduser() != BUN_OMP:
        return None
    environment = os.environ.copy()
    current_path = environment.get("PATH", "")
    environment["PATH"] = str(BUN_OMP.parent) + os.pathsep + current_path
    return environment


def command(argv: list[str], cwd: Path | None = None) -> str:
    """Run a read-only command and return captured stdout."""
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            env=_command_environment(argv),
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"command failed: {exc}") from exc
    return (result.stdout or "").strip()


def _mutating_command(argv: list[str], cwd: Path | None = None) -> str:
    try:
        subprocess.run(
            argv,
            check=True,
            timeout=UPDATE_TIMEOUT,
            env=_command_environment(argv),
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"update command failed: {exc}") from exc
    return ""


def _urlopen(url: str, *, error_body: bool = False) -> bytes:
    if not url.startswith("https://"):
        raise UpdateError(f"refusing non-HTTPS URL: {url}")
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "update-ai-tools/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if error_body:
            return exc.read()
        raise UpdateError(f"network request failed: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise UpdateError(f"network request failed: {exc}") from exc


def get_json(url: str) -> dict:
    """Fetch an HTTPS JSON object."""
    try:
        payload = json.loads(_urlopen(url).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise UpdateError(f"invalid UTF-8 JSON response: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(f"invalid JSON response: {exc}") from exc
    if not isinstance(payload, dict):
        raise UpdateError("JSON response is not an object")
    return payload


def _extract_output_version(output: str) -> ParsedVersion:
    # Version commands generally include a product name before the version.
    candidates = re.findall(r"(?<![A-Za-z0-9])v?\d+(?:\.\d+)+(?:_\d+)?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", output)
    if not candidates:
        raise UpdateError(f"unsupported version output: {output!r}")
    return parse_version(candidates[-1])


def _latest_json_version(data: dict, field: str = "version") -> ParsedVersion:
    return parse_version(data.get(field))


def _brew_env_args() -> list[str]:
    return [
        "env",
        "HOMEBREW_NO_AUTO_UPDATE=1",
        "HOMEBREW_NO_ANALYTICS=1",
        "HOMEBREW_NO_INSTALL_CLEANUP=1",
        REMOTE_BREW,
    ]


def _remote_ssh(*argv: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        REMOTE_HOST,
        *argv,
    ]


def _remote_command(*brew_args: str) -> list[str]:
    return _remote_ssh(*_brew_env_args(), *brew_args)


def _formulae_from_brew(output: str) -> dict[str, dict]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise UpdateError(f"invalid Homebrew JSON: {exc}") from exc
    formulae = payload.get("formulae") if isinstance(payload, dict) else None
    if not isinstance(formulae, list):
        raise UpdateError("Homebrew JSON has no formulae list")
    result: dict[str, dict] = {}
    for formula in formulae:
        if not isinstance(formula, dict):
            continue
        full_name = formula.get("full_name")
        name = formula.get("name")
        if isinstance(full_name, str):
            result[full_name] = formula
        if isinstance(name, str):
            result[name] = formula
    return result


def _installed_formula(formulae: dict[str, dict], name: str) -> ParsedVersion:
    formula = formulae.get(name)
    if formula is None:
        raise UpdateError(f"formula not installed: {name}")
    linked = formula.get("linked_keg")
    installed = formula.get("installed")
    raw: object = linked if isinstance(linked, str) and linked else None
    revision = 0
    if raw is None and isinstance(installed, list) and installed:
        item = installed[0]
        if isinstance(item, dict):
            raw = item.get("version")
            if isinstance(item.get("revision"), int):
                revision = item["revision"]
    if raw is None:
        raise UpdateError(f"formula has no installed version: {name}")
    return parse_version(raw, revision=revision)


def _latest_llama() -> ParsedVersion:
    data = get_json(LLAMA_FORMULA_URL)
    versions = data.get("versions")
    if not isinstance(versions, dict):
        raise UpdateError("llama.cpp metadata has no versions")
    revision = data.get("revision", 0)
    if not isinstance(revision, int) or revision < 0:
        raise UpdateError(f"unsupported Homebrew revision: {revision!r}")
    return parse_version(versions.get("stable"), revision=revision)


def _latest_mlx() -> ParsedVersion:
    source = _urlopen(MLX_FORMULA_URL).decode("utf-8")
    version: str | None = None
    revision = 0
    for line in source.splitlines():
        version_match = _FORMULA_VERSION_RE.match(line)
        if version_match:
            version = version_match.group(1)
            continue
        revision_match = _FORMULA_REVISION_RE.match(line)
        if revision_match:
            revision = int(revision_match.group(1))
    if version is None:
        raise UpdateError("mlx-serve formula has no literal version")
    return parse_version(version, revision=revision)


def _status(installed: ParsedVersion, latest: ParsedVersion) -> str:
    if installed.key() < latest.key():
        return "update available"
    if installed.key() > latest.key():
        return "installed newer"
    return "up to date"


@dataclass
class Row:
    name: str
    installed: str = "-"
    latest: str = "-"
    status: str = "error"
    error: str | None = None
    installed_version: ParsedVersion | None = None
    latest_version: ParsedVersion | None = None

    def render(self) -> str:
        line = (
            f"{self.name}: installed={self.installed}; "
            f"latest installable={self.latest}; status={self.status}"
        )
        if self.error:
            line += f"; error={self.error.replace(chr(10), ' ')}"
        return line


def _local_row(
    name: str,
    command_argv: list[str],
    dry_run: bool,
    update_argv: list[str] | None = None,
) -> Row:
    row = Row(name)
    try:
        row.installed_version = _extract_output_version(command(command_argv))
        row.installed = row.installed_version.display
    except Exception as exc:
        row.error = str(exc) or exc.__class__.__name__
    try:
        row.latest_version = _latest_local(name)
        row.latest = row.latest_version.display
    except Exception as exc:
        row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
    if row.error or row.installed_version is None or row.latest_version is None:
        return row
    row.status = _status(row.installed_version, row.latest_version)
    if row.status == "update available" and not dry_run:
        try:
            _mutating_command(update_argv or command_argv[:-1] + ["update"])
            if name in BACKGROUND_UPDATE_TOOLS:
                row.installed_version = _await_version(command_argv, row.latest_version)
            else:
                row.installed_version = _extract_output_version(command(command_argv))
            row.installed = row.installed_version.display
            if row.installed_version.key() < row.latest_version.key():
                raise UpdateError("update completed but target version was not reached")
            row.status = _status(row.installed_version, row.latest_version)
        except Exception as exc:
            row.status = "error"
            row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
    return row


def _join_errors(first: str | None, second: str) -> str:
    return second if not first else f"{first}; {second}"


def _latest_omlx() -> ParsedVersion:
    # The tap formula has no literal version; it is encoded in the release tarball URL.
    source = _urlopen(OMLX_FORMULA_URL).decode("utf-8")
    match = re.search(r'^\s*url\s+"[^"]*/refs/tags/v?([^"/]+?)\.tar\.gz"', source, re.M)
    if match is None:
        raise UpdateError("omlx formula has no release tarball url")
    return parse_version(match.group(1))


def _latest_hex() -> ParsedVersion:
    # The release host has no version feed; the 404 for its unknown "manifest" target names the stable version.
    body = _urlopen(HEX_RELEASE_URL, error_body=True).decode("utf-8", "replace")
    match = _HEX_STABLE_RE.search(body)
    if match is None:
        raise UpdateError("hex release host names no stable version")
    return parse_version(match.group(1))


def _latest_local(name: str) -> ParsedVersion:
    if name == "hex":
        return _latest_hex()
    url, field = LOCAL_LATEST[name]
    return _latest_json_version(get_json(url), field)


def _await_version(argv: list[str], target: ParsedVersion) -> ParsedVersion:
    """Poll a version command until it reaches target or UPDATE_TIMEOUT passes."""
    deadline = time.monotonic() + UPDATE_TIMEOUT
    while True:
        installed = _extract_output_version(command(argv))
        if installed.key() >= target.key() or time.monotonic() >= deadline:
            return installed
        time.sleep(UPDATE_POLL_INTERVAL)


REMOTE_LATEST = {"mlx-serve": _latest_mlx, "llama.cpp": _latest_llama, "omlx": _latest_omlx}


def _remote_rows(dry_run: bool, names: tuple[str, ...] | list[str] = tuple(REMOTE_FORMULAE)) -> list[Row]:
    rows = {name: Row(name) for name in names}
    formulae: dict[str, dict] = {}
    try:
        formulae = _formulae_from_brew(
            command(
                _remote_command(
                    "info", "--formula", "--json=v2", *(REMOTE_FORMULAE[n] for n in names)
                )
            )
        )
    except Exception as exc:
        for row in rows.values():
            row.error = str(exc) or exc.__class__.__name__

    for name, row in rows.items():
        try:
            row.installed_version = _installed_formula(formulae, REMOTE_FORMULAE[name])
            row.installed = row.installed_version.display
        except Exception as exc:
            row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
        try:
            row.latest_version = REMOTE_LATEST[name]()
            row.latest = row.latest_version.display
        except Exception as exc:
            row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
        if not row.error and row.installed_version and row.latest_version:
            row.status = _status(row.installed_version, row.latest_version)

    targets = [
        row for row in rows.values() if row.status == "update available" and row.error is None
    ]
    if dry_run or not targets:
        return list(rows.values())
    try:
        _mutating_command(_remote_command("update"))
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        for row in targets:
            row.status = "error"
            row.error = _join_errors(row.error, message)
        return list(rows.values())

    for row in targets:
        formula = REMOTE_FORMULAE[row.name]
        try:
            _mutating_command(_remote_command("upgrade", "--formula", formula))
        except Exception as exc:
            row.status = "error"
            row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)

        try:
            formulae = _formulae_from_brew(
                command(_remote_command("info", "--formula", "--json=v2", formula))
            )
            row.installed_version = _installed_formula(formulae, formula)
            row.installed = row.installed_version.display
            if row.error:
                continue
            if row.installed_version.key() < row.latest_version.key():
                raise UpdateError("update completed but target version was not reached")
            row.status = _status(row.installed_version, row.latest_version)
        except Exception as exc:
            row.status = "error"
            row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
    return list(rows.values())


def _slots_restartable(service: str) -> bool:
    """ai-server slots exits 0 only when nothing is busy or unknown on that service."""
    try:
        command(_remote_ssh("ai-server", "slots", service))
    except UpdateError:
        return False
    return True


def _restart_engines(dry_run: bool, names) -> bool:
    ok = True
    for name, service in ENGINES.items():
        if name not in names:
            continue
        if name not in ALWAYS_RESTARTABLE and not _slots_restartable(service):
            print(f"{service}: restart skipped; ai-server slots is not clear")
            continue
        if dry_run:
            print(f"{service}: would restart" + ("" if name in ALWAYS_RESTARTABLE else " (idle)"))
            continue
        try:
            _mutating_command(_remote_ssh("ai-server", "restart", service))
            print(f"{service}: restarted")
        except Exception as exc:
            print(f"{service}: restart failed: {str(exc) or exc.__class__.__name__}")
            ok = False
    return ok


def _herdr_session_running() -> bool:
    for line in command(["herdr", "session", "list"]).splitlines()[1:]:
        fields = line.split()
        if len(fields) > 1 and fields[0] == HERDR_SESSION and fields[1] == "running":
            return True
    return False


def _restart_stack(dry_run: bool, names=RESTARTABLE) -> bool:
    herdr = "herdr" in names
    if herdr and (os.environ.get("HERDR_AGENT") or os.environ.get("HERDR_ENV") == "1"):
        print("restart: refused; this runs inside the herdr session it would stop")
        return False
    if dry_run:
        if herdr:
            print(f"herdr: would stop session {HERDR_SESSION}, then relaunch")
        if "ai-memory" in names:
            print(f"ai-memory: would restart {AI_MEMORY_UNIT}")
        return _restart_engines(True, names)

    ok = True
    if herdr:
        try:
            if _herdr_session_running():
                _mutating_command(["herdr", "session", "stop", HERDR_SESSION])
                print(f"herdr: stopped session {HERDR_SESSION}")
            else:
                print(f"herdr: session {HERDR_SESSION} was not running")
        except Exception as exc:
            print(f"herdr: stop failed: {str(exc) or exc.__class__.__name__}")
            ok = False

    if "ai-memory" in names:
        try:
            _mutating_command(["systemctl", "--user", "restart", AI_MEMORY_UNIT])
            state = command(["systemctl", "--user", "is-active", AI_MEMORY_UNIT])
            if state != "active":
                raise UpdateError(f"{AI_MEMORY_UNIT} is {state or 'unknown'} after restart")
            print(f"ai-memory: restarted {AI_MEMORY_UNIT}")
        except Exception as exc:
            print(f"ai-memory: restart failed: {str(exc) or exc.__class__.__name__}")
            ok = False

    ok = _restart_engines(False, names) and ok

    if not herdr:
        return ok
    if sys.stdout.isatty():
        sys.stdout.flush()
        os.execvp("herdr", ["herdr"])
    print("herdr: launch it with `herdr`")
    return ok


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Update the local and remote AI tools, optionally restarting the stack. "
            "Without an action, updates every tool. "
            f"Tools: {', '.join(TOOLS)}. Restartable: {', '.join(RESTARTABLE)}."
        )
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("list", "update", "restart"),
        help="list: report versions and restartability; update: update tools; restart: restart services",
    )
    parser.add_argument("names", nargs="*", metavar="TOOL", help="limit to these tools (default: all)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report updates without running mutating update commands",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help=(
            "after updating, restart the selected restartable tools: stop the herdr "
            "default session, restart ai-memory, restart the idle inference engines, "
            "then relaunch herdr"
        ),
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def _collect_rows(names: list[str], dry_run: bool) -> list[Row]:
    rows = [
        _local_row(name, LOCAL_TOOLS[name][0], dry_run, LOCAL_TOOLS[name][1])
        for name in LOCAL_TOOLS
        if name in names
    ]
    remote = [name for name in REMOTE_FORMULAE if name in names]
    if remote:
        rows.extend(_remote_rows(dry_run, remote))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    allowed = RESTARTABLE if args.action == "restart" else TOOLS
    unknown = [name for name in args.names if name not in allowed]
    if unknown:
        parser.error(f"unknown tool {', '.join(unknown)}; choose from {', '.join(allowed)}")
    names = args.names or list(allowed)

    if args.action == "restart":
        return 0 if _restart_stack(args.dry_run, names) else 1

    list_only = args.action == "list"
    rows = _collect_rows(names, args.dry_run or list_only)
    for row in rows:
        print(row.render() + (f"; restart={'yes' if row.name in RESTARTABLE else 'no'}" if list_only else ""))
    restart_ok = True
    if args.restart and not list_only:
        restart_ok = _restart_stack(args.dry_run, [n for n in names if n in RESTARTABLE])
    return 1 if any(row.status == "error" for row in rows) or not restart_ok else 0


if __name__ == "__main__":
    sys.exit(main())
