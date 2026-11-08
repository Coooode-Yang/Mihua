import json
import pytest

pytest.importorskip("netpet_tools")
from netpet_tools import ToolConfig, ToolService
from westie_agent.bridge import MODEL_TOOLS, ToolBridge
from westie_agent.settings import Settings


@pytest.mark.parametrize("scenario", ["case_01_normal", "case_02_portal", "case_03_loss",
                                      "case_04_proxy_conflict", "case_05_incomplete", "case_06_forbidden"])
def test_external_tool_package_contract_in_mock(scenario):
    bridge = ToolBridge(Settings(tools_mode="mock", scenario_id=scenario))
    assert not bridge.problem and bridge.service is not None
    functions = bridge.functions(lambda _: None)
    assert {f.__name__ for f in functions} == MODEL_TOOLS
    snapshot = next(f for f in functions if f.__name__ == "get_network_snapshot")()
    assert snapshot["mode"] == "mock"
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["status"] in {"ok", "partial", "error"}
    json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    assert not bridge.plans


def test_external_mock_approved_action_can_be_revoked_by_single_use():
    bridge = ToolBridge(Settings(tools_mode="mock", scenario_id="case_01_normal"))
    action = next(f for f in bridge.functions(lambda _: None) if f.__name__ == "plan_action")
    plan = action(action_name="flush_dns")
    assert plan["success"], plan
    plan_id = plan["data"]["plan_id"]
    result = bridge.execute_approved(plan_id)
    assert result["mode"] == "mock"
    assert result["data"]["recovery_status"] in {"recovered", "not_recovered", "unknown", "not_checked"}
    with pytest.raises(ValueError):
        bridge.execute_approved(plan_id)
