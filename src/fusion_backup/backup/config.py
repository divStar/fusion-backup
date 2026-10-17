"""The two files a target directory carries: `.fusion-backup.json` (written by you) and
`.fusion-backup-state.json` (written by the tool)."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

CONFIG_NAME = ".fusion-backup.json"
STATE_NAME = ".fusion-backup-state.json"


class ItemMapping(BaseModel):
    """Where one cloud item goes: a design's `f3d` (plus an optional `step`), or a plain `file`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    f3d: str | None = None
    step: str | None = None
    file: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if bool(self.file) == bool(self.f3d) or (self.step and not self.f3d):
            raise ValueError("needs either 'file' or 'f3d' (with an optional 'step')")
        return self

    @property
    def target(self) -> str:
        """The path the downloaded file is written to."""
        return self.f3d or self.file or ""

    def destinations(self) -> list[tuple[str, str]]:
        """(role, path) of every file this mapping writes: "f3d" or "file", plus "step"."""
        written = [("f3d", self.f3d) if self.f3d else ("file", self.file or ""), ("step", self.step or "")]
        return [(role, path) for role, path in written if path]


class Mirror(BaseModel):
    """Default for items without an explicit mapping: keep the project's folders below `directory`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: str = "."
    step: bool = True

    def mapping_for(self, item_path: str, is_design: bool) -> ItemMapping:
        """Designs become `<directory>/<path>.f3d` (+ `.step`), other files keep their name."""
        base = str(PurePosixPath(self.directory, item_path))
        if not is_design:
            return ItemMapping(file=base)
        return ItemMapping(f3d=f"{base}.f3d", step=f"{base}.step" if self.step else None)


class TargetConfig(BaseModel):
    """Contents of `.fusion-backup.json`."""

    model_config = ConfigDict(extra="forbid")

    hub: str
    project: str
    items: dict[str, ItemMapping] = {}
    ignore: list[str] = []
    mirror: Mirror | None = None


class ItemState(BaseModel):
    """What the target holds of one cloud item.

    `item_id` identifies the cloud item, so an item deleted and re-created under the same name is noticed
    even though its version numbers start again. `step_source_sha256` is the .f3d the current STEP was
    generated from, `step_sha256` the STEP file as written; a STEP whose source differs from the current .f3d,
    or whose file no longer matches, is pending.
    """

    item_id: str | None = None
    version: int | None = None
    sha256: str | None = None
    step_source_sha256: str | None = None
    step_sha256: str | None = None


class TargetState(BaseModel):
    """Contents of `.fusion-backup-state.json`; `step_jobs` counts STEP requests per month ("YYYY-MM")."""

    items: dict[str, ItemState] = {}
    step_jobs: dict[str, int] = {}
