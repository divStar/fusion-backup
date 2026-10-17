"""APS Manufacturing Data Model API (GraphQL): STEP exports generated in the cloud.

Autodesk runs two versions of this API. `v2` and `v3` each implement `MfgDataModelApi` with their own
endpoint and queries; `open_api` is the only place that chooses between them.
"""

from collections.abc import Callable
from enum import StrEnum

from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.mfg_data_model.api import MfgDataModelApi, StepExport
from fusion_backup.aps.mfg_data_model.v2 import MfgDataModelV2
from fusion_backup.aps.mfg_data_model.v3 import MfgDataModelV3


class MfgApiVersion(StrEnum):
    """Which version of the Manufacturing Data Model API generates the STEP exports."""

    V2 = "v2"
    V3 = "v3"


_IMPLEMENTATIONS: dict[MfgApiVersion, Callable[[ApsClient], MfgDataModelApi]] = {
    MfgApiVersion.V2: MfgDataModelV2,
    MfgApiVersion.V3: MfgDataModelV3,
}


def open_api(client: ApsClient, version: MfgApiVersion) -> MfgDataModelApi:
    """The Manufacturing Data Model API in the chosen version."""
    return _IMPLEMENTATIONS[version](client)


__all__ = ["MfgApiVersion", "MfgDataModelApi", "StepExport", "open_api"]
