# Daily AI tools updater

Run from this directory when needed; it does not install a schedule or run automatically:

```sh
python3 update-ai-tools.py
python3 update-ai-tools.py --dry-run
python3 -B -m unittest discover -v
```

`--version` prints the updater version, and `--help` lists options. `--dry-run` reports
all seven tools using read-only version/metadata commands, HTTPS requests, and a `git
fetch` of the OMP fork; it does not update packages or mutate the remote host.

Without `--dry-run`, outdated tools may run `herdr update`, `pi update`, `codex update`,
the explicit `~/.bun/bin/omp update`, remote Homebrew updates, or the OMP fork flow:
`omp-backup`, rebase `main` onto `origin/main` in `~/code/open-source/oh-my-pi`, then
`bun run check` and `bun run build` in `packages/coding-agent`. “Latest installable”
is the tool/package release target (for the fork, the `origin/main` version); a newer
installed version is not downgraded, and a fork behind on commits is an update even
when versions match.

Failures are shown per tool, the remaining tools continue, and the command exits
nonzero. A fork with uncommitted tracked changes is not rebased, and a failed rebase
is aborted so the fork stays clean for the next run. This script does not restart
services or perform a Herdr handoff.
