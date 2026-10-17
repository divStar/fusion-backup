from collections.abc import Callable
from typing import Any, cast

import httpx2
import pytest
from fakes import API, MFG_APIS, MFG_V2, MfgScript, ScriptedTransport, aps_client

from fusion_backup.aps.source import DESIGN_ITEM_TYPE, ApsCloudSource
from fusion_backup.cloud import CloudError, CloudItem, CloudProject, StepUnavailableError

HUB_ID, PROJECT_ID = "a.hub", "a.project"
STORAGE = "urn:adsk.objects:os.object:bucket/obj.f3d"
ITEM = CloudItem("Hub", "Project", "Bracket", "urn:item:1", True, 7)


def make_source(transport: ScriptedTransport, **kwargs: Any) -> ApsCloudSource:
    kwargs.setdefault("sleep", lambda _: None)
    return ApsCloudSource(aps_client(transport), **kwargs)


class Submissions:
    """Records calls of the `on_submitted` callback."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def entry(item_id: str, name: str, item_type: str, version_id: str) -> dict[str, Any]:
    return {
        "type": "items",
        "id": item_id,
        "attributes": {"displayName": name, "extension": {"type": item_type}},
        "relationships": {"tip": {"data": {"id": version_id}}},
    }


def version(version_id: str, number: int, storage: str) -> dict[str, Any]:
    return {
        "type": "versions",
        "id": version_id,
        "attributes": {"versionNumber": number},
        "relationships": {"storage": {"data": {"id": storage}}},
    }


def add_project_lookup(transport: ScriptedTransport) -> None:
    transport.add("GET", f"{API}/project/v1/hubs", {"data": [{"id": HUB_ID, "attributes": {"name": "Hub"}}]})
    transport.add("GET", f"{API}/project/v1/hubs/{HUB_ID}/projects", {"data": [{"id": PROJECT_ID, "attributes": {"name": "Project"}}]})


def add_listing(transport: ScriptedTransport) -> None:
    add_project_lookup(transport)
    transport.add(
        "GET",
        f"{API}/project/v1/hubs/{HUB_ID}/projects/{PROJECT_ID}",
        {"data": {"relationships": {"rootFolder": {"data": {"id": "urn:root"}}}}},
    )
    transport.add(
        "GET",
        f"{API}/data/v1/projects/{PROJECT_ID}/folders/urn%3Aroot/contents",
        {
            "data": [
                entry("urn:item:1", "Bracket", DESIGN_ITEM_TYPE, "v1"),
                {"type": "folders", "id": "urn:sub", "attributes": {"displayName": "Parts"}},
            ],
            "included": [version("v1", 7, STORAGE)],
            "links": {"next": {"href": f"{API}/page2"}},
        },
    )
    transport.add(
        "GET",
        f"{API}/page2",
        {
            "data": [
                entry("urn:item:2", "drawing.dwg", "items:autodesk.core:File", "v2"),
                entry("urn:item:3", "broken", DESIGN_ITEM_TYPE, "missing"),
            ],
            "included": [version("v2", 3, "urn:adsk.objects:os.object:bucket/2.dwg")],
        },
    )
    transport.add(
        "GET",
        f"{API}/data/v1/projects/{PROJECT_ID}/folders/urn%3Asub/contents",
        {
            "data": [entry("urn:item:4", "Screw", DESIGN_ITEM_TYPE, "v4")],
            "included": [version("v4", 1, "urn:adsk.objects:os.object:bucket/4.f3d")],
        },
    )


def test_list_projects() -> None:
    transport = ScriptedTransport()
    add_project_lookup(transport)
    assert make_source(transport).list_projects() == [CloudProject("Hub", "Project")]


def test_list_items_follows_pages_and_subfolders_and_skips_items_without_a_version() -> None:
    transport = ScriptedTransport()
    add_listing(transport)
    items = make_source(transport).list_items("Hub", "Project")
    assert [(i.path, i.version, i.is_design) for i in items] == [("Bracket", 7, True), ("drawing.dwg", 3, False), ("Parts/Screw", 1, True)]


def test_unknown_hub_and_project_are_reported() -> None:
    transport = ScriptedTransport().add("GET", f"{API}/project/v1/hubs", {"data": [{"id": HUB_ID, "attributes": {"name": "Other"}}]})
    with pytest.raises(CloudError, match="Hub 'Hub' not found; visible hubs: Other"):
        make_source(transport).list_items("Hub", "Project")
    transport = ScriptedTransport()
    add_project_lookup(transport)
    with pytest.raises(CloudError, match="Project 'Nope' not found"):
        make_source(transport).list_items("Hub", "Nope")


def test_download_uses_the_signed_url_without_any_credentials() -> None:
    transport = ScriptedTransport()
    add_listing(transport)
    transport.add("GET", f"{API}/oss/v2/buckets/bucket/objects/obj.f3d/signeds3download", {"url": "https://s3.example/obj?sig=1"})
    transport.add("GET", "https://s3.example/obj", b"f3d-bytes")
    source = make_source(transport)
    source.list_items("Hub", "Project")
    assert source.download(ITEM) == b"f3d-bytes"
    assert "Authorization" not in transport.requests[-1].headers  # neither the bearer token nor the app's Basic auth


def test_failed_signed_download_does_not_reveal_the_signature() -> None:
    transport = ScriptedTransport()
    add_listing(transport)
    transport.add("GET", f"{API}/oss/v2/buckets/bucket/objects/obj.f3d/signeds3download", {"url": "https://s3.example/obj?sig=SECRET"})
    transport.add(
        "GET", "https://s3.example/obj", httpx2.Response(403, text="<Error><SignatureProvided>SECRET</SignatureProvided></Error>")
    )
    source = make_source(transport)
    source.list_items("Hub", "Project")
    with pytest.raises(CloudError) as caught:
        source.download(ITEM)
    assert "SECRET" not in str(caught.value) and "HTTP 403 for https://s3.example/obj" in str(caught.value)


def test_unexpected_answers_do_not_reveal_their_values() -> None:
    transport = ScriptedTransport()
    add_listing(transport)
    transport.add("GET", f"{API}/oss/v2/buckets/bucket/objects/obj.f3d/signeds3download", {"url": ["https://s3.example/obj?sig=SECRET"]})
    source = make_source(transport)
    source.list_items("Hub", "Project")
    with pytest.raises(CloudError, match=r"Unexpected answer from Autodesk \(SignedDownload\): url: ") as caught:
        source.download(ITEM)
    assert "SECRET" not in str(caught.value)


def test_download_needs_a_listing_first() -> None:
    with pytest.raises(CloudError, match="list the project first"):
        make_source(ScriptedTransport()).download(ITEM)


@pytest.fixture(params=MFG_APIS, ids=lambda mfg: mfg.version.value)
def mfg(request: pytest.FixtureRequest) -> MfgScript:
    """Every STEP test runs through the switch once per Manufacturing Data Model API version."""
    return cast(MfgScript, request.param)


def step_source(mfg: MfgScript, transport: ScriptedTransport, **kwargs: Any) -> ApsCloudSource:
    """A source switched to `mfg`'s version, with that version's hub lookup scripted first."""
    transport.routes.insert(0, ("POST", mfg.endpoint, mfg.hubs_answer()))
    return make_source(transport, mfg_api=mfg.version, **kwargs)


def add_tip(transport: ScriptedTransport, number: int) -> None:
    """The project lookup and the Data Management tip version, checked before a finished STEP is downloaded."""
    add_project_lookup(transport)
    tip = {"type": "versions", "id": "v1", "attributes": {"versionNumber": number}}
    transport.add("GET", f"{API}/data/v1/projects/{PROJECT_ID}/items/urn%3Aitem%3A1/tip", {"data": tip})


def test_generate_step_requests_once_then_only_polls(mfg: MfgScript) -> None:
    transport = ScriptedTransport()
    for answer in (mfg.step_answer(None), mfg.step_answer("IN_PROGRESS"), mfg.step_answer("SUCCESS", "https://cdn.example/x.stp?sig=1")):
        transport.add("POST", mfg.endpoint, answer)
    add_tip(transport, ITEM.version)
    transport.add("GET", "https://cdn.example/x.stp", b"ISO-10303-21;")
    submitted = Submissions()

    assert step_source(mfg, transport).generate_step(ITEM, submitted) == b"ISO-10303-21;"
    bodies = transport.json_bodies(mfg.endpoint)[1:]
    assert [body["variables"]["generate"] for body in bodies] == [True, False, False]
    assert bodies[0]["variables"]["hubId"] == "urn:mfg:hub" and bodies[0]["variables"]["itemId"] == "urn:item:1"
    assert mfg.format_argument in bodies[0]["query"]
    assert submitted.count == 1
    assert "Authorization" not in transport.requests[-1].headers


def test_steps_are_exported_through_v2_without_the_switch() -> None:
    transport = ScriptedTransport().add("POST", MFG_V2.endpoint, MFG_V2.hubs_answer())
    transport.add("POST", MFG_V2.endpoint, MFG_V2.step_answer("SUCCESS", "https://cdn.example/x.stp?sig=1"))
    add_tip(transport, ITEM.version)
    transport.add("GET", "https://cdn.example/x.stp", b"ISO-10303-21;")
    assert make_source(transport).generate_step(ITEM, Submissions()) == b"ISO-10303-21;"
    assert transport.routes == []


def test_refused_step_request_is_not_a_submitted_job(mfg: MfgScript) -> None:
    transport = ScriptedTransport().add("POST", mfg.endpoint, httpx2.Response(403, json={"detail": "quota"}))
    submitted = Submissions()
    with pytest.raises(StepUnavailableError, match="HTTP 403"):
        step_source(mfg, transport).generate_step(ITEM, submitted)
    assert submitted.count == 0


@pytest.mark.parametrize(
    ("answer", "message", "submitted_jobs"),
    [
        (lambda mfg: mfg.step_answer("FAILED"), "ended with FAILED", 1),
        (lambda mfg: mfg.step_answer("SUCCESS"), "without a download URL", 1),
        (lambda _: {"data": {"item": None}}, "not available in the Manufacturing Data Model API", 0),
        (lambda _: {"errors": [{"message": "nope"}]}, "GraphQL error: nope", 0),
        (lambda mfg: {"data": {"item": {mfg.design_field: {"derivatives": "nope"}}}}, "Unexpected GraphQL answer", 0),
    ],
)
def test_failed_step(mfg: MfgScript, answer: Callable[[MfgScript], dict[str, Any]], message: str, submitted_jobs: int) -> None:
    transport = ScriptedTransport().add("POST", mfg.endpoint, answer(mfg))
    submitted = Submissions()
    with pytest.raises(CloudError, match=message):
        step_source(mfg, transport).generate_step(ITEM, submitted)
    assert submitted.count == submitted_jobs


def test_step_of_a_design_changed_meanwhile_is_not_downloaded(mfg: MfgScript) -> None:
    transport = ScriptedTransport().add("POST", mfg.endpoint, mfg.step_answer("SUCCESS", "https://cdn.example/x.stp?sig=1"))
    add_tip(transport, ITEM.version + 1)
    with pytest.raises(CloudError, match="new version meanwhile"):
        step_source(mfg, transport).generate_step(ITEM, Submissions())
    assert transport.routes == []  # the STEP itself was never fetched


def test_step_timeout(mfg: MfgScript) -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    transport = ScriptedTransport()
    for _ in range(3):
        transport.add("POST", mfg.endpoint, mfg.step_answer("IN_PROGRESS"))
    source = step_source(mfg, transport, poll_seconds=10, step_timeout_seconds=15, clock=clock, sleep=sleep)
    with pytest.raises(CloudError, match="did not finish within 15 s"):
        source.generate_step(ITEM, Submissions())


def test_unknown_mfg_hub_fails_before_submitting(mfg: MfgScript) -> None:
    transport = ScriptedTransport().add("POST", mfg.endpoint, {"data": {"hubs": {"results": []}}})
    submitted = Submissions()
    with pytest.raises(CloudError, match="not found in the Manufacturing Data Model API"):
        make_source(transport, mfg_api=mfg.version).generate_step(ITEM, submitted)
    assert submitted.count == 0


def test_transport_errors_and_unexpected_answers_become_cloud_errors() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(CloudError, match="failed: ConnectError"):
        make_source(ScriptedTransport().add("GET", f"{API}/project/v1/hubs", fail)).list_projects()
    with pytest.raises(CloudError, match="Unexpected answer"):
        make_source(ScriptedTransport().add("GET", f"{API}/project/v1/hubs", {"data": "nope"})).list_projects()
