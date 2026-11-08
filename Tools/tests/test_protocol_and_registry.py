"""Tests for v1 protocol envelope, host registry boundary, packaging resources, and audit redaction."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from netpet_tools import ErrorCode, ToolConfig, ToolService

EXPECTED_ENVELOPE_KEYS = {
    "schema_version",
    "tool",
    "success",
    "status",
    "timestamp",
    "duration_ms",
    "mode",
    "scenario_id",
    "data",
    "summary",
    "warnings",
    "error",
}

EXPECTED_MODEL_TOOLS = [
    "get_network_snapshot",
    "query_dns",
    "probe_connectivity",
    "measure_latency_and_loss",
    "get_proxy_state",
    "check_target_routing_rule",
    "plan_action",
]


def assert_valid_v1_envelope(payload: dict[str, Any], *, expected_tool: str, expected_mode: str = "mock") -> None:
    assert isinstance(payload, dict)
    assert set(payload.keys()) == EXPECTED_ENVELOPE_KEYS
    assert payload["schema_version"] == "1.0"
    assert payload["tool"] == expected_tool
    assert payload["mode"] == expected_mode
    assert payload["status"] in ("ok", "partial", "error")
    assert isinstance(payload["duration_ms"], (int, float)) and payload["duration_ms"] >= 0.0
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["summary"], str) and payload["summary"]

    # Must strictly serialize with stdlib json.dumps without custom default hooks
    encoded = json.dumps(payload, ensure_ascii=False)
    assert isinstance(encoded, str)

    if payload["status"] == "error":
        assert payload["success"] is False
        assert isinstance(payload["error"], dict)
        assert set(payload["error"].keys()) == {"code", "message", "retryable"}
    else:
        assert payload["success"] is True
        assert payload["error"] is None


def test_available_tools_exact_registry_and_annotations() -> None:
    service = ToolService(ToolConfig(mode="mock", scenario_id="case_01_normal"))
    tools = service.available_tools

    assert list(tools.keys()) == EXPECTED_MODEL_TOOLS
    assert "approve_action" not in tools
    assert "execute_action" not in tools

    # Mutating returned dict must not affect service registry
    tools["injected"] = lambda: {}
    assert "injected" not in service.available_tools

    for name, fn in service.available_tools.items():
        assert callable(fn)
        assert fn.__doc__ and len(fn.__doc__.strip()) > 20, f"Missing docstring on {name}"
        sig = inspect.signature(fn)
        assert sig.return_annotation != inspect.Signature.empty, f"Missing return annotation on {name}"


def test_missing_or_invalid_scenario_never_falls_back_to_real() -> None:
    svc_missing = ToolService(ToolConfig(mode="mock", scenario_id="nonexistent_case_99"))
    res = svc_missing.get_network_snapshot()
    assert_valid_v1_envelope(res, expected_tool="get_network_snapshot")
    assert res["status"] == "error"
    assert res["error"]["code"] == ErrorCode.INVALID_SCENARIO.value

    svc_none = ToolService(ToolConfig(mode="mock", scenario_id=None))
    res2 = svc_none.query_dns("example.com")
    assert_valid_v1_envelope(res2, expected_tool="query_dns")
    assert res2["status"] == "error"
    assert res2["error"]["code"] == ErrorCode.INVALID_SCENARIO.value

    svc_traversal = ToolService(ToolConfig(mode="mock", scenario_id="../case_01_normal"))
    res3 = svc_traversal.get_proxy_state()
    assert res3["status"] == "error"
    assert res3["error"]["code"] == ErrorCode.INVALID_SCENARIO.value


def test_import_and_execution_from_arbitrary_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert Path.cwd() == tmp_path

    svc = ToolService(ToolConfig(mode="mock", scenario_id="case_02_portal"))
    snap = svc.get_network_snapshot()
    assert_valid_v1_envelope(snap, expected_tool="get_network_snapshot")
    assert snap["success"] is True
    assert snap["scenario_id"] == "case_02_portal"


def test_repo_scenarios_and_packaged_scenarios_are_identical() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo_scenarios = sorted((repo_root / "scenarios").glob("*.json"))
    pkg_scenarios = sorted((repo_root / "src" / "netpet_tools" / "_builtin_scenarios").glob("*.json"))

    assert [p.name for p in repo_scenarios] == [p.name for p in pkg_scenarios]
    assert len(repo_scenarios) == 6
    for r_file, p_file in zip(repo_scenarios, pkg_scenarios, strict=True):
        assert r_file.read_text(encoding="utf-8") == p_file.read_text(encoding="utf-8")


def test_audit_log_redacts_tokens_and_url_query_values(tmp_path: Path) -> None:
    log_file = tmp_path / "audit.jsonl"
    cfg = ToolConfig(
        mode="mock",
        scenario_id="case_02_portal",
        audit_log_path=str(log_file),
    )
    # Instantiation alone must NOT create audit log file
    svc = ToolService(cfg)
    assert not log_file.exists()

    sensitive_portal_url = "https://auth.campus.edu.test/portal/login?ticket=SECRET_TICKET_12345&password=MY_PASSWORD"
    plan_res = svc.plan_action("open_browser_url", {"url": sensitive_portal_url})
    assert plan_res["success"] is True
    plan_id = plan_res["data"]["plan_id"]

    token = svc.approve_action(plan_id)
    exec_res = svc.execute_action(plan_id, approval_token=token)
    assert exec_res["success"] is True

    raw_log = log_file.read_text(encoding="utf-8")
    assert token not in raw_log
    assert "SECRET_TICKET_12345" not in raw_log
    assert "MY_PASSWORD" not in raw_log

    lines = [json.loads(line) for line in raw_log.strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["tool"] == "plan_action"
    assert lines[1]["tool"] == "execute_action"
    assert lines[1]["params"]["approval_token"] == "<redacted>"
