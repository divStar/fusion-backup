# fusion-backup

[![CI](https://github.com/divStar/fusion-backup/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/divStar/fusion-backup/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Coverage ≥ 90 %](https://img.shields.io/badge/coverage-%E2%89%A590%25%20enforced-brightgreen.svg)](docs/development.md#tests)

Back up your Autodesk Fusion designs from the Autodesk cloud, without Fusion running. For every design it keeps the native `.f3d` and a STEP export next to it (can be bypassed), so your files stay usable even if you lose access to Fusion.

It runs locally or unattended in CI (GitHub Actions, Gitea Actions, GitLab CI, ...). It logs in as a robot account with static credentials, so there is no browser login and nothing to renew. It only writes files; committing them is up to you or your CI job.

## How it works

Each run lists the files of a Fusion project and, for every item it should back up:

1. **Downloads the `.f3d`** when the cloud has a version the target doesn't hold yet, and writes it when its bytes differ. Downloads are free.
2. **Generates a STEP in the cloud** only when the `.f3d` it would belong to differs from the one the existing STEP was made from. If generation fails, the new `.f3d` is still written and the STEP stays pending; later runs retry it.
3. **Records its state** in `.fusion-backup-state.json` in the target (item IDs, versions, hashes, STEP jobs per month). Keep it next to the files, e.g. commit it with them.

Files you uploaded to Fusion yourself (STEP, DWG, PDF, ...) are downloaded as they are. The rules behind these steps are explained, with diagrams, in [docs/architecture.md](docs/architecture.md).

> [!CAUTION]
> STEP exports are made by the APS Manufacturing Data Model API, which Autodesk runs in two versions:
> * **v2 is used by default**, because its exports are not metered at the time of writing. Autodesk has announced that v2 will be retired, without a date yet.
> * **v3 is implemented as well** (`--mfg-api v3` or `FUSION_BACKUP_MFG_API=v3`), but its exports are metered and may cost money once your plan's allowance is used up. Check Autodesk's current pricing before switching.
>
> Both give the same STEP: Autodesk keeps one export per design version, and v2 and v3 return the same file for it. The tool counts the export jobs it starts and prints the monthly total after each run, because Autodesk's own usage numbers [can't be read by the robot](docs/authentication.md#limitations). Set `--step-limit` to stop at your own monthly limit, or `--no-step` to skip STEPs entirely.

## Install

```bash
pipx install "git+https://github.com/divStar/fusion-backup"
```

Installing needs no credentials. Running does: every command except `--help` logs in as the robot described below, with credentials taken from environment variables that must be set before the command runs.

Developed and tested with Python 3.14 on macOS and Linux; Windows is untested (Fusion allows names such as `M3*4*5 screw` that Windows file systems don't).

The machine or CI runner needs outgoing HTTPS access to Autodesk: `developer.api.autodesk.com` and the hosts of the pre-signed download URLs it hands out (Amazon S3 and Autodesk's CDN). Air-gapped runners can't work.

## Set up the robot

fusion-backup authenticates as a **Secure Service Account (SSA)**: a robot user that belongs to an APS app and proves its identity with a private key. Setting it up takes a few minutes in the browser: an APS app of type Server-to-Server, a robot with a key, an invitation of the robot into your Fusion hub with at least Reader access to the project, and five environment variables with its credentials.

**[docs/authentication.md](docs/authentication.md)** has the step-by-step setup, the variables, the reasons behind this login and how to keep the key safe.

## Run

With the environment variables set, call the `fusion-backup` command:

* **Installed with pipx:** `fusion-backup` is on your `PATH`, e.g. `fusion-backup check`.
* **Without installing:** `pipx run --spec "git+https://github.com/divStar/fusion-backup" fusion-backup check` fetches it into a temporary environment for this one call.
* **From a clone of this repository:** after `python3.14 -m venv .venv && .venv/bin/pip install -e .`, run `.venv/bin/fusion-backup check` (or `.venv/bin/python -m fusion_backup check`).

A typical session, from checking the setup to a real run:

```bash
fusion-backup check --repo path/to/repo          # can the robot see the repository's project?
fusion-backup status --repo path/to/repo         # what is new in the cloud, which STEPs are pending
fusion-backup run --repo path/to/repo --dry-run  # what would be downloaded and exported, without writing
fusion-backup run --repo path/to/repo            # the backup itself
```

To handle only some items, repeat `--item` once per name (a single `--item` takes one name or pattern, not a list, because Fusion names may contain commas):

```bash
fusion-backup run --repo path/to/repo --item "Chassis bracket" --item "Front panel"
```

## Quick start: mirror a whole project

```bash
fusion-backup list                                                  # hubs and projects
fusion-backup list --hub MyHub --project MyProject                  # items with versions
fusion-backup run --out backup/ --hub MyHub --project MyProject     # everything, keeping Fusion's folders
```

Designs become `<name>.f3d` and `<name>.step`, uploaded files keep their names, and Fusion folders become directories.

To fetch just one design (its `.f3d` and STEP) or a few files, add `--item` with the name as shown by `list`; it can be repeated and accepts `*` and `?` as wildcards:

```bash
fusion-backup run --out backup/ --hub MyHub --project MyProject --item "Chassis bracket"
fusion-backup run --repo path/to/repo --item "Parts/*"
```

## Configured repositories

To decide where each item goes, put a `.fusion-backup.json` into the target directory (typically a git repository):

```json
{
  "hub": "MyHub",
  "project": "MyProject",
  "items": {
    "Chassis bracket": { "f3d": "cad/Chassis bracket.f3d", "step": "cad/Chassis bracket.step" },
    "Parts/Screw M3x6": { "f3d": "cad/parts/Screw M3x6.f3d" },
    "Display drawing.pdf": { "file": "drawings/Display drawing.pdf" }
  },
  "ignore": ["Old experiment", "Imported *"],
  "mirror": { "directory": "fusion", "step": true }
}
```

* **`items`** maps item names, as shown in Fusion, to paths in the repository. Items in Fusion folders are prefixed with the folder path (`Parts/Screw M3x6`); uploaded files keep their extension in the name.
  * `f3d` and the optional `step` map a design.
  * `file` maps anything else.
* **`ignore`** lists items that should not be backed up, by name or with `*` and `?` as wildcards. An item listed in `items` is always backed up, even if an `ignore` entry matches it, so `"ignore": ["*"]` backs up exactly the listed items and stays silent about everything else in the project.
* **`mirror`** (optional) sends every other item into `directory`, keeping Fusion's folders. Without it, unlisted items are only reported as unmapped.

Paths must stay inside the repository and may not point into `.git` or at the tool's own two files; two items (or a design's `.f3d` and `.step`) writing the same file, also differing only in upper/lower case, are rejected.

Several repositories can share one project: pass `--repo` once per repository. An item mapped into two repositories is downloaded and exported once; it is reported as unmapped only if no repository maps, mirrors or ignores it.

> [!TIP]
> STEP files usually have Windows line endings (CRLF), and git converts them to LF on machines with `core.autocrlf` set. The backed-up files then no longer match their recorded hashes, and the next run rewrites all of them. A `.gitattributes` in the repository keeps them byte for byte:
>
> ```gitattributes
> *.step -text
> *.STEP -text
> *.stp -text
> *.STP -text
> ```

## Commands

| Command | What it does |
|---|---|
| `check [--repo DIR]` | Connection test: logs in as the robot, lists the visible projects and verifies each repository's hub/project |
| `list [--hub H --project P]` | Lists hubs and projects, or the items of one project with their versions |
| `status --repo DIR` | Shows the cloud versions next to what each target holds, including pending STEPs |
| `run --repo DIR` | Backs up all mapped items |
| `run --out DIR --hub H --project P` | Backs up a whole project into a directory, without a configuration file |

A run first downloads everything that changed, then requests the STEP exports, each phase counted in its own progress lines (`[3/20] Downloading Chassis bracket v18 (.f3d)`, `[1/4] Generating STEP of Chassis bracket v18`), with sizes, durations and, while Autodesk works on a STEP, a status line every 10 seconds.

Options of `run` (`--item` and `--step-limit` also apply to `status`):
* `--item NAME` handles only the named cloud items instead of all; repeatable, `*` and `?` work as wildcards. A name that matches nothing is an error.
* `--dry-run` downloads and compares only: no backup files or state are written and no STEP is requested (a `--summary-file` is still written).
* `--no-step` downloads `.f3d` and plain files only, without checking or generating STEPs.
* `--step-limit N` (or `FUSION_BACKUP_STEP_LIMIT`) is your monthly limit of STEP jobs. Once this month's count reaches it, further STEPs stay pending until next month instead of being requested. Without it, there is no limit. `status` accepts it too, to show the count against the limit.
* `--mfg-api v2|v3` (or `FUSION_BACKUP_MFG_API`) chooses the Manufacturing Data Model API version that generates the STEPs; default `v2`. See the note in [How it works](#how-it-works).
* `--summary-file FILE` writes the run report to a file, e.g. for a commit message.

`-v` logs every item. `fusion-backup --help` and `fusion-backup <command> --help` show all options. `run` exits with 1 if any item failed; pending STEPs alone are not a failure.

## Documentation

* **[docs/authentication.md](docs/authentication.md)**: setting up the robot step by step, and how to keep its key safe.
* **[docs/architecture.md](docs/architecture.md)**: how fusion-backup works inside, with diagrams: its parts and types, a run, the download and STEP rules, the two Manufacturing Data Model API versions, the robot login and the reasons behind these choices.
* **[docs/development.md](docs/development.md)**: setting up a development environment, the checks every build runs, the code layout and the tests.
* **[examples/gitea/](examples/gitea/README.md)**: a complete, working setup for Gitea Actions (and, with one path changed, GitHub Actions): the workflow, the small commit script it uses, the secrets to add, notes for self-hosted runners, and the log of real runs, so you know what to expect.

## Project status and contributions

I designed fusion-backup, decided on its architecture and reviewed its code and documentation; the code itself was written by [Claude Opus 5.5](https://www.anthropic.com/claude) (Anthropic). It is tested by me and runs in my own homelab for my own purposes, so your mileage may vary. I fix what I run into myself; beyond that, I can't promise further development.

Pull requests are welcome if they fix a problem and don't introduce breaking changes: the configuration and state files, the paths and the contents of backed-up files (and with them their hashes) must stay compatible, so existing backups keep working without being rewritten.

## License

[MIT](LICENSE)
