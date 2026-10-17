"""A target directory (typically a git repository) with its configuration and state."""

import json
from pathlib import Path

from pydantic import ValidationError

from fusion_backup.backup.config import CONFIG_NAME, STATE_NAME, ItemMapping, ItemState, Mirror, TargetConfig, TargetState
from fusion_backup.backup.files import write_atomic


class BackupError(Exception):
    """A configuration or file problem; the message is meant for the user."""


class BackupTarget:
    """Where cloud items are written, and the record of what is there."""

    def __init__(self, root: Path, config: TargetConfig, state: TargetState):
        self.root = root.expanduser().resolve()
        self.config = config
        self.state = state
        # destination (case-folded, as macOS and Windows file systems ignore case) -> "item (role)" writing it
        self._claims: dict[str, str] = {}
        for item_path, mapping in config.items.items():
            for role, relative in mapping.destinations():
                self.claim(self.destination(relative), f"{item_path} ({role})")
        if config.mirror:
            self.path(config.mirror.directory)

    @classmethod
    def load(cls, root: Path) -> BackupTarget:
        """A directory with a `.fusion-backup.json`."""
        config_path = root / CONFIG_NAME
        if not config_path.is_file():
            raise BackupError(f"{config_path} not found")
        try:
            config = TargetConfig.model_validate_json(config_path.read_bytes())
        except ValidationError as err:
            raise BackupError(f"{config_path} is invalid:\n{err}") from err
        return cls(root, config, _load_state(root))

    @classmethod
    def mirror_into(cls, root: Path, hub: str, project: str, step: bool = True) -> BackupTarget:
        """A directory without configuration that receives a whole project (its state is still kept there).

        The directory is created on the first write, so a dry run leaves no trace.
        """
        return cls(root, TargetConfig(hub=hub, project=project, mirror=Mirror(step=step)), _load_state(root))

    @property
    def name(self) -> str:
        return self.root.name

    def mapping_for(self, item_path: str, is_design: bool) -> ItemMapping | None:
        """The explicit mapping, else the mirror default; None for ignored or unmapped items."""
        if item_path in self.config.items:
            return self.config.items[item_path]
        if item_path in self.config.ignore or self.config.mirror is None:
            return None
        return self.config.mirror.mapping_for(item_path, is_design)

    def ignores(self, item_path: str) -> bool:
        return item_path in self.config.ignore

    def path(self, relative: str) -> Path:
        """Resolve a configured path and make sure it stays inside the target."""
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise BackupError(f"{relative} points outside {self.root}")
        return target

    def destination(self, relative: str) -> Path:
        """A file the backup may write: inside the target, not in `.git` and not one of the tool's own files."""
        target = self.path(relative)
        inside = target.relative_to(self.root)
        if inside.parts[:1] == (".git",) or inside.as_posix() in (CONFIG_NAME, STATE_NAME):
            raise BackupError(f"{relative} would overwrite {inside.as_posix()}")
        return target

    def claim(self, path: Path, writer: str) -> None:
        """Reserve a destination for one writer, e.g. "Bracket (step)"; a second writer of the same file is an error."""
        key = str(path).casefold()
        owner = self._claims.setdefault(key, writer)
        if owner != writer:
            raise BackupError(f"{path.relative_to(self.root).as_posix()} is written by both '{owner}' and '{writer}'")

    def item_state(self, item_path: str) -> ItemState:
        """The (mutable) state entry of a cloud item."""
        return self.state.items.setdefault(item_path, ItemState())

    def count_step_job(self, month: str) -> None:
        self.state.step_jobs[month] = self.state.step_jobs.get(month, 0) + 1

    def step_jobs(self, month: str) -> int:
        return self.state.step_jobs.get(month, 0)

    def save_state(self) -> None:
        """Write the state file, sorted so its diffs stay readable."""
        data = self.state.model_dump(exclude_none=True)
        write_atomic(self.root / STATE_NAME, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def group_by_project(targets: list[BackupTarget]) -> dict[tuple[str, str], list[BackupTarget]]:
    """Targets grouped by (hub, project), so each project is listed only once."""
    groups: dict[tuple[str, str], list[BackupTarget]] = {}
    for target in targets:
        groups.setdefault((target.config.hub, target.config.project), []).append(target)
    return groups


def _load_state(root: Path) -> TargetState:
    state_path = root.expanduser() / STATE_NAME
    if not state_path.is_file():
        return TargetState()
    try:
        return TargetState.model_validate_json(state_path.read_bytes())
    except ValidationError as err:
        raise BackupError(f"{state_path} is invalid:\n{err}") from err
