"""`MfgDataModelV2` on its own: its endpoint, its query and how it reads v2 answers."""

import pytest
from fakes import MFG_V2, ScriptedTransport, aps_client

from fusion_backup.aps.mfg_data_model.api import StepExport
from fusion_backup.aps.mfg_data_model.v2 import MfgDataModelV2
from fusion_backup.cloud import CloudError


def test_hub_id() -> None:
    transport = ScriptedTransport().add("POST", MFG_V2.endpoint, MFG_V2.hubs_answer())
    assert MfgDataModelV2(aps_client(transport)).hub_id("Hub") == "urn:mfg:hub"
    transport.add("POST", MFG_V2.endpoint, MFG_V2.hubs_answer())
    with pytest.raises(CloudError, match="Hub 'Other' not found"):
        MfgDataModelV2(aps_client(transport)).hub_id("Other")


def test_step_export_asks_v2_for_the_root_component_versions_step() -> None:
    transport = ScriptedTransport().add("POST", MFG_V2.endpoint, MFG_V2.step_answer("SUCCESS", "https://cdn.example/x.stp?sig=1"))
    export = MfgDataModelV2(aps_client(transport)).step_export("urn:mfg:hub", "urn:item:1", generate=True)
    assert export == StepExport("SUCCESS", "https://cdn.example/x.stp?sig=1")
    [body] = transport.json_bodies(MFG_V2.endpoint)
    assert body["variables"] == {"hubId": "urn:mfg:hub", "itemId": "urn:item:1", "generate": True}
    assert "tipRootComponentVersion" in body["query"] and MFG_V2.format_argument in body["query"]


@pytest.mark.parametrize("design", [None, {"derivatives": []}, {"derivatives": [{"outputFormat": "STL", "status": "SUCCESS"}]}])
def test_no_step_yet(design: object) -> None:
    transport = ScriptedTransport().add("POST", MFG_V2.endpoint, {"data": {"item": {MFG_V2.design_field: design}}})
    assert MfgDataModelV2(aps_client(transport)).step_export("h", "i", generate=False) == StepExport(None, None)


def test_unknown_design() -> None:
    transport = ScriptedTransport().add("POST", MFG_V2.endpoint, {"data": {"item": None}})
    with pytest.raises(CloudError, match="not available in the Manufacturing Data Model API"):
        MfgDataModelV2(aps_client(transport)).step_export("h", "i", generate=False)
