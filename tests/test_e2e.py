"""End-to-end: the real CLI, backup and Autodesk implementation, with the robot's credentials read from the
environment and only Autodesk's HTTP answers scripted. A request the script doesn't expect fails the test,
so these tests also prove which calls are (not) made."""

import json
from pathlib import Path
from typing import cast

import httpx2
import pytest
from fakes import API, MFG_V2, MFG_V3, MfgScript, ScriptedTransport, robot_env, token_answer
from typer.testing import CliRunner

from fusion_backup import aps, cli
from fusion_backup.aps.auth import TOKEN_URL, SsaCredentials, connect
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.source import DESIGN_ITEM_TYPE, ApsCloudSource
from fusion_backup.backup.config import CONFIG_NAME, STATE_NAME

CONTENTS = f"{API}/data/v1/projects/p1/folders/urn%3Aroot/contents"


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> ScriptedTransport:
    scripted = ScriptedTransport()

    def open_source(credentials: SsaCredentials, mfg_api: aps.MfgApiVersion) -> ApsCloudSource:
        return ApsCloudSource(ApsClient(connect(credentials, transport=scripted)), mfg_api=mfg_api, sleep=lambda _: None)

    monkeypatch.setattr(aps, "open_source", open_source)
    for name, value in robot_env().items():
        monkeypatch.setenv(name, value)
    return scripted


def script_listing(transport: ScriptedTransport, design_version: int) -> None:
    transport.add("POST", TOKEN_URL, token_answer(design_version))  # every CLI run logs the robot in once
    transport.add("GET", f"{API}/project/v1/hubs", {"data": [{"id": "h1", "attributes": {"name": "Hub"}}]})
    transport.add("GET", f"{API}/project/v1/hubs/h1/projects", {"data": [{"id": "p1", "attributes": {"name": "Project"}}]})
    transport.add("GET", f"{API}/project/v1/hubs/h1/projects/p1", {"data": {"relationships": {"rootFolder": {"data": {"id": "urn:root"}}}}})
    transport.add(
        "GET",
        CONTENTS,
        {
            "data": [
                {
                    "type": "items",
                    "id": "urn:bracket",
                    "attributes": {"displayName": "Bracket", "extension": {"type": DESIGN_ITEM_TYPE}},
                    "relationships": {"tip": {"data": {"id": "vb"}}},
                },
                {
                    "type": "items",
                    "id": "urn:dwg",
                    "attributes": {"displayName": "drawing.dwg", "extension": {"type": "items:autodesk.core:File"}},
                    "relationships": {"tip": {"data": {"id": "vd"}}},
                },
            ],
            "included": [
                {
                    "type": "versions",
                    "id": "vb",
                    "attributes": {"versionNumber": design_version},
                    "relationships": {"storage": {"data": {"id": f"urn:adsk.objects:os.object:b/bracket-{design_version}.f3d"}}},
                },
                {
                    "type": "versions",
                    "id": "vd",
                    "attributes": {"versionNumber": 1},
                    "relationships": {"storage": {"data": {"id": "urn:adsk.objects:os.object:b/drawing.dwg"}}},
                },
            ],
        },
    )


def script_stored_file(transport: ScriptedTransport, name: str, content: bytes) -> None:
    transport.add("GET", f"{API}/oss/v2/buckets/b/objects/{name}/signeds3download", {"url": f"https://s3.example/{name}?sig=x"})
    transport.add("GET", f"https://s3.example/{name}", content)


def script_step_export(transport: ScriptedTransport, version: int, content: bytes, mfg: MfgScript) -> None:
    transport.add("POST", mfg.endpoint, mfg.hubs_answer())
    transport.add("POST", mfg.endpoint, mfg.step_answer("SUCCESS", f"https://cdn.example/{version}.stp?sig=x"))
    tip = {"type": "versions", "id": "vb", "attributes": {"versionNumber": version}}
    transport.add("GET", f"{API}/data/v1/projects/p1/items/urn%3Abracket/tip", {"data": tip})
    transport.add("GET", f"https://cdn.example/{version}.stp", content)


def make_repo(root: Path) -> Path:
    root.mkdir()
    (root / CONFIG_NAME).write_text(
        json.dumps(
            {
                "hub": "Hub",
                "project": "Project",
                "items": {
                    "Bracket": {"f3d": "cad/Bracket.f3d", "step": "cad/Bracket.step"},
                    "drawing.dwg": {"file": "drawings/drawing.dwg"},
                },
            }
        )
    )
    return root


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, list(args))
    return result.exit_code, result.output


@pytest.fixture(
    params=[
        pytest.param((None, MFG_V2), id="no-switch-v2"),
        pytest.param(("v2", MFG_V2), id="switch-v2"),
        pytest.param(("v3", MFG_V3), id="switch-v3"),
    ]
)
def mfg(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> MfgScript:
    """Every test runs without the switch (the default, v2) and with the switch set to each version through
    the environment, as a CI workflow would set it. Returns the version Autodesk is expected to be asked."""
    switch, expected = cast(tuple[str | None, MfgScript], request.param)
    monkeypatch.delenv("FUSION_BACKUP_MFG_API", raising=False)
    if switch:
        monkeypatch.setenv("FUSION_BACKUP_MFG_API", switch)
    return expected


def graphql_endpoints(transport: ScriptedTransport) -> set[str]:
    """The GraphQL endpoints the runs actually called."""
    return {str(request.url) for request in transport.requests if request.url.path.endswith(("/graphql", "/graphql/public"))}


def test_backup_lifecycle(transport: ScriptedTransport, mfg: MfgScript, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")

    # 1st run: everything is new
    script_listing(transport, design_version=1)
    script_stored_file(transport, "bracket-1.f3d", b"f3d-v1")
    script_stored_file(transport, "drawing.dwg", b"dwg")
    script_step_export(transport, 1, b"ISO-10303-21; v1", mfg)
    code, output = run("run", "--repo", str(repo))
    assert code == 0, output
    assert (repo / "cad/Bracket.f3d").read_bytes() == b"f3d-v1"
    assert (repo / "cad/Bracket.step").read_bytes() == b"ISO-10303-21; v1"
    assert (repo / "drawings/drawing.dwg").read_bytes() == b"dwg"
    assert "standard jobs, counted by fusion-backup): 1\n" in output  # no limit given, none assumed
    assert transport.routes == []  # every scripted answer was used
    downloads = [request for request in transport.requests if request.url.host in ("s3.example", "cdn.example")]
    assert downloads and all("Authorization" not in request.headers for request in downloads)

    # 2nd run, nothing changed in the cloud: listing only, no download, no STEP job
    script_listing(transport, design_version=1)
    code, output = run("run", "--repo", str(repo))
    assert code == 0, output
    assert "files written: 0, STEPs generated: 0" in output

    # 3rd run, a new version of the design: new .f3d and a new STEP, the drawing is left alone
    script_listing(transport, design_version=2)
    script_stored_file(transport, "bracket-2.f3d", b"f3d-v2")
    script_step_export(transport, 2, b"ISO-10303-21; v2", mfg)
    code, output = run("run", "--repo", str(repo))
    assert code == 0, output
    assert (repo / "cad/Bracket.step").read_bytes() == b"ISO-10303-21; v2"
    state = json.loads((repo / STATE_NAME).read_text())
    assert state["items"]["Bracket"]["version"] == 2 and state["step_jobs"] == {next(iter(state["step_jobs"])): 2}
    assert transport.routes == []
    assert graphql_endpoints(transport) == {mfg.endpoint}  # only the chosen version was asked


def test_refused_step_keeps_the_f3d_and_is_retried(transport: ScriptedTransport, mfg: MfgScript, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    script_listing(transport, design_version=1)
    script_stored_file(transport, "bracket-1.f3d", b"f3d-v1")
    transport.add("POST", mfg.endpoint, mfg.hubs_answer())
    transport.add("POST", mfg.endpoint, httpx2.Response(429, json={"detail": "monthly limit reached"}))
    script_stored_file(transport, "drawing.dwg", b"dwg")
    code, output = run("run", "--repo", str(repo))
    assert code == 0, output  # a pending STEP is not a failure
    assert (repo / "cad/Bracket.f3d").exists() and not (repo / "cad/Bracket.step").exists()
    assert "STEPs pending (retried next run):" in output and "(refused)" in output

    # next run: the .f3d is up to date, only the STEP is requested again
    script_listing(transport, design_version=1)
    script_step_export(transport, 1, b"ISO-10303-21; v1", mfg)
    code, output = run("run", "--repo", str(repo))
    assert code == 0, output
    assert (repo / "cad/Bracket.step").read_bytes() == b"ISO-10303-21; v1"
    assert transport.routes == []
    assert graphql_endpoints(transport) == {mfg.endpoint}


def test_dry_run_downloads_but_writes_nothing_and_requests_no_step(transport: ScriptedTransport, mfg: MfgScript, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    script_listing(transport, design_version=1)
    script_stored_file(transport, "bracket-1.f3d", b"f3d-v1")
    script_stored_file(transport, "drawing.dwg", b"dwg")
    code, output = run("run", "--repo", str(repo), "--dry-run")
    assert code == 0, output
    assert sorted(path.name for path in repo.iterdir()) == [CONFIG_NAME]
    assert "(would be generated)" in output and transport.routes == []
    assert graphql_endpoints(transport) == set(), f"no {mfg.version} request expected"
