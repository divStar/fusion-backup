"""The Autodesk Platform Services (APS) implementation of `fusion_backup.cloud.CloudSource`.

Everything Autodesk-specific lives here: authentication as a Secure Service Account (`auth`), the HTTP
layer (`client`), one module per Autodesk API (`data_management`, and `mfg_data_model` with one module per
API version) and their response models (`_dto`, private). `source.ApsCloudSource` is the only part that
hands out data, and only as domain objects.
"""

from fusion_backup.aps.auth import AuthenticationError, SsaCredentials, connect
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.mfg_data_model import MfgApiVersion
from fusion_backup.aps.source import ApsCloudSource


def open_source(credentials: SsaCredentials, mfg_api: MfgApiVersion = MfgApiVersion.V2) -> ApsCloudSource:
    """An `ApsCloudSource` logged in as the robot, generating STEP exports through `mfg_api`."""
    return ApsCloudSource(ApsClient(connect(credentials)), mfg_api=mfg_api)


__all__ = ["ApsCloudSource", "AuthenticationError", "MfgApiVersion", "SsaCredentials", "open_source"]
