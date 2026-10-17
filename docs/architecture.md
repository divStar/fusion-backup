# Architecture

This document explains how fusion-backup is built and why, for developers who want to understand or change it without reading every file first. How to install, configure and run the tool is in the [README](../README.md); how the robot login is set up is in [authentication.md](authentication.md).

In one sentence: fusion-backup lists a Fusion project in the Autodesk cloud, downloads every design's `.f3d` (and every uploaded file) that changed since the last run, has Autodesk generate a STEP for every design whose `.f3d` changed, writes everything into a target directory and records what it holds in a state file there.

## Context

```mermaid
flowchart LR
    operator["You or a CI job"]
    tool["fusion-backup"]
    target[("Target directory<br/>usually a git working copy")]

    subgraph autodesk["Autodesk Platform Services (APS)"]
        auth["Authentication API<br/>robot login"]
        dm["Data Management API<br/>hubs, projects, folders, versions"]
        oss["OSS v2<br/>signed download URLs"]
        mfg["Manufacturing Data Model API<br/>STEP exports, v2 or v3"]
    end
    storage["S3 / CDN<br/>pre-signed URLs"]

    operator -- "fusion-backup run" --> tool
    tool -- "JWT assertion → access token" --> auth
    tool -- "list items, tip version" --> dm
    tool -- "where is the file?" --> oss
    tool -- "request STEP, poll status" --> mfg
    tool -- "download .f3d, files, STEP" --> storage
    tool -- "files + state" --> target
    operator -- "git commit / push" --> target
```

* Fusion itself never runs. Everything comes from Autodesk's web APIs ([Data Management](https://aps.autodesk.com/en/docs/data/v2/), including its [signed S3 downloads](https://aps.autodesk.com/en/docs/data/v2/reference/http/buckets-:bucketKey-objects-:objectKey-signeds3download-GET), and the Manufacturing Data Model API), so the tool runs headless on any machine with outgoing HTTPS to Autodesk.
* The tool logs in as a **robot user**, an APS [Secure Service Account (SSA)](https://aps.autodesk.com/en/docs/ssa/v1), that is a member of the Fusion hub with at least Reader access to the project (see [below](#the-robot-login)).
* The tool **never runs git**. It only writes files; committing and pushing is the caller's job, e.g. a step in the CI workflow. That keeps it independent of any CI system or git host.

## Layers

The code is one Python package with four parts. Their allowed dependencies are enforced by [import-linter](https://github.com/seddonym/import-linter) on every build:

```mermaid
flowchart TD
    cli["cli<br/>commands, composition root"]
    backup["backup<br/>what to back up, when, where"]
    aps["aps<br/>Autodesk implementation"]
    cloud["cloud<br/>domain objects + CloudSource interface"]

    cli --> backup
    cli --> aps
    backup --> cloud
    aps --> cloud
```

* **`cloud`** is the contract: the domain objects (`CloudProject`, `CloudItem`), the `CloudSource` interface and its two errors. It knows nothing about Autodesk.
* **`backup`** contains all decisions (mapping, hashing, STEP rules, state, limits) and depends only on `cloud`. It can be tested completely against an in-memory `CloudSource`.
* **`aps`** implements `CloudSource` with Autodesk's APIs. Autodesk's response models (`aps._dto`) may not be imported from outside `aps`, so no Autodesk shape leaks into the backup logic.
* **`cli`** is the only place where the parts meet: it builds the robot login, the `ApsCloudSource` and the targets, and hands them to the run.

## Core types

### The backup side

```mermaid
classDiagram
    direction LR
    class CloudSource {
        <<interface>>
        list_projects() list~CloudProject~
        list_items(hub, project) list~CloudItem~
        download(item) bytes
        generate_step(item, on_submitted) bytes
    }
    class CloudItem {
        hub
        project
        path
        item_id
        is_design
        version
    }
    class BackupRun {
        options: RunOptions
        execute() Summary
    }
    class BackupTarget {
        root
        config: TargetConfig
        state: TargetState
        mapping_for(item) ItemMapping
        claim(path, writer)
        save_state()
    }
    class Task {
        stored_path
        step_path
        on_disk_sha
        step_on_disk_sha
        stored_is_current
        needs_step
    }
    class ItemMapping {
        f3d
        step
        file
    }
    class ItemState {
        item_id
        version
        sha256
        step_source_sha256
        step_sha256
    }

    BackupRun --> CloudSource : uses
    BackupRun --> "1..*" BackupTarget
    BackupRun ..> Task : plans one per item and target
    Task --> CloudItem
    Task --> BackupTarget
    Task --> ItemState : reads and updates
    BackupTarget --> ItemMapping : from .fusion-backup.json
    BackupTarget --> ItemState : in .fusion-backup-state.json
```

* **`BackupTarget`** is one directory with its configuration (`.fusion-backup.json`, written by you) and its state (`.fusion-backup-state.json`, written by the tool). It answers where an item goes (an explicit mapping, the mirror default, ignored or unmapped) and guards the paths: they must stay inside the directory, may not point into `.git` or at the tool's own files, and no two writers may claim the same file, including names that differ only in upper and lower case.
* **`Task`** is one cloud item in one target. It knows the paths, the recorded state and the hashes of the files on disk, and decides whether the stored file is current and whether a STEP is needed.
* **`BackupRun`** executes the tasks in two phases and collects a **`Summary`**. A failing item is recorded and the run continues; only the summary's errors decide the exit code.

### The Autodesk side

```mermaid
classDiagram
    direction LR
    class CloudSource {
        <<interface>>
    }
    class ApsCloudSource {
        list_items(hub, project)
        download(item)
        generate_step(item, on_submitted)
    }
    class DataManagementApi {
        hubs() / projects() / folder_contents()
        tip_version(project, item) int
        download(storage_urn) bytes
    }
    class MfgDataModelApi {
        <<interface>>
        hub_id(hub_name) str
        step_export(hub_id, item_id, generate) StepExport
    }
    class MfgDataModelV2
    class MfgDataModelV3
    class ApsClient {
        get(path, model)
        graphql(path, query, variables, model)
        download(signed_url)
    }

    ApsCloudSource ..|> CloudSource
    ApsCloudSource --> DataManagementApi
    ApsCloudSource --> MfgDataModelApi : chosen by open_api(version)
    MfgDataModelV2 ..|> MfgDataModelApi
    MfgDataModelV3 ..|> MfgDataModelApi
    DataManagementApi --> ApsClient
    MfgDataModelV2 --> ApsClient
    MfgDataModelV3 --> ApsClient
```

* **`ApsClient`** is the thin HTTP layer over the logged-in client. It parses answers into Pydantic models and turns every transport, HTTP and login failure into a `CloudError`. Pre-signed URLs and Autodesk's raw answers never appear in those messages, because both can contain credentials.
* There is **one class per Autodesk API**, and for the Manufacturing Data Model API **one class per API version** (see [below](#two-versions-of-the-manufacturing-data-model-api)).
* **`ApsCloudSource`** combines them and is the only class that hands data to the backup, and only as `cloud` domain objects.

## A run

```mermaid
sequenceDiagram
    participant cli
    participant run as BackupRun
    participant target as BackupTarget
    participant cloud as CloudSource
    participant disk as Target directory

    cli->>target: load configuration and state
    cli->>run: execute()
    run->>cloud: list_items(hub, project), once per project
    Note over run: plan: one Task per mapped item and target,<br/>paths checked and claimed
    loop Phase 1: every item whose stored file is not current
        run->>cloud: download(item)
        run->>disk: write, if the bytes differ
        run->>target: save state
    end
    loop Phase 2: every design that needs a STEP
        run->>cloud: generate_step(item, on_submitted)
        cloud-->>run: on_submitted() as soon as Autodesk accepted it
        run->>target: count the STEP job, save state
        cloud-->>run: STEP bytes
        run->>disk: write STEP
        run->>target: save state
    end
    run-->>cli: Summary (report, exit code)
```

* **Downloads come first, STEP exports second.** Downloads are free; exports may be metered. A failed or refused export never prevents the `.f3d` from being backed up; the STEP just stays pending for the next run.
* **An item mapped into several targets** is downloaded and exported once and written into each of them.
* **The state is saved after every step**, so an interrupted run loses nothing that was already written, and a STEP job is counted even if the process dies while Autodesk is still working on it.

## When is something downloaded, written or exported?

```mermaid
flowchart TD
    start(["cloud item, mapped into a target"]) --> current{"item ID, version and the<br/>file's SHA-256 on disk<br/>match the state?"}
    current -- yes --> stepwanted
    current -- no --> download["download the stored file"]
    download --> differ{"bytes differ<br/>from the file on disk?"}
    differ -- yes --> write["write it"] --> record
    differ -- no --> record["record item ID, version, SHA-256"]
    record --> stepwanted{"design with a<br/>STEP mapping?"}
    stepwanted -- no --> done(["done"])
    stepwanted -- yes --> stepcurrent{"existing STEP made from this .f3d<br/>and its file unchanged since?"}
    stepcurrent -- yes --> done
    stepcurrent -- no --> allowed{"dry run, refused earlier this run<br/>or monthly limit reached?"}
    allowed -- yes --> pending(["STEP pending,<br/>retried next run"])
    allowed -- no --> generate["generate the STEP"] --> done
```

The rules rest on two facts about Autodesk's files:

* **A version's stored `.f3d` is byte-stable**: downloading it twice gives the same bytes. So item ID plus version identify the content, and the SHA-256 of the file on disk proves the target still holds it. Local edits or a deleted file are therefore noticed and repaired. Timestamps are useless here, because a git checkout resets them.
* **STEP exports are not byte-stable** across generations: every export carries its own timestamp. A STEP is therefore never compared with a fresh export. Instead the state records which `.f3d` (by SHA-256) it was made from, plus the STEP file's own SHA-256, so a new STEP is requested only when its source changed or the file on disk was lost or altered.

The **item ID** is part of the state because a design that is deleted and re-created under the same name starts again at version 1; without the ID it would look unchanged.

## A STEP export

```mermaid
stateDiagram-v2
    [*] --> Requested : step_export(generate = true)
    Requested --> Refused : HTTP error (e.g. limit reached)
    Requested --> Accepted : answered
    Accepted --> Waiting : on_submitted() counts the job
    Waiting --> Waiting : poll every 10 s, generate = false
    Waiting --> Failed : FAILED, TIMEOUT or 15 min without result
    Waiting --> Finished : SUCCESS with a signed URL
    Finished --> Outdated : tip version differs from the backed-up version
    Finished --> Downloaded : version still the same
    Downloaded --> [*]
    Refused --> Pending
    Failed --> Pending
    Outdated --> Pending
    Pending --> [*] : retried next run
```

* **A job is counted the moment Autodesk accepts the request**, not when it succeeds, because that is when it may be metered. A refusal is not counted, and after one refusal the run requests no further STEPs.
* **Autodesk keeps one export per design version.** Requesting a STEP for a version that was already exported returns the existing file within seconds.
* **Before the finished STEP is downloaded, the Data Management API is asked for the design's current version.** If someone saved a new version meanwhile, the export may belong to that newer version, so it is discarded and retried next run instead of being stored next to an older `.f3d`.

## Two versions of the Manufacturing Data Model API

Autodesk runs [v2](https://aps.autodesk.com/en/docs/mfgdataapi/v2/) and [v3](https://aps.autodesk.com/en/docs/mfgdataapi/v3/developers_guide/) of the API side by side; what each costs and how to choose one is in the README's [How it works](../README.md#how-it-works). fusion-backup supports both behind one interface:

```mermaid
flowchart LR
    option["--mfg-api / FUSION_BACKUP_MFG_API<br/>default v2"] --> open_api["open_api(client, version)<br/>the only switch"]
    open_api -- v2 --> v2["MfgDataModelV2"]
    open_api -- v3 --> v3["MfgDataModelV3"]
    v2 & v3 -.implement.-> api["MfgDataModelApi<br/>hub_id, step_export → StepExport"]
```

| | v2 | v3 |
|---|---|---|
| Endpoint | `/mfg/graphql` | `/mfg/v3/graphql/public` |
| Design's derivatives below | `tipRootComponentVersion` | `tipRootModel` |
| STEP requested with | `outputFormat: [STEP]` | `targetFormat: [STEP]` |
| Version number of the design | available | not exposed |

* Each version lives in its own module with its own endpoint, query and response models, and both implement `MfgDataModelApi` explicitly, so the type checker rejects an implementation with a missing method or a different signature.
* Hub and item IDs are the same in both versions, and both return the same cached export for a design version, so switching does not change any backed-up file.
* The version check before downloading a STEP uses the Data Management API, because v3 does not expose version numbers. That way both versions behave identically.

## The robot login

Why the tool logs in as a robot instead of a user login or app credentials, how the robot is created with Autodesk's SSA Manager and invited into the hub, and how its token exchange works is described in [authentication.md](authentication.md). In the code:

* `aps.auth.connect` builds Authlib's JWT bearer client ([RFC 7523](https://datatracker.ietf.org/doc/html/rfc7523)) with a joserfc RSA key; it requests a new access token whenever the current one expires. Each token request gets a fresh claims set, because Authlib stores the generated JWT ID in it.
* `SsaCredentials` reads the static credentials from the environment and keeps the secrets out of its `repr`, so they never end up in logs.
* Autodesk's usage API rejects robots ([limitations](authentication.md#limitations)), so the tool counts the STEP jobs it starts per month in the state file itself, and `--step-limit` stops at a limit you choose.

## Design decisions in short

| Decision | Why |
|---|---|
| Robot login (Secure Service Account) | Static credentials for unattended runs; the alternatives are compared in [authentication.md](authentication.md#why-a-robot) |
| The tool writes files but never runs git | Committing belongs to the workflow around it; the tool stays usable with any CI and any git host, or without git |
| State file next to the backed-up files | The target describes itself; committing it with the files keeps both consistent, and any machine can continue where the last run stopped |
| SHA-256 and item IDs instead of timestamps | Git checkouts reset timestamps; hashes prove content, IDs catch re-created designs |
| Downloads before exports, exports retried later | The free, essential part (the `.f3d`) never depends on the metered, optional part (the STEP) |
| One interface, one module per API version | Autodesk's API versions differ in endpoint and query shape only; the rest of the tool never notices which one runs |
| Errors never contain signed URLs or raw answers | Both can carry credentials, and errors end up in CI logs |
| Libraries for everything generic | [Typer](https://typer.tiangolo.com/) (command line), [httpx2](https://github.com/pydantic/httpx2) (HTTP), [Authlib](https://authlib.org/) with [joserfc](https://jose.authlib.org/) (JWT login), [Pydantic](https://docs.pydantic.dev/) (Autodesk answers, configuration and state); the tool's own code is only the backup logic and the Autodesk specifics |

## Tests

The layers above are also the test seams: the backup logic is tested against an in-memory `CloudSource`, the Autodesk layer against scripted HTTP answers. How, and what the end-to-end tests cover, is in [development.md](development.md#tests).
