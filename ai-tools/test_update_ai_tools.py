import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("update-ai-tools.py")
SPEC = importlib.util.spec_from_file_location("update_ai_tools", SCRIPT)
update_ai_tools = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = update_ai_tools
SPEC.loader.exec_module(update_ai_tools)


def latest_payload(version):
    # Registries expose "version"; the GitHub release feed exposes "tag_name".
    return {"version": version, "tag_name": f"v{version}"}


LOCAL_LATEST = {
    "https://herdr.dev/latest.json": {"version": "18.10"},
    "https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent/latest": {"version": "18.10"},
    "https://registry.npmjs.org/@openai%2Fcodex/latest": {"version": "18.10"},
    "https://registry.npmjs.org/@anthropic-ai%2Fclaude-code/latest": {"version": "18.10"},
    "https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent/latest": {"version": "18.10"},
    "https://api.github.com/repos/akitaonrails/ai-memory/releases/latest": latest_payload("18.10"),
}


def brew_info(mlx_version="18.9", llama_version="18.9", mlx_revision=0, llama_revision=0):
    return {
        "formulae": [
            {
                "name": "ddalcu/mlx-serve/mlx-serve",
                "installed": [{"version": mlx_version, "revision": mlx_revision}],
            },
            {
                "name": "llama.cpp",
                "installed": [{"version": llama_version, "revision": llama_revision}],
            },
            {
                "name": "omlx",
                "full_name": "jundot/omlx/omlx",
                "installed": [{"version": "18.10", "revision": 0}],
            },
        ]
    }


def urlopen_for(mlx_body, hex_version="18.10"):
    def urlopen(request, timeout):
        if request.full_url == update_ai_tools.HEX_RELEASE_URL:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(hex_release_404(hex_version)))
        is_omlx = request.full_url == update_ai_tools.OMLX_FORMULA_URL
        return _Response(OMLX_FORMULA if is_omlx else mlx_body)

    return urlopen


BREW_INFO = brew_info()
MLX_FORMULA = b'version "18.10"\nrevision 0\n'
OMLX_FORMULA = b'  url "https://github.com/jundot/omlx/archive/refs/tags/v18.10.tar.gz"\n'
def hex_release_404(version):
    return f'{{"error":"hex/desktop/stable {version} has no target manifest; targets: linux-x64"}}\n'.encode()
RESTART_ENV = {"HERDR_AGENT": "", "HERDR_ENV": ""}


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class UpdateAiToolsBehaviorTests(unittest.TestCase):
    def command(self, argv, version="18.9"):
        if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
            return json.dumps(BREW_INFO)
        return f"{Path(argv[0]).name} {version}"

    def json_response(self, url):
        if url == update_ai_tools.LLAMA_FORMULA_URL:
            return {"versions": {"stable": "18.10"}, "revision": 0}
        return LOCAL_LATEST[url]

    def stateful_command(self, state, events):
        def command(argv):
            if "--json=v2" in argv:
                if "llama.cpp" in argv and "mlx-serve" not in argv:
                    names = ("llama",)
                elif "ddalcu/mlx-serve/mlx-serve" in argv and "llama.cpp" not in argv:
                    names = ("mlx",)
                else:
                    names = ("mlx", "llama")
                events.append(("info", *names))
                return json.dumps(
                    brew_info(
                        state["mlx"]["version"],
                        state["llama"]["version"],
                        state["mlx"]["revision"],
                        state["llama"]["revision"],
                    )
                )
            tool = Path(argv[0]).name
            return f"{tool} {state['local'][tool]}"

        return command

    def run_main(self, argv, command=None, latest=None, urlopen=None, subprocess_run=None):
        command = command or self.command
        latest = latest or self.json_response
        urlopen = urlopen or urlopen_for(MLX_FORMULA)
        stream = io.StringIO()
        with (
            patch.object(update_ai_tools, "command", side_effect=command),
            patch.object(update_ai_tools, "get_json", side_effect=latest),
            patch.object(update_ai_tools.urllib.request, "urlopen", side_effect=urlopen),
            patch.object(update_ai_tools.subprocess, "run", side_effect=subprocess_run),
            patch.object(update_ai_tools, "UPDATE_TIMEOUT", 0),
            redirect_stdout(stream),
        ):
            result = update_ai_tools.main(argv)
        return result, stream.getvalue()

    def test_dry_run_reports_updates_without_running_mutating_commands(self):
        result, output = self.run_main(["--dry-run"], subprocess_run=AssertionError("write"))

        self.assertEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=update available", output)
        self.assertIn("codex: installed=18.9; latest installable=18.10; status=update available", output)

    def test_newer_installed_version_is_not_downgraded_in_normal_mode(self):
        state = {
            "local": {name: "18.10" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.10", "revision": 0},
            "llama": {"version": "18.10", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return latest_payload("18.9")
            return {"versions": {"stable": "18.9"}, "revision": 0}

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            latest=latest,
            urlopen=urlopen_for(b'version "18.9"\nrevision 0\n'),
            subprocess_run=AssertionError("write"),
        )

        self.assertEqual(result, 0)
        self.assertIn("pi: installed=18.10; latest installable=18.9; status=installed newer", output)
        self.assertIn("llama.cpp: installed=18.10; latest installable=18.9; status=installed newer", output)

    def test_normal_update_postqueries_successful_local_and_remote_versions(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }
        events = []

        def update(argv, **kwargs):
            if argv[0] == "ssh":
                if "upgrade" in argv:
                    formula = "mlx" if "ddalcu/mlx-serve/mlx-serve" in argv else "llama"
                    events.append(("upgrade", formula))
                    state[formula]["version"] = "18.10"
                else:
                    events.append(("refresh",))
                return
            tool = Path(argv[0]).name
            expected = {"ai-memory": "upgrade", "hex": "check-for-updates"}.get(tool, "update")
            self.assertEqual(argv[-1], expected)
            events.append(("local-update", tool))
            state["local"][tool] = "18.10"

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            subprocess_run=update,
        )

        self.assertEqual(result, 0)
        for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory", "mlx-serve", "llama.cpp"):
            self.assertIn(f"{name}: installed=18.10; latest installable=18.10; status=up to date", output)
        refreshes = [index for index, event in enumerate(events) if event[0] == "refresh"]
        upgrades = [event for event in events if event[0] == "upgrade"]
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(upgrades, [("upgrade", "mlx"), ("upgrade", "llama")])
        self.assertLess(refreshes[0], events.index(("upgrade", "mlx")))

    def test_remote_failure_postqueries_failed_target_and_continues_sibling(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return latest_payload("18.9")
            return self.json_response(url)

        def update(argv, **kwargs):
            if argv[0] != "ssh":
                raise AssertionError(f"unexpected local write: {argv}")
            if "upgrade" not in argv:
                events.append(("refresh",))
                return
            formula = "mlx" if "ddalcu/mlx-serve/mlx-serve" in argv else "llama"
            events.append(("upgrade", formula))
            if formula == "mlx":
                state[formula]["version"] = "18.10"
                raise subprocess.CalledProcessError(1, argv)
            state[formula]["version"] = "18.10"

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            latest=latest,
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertIn("mlx-serve: installed=18.10; latest installable=18.10; status=error", output)
        self.assertIn("llama.cpp: installed=18.10; latest installable=18.10; status=up to date", output)
        self.assertIn("update command failed", output)
        self.assertEqual([event for event in events if event[0] in ("refresh", "upgrade")], [
            ("refresh",),
            ("upgrade", "mlx"),
            ("upgrade", "llama"),
        ])
        self.assertIn(("info", "mlx"), events[events.index(("upgrade", "mlx")) + 1:])
        self.assertIn(("info", "llama"), events[events.index(("upgrade", "llama")) + 1:])

    def test_unmet_local_target_is_nonzero_after_postquery(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }

        def latest(url):
            if url == update_ai_tools.LOCAL_LATEST["herdr"][0]:
                return {"version": "18.10"}
            if url in LOCAL_LATEST:
                return latest_payload("18.9")
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            self.assertEqual(argv, ["herdr", "update"])

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, []),
            latest=latest,
            urlopen=urlopen_for(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("update completed but target version was not reached", output)

    def test_unmet_remote_target_is_nonzero_after_postquery(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return latest_payload("18.9")
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            self.assertEqual(argv[0], "ssh")
            if "upgrade" not in argv:
                events.append(("refresh",))
                return
            events.append(("upgrade", "mlx"))

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            latest=latest,
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertIn("mlx-serve: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("update completed but target version was not reached", output)
        self.assertEqual([event for event in events if event[0] in ("refresh", "upgrade")], [
            ("refresh",),
            ("upgrade", "mlx"),
        ])

    def test_homebrew_revision_only_update_is_available(self):
        state = {
            "local": {name: "18.10" for name in ("herdr", "hex", "pi", "codex", "claude", "omp", "ai-memory")},
            "mlx": {"version": "18.10", "revision": 0},
            "llama": {"version": "18.10", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return latest_payload("18.10")
            return {"versions": {"stable": "18.10"}, "revision": 1}

        def update(argv, **kwargs):
            self.assertEqual(argv[0], "ssh")
            if "upgrade" not in argv:
                events.append(("refresh",))
                return
            formula = "mlx" if "ddalcu/mlx-serve/mlx-serve" in argv else "llama"
            events.append(("upgrade", formula))
            self.assertEqual(formula, "llama")
            state[formula]["revision"] = 1

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            latest=latest,
            subprocess_run=update,
        )

        self.assertEqual(result, 0)
        self.assertIn("llama.cpp: installed=18.10_1; latest installable=18.10_1; status=up to date", output)
        self.assertEqual([event for event in events if event[0] in ("refresh", "upgrade")], [
            ("refresh",),
            ("upgrade", "llama"),
        ])

    def test_malformed_latest_version_is_an_error(self):
        def malformed(url):
            if url == update_ai_tools.LOCAL_LATEST["herdr"][0]:
                return {"version": "not-a-version"}
            return self.json_response(url)

        result, output = self.run_main(["--dry-run"], latest=malformed)

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=-; status=error", output)
        self.assertIn("pi: installed=18.9; latest installable=18.10; status=update available", output)

    def test_latest_outage_is_visible_and_other_tools_continue(self):
        def unavailable(url):
            if url == update_ai_tools.LOCAL_LATEST["pi"][0]:
                raise update_ai_tools.UpdateError("network request failed")
            return self.json_response(url)

        result, output = self.run_main(["--dry-run"], latest=unavailable)

        self.assertNotEqual(result, 0)
        self.assertIn("pi: installed=18.9; latest installable=-; status=error", output)
        self.assertEqual(output.count("status="), 10)

    def test_claude_version_output_with_product_suffix_is_parsed(self):
        def command(argv):
            if Path(argv[0]).name == "claude":
                return "2.1.310 (Claude Code)"
            return self.command(argv)

        def latest(url):
            if url == update_ai_tools.LOCAL_LATEST["claude"][0]:
                return {"version": "2.1.310"}
            return self.json_response(url)

        result, output = self.run_main(["--dry-run"], command=command, latest=latest)

        self.assertEqual(result, 0)
        self.assertIn(
            "claude: installed=2.1.310; latest installable=2.1.310; status=up to date",
            output,
        )

    def test_update_failure_is_visible_and_nonzero(self):
        def fail_update(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv)

        result, output = self.run_main([], subprocess_run=fail_update)

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("update command failed", output)
        self.assertEqual(output.count("status="), 10)

    def restart_command(self, busy=()):
        def command(argv):
            if argv[:2] == ["herdr", "session"]:
                return "name status\ndefault running"
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return "active"
            if "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            if "slots" in argv:
                if argv[-1] in busy:
                    raise update_ai_tools.UpdateError("command failed: exit status 1")
                return f"{argv[-1]}  model  0/4 busy"
            return f"{Path(argv[0]).name} 18.9"

        return command

    def restart_latest(self):
        def latest(url):
            if url in LOCAL_LATEST:
                return latest_payload("18.9")
            return {"versions": {"stable": "18.9"}, "revision": 0}

        return latest

    def restart_urlopen(self):
        return urlopen_for(b'version "18.9"\nrevision 0\n', hex_version="18.9")

    def test_restart_refuses_when_running_inside_herdr(self):
        with patch.dict(os.environ, {"HERDR_AGENT": "omp"}):
            result, output = self.run_main(
                ["--dry-run", "--restart"],
                command=self.restart_command(),
                latest=self.restart_latest(),
                urlopen=self.restart_urlopen(),
                subprocess_run=AssertionError("write"),
            )

        self.assertNotEqual(result, 0)
        self.assertIn("restart: refused", output)
        self.assertNotIn("herdr: would stop", output)

    def test_restart_dry_run_reports_without_mutating(self):
        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["--dry-run", "--restart"],
                command=self.restart_command(),
                latest=self.restart_latest(),
                urlopen=self.restart_urlopen(),
                subprocess_run=AssertionError("write"),
            )

        self.assertEqual(result, 0)
        self.assertIn("herdr: would stop session default, then relaunch", output)
        self.assertIn("ai-memory: would restart ai-memory.service", output)
        self.assertIn("mlx-serve: would restart (idle)", output)
        self.assertIn("llama-server-small: would restart (idle)", output)

    def test_restart_stops_herdr_then_restarts_services_and_relaunches(self):
        events = []

        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["--restart"],
                command=self.restart_command(),
                latest=self.restart_latest(),
                urlopen=self.restart_urlopen(),
                subprocess_run=lambda argv, **kwargs: events.append(argv),
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            events,
            [
                ["herdr", "session", "stop", "default"],
                ["systemctl", "--user", "restart", "ai-memory.service"],
                update_ai_tools._remote_ssh("ai-server", "restart", "mlx-serve"),
                update_ai_tools._remote_ssh("ai-server", "restart", "llama-server-small"),
                update_ai_tools._remote_ssh("ai-server", "restart", "omlx-stt"),
            ],
        )
        self.assertIn("mlx-serve: restarted", output)
        self.assertIn("llama-server-small: restarted", output)
        self.assertIn("herdr: launch it with `herdr`", output)

    def test_restart_skips_a_service_whose_slots_check_fails(self):
        events = []

        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["--restart"],
                command=self.restart_command(busy=("llama-server-small",)),
                latest=self.restart_latest(),
                urlopen=self.restart_urlopen(),
                subprocess_run=lambda argv, **kwargs: events.append(argv),
            )

        self.assertEqual(result, 0)
        self.assertIn(
            "llama-server-small: restart skipped; ai-server slots is not clear", output
        )
        self.assertIn("mlx-serve: restarted", output)
        self.assertFalse(any("llama-server-small" in argv for argv in events))

    def test_restart_reports_remote_slots_failure_without_aborting(self):
        def command(argv):
            if "slots" in argv:
                raise update_ai_tools.UpdateError("command failed: exit status 255")
            return self.restart_command()(argv)

        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["--restart"],
                command=command,
                latest=self.restart_latest(),
                urlopen=self.restart_urlopen(),
                subprocess_run=lambda argv, **kwargs: None,
            )

        self.assertEqual(result, 0)
        self.assertIn("mlx-serve: restart skipped; ai-server slots is not clear", output)
        self.assertIn("llama-server-small: restart skipped; ai-server slots is not clear", output)

    def test_update_only_llama_leaves_mlx_and_locals_untouched(self):
        events = []

        def update(argv, **kwargs):
            events.append(argv)

        result, output = self.run_main(["update", "llama.cpp"], subprocess_run=update)

        self.assertEqual(output.count("status="), 1)
        self.assertTrue(all("mlx-serve" not in argv for argv in events))
        self.assertTrue(all(argv[0] == "ssh" for argv in events))
        self.assertIn("llama.cpp: installed=18.9", output)

    def test_list_reports_restartability_without_mutating(self):
        result, output = self.run_main(["list"], subprocess_run=AssertionError("write"))

        self.assertEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=update available; restart=yes", output)
        self.assertIn("codex: installed=18.9; latest installable=18.10; status=update available; restart=no", output)

    def test_restart_one_engine_touches_nothing_else(self):
        events = []

        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["restart", "mlx-serve"],
                command=self.restart_command(),
                subprocess_run=lambda argv, **kwargs: events.append(argv),
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, [update_ai_tools._remote_ssh("ai-server", "restart", "mlx-serve")])
        self.assertNotIn("herdr", output)

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(SystemExit) as caught, redirect_stderr(io.StringIO()):
            self.run_main(["update", "nope"])

        self.assertEqual(caught.exception.code, 2)

    def test_omlx_restarts_without_a_slots_check(self):
        events = []

        def command(argv):
            if "slots" in argv:
                raise update_ai_tools.UpdateError("command failed: exit status 1")
            return self.restart_command()(argv)

        with patch.dict(os.environ, RESTART_ENV):
            result, output = self.run_main(
                ["restart", "omlx"],
                command=command,
                subprocess_run=lambda argv, **kwargs: events.append(argv),
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, [update_ai_tools._remote_ssh("ai-server", "restart", "omlx-stt")])

class HexToolTests(unittest.TestCase):
    def test_latest_hex_reads_stable_version_from_release_host(self):
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(hex_release_404("18.10")))

        with patch.object(update_ai_tools.urllib.request, "urlopen", side_effect=urlopen):
            latest = update_ai_tools._latest_hex()

        self.assertEqual(latest.display, "18.10")

    def test_latest_hex_without_a_stable_version_is_an_error(self):
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"not found"}'))

        with (
            patch.object(update_ai_tools.urllib.request, "urlopen", side_effect=urlopen),
            self.assertRaises(update_ai_tools.UpdateError),
        ):
            update_ai_tools._latest_hex()

    def test_hex_update_waits_for_the_background_install(self):
        reported = iter(["hex 18.9", "hex 18.9", "hex 18.9", "hex 18.10"])
        updates = []
        with (
            patch.object(update_ai_tools, "command", side_effect=lambda argv: next(reported)),
            patch.object(update_ai_tools, "_latest_hex", return_value=update_ai_tools.parse_version("18.10")),
            patch.object(update_ai_tools.subprocess, "run", side_effect=lambda argv, **kwargs: updates.append(argv)),
            patch.object(update_ai_tools.time, "sleep"),
        ):
            row = update_ai_tools._local_row("hex", ["hex", "--version"], False, ["hex", "check-for-updates"])

        self.assertEqual(updates, [["hex", "check-for-updates"]])
        self.assertEqual(row.status, "up to date")
        self.assertEqual(row.installed, "18.10")

    def test_hex_update_that_never_lands_is_an_error(self):
        with (
            patch.object(update_ai_tools, "command", return_value="hex 18.9"),
            patch.object(update_ai_tools, "_latest_hex", return_value=update_ai_tools.parse_version("18.10")),
            patch.object(update_ai_tools.subprocess, "run"),
            patch.object(update_ai_tools, "UPDATE_TIMEOUT", 0),
        ):
            row = update_ai_tools._local_row("hex", ["hex", "--version"], False, ["hex", "check-for-updates"])

        self.assertEqual(row.status, "error")
        self.assertIn("update completed but target version was not reached", row.error)

if __name__ == "__main__":
    unittest.main()
