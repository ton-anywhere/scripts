import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("update-ai-tools.py")
SPEC = importlib.util.spec_from_file_location("update_ai_tools", SCRIPT)
update_ai_tools = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = update_ai_tools
SPEC.loader.exec_module(update_ai_tools)


LOCAL_LATEST = {
    "https://herdr.dev/latest.json": {"version": "18.10"},
    "https://registry.npmjs.org/@earendil-works%2Fpi-coding-agent/latest": {"version": "18.10"},
    "https://registry.npmjs.org/@openai%2Fcodex/latest": {"version": "18.10"},
    "https://registry.npmjs.org/@oh-my-pi%2Fpi-coding-agent/latest": {"version": "18.10"},
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
        ]
    }


BREW_INFO = brew_info()
MLX_FORMULA = b'version "18.10"\nrevision 0\n'


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
        if argv[0] == "git":
            return self.git_command(argv, version)
        return f"{Path(argv[0]).name} {version}"

    def git_command(self, argv, version="18.9"):
        if "rev-list" in argv:
            return "0"
        if "show" in argv:
            return json.dumps({"version": version})
        return ""

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
            if argv[0] == "git":
                return self.git_command(argv)
            tool = Path(argv[0]).name
            return f"{tool} {state['local'][tool]}"

        return command

    def run_main(self, argv, command=None, latest=None, urlopen=None, subprocess_run=None):
        command = command or self.command
        latest = latest or self.json_response
        urlopen = urlopen or (lambda request, timeout: _Response(MLX_FORMULA))
        stream = io.StringIO()
        with (
            patch.object(update_ai_tools, "command", side_effect=command),
            patch.object(update_ai_tools, "get_json", side_effect=latest),
            patch.object(update_ai_tools.urllib.request, "urlopen", side_effect=urlopen),
            patch.object(update_ai_tools.subprocess, "run", side_effect=subprocess_run),
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
            "local": {name: "18.10" for name in ("herdr", "pi", "codex", "omp")},
            "mlx": {"version": "18.10", "revision": 0},
            "llama": {"version": "18.10", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=AssertionError("write"),
        )

        self.assertEqual(result, 0)
        self.assertIn("pi: installed=18.10; latest installable=18.9; status=installed newer", output)
        self.assertIn("llama.cpp: installed=18.10; latest installable=18.9; status=installed newer", output)

    def test_normal_update_postqueries_successful_local_and_remote_versions(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "pi", "codex", "omp")},
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
            self.assertEqual(argv[-1], "update")
            events.append(("local-update", tool))
            state["local"][tool] = "18.10"

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, events),
            subprocess_run=update,
        )

        self.assertEqual(result, 0)
        for name in ("herdr", "pi", "codex", "omp", "mlx-serve", "llama.cpp"):
            self.assertIn(f"{name}: installed=18.10; latest installable=18.10; status=up to date", output)
        refreshes = [index for index, event in enumerate(events) if event[0] == "refresh"]
        upgrades = [event for event in events if event[0] == "upgrade"]
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(upgrades, [("upgrade", "mlx"), ("upgrade", "llama")])
        self.assertLess(refreshes[0], events.index(("upgrade", "mlx")))

    def test_remote_failure_postqueries_failed_target_and_continues_sibling(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "pi", "codex", "omp")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
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
            "local": {name: "18.9" for name in ("herdr", "pi", "codex", "omp")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }

        def latest(url):
            if url == update_ai_tools.LOCAL_LATEST["herdr"]:
                return {"version": "18.10"}
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            self.assertEqual(argv, ["herdr", "update"])

        result, output = self.run_main(
            [],
            command=self.stateful_command(state, []),
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("update completed but target version was not reached", output)

    def test_unmet_remote_target_is_nonzero_after_postquery(self):
        state = {
            "local": {name: "18.9" for name in ("herdr", "pi", "codex", "omp")},
            "mlx": {"version": "18.9", "revision": 0},
            "llama": {"version": "18.9", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
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
            "local": {name: "18.10" for name in ("herdr", "pi", "codex", "omp")},
            "mlx": {"version": "18.10", "revision": 0},
            "llama": {"version": "18.10", "revision": 0},
        }
        events = []

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.10"}
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
            if url == update_ai_tools.LOCAL_LATEST["herdr"]:
                return {"version": "not-a-version"}
            return self.json_response(url)

        result, output = self.run_main(["--dry-run"], latest=malformed)

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=-; status=error", output)
        self.assertIn("pi: installed=18.9; latest installable=18.10; status=update available", output)

    def test_latest_outage_is_visible_and_other_tools_continue(self):
        def unavailable(url):
            if url == update_ai_tools.LOCAL_LATEST["pi"]:
                raise update_ai_tools.UpdateError("network request failed")
            return self.json_response(url)

        result, output = self.run_main(["--dry-run"], latest=unavailable)

        self.assertNotEqual(result, 0)
        self.assertIn("pi: installed=18.9; latest installable=-; status=error", output)
        self.assertEqual(output.count("status="), 7)

    def test_update_failure_is_visible_and_nonzero(self):
        def fail_update(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv)

        result, output = self.run_main([], subprocess_run=fail_update)

        self.assertNotEqual(result, 0)
        self.assertIn("herdr: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("update command failed", output)
        self.assertEqual(output.count("status="), 7)


    def test_fork_dry_run_reports_upstream_commits_without_writes(self):
        def command(argv, version="18.9"):
            if argv[0] == "git":
                if "rev-list" in argv:
                    return "2"
                if "show" in argv:
                    return json.dumps({"version": "18.10"})
                return ""
            if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            return f"{Path(argv[0]).name} {version}"

        result, output = self.run_main(["--dry-run"], command=command, subprocess_run=AssertionError("write"))

        self.assertEqual(result, 0)
        self.assertIn("omp-fork: installed=18.9; latest installable=18.10; status=update available", output)
        self.assertEqual(output.count("status="), 7)

    def test_fork_update_rebases_rebuilds_and_verifies(self):
        state = {"behind": 2, "version": "18.9"}
        events = []

        def command(argv, version="18.9"):
            if argv[0] == "git":
                if "rev-list" in argv:
                    return str(state["behind"])
                if "show" in argv:
                    return json.dumps({"version": "18.10"})
                return ""
            if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            return f"{Path(argv[0]).name} {state['version']}"

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            if argv[0] == "git" and "rebase" in argv:
                state["behind"] = 0
                state["version"] = "18.10"
            events.append(argv[-1] if argv[0] == "git" else Path(argv[-1]).name)

        result, output = self.run_main(
            [],
            command=command,
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["omp-backup", "main", "origin/main", "check", "build"])
        self.assertIn("omp-fork: installed=18.10; latest installable=18.10; status=up to date", output)

    def test_fork_uncommitted_tracked_changes_block_rebase(self):
        events = []

        def command(argv, version="18.9"):
            if argv[0] == "git":
                if "rev-list" in argv:
                    return "2"
                if "show" in argv:
                    return json.dumps({"version": "18.10"})
                if "status" in argv:
                    return " M packages/coding-agent/src/cli.ts"
                return ""
            if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            return f"{Path(argv[0]).name} {version}"

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            events.append(argv[-1] if argv[0] == "git" else Path(argv[-1]).name)

        result, output = self.run_main(
            [],
            command=command,
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertEqual(events, ["omp-backup", "main"])
        self.assertIn("omp-fork: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("uncommitted fork changes", output)

    def test_fork_still_behind_after_rebuild_is_an_error(self):
        events = []

        def command(argv, version="18.9"):
            if argv[0] == "git":
                if "rev-list" in argv:
                    return "2"
                if "show" in argv:
                    return json.dumps({"version": "18.10"})
                return ""
            if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            return f"{Path(argv[0]).name} 18.9"

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            events.append(argv[-1] if argv[0] == "git" else Path(argv[-1]).name)

        result, output = self.run_main(
            [],
            command=command,
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertEqual(events, ["omp-backup", "main", "origin/main", "check", "build"])
        self.assertIn("omp-fork: installed=18.9; latest installable=18.10; status=error", output)
        self.assertIn("main is still behind origin/main", output)

    def test_fork_rebase_failure_aborts_and_skips_rebuild(self):
        events = []

        def command(argv, version="18.9"):
            if argv[0] == "git":
                if "rev-list" in argv:
                    return "2"
                if "show" in argv:
                    return json.dumps({"version": "18.10"})
                return ""
            if argv[:2] == ["ssh", "-o"] or "--json=v2" in argv:
                return json.dumps(BREW_INFO)
            return f"{Path(argv[0]).name} 18.9"

        def latest(url):
            if url in LOCAL_LATEST:
                return {"version": "18.9"}
            return {"versions": {"stable": "18.9"}, "revision": 0}

        def update(argv, **kwargs):
            events.append(argv[-1] if argv[0] == "git" else Path(argv[-1]).name)
            if argv[0] == "git" and "rebase" in argv and "--abort" not in argv:
                raise subprocess.CalledProcessError(1, argv)

        result, output = self.run_main(
            [],
            command=command,
            latest=latest,
            urlopen=lambda request, timeout: _Response(b'version "18.9"\nrevision 0\n'),
            subprocess_run=update,
        )

        self.assertNotEqual(result, 0)
        self.assertEqual(events, ["omp-backup", "main", "origin/main", "--abort"])
        self.assertIn("omp-fork: installed=18.9; latest installable=18.10; status=error", output)


if __name__ == "__main__":
    unittest.main()
