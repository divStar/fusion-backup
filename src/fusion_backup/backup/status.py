"""A read-only report of what the cloud has and what the targets hold."""

from collections.abc import Callable
from datetime import datetime

from fusion_backup.backup.files import file_sha256
from fusion_backup.backup.report import step_jobs_line, utc_now
from fusion_backup.backup.selection import ALL_ITEMS, ItemSelection
from fusion_backup.backup.target import BackupError, BackupTarget, group_by_project
from fusion_backup.cloud import CloudItem, CloudSource


def status_lines(
    cloud: CloudSource,
    targets: list[BackupTarget],
    *,
    selection: ItemSelection = ALL_ITEMS,
    step_limit: int | None = None,
    now: Callable[[], datetime] = utc_now,
) -> list[str]:
    """Every selected cloud item with its version and what each target holds; changes nothing."""
    lines: list[str] = []
    listed: list[CloudItem] = []
    for (hub, project), group in group_by_project(targets).items():
        lines.append(f"{hub} / {project}")
        items = cloud.list_items(hub, project)
        listed.extend(items)
        for item in filter(selection.includes, items):
            held = [text for target in group if (text := _describe(target, item))]
            lines.append(f"  v{item.version:<4} {item.path:<45} {'; '.join(held) or 'UNMAPPED'}")
    if unmatched := selection.unmatched(listed):
        raise BackupError(f"No cloud item matches {', '.join(repr(pattern) for pattern in unmatched)}")
    month = now().strftime("%Y-%m")
    used = sum(target.step_jobs(month) for target in targets)
    lines.append(step_jobs_line(used, step_limit))
    return lines


def _describe(target: BackupTarget, item: CloudItem) -> str:
    mapping = target.mapping_for(item.path, item.is_design)  # an explicit mapping wins over a matching ignore pattern
    if mapping is None:
        return f"{target.name}: ignored" if target.ignores(item.path) else ""
    entry = target.state.items.get(item.path)
    if entry is None or entry.version is None:
        return f"{target.name}: nothing yet" + (", STEP pending" if mapping.step else "")
    text = f"{target.name}: v{entry.version}"
    if (entry.item_id, entry.version) != (item.item_id, item.version):
        text += " (cloud has a newer or replaced version)"
    if _sha_on_disk(target, mapping.target) != entry.sha256:
        text += " (file on disk differs)"
    if mapping.step:
        step_sha = _sha_on_disk(target, mapping.step)
        step_ok = step_sha is not None and step_sha == entry.step_sha256 and entry.step_source_sha256 == entry.sha256
        text += ", STEP ok" if step_ok else ", STEP pending"
    return text


def _sha_on_disk(target: BackupTarget, relative: str) -> str | None:
    """SHA-256 of a mapped file, or None if it is missing or its path is unusable."""
    try:
        return file_sha256(target.path(relative))
    except BackupError:
        return None
