"""The interface every Manufacturing Data Model API version implements, and the parts they share."""

from dataclasses import dataclass
from typing import Protocol

from fusion_backup.aps._dto import Derivative, MfgHubsData
from fusion_backup.aps.client import ApsClient
from fusion_backup.cloud import CloudError

_HUBS_QUERY = "{ hubs { results { id name } } }"


@dataclass(frozen=True)
class StepExport:
    """A design's STEP export as Autodesk reports it; `status` is None while there is none yet."""

    status: str | None
    signed_url: str | None


class MfgDataModelApi(Protocol):
    """STEP exports generated in the cloud, through one version of the Manufacturing Data Model API.

    Asking for generation may be metered as one "MFG Data Model - standard job"; reading the status is not.
    This API uses its own hub IDs, while item IDs are shared with the Data Management API.
    """

    def hub_id(self, hub_name: str) -> str:
        """This API's ID of the hub with that name; raises CloudError if there is none."""
        ...

    def step_export(self, hub_id: str, item_id: str, *, generate: bool) -> StepExport:
        """The STEP export of the design's current version; with `generate=True`, Autodesk starts it if needed."""
        ...


def find_hub_id(client: ApsClient, endpoint: str, hub_name: str) -> str:
    """The hub lookup, which is the same query in every version."""
    hubs = client.graphql(endpoint, _HUBS_QUERY, {}, MfgHubsData).hubs.results
    match = next((hub for hub in hubs if hub.name == hub_name), None)
    if match is None:
        raise CloudError(f"Hub '{hub_name}' not found in the Manufacturing Data Model API")
    return match.id


def step_of(derivatives: tuple[Derivative, ...]) -> StepExport:
    """The STEP entry among a design's derivatives."""
    step = next((derivative for derivative in derivatives if derivative.output_format == "STEP"), None)
    return StepExport(step.status, step.signed_url) if step else StepExport(None, None)
