# Daily AI tools updater

Run from this directory when needed; it does not install a schedule or run automatically:

```sh
python3 update-ai-tools.py
python3 update-ai-tools.py --dry-run
python3 -B -m unittest discover -v
```

`--version` prints the updater version, and `--help` lists options. `--dry-run` reports
all six tools using read-only version/metadata commands and HTTPS requests; it does
not update packages or mutate the remote host.

Without `--dry-run`, outdated tools may run `herdr update`, `pi update`, `codex update`,
the explicit `~/.bun/bin/omp update`, or remote Homebrew updates. “Latest installable”
is the tool/package release target; a newer installed version is not downgraded.

Failures are shown per tool, the remaining tools continue, and the command exits
nonzero. This script does not restart services, perform a Herdr handoff, update the
forked `omp` binary, or run its git/build workflow.
