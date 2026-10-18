import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes import FakeCloud

from fusion_backup.backup import BackupError, BackupRun, BackupTarget, status_lines
from fusion_backup.backup.config import CONFIG_NAME, STATE_NAME


def write_config(root: Path, items: dict[str, object], **extra: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(json.dumps({"hub": "Hub", "project": "Project", "items": items, **extra}))
    return root


def test_loads_mappings_ignore_and_mirror(tmp_path: Path) -> None:
    root = write_config(
        tmp_path,
        {"A": {"f3d": "a.f3d", "step": "a.step"}, "B": {"file": "b.dwg"}},
        ignore=["C"],
        mirror={"directory": "cloud/", "step": False},
    )
    target = BackupTarget.load(root)
    assert target.mapping_for("A", True) == target.config.items["A"]
    assert target.mapping_for("B", False) is not None and target.config.items["B"].target == "b.dwg"
    assert target.mapping_for("C", True) is None and target.ignores("C")
    mirrored = target.mapping_for("Sub/D", True)
    assert mirrored is not None and mirrored.f3d == "cloud/Sub/D.f3d" and mirrored.step is None
    plain = target.mapping_for("e.pdf", False)
    assert plain is not None and plain.file == "cloud/e.pdf"


def test_ignore_takes_wildcards_and_exact_names(tmp_path: Path) -> None:
    target = BackupTarget.load(write_config(tmp_path, {}, ignore=["Imported * STEP", "Part [v2]"]))
    assert target.ignores("Imported display STEP") and not target.ignores("Imported display")
    assert target.ignores("Part [v2]")  # an exact name counts, although [v2] alone would be a character class


def test_explicit_mapping_wins_over_an_ignore_pattern(tmp_path: Path) -> None:
    target = BackupTarget.load(write_config(tmp_path, {"Board": {"f3d": "board.f3d"}}, ignore=["*"]))
    assert target.mapping_for("Board", True) == target.config.items["Board"]
    assert target.mapping_for("Bracket", True) is None and target.ignores("Bracket")

    cloud = FakeCloud()
    cloud.put("Board", 1, b"b")
    cloud.put("Bracket", 1, b"c")
    lines = status_lines(cloud, [target], now=lambda: datetime(2026, 10, 15, tzinfo=UTC))
    assert lines[1].split()[1] == "Board" and lines[1].endswith(f"{target.name}: nothing yet")
    assert lines[2].split()[1] == "Bracket" and lines[2].endswith(f"{target.name}: ignored")


def test_without_mirror_unlisted_items_are_unmapped(tmp_path: Path) -> None:
    assert BackupTarget.load(write_config(tmp_path, {})).mapping_for("X", True) is None


@pytest.mark.parametrize(
    "raw",
    [
        {"f3d": "a.f3d", "file": "a.dwg"},
        {"step": "a.step"},
        {"file": "a.dwg", "step": "a.step"},
        {},
        {"f3d": "a.f3d", "stp": "a.step"},
    ],
)
def test_rejects_invalid_mappings(tmp_path: Path, raw: dict[str, str]) -> None:
    with pytest.raises(BackupError, match="is invalid"):
        BackupTarget.load(write_config(tmp_path, {"A": raw}))


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ({"A": {"file": ".git/config"}}, "would overwrite .git/config"),
        ({"A": {"file": CONFIG_NAME}}, f"would overwrite {CONFIG_NAME}"),
        ({"A": {"f3d": "x.f3d", "step": STATE_NAME}}, f"would overwrite {STATE_NAME}"),
        ({"A": {"f3d": "a.f3d"}, "B": {"file": "A.F3D"}}, r"written by both 'A \(f3d\)' and 'B \(file\)'"),
        ({"A": {"f3d": "a.step", "step": "a.step"}}, r"written by both 'A \(f3d\)' and 'A \(step\)'"),
    ],
)
def test_rejects_reserved_and_colliding_destinations(tmp_path: Path, items: dict[str, object], message: str) -> None:
    with pytest.raises(BackupError, match=message):
        BackupTarget.load(write_config(tmp_path, items))


def test_rejects_paths_outside_the_target(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="outside"):
        BackupTarget.load(write_config(tmp_path / "repo", {"A": {"f3d": "../escape.f3d"}}))
    with pytest.raises(BackupError, match="outside"):
        BackupTarget.load(write_config(tmp_path / "other", {}, mirror={"directory": "../x"}))


def test_missing_or_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="not found"):
        BackupTarget.load(tmp_path)
    (tmp_path / CONFIG_NAME).write_text('{"items": {}}')
    with pytest.raises(BackupError, match="is invalid"):
        BackupTarget.load(tmp_path)
    write_config(tmp_path, {})
    (tmp_path / STATE_NAME).write_text('{"items": {"A": {"version": "many"}}}')
    with pytest.raises(BackupError, match="is invalid"):
        BackupTarget.load(tmp_path)


def test_state_round_trip_keeps_the_file_format(tmp_path: Path) -> None:
    target = BackupTarget.load(write_config(tmp_path, {}))
    target.count_step_job("2026-10")
    target.item_state("A").version = 3
    target.save_state()
    assert json.loads((tmp_path / STATE_NAME).read_text()) == {"items": {"A": {"version": 3}}, "step_jobs": {"2026-10": 1}}
    again = BackupTarget.load(tmp_path)
    assert again.step_jobs("2026-10") == 1 and again.state.items["A"].version == 3


def test_status_lines(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("Bracket", 1, b"f3d")
    cloud.put("Ignored", 1, b"i")
    root = write_config(tmp_path / "repo", {"Bracket": {"f3d": "Bracket.f3d", "step": "Bracket.step"}}, ignore=["Ignored"])
    october = datetime(2026, 10, 15, tzinfo=UTC)
    before = status_lines(cloud, [BackupTarget.load(root)], now=lambda: october)
    assert "repo: nothing yet, STEP pending" in before[1] and "repo: ignored" in before[2]
    BackupRun(cloud, [BackupTarget.load(root)], now=lambda: october).execute()
    cloud.put("Other", 4, b"o")
    after = status_lines(cloud, [BackupTarget.load(root)], now=lambda: october)
    assert after[0] == "Hub / Project"
    assert "repo: v1, STEP ok" in after[1] and "UNMAPPED" in after[3]
    assert after[-1].endswith(": 1")
    with_limit = status_lines(cloud, [BackupTarget.load(root)], step_limit=30, now=lambda: october)
    assert with_limit[-1].endswith(": 1 of 30 (limit set by you)")

    (root / "Bracket.f3d").write_bytes(b"edited")
    (root / "Bracket.step").write_bytes(b"edited")
    edited = status_lines(cloud, [BackupTarget.load(root)], now=lambda: october)
    assert "repo: v1 (file on disk differs), STEP pending" in edited[1]

    (root / "Bracket.f3d").write_bytes(b"f3d")
    (root / "Bracket.step").unlink()
    cloud.put("Bracket", 2, b"f3d v2")
    changed = status_lines(cloud, [BackupTarget.load(root)], now=lambda: october)
    assert "repo: v1 (cloud has a newer or replaced version), STEP pending" in changed[1]


def test_status_of_an_unusable_mirrored_name(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("../escape", 1, b"x")
    root = write_config(tmp_path / "repo", {}, mirror={"directory": "."})
    target = BackupTarget.load(root)
    target.item_state("../escape").version = 1
    lines = status_lines(cloud, [target], now=lambda: datetime(2026, 10, 15, tzinfo=UTC))
    assert "repo: v1 (cloud has a newer or replaced version), STEP pending" in lines[1]
