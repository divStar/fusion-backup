"""`MfgDataModelV3` on its own: its endpoint, its query and how it reads v3 answers."""

import pytest
from fakes import MFG_V3, ScriptedTransport, aps_client

from fusion_backup.aps.mfg_data_model.api import StepExport
from fusion_backup.aps.mfg_data_model.v3 import MfgDataModelV3
from fusion_backup.cloud import CloudError


def test_hub_id() -> None:
    transport = ScriptedTransport().add("POST", MFG_V3.endpoint, MFG_V3.hubs_answer())
    assert MfgDataModelV3(aps_client(transport)).hub_id("Hub") == "urn:mfg:hub"
    transport.add("POST", MFG_V3.endpoint, MFG_V3.hubs_answer())
    with pytest.raises(CloudError, match="Hub 'Other' not found"):
        MfgDataModelV3(aps_client(transport)).hub_id("Other")


def test_step_export_asks_v3_for_the_root_models_step() -> None:
    transport = ScriptedTransport().add("POST", MFG_V3.endpoint, MFG_V3.step_answer("SUCCESS", "https://cdn.example/x.stp?sig=1"))
    export = MfgDataModelV3(aps_client(transport)).step_export("urn:mfg:hub", "urn:item:1", generate=True)
    assert export == StepExport("SUCCESS", "https://cdn.example/x.stp?sig=1")
    [body] = transport.json_bodies(MFG_V3.endpoint)
    assert body["variables"] == {"hubId": "urn:mfg:hub", "itemId": "urn:item:1", "generate": True}
    assert "tipRootModel" in body["query"] and MFG_V3.format_argument in body["query"]


@pytest.mark.parametrize("design", [None, {"derivatives": []}, {"derivatives": [{"outputFormat": "STL", "status": "SUCCESS"}]}])
def test_no_step_yet(design: object) -> None:
    transport = ScriptedTransport().add("POST", MFG_V3.endpoint, {"data": {"item": {MFG_V3.design_field: design}}})
    assert MfgDataModelV3(aps_client(transport)).step_export("h", "i", generate=False) == StepExport(None, None)


def test_unknown_design() -> None:
    transport = ScriptedTransport().add("POST", MFG_V3.endpoint, {"data": {"item": None}})
    with pytest.raises(CloudError, match="not available in the Manufacturing Data Model API"):
        MfgDataModelV3(aps_client(transport)).step_export("h", "i", generate=False)
