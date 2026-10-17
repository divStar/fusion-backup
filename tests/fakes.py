"""Test doubles: robot credentials with a throwaway key, an in-memory cloud and a scripted HTTP transport."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import httpx2
from joserfc.jwk import RSAKey

from fusion_backup.aps.auth import TOKEN_URL, SsaCredentials, connect
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.mfg_data_model import MfgApiVersion
from fusion_backup.cloud import CloudItem, CloudProject, StepUnavailableError

API = "https://developer.api.autodesk.com"


@cache
def robot_key() -> RSAKey:
    """A throwaway RSA key, generated once per test session."""
    return RSAKey.generate_key(2048, private=True)


def robot_credentials() -> SsaCredentials:
    return SsaCredentials("client", "secret", "SA123", "kid-1", robot_key().as_pem(private=True).decode())


def robot_env() -> dict[str, str]:
    credentials = robot_credentials()
    return {
        "APS_CLIENT_ID": credentials.client_id,
        "APS_CLIENT_SECRET": credentials.client_secret,
        "APS_SSA_ID": credentials.service_account_id,
        "APS_SSA_KEY_ID": credentials.key_id,
        "APS_SSA_PRIVATE_KEY": credentials.private_key,
    }


@dataclass
class FakeCloud:
    """`CloudSource` in memory: `files` maps item path to (version, bytes); a STEP is b"STEP:" + the bytes.

    `download_failures` makes a download fail. `step_failures` makes an export fail after it was submitted
    (StepUnavailableError: refused before), `failures_before_submit` before anything was submitted.
    """

    hub: str = "Hub"
    project: str = "Project"
    files: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    designs: set[str] = field(default_factory=set)
    item_ids: dict[str, str] = field(default_factory=dict)
    download_failures: dict[str, Exception] = field(default_factory=dict)
    step_failures: dict[str, Exception] = field(default_factory=dict)
    failures_before_submit: dict[str, Exception] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)
    step_requests: list[str] = field(default_factory=list)
    mfg_api: str | None = None  # the Manufacturing Data Model API version the CLI opened this source with

    def put(self, path: str, version: int, data: bytes, *, design: bool = True, item_id: str | None = None) -> None:
        self.files[path] = (version, data)
        self.item_ids[path] = item_id or f"id:{path}"
        if design:
            self.designs.add(path)

    def list_projects(self) -> list[CloudProject]:
        return [CloudProject(self.hub, self.project)]

    def list_items(self, hub: str, project: str) -> list[CloudItem]:
        assert (hub, project) == (self.hub, self.project)
        return [
            CloudItem(hub, project, path, self.item_ids[path], path in self.designs, version)
            for path, (version, _) in sorted(self.files.items(), key=lambda entry: entry[0].lower())
        ]

    def download(self, item: CloudItem) -> bytes:
        self.downloads.append(item.path)
        if item.path in self.download_failures:
            raise self.download_failures[item.path]
        return self.files[item.path][1]

    def generate_step(self, item: CloudItem, on_submitted: Callable[[], None]) -> bytes:
        self.step_requests.append(item.path)
        if item.path in self.failures_before_submit:
            raise self.failures_before_submit[item.path]
        failure = self.step_failures.get(item.path)
        if isinstance(failure, StepUnavailableError):
            raise failure
        on_submitted()
        if failure:
            raise failure
        return b"STEP:" + self.files[item.path][1]


type Answer = dict[str, Any] | bytes | httpx2.Response | Callable[[httpx2.Request], httpx2.Response]


class ScriptedTransport(httpx2.MockTransport):
    """Answers requests from routes added with `add(method, url_prefix, answer)`; each route answers once,
    in the order added. Every request is recorded in `requests`."""

    def __init__(self) -> None:
        super().__init__(self._handle)
        self.routes: list[tuple[str, str, Answer]] = []
        self.requests: list[httpx2.Request] = []

    def add(self, method: str, url_prefix: str, answer: Answer) -> ScriptedTransport:
        self.routes.append((method, url_prefix, answer))
        return self

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        for index, (method, prefix, answer) in enumerate(self.routes):
            if request.method == method and str(request.url).startswith(prefix):
                del self.routes[index]
                if callable(answer):
                    return answer(request)
                if isinstance(answer, httpx2.Response):
                    return answer
                if isinstance(answer, bytes):
                    return httpx2.Response(200, content=answer)
                return httpx2.Response(200, json=answer)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def json_bodies(self, url_suffix: str) -> list[Any]:
        """Decoded JSON bodies of all requests to URLs ending with `url_suffix`."""
        return [json.loads(request.content) for request in self.requests if str(request.url).endswith(url_suffix)]


def token_answer(n: int = 1, expires_in: int = 3600) -> dict[str, Any]:
    return {"access_token": f"access-{n}", "token_type": "Bearer", "expires_in": expires_in}


def aps_client(transport: ScriptedTransport) -> ApsClient:
    """An `ApsClient` logged in as the robot over the scripted transport (the token answer is added first)."""
    transport.routes.insert(0, ("POST", TOKEN_URL, token_answer()))
    return ApsClient(connect(robot_credentials(), transport=transport))


@dataclass(frozen=True)
class MfgScript:
    """How Autodesk answers in one Manufacturing Data Model API version: endpoint, query and answer shape."""

    version: MfgApiVersion
    endpoint: str
    design_field: str  # where a design's derivatives hang below the item
    format_argument: str  # how the query asks for STEP

    def hubs_answer(self) -> dict[str, Any]:
        return {"data": {"hubs": {"results": [{"id": "urn:mfg:hub", "name": "Hub"}]}}}

    def step_answer(self, status: str | None, url: str | None = None) -> dict[str, Any]:
        derivative = {"outputFormat": "STEP", "status": status, "signedUrl": url}
        return {"data": {"item": {self.design_field: {"derivatives": [derivative]}}}}


MFG_V2 = MfgScript(MfgApiVersion.V2, f"{API}/mfg/graphql", "tipRootComponentVersion", "outputFormat: [STEP]")
MFG_V3 = MfgScript(MfgApiVersion.V3, f"{API}/mfg/v3/graphql/public", "tipRootModel", "targetFormat: [STEP]")
MFG_APIS = [MFG_V2, MFG_V3]
