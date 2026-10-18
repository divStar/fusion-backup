# Example: scheduled backups with Gitea Actions

A working setup that backs up a repository's Fusion designs every three days and commits whatever changed. It runs as shown in a self-hosted Gitea; GitHub Actions only needs the workflow in `.github/workflows/` instead of `.gitea/workflows/`.

## Files

| File | Goes to | Purpose |
|---|---|---|
| [`fusion-backup.yml`](fusion-backup.yml) | `.gitea/workflows/fusion-backup.yml` | The workflow: install fusion-backup, back up, commit, push |
| [`commit-backup.sh`](commit-backup.sh) | `.gitea/scripts/commit-backup.sh` | Commits and pushes only if something changed, with the run report as the message |
| [`backup-commit-message.txt`](backup-commit-message.txt) | `.gitea/backup-commit-message.txt` | The first line of each backup commit |

The repository also needs its [`.fusion-backup.json`](../../README.md#configured-repositories) and, so git never changes STEP files, the [`.gitattributes`](../../README.md#configured-repositories) from the tip there.

## Setup

1. **Robot:** set up the Secure Service Account as described in [docs/authentication.md](../../docs/authentication.md).
2. **Secrets:** in Gitea, open **Settings → Actions → Secrets** of the repository (or of its organisation, to share them between repositories) and add `APS_CLIENT_ID`, `APS_CLIENT_SECRET`, `APS_SSA_ID`, `APS_SSA_KEY_ID` and `APS_SSA_PRIVATE_KEY` (the whole PEM).
3. **Files:** copy the three files above into the repository, commit and push.
4. **First run:** start it by hand (**Actions → Fusion backup → Run workflow**) rather than waiting for the schedule, so you can watch it.

The workflow only runs on its schedule and by hand, never on push.

## What a run looks like

Two complete logs of real runs, anonymised (names, repository and host replaced, nothing else):

* [`example-run.log`](example-run.log): **a run with changes**, the first one in a repository: 22 downloads, 19 STEP exports between 3 seconds and a minute each, and one commit with the run report as its message; about 9 minutes in all.
* [`example-run-unchanged.log`](example-run-unchanged.log): **a run where nothing changed**, right after it: no downloads, no STEP jobs, "Nothing changed, nothing to commit.", and no commit; about 35 seconds in all.

## Self-hosted runners

Two things a job container needs that a runner on gitea.com or GitHub already has:

* **Network access to the Git server** from the job's container. With `act_runner`, `container.network` in its configuration selects the Docker network jobs join; it must be one from which the Gitea host name is reachable.
* **Trust in a private CA**, if the Git server's certificate comes from one: mount the host's CA bundle into job containers (`container.options: -v /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro`, also listed in `container.valid_volumes`) and set `NODE_EXTRA_CA_CERTS` to the same file in `runner.envs`, because Node-based actions such as `actions/checkout` ignore the system bundle.

The job also needs outgoing HTTPS to GitHub (for the actions and fusion-backup itself) and to Autodesk, as listed under [Install](../../README.md#install).
