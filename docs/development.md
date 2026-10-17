# Development

How the code is laid out, how to work on it and how it is tested. Why it is built this way is in [architecture.md](architecture.md).

## Setup and checks

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e . --group dev

.venv/bin/ruff format .        # formatting (140 characters per line)
.venv/bin/ruff check .         # lint
.venv/bin/mypy                 # types (strict)
.venv/bin/lint-imports         # package boundaries
.venv/bin/pytest               # tests with coverage report; fails below 90 %
```

GitHub Actions runs all of these on every push and pull request to `master`; the coverage table appears in each run's summary.

None of the checks is silenced anywhere in the code: there are no `# type: ignore`, `# noqa` or similar comments. Authlib ships no type hints, so `stubs/` contains stubs for the few parts of it that are used.

## Code layout

The code is one package, `src/fusion_backup`, with four subpackages; their roles and allowed dependencies are explained in [architecture.md](architecture.md#layers), and [import-linter](https://github.com/seddonym/import-linter) checks the dependencies.

| Package | Modules |
|---|---|
| `cloud` | `model`: the domain objects (`CloudItem`, `CloudProject`), the `CloudSource` interface and its errors. No Autodesk specifics. |
| `aps` | The Autodesk implementation of `CloudSource`: the robot login (`auth`), the HTTP layer (`client`), the Data Management API (`data_management`), the Manufacturing Data Model API (`mfg_data_model`: the interface in `api`, one module per API version in `v2` and `v3`, the choice between them in its `__init__`), the shared response models (`_dto`, private to this package) and `source`, which combines them into `ApsCloudSource`. |
| `backup` | The backup itself: the target's configuration and state files (`config`, `target`), one item in one target (`task`), the `--item` choice (`selection`), the run (`run`), its summary (`report`), the status report (`status`) and file writing and hashing (`files`). Knows only `cloud`. |
| `cli` | The commands, and the only place where the parts are wired together. |

## Tests

The tests never contact Autodesk. They work on three levels:

* **Unit tests of the backup logic** run against an in-memory `CloudSource` and cover the decisions: when to download, when to request and count a STEP, retries, dry runs, mirroring, mapping, path and collision checks, and that one failing item doesn't stop the others (`test_backup_*.py`).
* **Unit tests of the Autodesk layer** feed hand-written answers, shaped like Autodesk's real ones, through httpx2's mock transport and check the requests sent: the signed JWT assertion and app authentication of the robot login, token renewal, paging, folders, signed downloads without any credentials, STEP polling, refusals and timeouts, and that error messages never contain signed URLs (`test_aps_*.py`). Each Manufacturing Data Model API version is tested on its own (`test_mfg_data_model_v2.py`, `test_mfg_data_model_v3.py`), the choice between them in `test_mfg_data_model.py`, and every STEP test of the cloud source runs once per version.
* **End-to-end tests** run the real command line through every layer, with the robot's credentials in the environment and only Autodesk's HTTP answers scripted, over several consecutive runs; an unexpected request fails the test (`test_e2e.py`, plus `test_cli.py` for the commands). Each of them runs three times: without `FUSION_BACKUP_MFG_API` (the default, v2) and with it set to `v2` and to `v3`, checking that only the chosen version's endpoint is called.

The test doubles live in `tests/fakes.py`: throwaway robot credentials, the in-memory cloud, the scripted HTTP transport (every request must match a scripted answer, or the test fails) and one description per Manufacturing Data Model API version (`MFG_V2`, `MFG_V3`) with its endpoint and answer shape.
