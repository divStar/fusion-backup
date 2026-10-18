import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes import FakeCloud

from fusion_backup.backup import BackupRun, BackupTarget, ItemSelection, RunOptions, Summary
from fusion_backup.backup.config import CONFIG_NAME, STATE_NAME
from fusion_backup.backup.report import format_size
from fusion_backup.cloud import CloudError, StepUnavailableError


def october() -> datetime:
    return datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def make_target(root: Path, items: dict[str, dict[str, str]], **extra: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(json.dumps({"hub": "Hub", "project": "Project", "items": items, **extra}))
    return root


def run(cloud: FakeCloud, *roots: Path, dry_run: bool = False, steps: bool = True) -> Summary:
    targets = [BackupTarget.load(root) for root in roots]
    return BackupRun(cloud, targets, RunOptions(dry_run=dry_run, steps=steps), now=october).execute()


@pytest.fixture
def cloud() -> FakeCloud:
    fake = FakeCloud()
    fake.put("Bracket", 1, b"f3d-v1")
    return fake


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_target(tmp_path / "repo", {"Bracket": {"f3d": "cad/Bracket.f3d", "step": "cad/Bracket.step"}})


def test_first_run_writes_f3d_step_and_state(cloud: FakeCloud, root: Path) -> None:
    summary = run(cloud, root)
    assert (root / "cad/Bracket.f3d").read_bytes() == b"f3d-v1"
    assert (root / "cad/Bracket.step").read_bytes() == b"STEP:f3d-v1"
    assert summary.steps_generated == 1 and summary.step_jobs_this_month == 1
    state = json.loads((root / STATE_NAME).read_text())
    assert state["items"]["Bracket"]["version"] == 1
    assert state["items"]["Bracket"]["step_source_sha256"] == state["items"]["Bracket"]["sha256"]
    assert state["step_jobs"] == {"2026-10": 1}


def test_unchanged_version_downloads_nothing(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    summary = run(cloud, root)
    assert cloud.downloads == ["Bracket"] and cloud.step_requests == ["Bracket"]
    assert summary.written == [] and summary.steps_generated == 0


def test_new_version_with_same_bytes_needs_no_step(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    cloud.put("Bracket", 2, b"f3d-v1")
    summary = run(cloud, root)
    assert cloud.downloads == ["Bracket", "Bracket"] and cloud.step_requests == ["Bracket"]
    assert summary.written == []


def test_changed_f3d_triggers_step(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    cloud.put("Bracket", 2, b"f3d-v2")
    summary = run(cloud, root)
    assert (root / "cad/Bracket.step").read_bytes() == b"STEP:f3d-v2"
    assert summary.step_jobs_this_month == 2


def test_failed_step_keeps_f3d_and_retries_next_run(cloud: FakeCloud, root: Path) -> None:
    cloud.step_failures["Bracket"] = CloudError("STEP export ended with FAILED")
    summary = run(cloud, root)
    assert (root / "cad/Bracket.f3d").read_bytes() == b"f3d-v1"
    assert not (root / "cad/Bracket.step").exists()
    assert summary.steps_pending == ["repo/cad/Bracket.step (STEP export ended with FAILED)"]
    assert summary.step_jobs_this_month == 1

    del cloud.step_failures["Bracket"]
    summary = run(cloud, root)
    assert cloud.downloads == ["Bracket"]  # the .f3d was not fetched again
    assert (root / "cad/Bracket.step").read_bytes() == b"STEP:f3d-v1"
    assert summary.step_jobs_this_month == 2


def test_refusal_stops_further_step_requests(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("A", 1, b"a")
    cloud.put("B", 1, b"b")
    cloud.step_failures["A"] = StepUnavailableError("HTTP 403")
    root = make_target(tmp_path / "repo", {"A": {"f3d": "A.f3d", "step": "A.step"}, "B": {"f3d": "B.f3d", "step": "B.step"}})
    summary = run(cloud, root)
    assert cloud.step_requests == ["A"]
    assert summary.steps_pending == ["repo/A.step (refused)", "repo/B.step (refused earlier in this run)"]
    assert summary.step_jobs_this_month == 0 and (root / "B.f3d").exists()


def test_step_limit_keeps_further_steps_pending_until_next_month(tmp_path: Path) -> None:
    cloud = FakeCloud()
    for name in ("A", "B", "C"):
        cloud.put(name, 1, name.encode())
    root = make_target(tmp_path / "repo", {name: {"f3d": f"{name}.f3d", "step": f"{name}.step"} for name in ("A", "B", "C")})
    options = RunOptions(step_limit=2)

    summary = BackupRun(cloud, [BackupTarget.load(root)], options, now=october).execute()
    assert cloud.step_requests == ["A", "B"]
    assert summary.steps_pending == ["repo/C.step (monthly limit of 2 reached)"]
    assert (root / "C.f3d").exists()  # the .f3d is backed up regardless
    assert "2 of 2 (limit set by you)" in summary.render()

    def november() -> datetime:
        return datetime(2026, 11, 1, tzinfo=UTC)

    summary = BackupRun(cloud, [BackupTarget.load(root)], options, now=november).execute()
    assert cloud.step_requests == ["A", "B", "C"] and summary.steps_pending == []


def test_step_limit_zero_requests_no_step_and_no_limit_shows_no_assumed_allowance(cloud: FakeCloud, root: Path) -> None:
    summary = BackupRun(cloud, [BackupTarget.load(root)], RunOptions(step_limit=0), now=october).execute()
    assert cloud.step_requests == [] and len(summary.steps_pending) == 1
    summary = run(cloud, root)
    assert summary.render().splitlines()[2].endswith(": 1")


def test_replaced_cloud_item_with_the_same_name_and_version_is_downloaded(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    cloud.put("Bracket", 1, b"new design", item_id="id:replacement")  # deleted and re-created: version 1 again
    run(cloud, root)
    assert (root / "cad/Bracket.f3d").read_bytes() == b"new design"
    assert (root / "cad/Bracket.step").read_bytes() == b"STEP:new design"


def test_step_job_counts_when_submitted_not_when_it_fails_before(cloud: FakeCloud, root: Path) -> None:
    cloud.failures_before_submit["Bracket"] = CloudError("hub not found")
    summary = run(cloud, root)
    assert summary.step_jobs_this_month == 0 and summary.steps_pending == ["repo/cad/Bracket.step (hub not found)"]
    cloud.failures_before_submit.clear()
    cloud.step_failures["Bracket"] = CloudError("STEP export ended with FAILED")
    summary = run(cloud, root)
    assert summary.step_jobs_this_month == 1  # accepted by the cloud, so it counts although it failed


def test_failed_step_is_requested_once_per_run_for_all_targets(tmp_path: Path, cloud: FakeCloud) -> None:
    cloud.step_failures["Bracket"] = CloudError("boom")
    first = make_target(tmp_path / "one", {"Bracket": {"f3d": "Bracket.f3d", "step": "Bracket.step"}})
    second = make_target(tmp_path / "two", {"Bracket": {"f3d": "Bracket.f3d", "step": "Bracket.step"}})
    summary = run(cloud, first, second)
    assert cloud.step_requests == ["Bracket"]
    assert summary.steps_pending == ["one/Bracket.step (boom)", "two/Bracket.step (boom)"]
    assert summary.step_jobs_this_month == 1


def test_file_system_errors_fail_one_item_and_the_run_goes_on(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("A", 1, b"a")
    cloud.put("B", 1, b"b")
    root = make_target(tmp_path / "repo", {"A": {"f3d": "blocked/A.f3d"}, "B": {"f3d": "B.f3d"}})
    (root / "blocked").write_text("a file where a folder is expected")
    summary = run(cloud, root)
    assert len(summary.errors) == 1 and summary.errors[0].startswith("A: ")
    assert (root / "B.f3d").read_bytes() == b"b"
    assert json.loads((root / STATE_NAME).read_text())["items"]["B"]["version"] == 1


def test_mirrored_names_that_differ_only_in_case_do_not_overwrite_each_other(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("Part", 1, b"upper")
    cloud.put("part", 1, b"lower")
    root = make_target(tmp_path / "repo", {}, mirror={"directory": ".", "step": False})
    summary = run(cloud, root)
    assert len(summary.errors) == 1 and "written by both 'Part (f3d)' and 'part (f3d)'" in summary.errors[0]


def test_downloads_come_first_then_steps_each_counted_separately(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cloud = FakeCloud()
    cloud.put("A", 1, b"a" * 1500)
    cloud.put("B", 1, b"b")
    cloud.put("drawing.dwg", 1, b"d", design=False)
    items = {"A": {"f3d": "A.f3d", "step": "A.step"}, "B": {"f3d": "B.f3d"}, "drawing.dwg": {"file": "drawing.dwg"}}
    root = make_target(tmp_path / "repo", items)
    caplog.set_level(logging.INFO, logger="fusion_backup")
    run(cloud, root)
    progress = [record.getMessage() for record in caplog.records if record.getMessage().startswith("[")]
    assert progress == [
        "[1/3] Downloading A v1 (.f3d)",
        "[2/3] Downloading B v1 (.f3d)",
        "[3/3] Downloading drawing.dwg v1 (file)",
        "[1/1] Generating STEP of A v1",
    ]
    assert any(record.getMessage().startswith("      1.5 KB in ") for record in caplog.records)


def test_failed_download_skips_the_item_and_continues(cloud: FakeCloud, root: Path, tmp_path: Path) -> None:
    cloud.put("Other", 1, b"o")
    other = make_target(tmp_path / "other", {"Other": {"f3d": "Other.f3d"}})
    cloud.download_failures["Bracket"] = CloudError("HTTP 500")
    summary = run(cloud, root, other)
    assert summary.errors == ["Bracket: HTTP 500"] and cloud.step_requests == []
    assert (other / "Other.f3d").exists()


def test_item_already_present_in_one_of_two_targets(tmp_path: Path, cloud: FakeCloud) -> None:
    first = make_target(tmp_path / "one", {"Bracket": {"f3d": "Bracket.f3d"}})
    run(cloud, first)
    second = make_target(tmp_path / "two", {"Bracket": {"f3d": "Bracket.f3d"}})
    summary = run(cloud, first, second)
    assert cloud.downloads == ["Bracket", "Bracket"]
    assert summary.written == ["two/Bracket.f3d (v1)"]


def test_unwritable_step_and_state_are_reported(cloud: FakeCloud, root: Path) -> None:
    (root / "cad/Bracket.step").mkdir(parents=True)  # a folder where the STEP file should go
    (root / STATE_NAME).mkdir()  # and one where the state file should go
    summary = run(cloud, root)
    assert (root / "cad/Bracket.f3d").exists()
    assert any(error.startswith("Bracket: ") for error in summary.errors)
    assert any("cannot save the state" in error for error in summary.errors)


@pytest.mark.parametrize(
    ("size", "text"), [(0, "0 B"), (999, "999 B"), (1500, "1.5 KB"), (6_600_000, "6.6 MB"), (2_500_000_000_000, "2500.0 GB")]
)
def test_format_size(size: int, text: str) -> None:
    assert format_size(size) == text


def test_selected_items_only(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("Chassis bracket", 1, b"c")
    cloud.put("Parts/Screw M3", 1, b"s")
    cloud.put("Parts/Nut M3", 1, b"n")
    cloud.put("Unmapped", 1, b"u")
    items = {"Chassis bracket": {"f3d": "c.f3d", "step": "c.step"}, "Parts/Screw M3": {"f3d": "s.f3d"}, "Parts/Nut M3": {"f3d": "n.f3d"}}
    root = make_target(tmp_path / "repo", items)
    selection = ItemSelection(("Chassis bracket", "Parts/S*"))
    summary = BackupRun(cloud, [BackupTarget.load(root)], RunOptions(selection=selection), now=october).execute()
    assert sorted(cloud.downloads) == ["Chassis bracket", "Parts/Screw M3"] and cloud.step_requests == ["Chassis bracket"]
    assert (root / "c.step").exists() and not (root / "n.f3d").exists()
    assert summary.checked == 2 and summary.unmapped == [] and summary.errors == []


def test_a_selection_that_matches_nothing_is_an_error(cloud: FakeCloud, root: Path) -> None:
    selection = ItemSelection(("Bracket", "chassis*"))
    summary = BackupRun(cloud, [BackupTarget.load(root)], RunOptions(selection=selection), now=october).execute()
    assert summary.errors == ["No cloud item matches 'chassis*'"]
    assert cloud.downloads == ["Bracket"]


def test_missing_step_file_is_regenerated(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    (root / "cad/Bracket.step").unlink()
    run(cloud, root)
    assert cloud.step_requests == ["Bracket", "Bracket"]


def test_changed_step_file_is_regenerated_without_downloading_the_f3d(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    (root / "cad/Bracket.step").write_bytes(b"edited by hand")
    run(cloud, root)
    assert cloud.downloads == ["Bracket"] and cloud.step_requests == ["Bracket", "Bracket"]
    assert (root / "cad/Bracket.step").read_bytes() == b"STEP:f3d-v1"


def test_changed_f3d_on_disk_is_restored_without_a_new_step(cloud: FakeCloud, root: Path) -> None:
    run(cloud, root)
    (root / "cad/Bracket.f3d").write_bytes(b"edited by hand")
    summary = run(cloud, root)
    assert cloud.downloads == ["Bracket", "Bracket"] and cloud.step_requests == ["Bracket"]
    assert (root / "cad/Bracket.f3d").read_bytes() == b"f3d-v1"
    assert summary.written == ["repo/cad/Bracket.f3d (v1)"]


def test_dry_run_writes_nothing_and_requests_no_step(cloud: FakeCloud, root: Path) -> None:
    summary = run(cloud, root, dry_run=True)
    assert not (root / "cad").exists() and not (root / STATE_NAME).exists()
    assert cloud.step_requests == []
    assert summary.written == ["repo/cad/Bracket.f3d (v1)"]
    assert summary.steps_pending == ["repo/cad/Bracket.step (would be generated)"]
    assert "(dry run)" in summary.render() and "would write" in summary.render()


def test_no_step_skips_check_and_generation(cloud: FakeCloud, root: Path) -> None:
    summary = run(cloud, root, steps=False)
    assert (root / "cad/Bracket.f3d").exists() and not (root / "cad/Bracket.step").exists()
    assert cloud.step_requests == [] and summary.steps_pending == []


def test_plain_files_unmapped_and_ignored_items(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("drawing.dwg", 3, b"dwg", design=False)
    cloud.put("Unmapped part", 1, b"x")
    cloud.put("Ignored part", 1, b"y")
    root = make_target(tmp_path / "repo", {"drawing.dwg": {"file": "drawings/drawing.dwg"}}, ignore=["Ignored part"])
    summary = run(cloud, root)
    assert (root / "drawings/drawing.dwg").read_bytes() == b"dwg"
    assert summary.unmapped == ["Project/Unmapped part"]
    assert cloud.step_requests == []
    assert "Unmapped cloud items" in summary.render()


def test_ignore_everything_but_the_explicit_mappings(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("Board", 1, b"b")
    cloud.put("Bracket", 1, b"c")
    root = make_target(tmp_path / "repo", {"Board": {"f3d": "board.f3d"}}, ignore=["*"])
    summary = run(cloud, root)
    assert cloud.downloads == ["Board"] and (root / "board.f3d").read_bytes() == b"b"
    assert summary.unmapped == []


def test_item_mapped_in_two_targets_is_fetched_once(tmp_path: Path, cloud: FakeCloud) -> None:
    first = make_target(tmp_path / "one", {"Bracket": {"f3d": "Bracket.f3d", "step": "Bracket.step"}})
    second = make_target(tmp_path / "two", {"Bracket": {"f3d": "x/Bracket.f3d", "step": "x/Bracket.step"}})
    summary = run(cloud, first, second)
    assert cloud.downloads == ["Bracket"] and cloud.step_requests == ["Bracket"]
    assert (second / "x/Bracket.step").read_bytes() == b"STEP:f3d-v1"
    assert summary.step_jobs_this_month == 1


def test_mirror_keeps_folders_and_explicit_mappings_win(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("Bracket", 1, b"bracket")
    cloud.put("Parts/Screw", 2, b"screw")
    cloud.put("Docs/manual.pdf", 1, b"pdf", design=False)
    cloud.put("Old", 1, b"old")
    root = make_target(tmp_path / "repo", {"Bracket": {"f3d": "special/Bracket.f3d"}}, ignore=["Old"], mirror={"directory": "fusion"})
    summary = run(cloud, root)
    assert (root / "special/Bracket.f3d").read_bytes() == b"bracket"
    assert not (root / "special/Bracket.step").exists()
    assert (root / "fusion/Parts/Screw.f3d").read_bytes() == b"screw"
    assert (root / "fusion/Parts/Screw.step").read_bytes() == b"STEP:screw"
    assert (root / "fusion/Docs/manual.pdf").read_bytes() == b"pdf"
    assert not (root / "fusion/Old.f3d").exists() and summary.unmapped == []


def test_mirror_into_directory_without_config(tmp_path: Path, cloud: FakeCloud) -> None:
    out = tmp_path / "out"
    BackupRun(cloud, [BackupTarget.mirror_into(out, "Hub", "Project", step=False)], RunOptions(dry_run=True), now=october).execute()
    assert not out.exists()  # a dry run creates nothing
    summary = BackupRun(cloud, [BackupTarget.mirror_into(out, "Hub", "Project", step=False)], now=october).execute()
    assert (out / "Bracket.f3d").read_bytes() == b"f3d-v1" and not (out / "Bracket.step").exists()
    assert (out / STATE_NAME).is_file() and summary.steps_generated == 0


def test_mirror_reports_clean_paths(tmp_path: Path, cloud: FakeCloud) -> None:
    summary = BackupRun(cloud, [BackupTarget.mirror_into(tmp_path / "out", "Hub", "Project", step=True)], now=october).execute()
    assert summary.written == ["out/Bracket.f3d (v1)", "out/Bracket.step (STEP of v1)"]


def test_item_path_escaping_the_target_is_an_error(tmp_path: Path) -> None:
    cloud = FakeCloud()
    cloud.put("../escape", 1, b"x")
    root = make_target(tmp_path / "repo", {}, mirror={"directory": "."})
    summary = run(cloud, root)
    assert summary.errors and "outside" in summary.errors[0]
    assert not (tmp_path / "escape.f3d").exists()


def test_existing_file_permissions_are_kept(cloud: FakeCloud, root: Path) -> None:
    target = root / "cad/Bracket.f3d"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    target.chmod(0o640)
    run(cloud, root)
    assert target.stat().st_mode & 0o777 == 0o640
    assert (root / "cad/Bracket.step").stat().st_mode & 0o777 == 0o644
