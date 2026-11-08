"""Security, authorization boundary, TTL expiration, replay protection, and conditional rollback tests."""

from __future__ import annotations

import pytest

from netpet_tools import ActionApprovalError, ErrorCode, ToolConfig, ToolService


def test_execute_without_approval_or_wrong_token_rejected() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict"))
    plan_res = svc.plan_action("flush_dns")
    plan_id = plan_res["data"]["plan_id"]

    # Unapproved plan
    r1 = svc.execute_action(plan_id, approval_token="forged_token_123")
    assert r1["success"] is False
    assert r1["error"]["code"] == ErrorCode.PERMISSION_DENIED.value
    assert r1["data"]["execution_status"] == "not_executed"

    # Approved plan, wrong token
    real_token = svc.approve_action(plan_id)
    r2 = svc.execute_action(plan_id, approval_token="wrong_token")
    assert r2["success"] is False
    assert r2["error"]["code"] == ErrorCode.PERMISSION_DENIED.value
    assert r2["data"]["execution_status"] == "not_executed"

    # Valid token still works once
    r3 = svc.execute_action(plan_id, approval_token=real_token)
    assert r3["success"] is True
    assert r3["data"]["execution_status"] == "succeeded"
    assert r3["data"]["recovery_status"] == "not_checked"  # No verification_target provided!

    # Single-use token replay MUST fail
    r4 = svc.execute_action(plan_id, approval_token=real_token)
    assert r4["success"] is False
    assert r4["error"]["code"] == ErrorCode.PERMISSION_DENIED.value
    assert r4["data"]["execution_status"] == "not_executed"
    assert r4["data"]["reason"] == "replay_rejected"


def test_expired_plan_rejected_for_approval_and_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])

    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal", plan_ttl_seconds=30))
    plan_a = svc.plan_action("flush_dns")["data"]["plan_id"]
    plan_b = svc.plan_action("flush_dns")["data"]["plan_id"]
    token_b = svc.approve_action(plan_b)

    # Advance monotonic clock past 30s TTL
    clock["now"] = 1045.0

    with pytest.raises(ActionApprovalError) as exc_info:
        svc.approve_action(plan_a)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT

    res_b = svc.execute_action(plan_b, approval_token=token_b)
    assert res_b["success"] is False
    assert res_b["error"]["code"] == ErrorCode.INVALID_INPUT.value
    assert res_b["data"]["execution_status"] == "not_executed"
    assert res_b["data"]["reason"] == "plan_expired"


def test_real_mode_allow_writes_false_blocks_execution_before_os_touch() -> None:
    svc = ToolService(ToolConfig(mode="real", allow_writes=False))
    plan = svc.plan_action("flush_dns")
    assert plan["success"] is True
    plan_id = plan["data"]["plan_id"]

    tok = svc.approve_action(plan_id)
    res = svc.execute_action(plan_id, approval_token=tok)
    assert res["success"] is False
    assert res["error"]["code"] == ErrorCode.PERMISSION_DENIED.value
    assert res["data"]["execution_status"] == "not_executed"
    assert res["data"]["reason"] == "allow_writes_disabled"


def test_open_browser_url_blocks_untrusted_host() -> None:
    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_02_portal"))
    bad_plan = svc.plan_action("open_browser_url", {"url": "https://phishing.evil.example.com/login"})
    assert bad_plan["success"] is False
    assert bad_plan["error"]["code"] == ErrorCode.PERMISSION_DENIED.value


def test_proxy_rollback_succeeds_when_unmodified_and_aborts_on_concurrent_change() -> None:
    # 1. Clean rollback when write fails without external concurrent mutation
    svc1 = ToolService(ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict"))
    state1, _ = svc1._get_mock_context()
    state1["simulated_action_faults"] = {"set_system_proxy_fail_after_write": True}

    p1 = svc1.plan_action("set_system_proxy", {"enable": False})["data"]["plan_id"]
    t1 = svc1.approve_action(p1)
    r1 = svc1.execute_action(p1, approval_token=t1)
    assert r1["success"] is False
    assert r1["data"]["execution_status"] == "failed"
    assert r1["data"]["rollback_status"] == "succeeded"
    # Original state (proxy_enable=True) restored
    assert svc1.get_proxy_state()["data"]["hkcu_internet_settings"]["proxy_enable"] is True

    # 2. Aborted rollback when another process modified HKCU proxy settings concurrently
    svc2 = ToolService(ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict"))
    state2, _ = svc2._get_mock_context()
    state2["simulated_action_faults"] = {
        "set_system_proxy_fail_after_write": True,
        "concurrent_external_proxy_mutation": True,
    }

    p2 = svc2.plan_action("set_system_proxy", {"enable": False})["data"]["plan_id"]
    t2 = svc2.approve_action(p2)
    r2 = svc2.execute_action(p2, approval_token=t2)
    assert r2["success"] is False
    assert r2["data"]["execution_status"] == "failed"
    assert r2["data"]["rollback_status"] == "failed"
    # Concurrent external change ('127.0.0.1:19999') must NOT be clobbered!
    assert svc2.get_proxy_state()["data"]["hkcu_internet_settings"]["proxy_server"] == "127.0.0.1:19999"
