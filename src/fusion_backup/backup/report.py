"""What a run reports: the summary at the end and the formatting helpers its log lines share."""

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def step_jobs_line(used: int, limit: int | None) -> str:
    """The report line on STEP jobs, e.g. "STEP jobs this month (...): 7 of 30 (limit set by you)"."""
    of_limit = f" of {limit} (limit set by you)" if limit is not None else ""
    return f"STEP jobs this month (MFG Data Model - standard jobs, counted by fusion-backup): {used}{of_limit}"


def format_size(size: int) -> str:
    """Human-readable size in decimal units, e.g. "6.6 MB"."""
    if size < 1000:
        return f"{size} B"
    scaled = float(size)
    for unit in ("KB", "MB", "GB"):
        scaled /= 1000
        if scaled < 1000 or unit == "GB":
            break
    return f"{scaled:.1f} {unit}"


@dataclass
class Summary:
    """What a run did; printed at the end and usable as a commit message body."""

    dry_run: bool
    started: datetime
    checked: int = 0
    written: list[str] = field(default_factory=list)
    steps_generated: int = 0
    steps_pending: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    step_jobs_this_month: int = 0
    step_limit: int | None = None

    def render(self) -> str:
        """Human-readable report."""
        verb = "would write" if self.dry_run else "written"
        lines = [
            f"Fusion backup {self.started:%Y-%m-%d %H:%M} UTC{' (dry run)' if self.dry_run else ''}",
            f"Cloud items checked: {self.checked}, files {verb}: {len(self.written)}, "
            f"STEPs generated: {self.steps_generated}, STEPs pending: {len(self.steps_pending)}",
            step_jobs_line(self.step_jobs_this_month, self.step_limit),
        ]
        sections = [
            (f"Files {verb}", self.written),
            ("STEPs pending (retried next run)", self.steps_pending),
            ("Unmapped cloud items (map them or add them to 'ignore')", self.unmapped),
            ("Errors", self.errors),
        ]
        for title, entries in sections:
            if entries:
                lines.append(f"{title}:")
                lines.extend(f"  - {entry}" for entry in entries)
        return "\n".join(lines)
