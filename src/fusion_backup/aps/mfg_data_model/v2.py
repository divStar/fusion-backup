"""Manufacturing Data Model API v2: the STEP export of a design's root component version.

Autodesk has announced that v2 will be retired; it is not priced (only v3 is).
"""

from fusion_backup.aps._dto import Derivative, Dto
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.mfg_data_model.api import MfgDataModelApi, StepExport, find_hub_id, step_of
from fusion_backup.cloud import CloudError

_ENDPOINT = "/mfg/graphql"

_STEP_QUERY = """
query($hubId: ID!, $itemId: ID!, $generate: Boolean) {
  item(hubId: $hubId, itemId: $itemId) {
    ... on DesignItem {
      tipRootComponentVersion {
        derivatives(derivativeInput: { outputFormat: [STEP], generate: $generate }) {
          outputFormat status signedUrl
        }
      }
    }
  }
}
"""


class _ComponentVersion(Dto):
    derivatives: tuple[Derivative, ...] = ()


class _DesignItem(Dto):
    tip_root_component_version: _ComponentVersion | None = None


class _StepData(Dto):
    item: _DesignItem | None = None


class MfgDataModelV2(MfgDataModelApi):
    """`MfgDataModelApi` through v2."""

    def __init__(self, client: ApsClient):
        self._client = client

    def hub_id(self, hub_name: str) -> str:
        return find_hub_id(self._client, _ENDPOINT, hub_name)

    def step_export(self, hub_id: str, item_id: str, *, generate: bool) -> StepExport:
        variables = {"hubId": hub_id, "itemId": item_id, "generate": generate}
        item = self._client.graphql(_ENDPOINT, _STEP_QUERY, variables, _StepData).item
        if item is None:
            raise CloudError("the design is not available in the Manufacturing Data Model API")
        return step_of(item.tip_root_component_version.derivatives if item.tip_root_component_version else ())
