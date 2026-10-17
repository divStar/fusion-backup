"""Command line interface, and the one place where the parts are wired together: the Autodesk cloud
source, logged in as the robot from environment variables, and the backup working against it."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from fusion_backup import aps
from fusion_backup.backup import BackupError, BackupRun, BackupTarget, ItemSelection, RunOptions, status_lines
from fusion_backup.backup.config import CONFIG_NAME
from fusion_backup.cloud import CloudError, CloudSource

app = typer.Typer(
    help="Back up Autodesk Fusion designs (.f3d and STEP) from the Autodesk cloud into git repositories. "
    "Logs in as a Secure Service Account configured through APS_* environment variables.",
    no_args_is_help=True,
    add_completion=False,
)

_RepoOption = Annotated[list[Path] | None, typer.Option("--repo", help=f"Directory containing {CONFIG_NAME} (repeatable).")]
_OutOption = Annotated[Path | None, typer.Option(help="Without a configuration: mirror one project into this directory.")]
_HubOption = Annotated[str | None, typer.Option(help="Hub name (for --out and list).")]
_ProjectOption = Annotated[str | None, typer.Option(help="Project name (for --out and list).")]
_ItemOption = Annotated[
    list[str] | None,
    typer.Option(
        "--item",
        help="Only this cloud item, by its name as shown by 'list' (repeatable; * and ? work as wildcards). Default: all items.",
    ),
]
_StepLimitOption = Annotated[
    int | None,
    typer.Option(
        envvar="FUSION_BACKUP_STEP_LIMIT",
        min=0,
        help="Your monthly limit of STEP jobs. Once reached, further STEPs stay pending until next month. Default: no limit.",
    ),
]
_MfgApiOption = Annotated[
    aps.MfgApiVersion,
    typer.Option(
        envvar="FUSION_BACKUP_MFG_API",
        help="Manufacturing Data Model API version that generates the STEPs: v2 (not priced, to be retired by Autodesk) "
        "or v3 (priced per export).",
    ),
]


@app.callback()
def _global_options(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Also log unchanged items and polling.")] = False,
) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("fusion_backup").setLevel(logging.DEBUG if verbose else logging.INFO)


@app.command()
def check(repo: _RepoOption = None) -> None:
    """Connection test: log in as the robot and list the projects it can see."""
    with _user_errors():
        targets = [BackupTarget.load(path) for path in repo or []]
        projects = _source().list_projects()
        typer.echo("Robot login works.")
        typer.echo("Visible projects:" if projects else "It sees no projects yet - add it as a member of your hub and project.")
        for project in projects:
            typer.echo(f"  {project.hub} / {project.name}")
        visible = {(project.hub, project.name) for project in projects}
        missing = [target for target in targets if (target.config.hub, target.config.project) not in visible]
        for target in missing:
            typer.echo(f"Not visible to the robot: {target.name} ({target.config.hub} / {target.config.project})", err=True)
    if missing:
        raise typer.Exit(1)


@app.command("list")
def list_(hub: _HubOption = None, project: _ProjectOption = None) -> None:
    """List hubs and projects, or the items of one project with their versions."""
    with _user_errors():
        if bool(hub) != bool(project):
            raise BackupError("list needs both --hub and --project, or neither")
        source = _source()
        if hub and project:
            for item in source.list_items(hub, project):
                typer.echo(f"v{item.version:<4} {'design' if item.is_design else 'file':<6}  {item.path}")
        else:
            for found in source.list_projects():
                typer.echo(f"{found.hub} / {found.name}")


@app.command()
def status(
    repo: _RepoOption = None,
    out: _OutOption = None,
    hub: _HubOption = None,
    project: _ProjectOption = None,
    item: _ItemOption = None,
    step_limit: _StepLimitOption = None,
) -> None:
    """Show the cloud versions next to what each target holds, including pending STEPs."""
    with _user_errors():
        targets = _targets(repo, out, hub, project, step=True)
        lines = status_lines(_source(), targets, selection=ItemSelection(tuple(item or ())), step_limit=step_limit)
        typer.echo("\n".join(lines))


@app.command()
def run(
    repo: _RepoOption = None,
    out: _OutOption = None,
    hub: _HubOption = None,
    project: _ProjectOption = None,
    item: _ItemOption = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            help="Download and compare only: no backup files or state are written and no STEP is requested "
            "(--summary-file is still written)."
        ),
    ] = False,
    no_step: Annotated[bool, typer.Option(help="Back up .f3d and plain files only; skip the STEP check and generation.")] = False,
    step_limit: _StepLimitOption = None,
    mfg_api: _MfgApiOption = aps.MfgApiVersion.V2,
    summary_file: Annotated[Path | None, typer.Option(help="Also write the run report to this file.")] = None,
) -> None:
    """Back up all mapped items (or only those chosen with --item). Exits with 1 if any item failed or an --item
    matched nothing; pending STEPs alone are not a failure."""
    with _user_errors():
        targets = _targets(repo, out, hub, project, step=not no_step)
        options = RunOptions(dry_run=dry_run, steps=not no_step, step_limit=step_limit, selection=ItemSelection(tuple(item or ())))
        summary = BackupRun(_source(mfg_api), targets, options).execute()
        report = summary.render()
        typer.echo(report)
        if summary_file:
            summary_file.write_text(report + "\n")
    if summary.errors:
        raise typer.Exit(1)


def _targets(repo: list[Path] | None, out: Path | None, hub: str | None, project: str | None, *, step: bool) -> list[BackupTarget]:
    if out:
        if repo or not (hub and project):
            raise BackupError("--out needs --hub and --project and cannot be combined with --repo")
        return [BackupTarget.mirror_into(out, hub, project, step=step)]
    if not repo:
        raise BackupError("Pass --repo (repeatable), or --out with --hub and --project")
    return [BackupTarget.load(path) for path in repo]


def _source(mfg_api: aps.MfgApiVersion = aps.MfgApiVersion.V2) -> CloudSource:
    return aps.open_source(aps.SsaCredentials.from_env(), mfg_api)


@contextmanager
def _user_errors() -> Iterator[None]:
    """Show expected failures as one readable line and exit with 1, instead of a traceback."""
    try:
        yield
    except (BackupError, CloudError) as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1) from err
