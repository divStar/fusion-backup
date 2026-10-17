"""`CloudSource` implemented with the APS APIs: maps Autodesk's DTOs to the domain objects."""

import logging
import time
from collections.abc import Callable

from fusion_backup.aps.client import ApsClient, ApsRequestError
from fusion_backup.aps.data_management import DataManagementApi
from fusion_backup.aps.mfg_data_model import MfgApiVersion, open_api
from fusion_backup.cloud import CloudError, CloudItem, CloudProject, StepUnavailableError

DESIGN_ITEM_TYPE = "items:autodesk.fusion360:Design"
_STEP_DONE = {"SUCCESS", "COMPLETE"}
_STEP_FAILED = {"FAILED", "TIMEOUT"}

log = logging.getLogger(__name__)


class ApsCloudSource:
    """Fusion projects in the Autodesk cloud.

    Listing a project remembers where each item's file is stored, so `download` needs no extra lookup.
    `mfg_api` chooses the Manufacturing Data Model API version that generates the STEP exports.
    """

    def __init__(
        self,
        client: ApsClient,
        *,
        mfg_api: MfgApiVersion = MfgApiVersion.V2,
        poll_seconds: float = 10,
        step_timeout_seconds: float = 900,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._data_management = DataManagementApi(client)
        self._mfg = open_api(client, mfg_api)
        self._poll_seconds = poll_seconds
        self._step_timeout_seconds = step_timeout_seconds
        self._sleep = sleep
        self._clock = clock
        self._storage: dict[tuple[str, int], str] = {}  # (item ID, version) -> storage URN
        self._project_ids: dict[tuple[str, str], tuple[str, str]] = {}  # (hub, project) -> (hub ID, project ID)
        self._mfg_hub_ids: dict[str, str] = {}

    def list_projects(self) -> list[CloudProject]:
        return [
            CloudProject(hub.attributes.name, project.attributes.name)
            for hub in self._data_management.hubs()
            for project in self._data_management.projects(hub.id)
        ]

    def list_items(self, hub: str, project: str) -> list[CloudItem]:
        hub_id, project_id = self._find_project(hub, project)
        items: list[CloudItem] = []
        folders = [(self._data_management.root_folder_id(hub_id, project_id), "")]
        while folders:
            folder_id, prefix = folders.pop()
            for page in self._data_management.folder_contents(project_id, folder_id):
                versions = {version.id: version for version in page.included if version.type == "versions"}
                for entry in page.data:
                    name = prefix + entry.attributes.display_name
                    if entry.type == "folders":
                        folders.append((entry.id, f"{name}/"))
                        continue
                    tip = versions.get(entry.relationships.tip.data.id) if entry.relationships.tip else None
                    if tip is None or tip.relationships.storage is None:
                        log.warning("Skipping %s: Autodesk returned no stored version", name)
                        continue
                    version = tip.attributes.version_number
                    self._storage[(entry.id, version)] = tip.relationships.storage.data.id
                    is_design = entry.attributes.extension is not None and entry.attributes.extension.type == DESIGN_ITEM_TYPE
                    items.append(CloudItem(hub, project, name, entry.id, is_design, version))
        return sorted(items, key=lambda item: item.path.lower())

    def download(self, item: CloudItem) -> bytes:
        storage_urn = self._storage.get((item.item_id, item.version))
        if storage_urn is None:
            raise CloudError(f"{item.path} v{item.version} was not part of a listing; list the project first")
        return self._data_management.download(storage_urn)

    def generate_step(self, item: CloudItem, on_submitted: Callable[[], None]) -> bytes:
        hub_id = self._mfg_hub_id(item.hub)
        try:
            export = self._mfg.step_export(hub_id, item.item_id, generate=True)
        except ApsRequestError as err:
            raise StepUnavailableError(str(err)) from err
        on_submitted()  # Autodesk accepted the request: from here on it counts as a job
        started = self._clock()
        while export.status not in _STEP_DONE:
            if export.status in _STEP_FAILED:
                raise CloudError(f"STEP export ended with {export.status}")
            waited = self._clock() - started
            if waited > self._step_timeout_seconds:
                raise CloudError(f"STEP export did not finish within {self._step_timeout_seconds:.0f} s")
            log.info("      %s after %.0f s", (export.status or "waiting").lower().replace("_", " "), waited)
            self._sleep(self._poll_seconds)
            export = self._mfg.step_export(hub_id, item.item_id, generate=False)
        if not export.signed_url:
            raise CloudError("STEP export finished without a download URL")
        # the export belongs to the design's current version, which must still be the one backed up
        _, project_id = self._find_project(item.hub, item.project)
        if self._data_management.tip_version(project_id, item.item_id) != item.version:
            raise CloudError("the design got a new version meanwhile; retrying next run")
        return self._client.download(export.signed_url)

    def _find_project(self, hub: str, project: str) -> tuple[str, str]:
        if (hub, project) not in self._project_ids:
            self._project_ids[hub, project] = self._look_up_project(hub, project)
        return self._project_ids[hub, project]

    def _look_up_project(self, hub: str, project: str) -> tuple[str, str]:
        hubs = self._data_management.hubs()
        hub_entry = next((h for h in hubs if h.attributes.name == hub), None)
        if hub_entry is None:
            raise CloudError(f"Hub '{hub}' not found; visible hubs: {', '.join(h.attributes.name for h in hubs) or 'none'}")
        project_entry = next((p for p in self._data_management.projects(hub_entry.id) if p.attributes.name == project), None)
        if project_entry is None:
            raise CloudError(f"Project '{project}' not found in hub '{hub}'")
        return hub_entry.id, project_entry.id

    def _mfg_hub_id(self, hub: str) -> str:
        if hub not in self._mfg_hub_ids:
            self._mfg_hub_ids[hub] = self._mfg.hub_id(hub)
        return self._mfg_hub_ids[hub]
