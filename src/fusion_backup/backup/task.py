"""One cloud item mapped into one target: where its files go and whether they are up to date."""

from dataclasses import dataclass
from pathlib import Path

from fusion_backup.backup.config import ItemMapping, ItemState
from fusion_backup.backup.files import file_sha256
from fusion_backup.backup.target import BackupTarget
from fusion_backup.cloud import CloudItem


@dataclass
class Task:
    """One cloud item mapped into one target."""

    target: BackupTarget
    item: CloudItem
    stored_path: Path
    step_path: Path | None  # None: no STEP wanted for this mapping (or STEPs are switched off)
    step_label: str
    entry: ItemState
    on_disk_sha: str | None
    step_on_disk_sha: str | None
    cloud_sha: str | None = None  # SHA-256 of the cloud version, once the download phase handled the item

    @classmethod
    def create(cls, target: BackupTarget, mapping: ItemMapping, item: CloudItem, *, steps: bool) -> Task:
        """Check and reserve the item's destinations in the target; raises BackupError for unusable paths."""
        for role, relative in mapping.destinations():
            target.claim(target.destination(relative), f"{item.path} ({role})")
        stored_path = target.destination(mapping.target)
        step_path = target.destination(mapping.step) if steps and mapping.step else None
        step_label = f"{target.name}/{mapping.step}"
        on_disk_sha, step_on_disk_sha = file_sha256(stored_path), file_sha256(step_path) if step_path else None
        return cls(target, item, stored_path, step_path, step_label, target.item_state(item.path), on_disk_sha, step_on_disk_sha)

    @property
    def stored_is_current(self) -> bool:
        """The target already holds exactly this cloud version: same item ID and version, and the file on disk
        still has the SHA-256 recorded when it was written."""
        held = (self.entry.item_id, self.entry.version, self.entry.sha256)
        return self.on_disk_sha is not None and held == (self.item.item_id, self.item.version, self.on_disk_sha)

    @property
    def needs_step(self) -> bool:
        """A STEP is wanted, and the existing one is missing, was made from a different .f3d, or was changed on disk."""
        if self.step_path is None or self.cloud_sha is None:
            return False
        step_is_current = self.step_on_disk_sha is not None and self.step_on_disk_sha == self.entry.step_sha256
        return self.entry.step_source_sha256 != self.cloud_sha or not step_is_current
