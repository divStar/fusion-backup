"""The backup run: bring every selected and mapped cloud item into its targets."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fusion_backup.backup.files import sha256, write_atomic
from fusion_backup.backup.report import Summary, format_size, utc_now
from fusion_backup.backup.selection import ALL_ITEMS, ItemSelection
from fusion_backup.backup.target import BackupError, BackupTarget, group_by_project
from fusion_backup.backup.task import Task
from fusion_backup.cloud import CloudError, CloudItem, CloudSource, StepUnavailableError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    """How a run behaves."""

    dry_run: bool = False  # download and compare only: write nothing, request no STEP
    steps: bool = True  # False: neither check nor generate any STEP
    step_limit: int | None = None  # STEP jobs per month; once reached, further STEPs stay pending. None: no limit
    selection: ItemSelection = ALL_ITEMS


@dataclass
class BackupRun:
    """One pass over all targets, in two phases.

    1. Downloads (free): each mapped item's stored file (.f3d or an uploaded file) is downloaded when the cloud
       has a version the target does not hold yet, and written when its bytes differ.
    2. STEP exports (metered): a STEP is requested only when the .f3d it would belong to is not the one the
       existing STEP came from. If that fails, the .f3d is kept and the STEP stays pending until a later run.

    An item mapped into several targets is downloaded and exported once. Progress is logged as "[3/20] ...",
    counted separately per phase.
    """

    cloud: CloudSource
    targets: list[BackupTarget]
    options: RunOptions = RunOptions()
    now: Callable[[], datetime] = utc_now
    clock: Callable[[], float] = time.monotonic

    summary: Summary = field(init=False)
    _month: str = field(init=False)
    _step_refused: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        started = self.now()
        self._month = started.strftime("%Y-%m")
        self.summary = Summary(dry_run=self.options.dry_run, started=started, step_limit=self.options.step_limit)

    def execute(self) -> Summary:
        """Back up everything; per-item failures are collected instead of ending the run."""
        by_item = self._plan()
        for task in (task for tasks in by_item.values() for task in tasks if task.stored_is_current):
            task.cloud_sha = task.on_disk_sha
            log.debug("  %s: v%s unchanged in %s (item ID, version and SHA-256 match)", task.item.path, task.item.version, task.target.name)
        downloads = [tasks for tasks in by_item.values() if not all(task.stored_is_current for task in tasks)]
        for number, tasks in enumerate(downloads, 1):
            self._download(tasks, f"[{number}/{len(downloads)}]")
        if self.options.steps:
            exports = [steps for tasks in by_item.values() if (steps := _pending_steps(tasks))]
            for number, steps in enumerate(exports, 1):
                self._export(steps, f"[{number}/{len(exports)}]")
        self.summary.step_jobs_this_month = self._step_jobs_used()
        return self.summary

    def _plan(self) -> dict[str, list[Task]]:
        """List the projects and decide where each selected item goes; returns the tasks grouped by item ID."""
        by_item: dict[str, list[Task]] = {}
        listed: list[CloudItem] = []
        for (hub, project), targets in group_by_project(self.targets).items():
            items = self.cloud.list_items(hub, project)
            listed.extend(items)
            for item in filter(self.options.selection.includes, items):
                self.summary.checked += 1
                mapped = [(target, mapping) for target in targets if (mapping := target.mapping_for(item.path, item.is_design))]
                if not mapped and not any(target.ignores(item.path) for target in targets):
                    self.summary.unmapped.append(f"{project}/{item.path}")
                for target, mapping in mapped:
                    try:
                        by_item.setdefault(item.item_id, []).append(Task.create(target, mapping, item, steps=self.options.steps))
                    except (BackupError, OSError) as err:
                        self._fail(item, err)
        for pattern in self.options.selection.unmatched(listed):
            log.error("No cloud item matches '%s'", pattern)
            self.summary.errors.append(f"No cloud item matches '{pattern}'")
        return by_item

    def _download(self, tasks: list[Task], progress: str) -> None:
        item = tasks[0].item
        log.info("%s Downloading %s v%s (%s)", progress, item.path, item.version, ".f3d" if item.is_design else "file")
        started = self.clock()
        try:
            data = self.cloud.download(item)
        except CloudError as err:
            self._fail(item, err)
            return
        log.info("      %s in %.1f s", format_size(len(data)), self.clock() - started)
        cloud_sha = sha256(data)
        for task in (task for task in tasks if not task.stored_is_current):
            try:
                if cloud_sha != task.on_disk_sha:
                    self.summary.written.append(f"{task.target.name}/{task.stored_path.relative_to(task.target.root)} (v{item.version})")
                    if not self.options.dry_run:
                        write_atomic(task.stored_path, data)
                task.entry.item_id, task.entry.version, task.entry.sha256 = item.item_id, item.version, cloud_sha
                task.cloud_sha = cloud_sha
            except OSError as err:
                self._fail(item, err)
            self._save_state(task.target)

    def _export(self, steps: list[tuple[Task, Path]], progress: str) -> None:
        tasks = [task for task, _ in steps]
        item = tasks[0].item
        if reason := self._step_skip_reason():
            if not self._step_refused:
                log.info("%s STEP of %s v%s: %s", progress, item.path, item.version, reason)
            self.summary.steps_pending.extend(f"{task.step_label} ({reason})" for task in tasks)
            return
        log.info("%s Generating STEP of %s v%s", progress, item.path, item.version)
        owner = tasks[0].target

        def count_job() -> None:
            # counted and saved the moment the cloud accepts the request, so even a crash keeps the count
            owner.count_step_job(self._month)
            self._save_state(owner)

        started = self.clock()
        try:
            data = self.cloud.generate_step(item, on_submitted=count_job)
        except StepUnavailableError as err:
            self._step_refused = True  # e.g. the monthly limit: don't keep asking during this run
            log.warning("      refused, skipping the remaining STEPs this run: %s", err)
            self.summary.steps_pending.extend(f"{task.step_label} (refused)" for task in tasks)
            return
        except CloudError as err:
            log.warning("      failed: %s", err)
            self.summary.steps_pending.extend(f"{task.step_label} ({err})" for task in tasks)
            return
        log.info("      %s after %.0f s", format_size(len(data)), self.clock() - started)
        step_sha = sha256(data)
        for task, step_path in steps:
            try:
                write_atomic(step_path, data)
            except OSError as err:
                self._fail(item, err)
                continue
            task.entry.step_source_sha256, task.entry.step_sha256 = task.cloud_sha, step_sha
            self.summary.steps_generated += 1
            self.summary.written.append(f"{task.step_label} (STEP of v{item.version})")
            self._save_state(task.target)

    def _step_skip_reason(self) -> str | None:
        """Why no STEP may be requested right now, or None if it may."""
        if self.options.dry_run:
            return "would be generated"
        if self._step_refused:
            return "refused earlier in this run"
        limit = self.options.step_limit
        if limit is not None and self._step_jobs_used() >= limit:
            return f"monthly limit of {limit} reached"
        return None

    def _step_jobs_used(self) -> int:
        return sum(target.step_jobs(self._month) for target in self.targets)

    def _fail(self, item: CloudItem, err: Exception) -> None:
        log.error("%s: %s", item.path, err)
        self.summary.errors.append(f"{item.path}: {err}")

    def _save_state(self, target: BackupTarget) -> None:
        if self.options.dry_run:
            return
        try:
            target.save_state()
        except OSError as err:
            log.error("%s: cannot save the state: %s", target.name, err)
            self.summary.errors.append(f"{target.name}: cannot save the state: {err}")


def _pending_steps(tasks: list[Task]) -> list[tuple[Task, Path]]:
    """The tasks of one item that need a STEP, each with its STEP path."""
    return [(task, task.step_path) for task in tasks if task.needs_step and task.step_path is not None]
