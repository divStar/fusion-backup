"""Manufacturing Data Model API v3: the STEP export of a design's root model.

v3 is priced: each requested export may count as one "MFG Data Model - standard job".
"""

from fusion_backup.aps._dto import Derivative, Dto
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.mfg_data_model.api import MfgDataModelApi, StepExport, find_hub_id, step_of
from fusion_backup.cloud import CloudError

_ENDPOINT = "/mfg/v3/graphql/public"

_STEP_QUERY = """
query($hubId: ID!, $itemId: ID!, $generate: Boolean) {
  item(hubId: $hubId, itemId: $itemId) {
    ... on DesignItem {
      tipRootModel {
        derivatives(derivativeInput: { targetFormat: [STEP], generate: $generate }) {
          outputFormat status signedUrl
        }
      }
    }
  }
}
"""


class _Model(Dto):
    derivatives: tuple[Derivative, ...] = ()


class _DesignItem(Dto):
    tip_root_model: _Model | None = None


class _StepData(Dto):
    item: _DesignItem | None = None


class MfgDataModelV3(MfgDataModelApi):
    """`MfgDataModelApi` through v3."""

    def __init__(self, client: ApsClient):
        self._client = client

    def hub_id(self, hub_name: str) -> str:
        return find_hub_id(self._client, _ENDPOINT, hub_name)

    def step_export(self, hub_id: str, item_id: str, *, generate: bool) -> StepExport:
        variables = {"hubId": hub_id, "itemId": item_id, "generate": generate}
        item = self._client.graphql(_ENDPOINT, _STEP_QUERY, variables, _StepData).item
        if item is None:
            raise CloudError("the design is not available in the Manufacturing Data Model API")
        return step_of(item.tip_root_model.derivatives if item.tip_root_model else ())
