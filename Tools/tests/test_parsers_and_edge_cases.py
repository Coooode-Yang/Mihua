"""Offline parser tests (Chinese/English Windows CLI fixtures) and strict validation edge cases."""

from __future__ import annotations

from pathlib import Path

import dns.exception
import dns.message
import dns.rcode
import dns.resolver
import pytest

from netpet_tools import ErrorCode, RoutingRuleConfig, ToolConfig, ToolService
from netpet_tools.dns_diagnostics import classify_no_nameservers_error
from netpet_tools.network_snapshot import (
    assemble_snapshot_payload,
    classify_adapter_type,
    compute_wlan_signal_fields,
    evaluate_dhcp_fields,
    parse_ipconfig_all,
    parse_netsh_wlan_interfaces,
)
from netpet_tools.proxy_manager import (
    does_proxy_override_bypass,
    evaluate_target_routing_rule,
    parse_netsh_winhttp_proxy,
)
from netpet_tools.safe_actions import parse_netsh_wlan_profiles
from netpet_tools.web_probe import evaluate_portal_evidence, parse_windows_ping_output

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_parse_netsh_wlan_chinese_and_english_and_location_denied() -> None:
    zh = parse_netsh_wlan_interfaces((FIXTURES_DIR / "netsh_wlan_zh_connected.txt").read_text(encoding="utf-8"), 0)
    assert zh["location_restricted"] is False
    wlan = zh["interfaces"]["WLAN"]
    assert wlan["state"] == "connected"
    assert wlan["ssid"] == "Campus-WLAN"
    assert wlan["bssid"] == "70:4f:57:11:22:33"
    assert wlan["channel"] == 44
    assert wlan["signal_quality_pct"] == 86
    assert wlan["profile_name"] == "Campus-WLAN"

    en = parse_netsh_wlan_interfaces((FIXTURES_DIR / "netsh_wlan_en_connected.txt").read_text(encoding="utf-8"), 0)
    wifi = en["interfaces"]["Wi-Fi"]
    assert wifi["state"] == "connected"
    assert wifi["ssid"] == "Campus-WLAN-5G"
    assert wifi["channel"] == 149
    assert wifi["signal_quality_pct"] == 100
    assert wifi["profile_name"] == "Campus-WLAN-5G"

    denied_zh = parse_netsh_wlan_interfaces(
        (FIXTURES_DIR / "netsh_wlan_location_denied_zh.txt").read_text(encoding="utf-8"), 5
    )
    assert denied_zh["location_restricted"] is True

    denied_en = parse_netsh_wlan_interfaces(
        (FIXTURES_DIR / "netsh_wlan_location_denied_en.txt").read_text(encoding="utf-8"), 1
    )
    assert denied_en["location_restricted"] is True


def test_parse_ipconfig_all_chinese_and_english() -> None:
    zh_adapters = parse_ipconfig_all((FIXTURES_DIR / "ipconfig_all_zh.txt").read_text(encoding="utf-8"))
    assert len(zh_adapters) == 2
    assert zh_adapters[0]["name"] == "以太网"
    assert zh_adapters[0]["status_raw"] == "disconnected"
    assert zh_adapters[1]["name"] == "WLAN"
    assert zh_adapters[1]["ipv4_addresses"] == [{"ip": "169.254.88.12", "prefix_length": None}]
    assert any(a["ip"] == "2001:da8:8000:18::99" for a in zh_adapters[1]["ipv6_addresses"])
    assert "10.10.0.21" in zh_adapters[1]["dns_servers"]

    en_adapters = parse_ipconfig_all((FIXTURES_DIR / "ipconfig_all_en.txt").read_text(encoding="utf-8"))
    assert len(en_adapters) == 1
    assert en_adapters[0]["name"] == "Wi-Fi"
    assert en_adapters[0]["dhcp_enabled"] is True
    assert en_adapters[0]["default_gateways_ipv4"] == ["10.24.18.1"]


def test_empty_interface_list_and_virtual_wifi_direct_classification() -> None:
    assert classify_adapter_type("本地连接* 1", "Microsoft Wi-Fi Direct Virtual Adapter", 71) == "virtual"
    assert classify_adapter_type("Clash", "Meta Tunnel", 53) == "virtual"
    assert classify_adapter_type("WLAN", "Intel(R) Wi-Fi 6 AX201", 71) == "wifi"

    data, status, warnings, _ = assemble_snapshot_payload([], requested_interface="auto")
    assert status == "partial"
    assert data["selected_interface"] is None
    assert warnings


def test_signal_quality_and_dhcp_non_fabrication_rules() -> None:
    assert compute_wlan_signal_fields(0)["rssi_estimated_dbm"] is None
    assert compute_wlan_signal_fields(100)["rssi_estimated_dbm"] is None
    assert compute_wlan_signal_fields(50)["rssi_estimated_dbm"] == -75.0
    assert compute_wlan_signal_fields(80, native_rssi_dbm=-62) == {
        "signal_quality_pct": 80,
        "rssi_dbm": -62,
        "rssi_estimated_dbm": None,
        "rssi_source": "native_rssi",
        "rssi_boundary_note": None,
    }

    # Private IP exists (caller context), DHCP enabled, but no lease timestamps -> dhcp_lease_valid MUST be None
    dhcp = evaluate_dhcp_fields(True, None, None)
    assert dhcp["dhcp_enabled"] is True
    assert dhcp["dhcp_lease_present"] is None
    assert dhcp["dhcp_lease_valid"] is None


def test_parse_ping_chinese_and_english_ignores_destination_unreachable_lines() -> None:
    zh_txt = (FIXTURES_DIR / "ping_zh.txt").read_text(encoding="utf-8")
    sent_zh, samples_zh, sub_zh = parse_windows_ping_output(zh_txt, 4)
    # Only 2 genuine echo replies with time (<1ms and 14ms); '无法访问目标主机' is NOT a valid echo reply!
    assert sent_zh == 4
    assert len(samples_zh) == 2
    assert sub_zh == 1

    en_txt = (FIXTURES_DIR / "ping_en.txt").read_text(encoding="utf-8")
    sent_en, samples_en, sub_en = parse_windows_ping_output(en_txt, 4)
    assert sent_en == 4
    assert samples_en == [12.0, 15.0, 0.5]
    assert sub_en == 1


def test_parse_winhttp_and_wlan_profiles_chinese_and_english() -> None:
    wh_zh = parse_netsh_winhttp_proxy((FIXTURES_DIR / "winhttp_zh.txt").read_text(encoding="utf-8"))
    assert wh_zh["direct_access"] is False
    assert "127.0.0.1:7890" in wh_zh["proxy_server"]
    assert "*.campus.edu.test" in wh_zh["bypass_list"]

    wh_en = parse_netsh_winhttp_proxy((FIXTURES_DIR / "winhttp_en.txt").read_text(encoding="utf-8"))
    assert wh_en["direct_access"] is True
    assert wh_en["proxy_server"] is None

    prof_zh = parse_netsh_wlan_profiles((FIXTURES_DIR / "wlan_profiles_zh.txt").read_text(encoding="utf-8"))
    assert prof_zh == ["Campus-WLAN", "Campus-WLAN-5G"]

    prof_en = parse_netsh_wlan_profiles((FIXTURES_DIR / "wlan_profiles_en.txt").read_text(encoding="utf-8"))
    assert prof_en == ["Campus-WLAN", "Eduroam"]


def test_dnspython_rcode_classification_servfail_vs_refused() -> None:
    req = dns.message.make_query("example.com", "A")
    msg_refused = dns.message.make_response(req)
    msg_refused.set_rcode(dns.rcode.REFUSED)
    exc_refused = dns.resolver.NoNameservers(
        request=req,
        errors=[("10.0.0.1", False, 53, "REFUSED", msg_refused)],
    )
    assert classify_no_nameservers_error(exc_refused)[0] == "REFUSED"

    msg_servfail = dns.message.make_response(req)
    msg_servfail.set_rcode(dns.rcode.SERVFAIL)
    exc_servfail = dns.resolver.NoNameservers(
        request=req,
        errors=[("10.0.0.1", False, 53, "SERVFAIL", msg_servfail)],
    )
    assert classify_no_nameservers_error(exc_servfail)[0] == "SERVFAIL"


def test_ordinary_302_login_page_is_clue_only_not_confirmed_portal() -> None:
    ev = evaluate_portal_evidence(
        probed_url="https://mail.example.com",
        is_known_check_endpoint=False,
        expected_status=None,
        expected_body_prefix=None,
        expected_header_key=None,
        expected_header_value=None,
        outcome="response",
        status_code=302,
        headers={"Location": "https://sso.example.com/login"},
        body_snippet="",
        trusted_portal_hosts=["auth.campus.edu.test"],
    )
    assert ev["portal_state"] == "unknown"
    assert ev["login_url_trusted"] is False
    assert ev["candidate_login_url"] is None


def test_domain_suffix_matching_blocks_evil_trailing_domain_and_prefix_collision() -> None:
    cfg = ToolConfig(mode="mock", scenario_id="case_01_normal")
    cfg.proxy.routing_rules = [
        RoutingRuleConfig(
            rule_id="r1",
            match_type="domain_suffix",
            pattern="school.example.test",
            policy="direct",
            user_confirmed=True,
        )
    ]
    proxy_state = {
        "hkcu_internet_settings": {"proxy_enable": False},
        "env_proxies": {},
        "local_port_probes": [],
        "configured_client": {"mode": "direct", "tun_state": "disabled"},
    }

    # Exact and genuine subdomain MUST match
    d1, _, _, _ = evaluate_target_routing_rule(
        url_or_domain="school.example.test",
        probe_cfg=cfg.probe,
        proxy_cfg=cfg.proxy,
        proxy_state_data=proxy_state,
    )
    assert d1["expected_policy"] == "direct"
    assert d1["conflict"] is False

    d2, _, _, _ = evaluate_target_routing_rule(
        url_or_domain="portal.school.example.test",
        probe_cfg=cfg.probe,
        proxy_cfg=cfg.proxy,
        proxy_state_data=proxy_state,
    )
    assert d2["expected_policy"] == "direct"

    # Spoofed trailing domain and prefix collision MUST NOT match!
    for spoofed in ("school.example.test.evil.test", "myschool.example.test"):
        d_bad, _, _, _ = evaluate_target_routing_rule(
            url_or_domain=spoofed,
            probe_cfg=cfg.probe,
            proxy_cfg=cfg.proxy,
            proxy_state_data=proxy_state,
        )
        assert d_bad["expected_policy"] == "unknown", f"Spoofed host {spoofed} wrongly matched!"

    assert does_proxy_override_bypass("intranet", "<local>") is True
    assert does_proxy_override_bypass("jwxt.campus.edu.test", "<local>") is False


@pytest.mark.parametrize(
    ("bad_domain",),
    [
        ("https://example.com/path",),
        ("10.10.0.1",),
        ("example.com:8080",),
        ("-badlabel.com",),
        ("singlelabel",),
        ("bad\x00domain.com",),
    ],
)
def test_invalid_dns_domains_rejected(bad_domain: str) -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    res = svc.query_dns(bad_domain)
    assert res["success"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT.value


def test_idna_chinese_domain_accepted() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    res = svc.query_dns("清华大学.cn")
    assert res["success"] is True
    assert res["data"]["ascii_domain"].startswith("xn--")


@pytest.mark.parametrize(
    ("bad_url", "expected_code"),
    [
        ("ftp://example.com/file", ErrorCode.INVALID_INPUT.value),
        ("http://user:pass@example.com/", ErrorCode.INVALID_INPUT.value),
        ("http://127.0.0.1:8080/admin", ErrorCode.INVALID_INPUT.value),
        ("http://localhost/status", ErrorCode.INVALID_INPUT.value),
        ("http://169.254.169.254/latest/meta-data/", ErrorCode.INVALID_INPUT.value),
        ("http://192.168.1.1/", ErrorCode.PERMISSION_DENIED.value),
    ],
)
def test_ssrf_and_invalid_urls_rejected(bad_url: str, expected_code: str) -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    res = svc.probe_connectivity(target_url=bad_url)
    assert res["success"] is False
    assert res["error"]["code"] == expected_code


@pytest.mark.parametrize("bad_count", [0, -1, 11, True, False, "4"])
def test_icmp_out_of_bounds_count_and_flag_injection_rejected(bad_count: object) -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    res = svc.measure_latency_and_loss("223.5.5.5", count=bad_count)  # type: ignore[arg-type]
    assert res["success"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT.value

    res_flag = svc.measure_latency_and_loss("-t", count=4)
    assert res_flag["success"] is False
    assert res_flag["error"]["code"] == ErrorCode.INVALID_INPUT.value
