"""The switch between the Manufacturing Data Model API versions."""

from fakes import ScriptedTransport, aps_client

from fusion_backup.aps.mfg_data_model import MfgApiVersion, open_api
from fusion_backup.aps.mfg_data_model.v2 import MfgDataModelV2
from fusion_backup.aps.mfg_data_model.v3 import MfgDataModelV3


def test_each_version_opens_its_own_implementation() -> None:
    client = aps_client(ScriptedTransport())
    assert type(open_api(client, MfgApiVersion.V2)) is MfgDataModelV2
    assert type(open_api(client, MfgApiVersion.V3)) is MfgDataModelV3


def test_every_version_has_an_implementation() -> None:
    client = aps_client(ScriptedTransport())
    for version in MfgApiVersion:
        open_api(client, version)  # a version without an implementation raises KeyError
