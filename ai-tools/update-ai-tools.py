#!/usr/bin/env python3
"""Update the local AI tools and the remote Homebrew AI formulas."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.2.0"
COMMAND_TIMEOUT = 30
UPDATE_TIMEOUT = 1800
URL_TIMEOUT = 20
BUN_OMP = Path.home() / ".bun" / "bin" / "omp"
REMOTE_BREW = "/opt/homebrew/bin/brew"
REMOTE_HOST = "ai-server"
MLX_FORMULA_URL = (
    "https://raw.githubusercontent.com/ddalcu/mlx-serve/main/Formula/mlx-serve.rb"
)
LLAMA_FORMULA_URL = "https://formulae.brew.sh/api/formula/llama.cpp.json"

LOCAL_LATEST = {
    "herdr": "https://herdr.dev/latest.json",
    "pi": "https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent/latest",
    "codex": "https://registry.npmjs.org/@openai%2Fcodex/latest",
    "claude": "https://registry.npmjs.org/@anthropic-ai%2Fclaude-code/latest",
    "omp": "https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent/latest",
}

_VERSION_RE = re.compile(
    r"^v?(?P<base>\d+(?:\.\d+)+)"
    r"(?:_(?P<revision>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
_FORMULA_VERSION_RE = re.compile(r'^\s*version\s+["\']([^"\']+)["\']\s*$')
_FORMULA_REVISION_RE = re.compile(r"^\s*revision\s+(\d+)\s*$")


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


def _urlopen(url: str) -> bytes:
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


def _latest_json_version(data: dict) -> ParsedVersion:
    return parse_version(data.get("version"))


def _brew_env_args() -> list[str]:
    return [
        "env",
        "HOMEBREW_NO_AUTO_UPDATE=1",
        "HOMEBREW_NO_ANALYTICS=1",
        "HOMEBREW_NO_INSTALL_CLEANUP=1",
        REMOTE_BREW,
    ]


def _remote_command(*brew_args: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        REMOTE_HOST,
        *_brew_env_args(),
        *brew_args,
    ]


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


def _local_row(name: str, command_argv: list[str], dry_run: bool) -> Row:
    row = Row(name)
    try:
        row.installed_version = _extract_output_version(command(command_argv))
        row.installed = row.installed_version.display
    except Exception as exc:
        row.error = str(exc) or exc.__class__.__name__
    try:
        row.latest_version = _latest_json_version(get_json(LOCAL_LATEST[name]))
        row.latest = row.latest_version.display
    except Exception as exc:
        row.error = _join_errors(row.error, str(exc) or exc.__class__.__name__)
    if row.error or row.installed_version is None or row.latest_version is None:
        return row
    row.status = _status(row.installed_version, row.latest_version)
    if row.status == "update available" and not dry_run:
        try:
            _mutating_command(command_argv[:-1] + ["update"])
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


def _remote_rows(dry_run: bool) -> tuple[Row, Row]:
    mlx = Row("mlx-serve")
    llama = Row("llama.cpp")
    try:
        formulae = _formulae_from_brew(
            command(_remote_command("info", "--formula", "--json=v2", "mlx-serve", "llama.cpp"))
        )
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        mlx.error = message
        llama.error = message
        formulae = {}

    try:
        mlx.installed_version = _installed_formula(formulae, "ddalcu/mlx-serve/mlx-serve")
        mlx.installed = mlx.installed_version.display
    except Exception as exc:
        mlx.error = _join_errors(mlx.error, str(exc) or exc.__class__.__name__)
    try:
        llama.installed_version = _installed_formula(formulae, "llama.cpp")
        llama.installed = llama.installed_version.display
    except Exception as exc:
        llama.error = _join_errors(llama.error, str(exc) or exc.__class__.__name__)

    try:
        mlx.latest_version = _latest_mlx()
        mlx.latest = mlx.latest_version.display
    except Exception as exc:
        mlx.error = _join_errors(mlx.error, str(exc) or exc.__class__.__name__)
    try:
        llama.latest_version = _latest_llama()
        llama.latest = llama.latest_version.display
    except Exception as exc:
        llama.error = _join_errors(llama.error, str(exc) or exc.__class__.__name__)

    for row in (mlx, llama):
        if row.error or row.installed_version is None or row.latest_version is None:
            continue
        row.status = _status(row.installed_version, row.latest_version)

    targets = [
        row
        for row in (mlx, llama)
        if row.status == "update available" and row.error is None
    ]
    if dry_run or not targets:
        return mlx, llama
    try:
        _mutating_command(_remote_command("update"))
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        for row in targets:
            row.status = "error"
            row.error = _join_errors(row.error, message)
        return mlx, llama

    for row in targets:
        formula = "ddalcu/mlx-serve/mlx-serve" if row is mlx else "llama.cpp"
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
    return mlx, llama


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update the local and remote AI tools without restarts."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report updates without running mutating update commands",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows = [
        _local_row("herdr", ["herdr", "--version"], args.dry_run),
        _local_row("pi", ["pi", "--version"], args.dry_run),
        _local_row("codex", ["codex", "--version"], args.dry_run),
        _local_row("claude", ["claude", "--version"], args.dry_run),
        _local_row("omp", [str(BUN_OMP), "--version"], args.dry_run),
    ]
    rows.extend(_remote_rows(args.dry_run))
    for row in rows:
        print(row.render())
    return 1 if any(row.status == "error" for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
