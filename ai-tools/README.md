# Daily AI tools updater

Run from this directory when needed; it does not install a schedule or run automatically:

```sh
python3 update-ai-tools.py
python3 update-ai-tools.py --dry-run
python3 update-ai-tools.py --restart
python3 update-ai-tools.py list                      # versions + which tools are restartable
python3 update-ai-tools.py update pi codex           # only these tools
python3 update-ai-tools.py restart ai-memory mlx-serve  # only these (herdr, ai-memory, mlx-serve, llama.cpp, omlx)
python3 -B -m unittest discover -v
```

`--version` prints the updater version, and `--help` lists options. `--dry-run` reports
all ten tools using read-only version/metadata commands and HTTPS requests; it does not
update packages, mutate the remote host, or restart anything.

Without `--dry-run`, outdated tools may run `herdr update`, `pi update`, `codex update`,
`claude update`, `hex check-for-updates`, the explicit `~/.bun/bin/omp update`, `ai-memory upgrade` (the upstream
GitHub-release path: checksum-verified binary replace plus hook refresh), or remote
Homebrew updates. “Latest installable” is the tool/package release target; a newer
installed version is not downgraded.

Failures are shown per tool, the remaining tools continue, and the command exits
nonzero. The custom OMP build is not handled here: update it via `~/.bun/bin/omp update`.

Hex installs its own update in the background, so after `hex check-for-updates` the script
polls `hex --version` for up to 30 minutes and reports an error if the target is never reached.

## Restart mode

`--restart` runs after the update pass, in this order:

1. `herdr session stop default`. This ends that session's panes, agents included; the
   script never restarts codex, omp or pi itself.
2. `systemctl --user restart ai-memory.service`, then verifies the unit is `active`.
3. `ai-server restart mlx-serve`, `llama-server-small` and `omlx-stt` on `ai-server`
   (which waits for each port), each only when `ai-server slots <service>` exits 0, except
   `omlx-stt`, which only serves the stt services and restarts without that check. That
   exit code covers a busy slot, a model still loading, an unreachable server, and
   `mlx-serve` answering no metrics; a nonzero one skips that engine and the run
   continues.
4. `herdr`, replacing the script process, when stdout is a terminal; otherwise it prints
   the command to run.

It refuses to run at all when started inside the herdr session it would stop
(`HERDR_AGENT`/`HERDR_ENV` set).
