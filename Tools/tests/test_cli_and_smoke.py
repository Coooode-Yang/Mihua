"""Tests for CLI entry point, host integration example, and optional manual real smoke tests."""

from __future__ import annotations

import json
import pytest

from netpet_tools import ToolConfig, ToolService
from netpet_tools.cli import main as cli_main
from examples.host_integration import run_demo


def test_cli_mock_snapshot_and_list_commands(capsys: pytest.CaptureFixture[str]) -> None:
    rc_list = cli_main(["--list-tools"])
    assert rc_list == 0
    out_tools = json.loads(capsys.readouterr().out)
    assert len(out_tools["available_tools"]) == 7

    rc_scen = cli_main(["--list-scenarios"])
    assert rc_scen == 0
    out_scen = json.loads(capsys.readouterr().out)
    assert "case_02_portal" in out_scen["builtin_scenarios"]

    rc_snap = cli_main(
        ["--mode", "mock", "--scenario", "case_02_portal", "--tool", "get_network_snapshot"]
    )
    assert rc_snap == 0
    snap_json = json.loads(capsys.readouterr().out)
    assert snap_json["schema_version"] == "1.0"
    assert snap_json["mode"] == "mock"
    assert snap_json["scenario_id"] == "case_02_portal"
    assert snap_json["success"] is True


def test_cli_rejects_execute_action_and_disables_writes(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["--mode", "mock", "--scenario", "case_01_normal", "--tool", "execute_action"])
    assert rc == 2
    err = json.loads(capsys.readouterr().err)
    assert "execute_action" in err["error"]


def test_host_integration_example_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    run_demo()
    output = capsys.readouterr().out
    assert "recovery_status" in output
    assert '"recovered"' in output


@pytest.mark.real_smoke
def test_manual_real_readonly_smoke() -> None:
    """Manual read-only smoke test against real Windows host (deselected by default)."""
    svc = ToolService(ToolConfig(mode="real", allow_writes=False))
    snap = svc.get_network_snapshot()
    assert snap["mode"] == "real"
    assert snap["scenario_id"] is None
    assert snap["status"] in ("ok", "partial")

    proxy = svc.get_proxy_state()
    assert proxy["mode"] == "real"
    assert proxy["status"] in ("ok", "partial")
