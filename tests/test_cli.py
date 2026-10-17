import json
from pathlib import Path

import pytest
from fakes import FakeCloud, robot_env
from typer.testing import CliRunner

from fusion_backup import aps, cli
from fusion_backup.aps import SsaCredentials
from fusion_backup.backup.config import CONFIG_NAME

runner = CliRunner()


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    fake.put("Bracket", 18, b"f3d")
    fake.put("drawing.dwg", 3, b"dwg", design=False)
    for name, value in robot_env().items():
        monkeypatch.setenv(name, value)
    received: list[SsaCredentials] = []

    def open_source(credentials: SsaCredentials, mfg_api: aps.MfgApiVersion) -> FakeCloud:
        received.append(credentials)
        fake.mfg_api = mfg_api
        return fake

    monkeypatch.setattr(aps, "open_source", open_source)
    return fake


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.output


def make_repo(root: Path, project: str = "Project", items: dict[str, dict[str, str]] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    mapping = items if items is not None else {"Bracket": {"f3d": "Bracket.f3d"}}
    (root / CONFIG_NAME).write_text(json.dumps({"hub": "Hub", "project": project, "items": mapping}))
    return root


def test_check_lists_projects_and_verifies_repositories(cloud: FakeCloud, tmp_path: Path) -> None:
    code, output = invoke("check", "--repo", str(make_repo(tmp_path / "ok")))
    assert code == 0 and "Robot login works" in output and "Hub / Project" in output
    code, output = invoke("check", "--repo", str(make_repo(tmp_path / "other", project="Elsewhere")))
    assert code == 1 and "Not visible to the robot: other" in output


def test_list(cloud: FakeCloud) -> None:
    assert invoke("list") == (0, "Hub / Project\n")
    code, output = invoke("list", "--hub", "Hub", "--project", "Project")
    assert code == 0 and output.splitlines() == ["v18   design  Bracket", "v3    file    drawing.dwg"]
    code, output = invoke("list", "--hub", "Hub")
    assert code == 1 and "both --hub and --project" in output


def test_run_and_status_with_a_repository(cloud: FakeCloud, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    summary = tmp_path / "summary.txt"
    code, output = invoke("run", "--repo", str(repo), "--summary-file", str(summary))
    assert code == 0 and (repo / "Bracket.f3d").read_bytes() == b"f3d"
    assert "Unmapped cloud items" in output and summary.read_text().startswith("Fusion backup")
    code, output = invoke("status", "--repo", str(repo))
    assert code == 0 and "repo: v18" in output


def test_run_and_status_for_a_single_item(cloud: FakeCloud, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code, output = invoke("run", "--out", str(out), "--hub", "Hub", "--project", "Project", "--item", "Bracket")
    assert code == 0, output
    assert sorted(path.name for path in out.iterdir() if not path.name.startswith(".")) == ["Bracket.f3d", "Bracket.step"]
    code, output = invoke("status", "--out", str(out), "--hub", "Hub", "--project", "Project", "--item", "Bracket")
    assert code == 0 and "Bracket" in output and "drawing.dwg" not in output
    code, output = invoke("status", "--out", str(out), "--hub", "Hub", "--project", "Project", "--item", "nope")
    assert code == 1 and "No cloud item matches 'nope'" in output


def test_run_exports_steps_through_v2_unless_v3_is_chosen(cloud: FakeCloud, tmp_path: Path) -> None:
    args = ("run", "--out", str(tmp_path / "out"), "--hub", "Hub", "--project", "Project", "--dry-run")
    assert invoke(*args)[0] == 0 and cloud.mfg_api == "v2"
    assert invoke(*args, "--mfg-api", "v3")[0] == 0 and cloud.mfg_api == "v3"
    code, output = invoke(*args, "--mfg-api", "v4")
    assert code == 2 and "v4" in output


def test_run_mirrors_into_a_directory(cloud: FakeCloud, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code, _ = invoke("run", "--out", str(out), "--hub", "Hub", "--project", "Project", "--no-step")
    assert code == 0 and (out / "Bracket.f3d").exists() and (out / "drawing.dwg").exists()
    assert not (out / "Bracket.step").exists()


def test_run_exits_with_1_when_an_item_failed_but_backs_up_the_others(cloud: FakeCloud, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", items={"Bracket": {"f3d": "blocked/Bracket.f3d"}, "drawing.dwg": {"file": "drawing.dwg"}})
    (repo / "blocked").write_text("a file where a folder is expected")
    code, output = invoke("run", "--repo", str(repo))
    assert code == 1 and "Bracket:" in output  # writing below a file fails for this item only
    assert (repo / "drawing.dwg").read_bytes() == b"dwg"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["run"], "Pass --repo"),
        (["run", "--out", "x"], "--out needs --hub and --project"),
        (["status", "--repo", "/nonexistent"], "not found"),
        (["run", "--repo", "{repo}", "--step-limit", "-1"], "-1"),
    ],
)
def test_usage_errors(cloud: FakeCloud, tmp_path: Path, args: list[str], message: str) -> None:
    repo = make_repo(tmp_path / "repo")
    code, output = invoke(*(arg.replace("{repo}", str(repo)) for arg in args))
    assert code != 0 and message in output


def test_missing_robot_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in robot_env():
        monkeypatch.delenv(name, raising=False)
    code, output = invoke("list")
    assert code == 1 and "Missing environment variables: APS_CLIENT_ID" in output
