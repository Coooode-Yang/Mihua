"""Tests for dynamic campus profile registration and LLM domain verification."""

from __future__ import annotations

import pytest

from netpet_tools import ErrorCode, ToolConfig, ToolExecutionError, ToolService


def test_set_campus_domain_updates_rules_and_allows_portal_browser() -> None:
    service = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))

    # Before registration: bnuz.edu.cn has no explicit rule
    r_before = service.check_target_routing_rule("https://jwxt.bnuz.edu.cn/home")
    assert r_before["data"]["expected_policy"] == "unknown"

    # User inputs university name -> LLM infers bnuz.edu.cn -> Host sets it
    reg_res = service.set_campus_domain("bnuz.edu.cn", alias_name="北京师范大学珠海校区")
    assert reg_res["success"] is True
    assert reg_res["domain"] == "bnuz.edu.cn"
    assert reg_res["alias_name"] == "北京师范大学珠海校区"

    # After registration: jwxt.bnuz.edu.cn immediately matches direct policy with user confirmation
    r_after = service.check_target_routing_rule("https://jwxt.bnuz.edu.cn/home")
    assert r_after["data"]["expected_policy"] == "direct"
    assert r_after["data"]["matched_rule"]["user_confirmed"] is True

    # After registration: open_browser_url to campus portal is allowlisted
    plan = service.plan_action("open_browser_url", {"url": "https://auth.bnuz.edu.cn/portal/login"})
    assert plan["success"] is True

    # Active profile getter reflects the added campus
    prof = service.get_campus_profile()
    assert "bnuz.edu.cn" in prof["trusted_portal_hosts"]
    assert "bnuz.edu.cn" in prof["allowed_browser_hosts"]
    assert any(r["pattern"] == "bnuz.edu.cn" for r in prof["campus_routing_rules"])


def test_set_campus_domain_with_auto_verify_dns() -> None:
    service = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))

    # Case A: domain resolvable in mock scenario
    res_valid = service.set_campus_domain(
        "www.campus.edu.test",
        alias_name="测试高校",
        auto_verify_dns=True,
    )
    assert res_valid["success"] is True
    assert res_valid["dns_verified"] is True
    assert res_valid["dns_primary_status"] == "NOERROR"
    assert len(res_valid["dns_records"]) > 0

    # Case B: hallucinated domain returning NXDOMAIN with enforce_resolution=True
    res_fake = service.set_campus_domain(
        "hallucinated-bnu-school.edu.test",
        alias_name="幻觉高校",
        auto_verify_dns=True,
        enforce_resolution=True,
    )
    assert res_fake["success"] is False
    assert res_fake["dns_verified"] is False
    assert "未能通过 DNS 解析验证" in res_fake["summary"]


def test_tool_config_with_campus_factory() -> None:
    cfg = ToolConfig.with_campus("bnu.edu.cn", alias_name="北京师范大学", mode="mock")
    assert "bnu.edu.cn" in cfg.probe.trusted_portal_hosts
    assert "bnu.edu.cn" in cfg.probe.allowed_browser_hosts
    assert "bnu.edu.cn" in cfg.probe.allowed_private_targets
    assert any(r.pattern == "bnu.edu.cn" and r.policy == "direct" for r in cfg.proxy.routing_rules)

    svc = ToolService(cfg)
    rule_res = svc.check_target_routing_rule("mail.bnu.edu.cn")
    assert rule_res["data"]["expected_policy"] == "direct"


def test_invalid_campus_domain_rejected() -> None:
    service = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    with pytest.raises(ToolExecutionError) as exc_info:
        service.set_campus_domain("invalid domain with spaces")
    assert exc_info.value.code == ErrorCode.INVALID_INPUT
