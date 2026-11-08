"""End-to-end offline tests across all six v1 mock scenarios."""

from __future__ import annotations

from netpet_tools import ErrorCode, ToolConfig, ToolService
from tests.test_protocol_and_registry import assert_valid_v1_envelope


def test_case_01_normal_full_diagnostics() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))

    snap = svc.get_network_snapshot(interface="auto", include_recent_events=True)
    assert_valid_v1_envelope(snap, expected_tool="get_network_snapshot")
    assert snap["status"] == "ok"
    sel = snap["data"]["selected_interface"]
    assert sel["name"] == "WLAN"
    assert sel["adapter_type"] == "wifi"
    assert sel["dhcp_enabled"] is True
    assert sel["dhcp_lease_valid"] is True
    assert sel["wlan"]["signal_quality_pct"] == 86
    assert sel["wlan"]["rssi_dbm"] is None
    assert sel["wlan"]["rssi_estimated_dbm"] == -57.0
    assert sel["wlan"]["rssi_source"] == "estimated_from_quality"
    assert len(snap["data"]["recent_wlan_events"]) == 1

    dns_res = svc.query_dns("www.campus.edu.test", compare_public=True)
    assert_valid_v1_envelope(dns_res, expected_tool="query_dns")
    assert dns_res["data"]["primary_result"]["status"] == "NOERROR"
    assert dns_res["data"]["queries"][1]["status"] == "NXDOMAIN"
    assert any("split-horizon" in n for n in dns_res["data"]["comparison"]["observation_notes"])

    probe = svc.probe_connectivity()
    assert_valid_v1_envelope(probe, expected_tool="probe_connectivity")
    assert probe["data"]["http_status"] == 204
    assert probe["data"]["portal_state"] == "not_detected"

    icmp = svc.measure_latency_and_loss("10.24.18.1", count=4)
    assert_valid_v1_envelope(icmp, expected_tool="measure_latency_and_loss")
    assert icmp["data"]["sent"] == 4
    assert icmp["data"]["received"] == 4
    assert icmp["data"]["loss_rate_pct"] == 0.0


def test_case_02_portal_detection_and_browser_launch_boundary() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_02_portal"))

    probe = svc.probe_connectivity()
    assert probe["status"] == "ok"
    assert probe["data"]["http_status"] == 302
    assert probe["data"]["portal_state"] == "confirmed"
    assert probe["data"]["login_url_trusted"] is True
    login_url = probe["data"]["candidate_login_url"]
    assert login_url.startswith("https://auth.campus.edu.test/portal/login")

    plan = svc.plan_action(
        "open_browser_url",
        {"url": login_url},
        verification_target="http://connect.rom.miui.com/generate_204",
    )
    assert plan["status"] == "ok"
    token = svc.approve_action(plan["data"]["plan_id"])
    exec_res = svc.execute_action(plan["data"]["plan_id"], approval_token=token)
    assert exec_res["status"] == "ok"
    assert exec_res["data"]["execution_status"] == "succeeded"
    assert exec_res["data"]["action_details"]["browser_launched"] is True
    assert exec_res["data"]["action_details"]["authentication_completed"] is False
    # Browser launch does NOT complete login, so portal remains active on re-verification
    assert exec_res["data"]["recovery_status"] == "not_recovered"


def test_case_03_loss_signal_clamping_and_exact_packet_loss_math() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_03_loss"))

    snap = svc.get_network_snapshot()
    wlan = snap["data"]["selected_interface"]["wlan"]
    assert wlan["signal_quality_pct"] == 100
    assert wlan["rssi_dbm"] is None
    assert wlan["rssi_estimated_dbm"] is None
    assert wlan["rssi_source"] == "boundary_clamped"

    # 10 samples -> 6 lost = 60.0%
    icmp_10 = svc.measure_latency_and_loss("10.24.18.1", count=10)
    assert icmp_10["data"]["sent"] == 10
    assert icmp_10["data"]["received"] == 4
    assert icmp_10["data"]["lost"] == 6
    assert icmp_10["data"]["loss_rate_pct"] == 60.0

    # 4 samples -> 2 lost = 50.0% (4 samples can never yield 60%)
    icmp_4 = svc.measure_latency_and_loss("10.24.18.1", count=4)
    assert icmp_4["data"]["sent"] == 4
    assert icmp_4["data"]["received"] == 2
    assert icmp_4["data"]["lost"] == 2
    assert icmp_4["data"]["loss_rate_pct"] == 50.0

    dns_res = svc.query_dns("example.com", compare_public=True)
    assert dns_res["success"] is True
    assert dns_res["data"]["primary_result"]["status"] == "TIMEOUT"


def test_case_04_proxy_conflict_dual_route_evidence_and_instance_isolation() -> None:
    svc_a = ToolService(ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict"))
    svc_b = ToolService(ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict"))

    target = "https://jwxt.campus.edu.test"
    rule_check = svc_a.check_target_routing_rule(target)
    assert rule_check["data"]["expected_policy"] == "direct"
    # ProxyEnable=True alone without client rule mode is insufficient to assert conflict=True
    assert rule_check["data"]["conflict"] is None
    assert rule_check["data"]["potential_conflict"] is True
    assert "compare_probe_connectivity_direct_vs_configured_proxy" in rule_check["data"]["missing_evidence"]

    p_direct = svc_a.probe_connectivity(target_url=target, route="direct")
    p_proxy = svc_a.probe_connectivity(target_url=target, route="configured_proxy")
    assert p_direct["data"]["http_status"] == 200
    assert p_proxy["data"]["http_status"] == 502

    plan = svc_a.plan_action("set_system_proxy", {"enable": False}, verification_target=target)
    tok = svc_a.approve_action(plan["data"]["plan_id"])
    exec_res = svc_a.execute_action(plan["data"]["plan_id"], approval_token=tok)

    assert exec_res["data"]["execution_status"] == "succeeded"
    assert exec_res["data"]["recovery_status"] == "recovered"
    assert exec_res["data"]["rollback_status"] == "not_needed"
    assert svc_a.get_proxy_state()["data"]["hkcu_internet_settings"]["proxy_enable"] is False

    # Instance B must remain completely untouched
    assert svc_b.get_proxy_state()["data"]["hkcu_internet_settings"]["proxy_enable"] is True


def test_case_05_incomplete_location_denied_apipa_ipv6_and_pac_unsupported() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_05_incomplete"))

    snap = svc.get_network_snapshot(include_recent_events=True)
    assert snap["success"] is True
    assert snap["status"] == "partial"
    sel = snap["data"]["selected_interface"]
    assert sel["has_apipa_ipv4"] is True
    assert sel["has_usable_ipv6"] is True
    assert sel["dhcp_lease_valid"] is None
    assert sel["wlan"]["state"] == "location_restricted"
    assert sel["wlan"]["permission_issue"] == ErrorCode.LOCATION_PERMISSION_REQUIRED.value
    assert sel["wlan"]["ssid"] is None

    # PAC-only without explicit configured_proxy_url must return UNSUPPORTED, never silently direct
    proxy_probe = svc.probe_connectivity(route="configured_proxy")
    assert proxy_probe["success"] is False
    assert proxy_probe["error"]["code"] == ErrorCode.UNSUPPORTED.value

    # PAC-managed system proxy refuses manual overwrite
    plan = svc.plan_action("set_system_proxy", {"enable": False})
    assert plan["success"] is False
    assert plan["error"]["code"] == ErrorCode.UNSUPPORTED.value


def test_case_06_forbidden_403_counterexample_not_recovered_by_proxy_toggle() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_06_forbidden"))
    url = "https://paper-db.campus.edu.test/restricted"

    d_res = svc.probe_connectivity(target_url=url, route="direct")
    p_res = svc.probe_connectivity(target_url=url, route="configured_proxy")
    assert d_res["success"] is True and d_res["data"]["http_status"] == 403
    assert p_res["success"] is True and p_res["data"]["http_status"] == 403

    plan = svc.plan_action("set_system_proxy", {"enable": False}, verification_target=url)
    tok = svc.approve_action(plan["data"]["plan_id"])
    exec_res = svc.execute_action(plan["data"]["plan_id"], approval_token=tok)

    assert exec_res["success"] is True
    assert exec_res["data"]["execution_status"] == "succeeded"
    assert exec_res["data"]["recovery_status"] == "not_recovered"
