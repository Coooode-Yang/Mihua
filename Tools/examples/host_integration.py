"""Minimal host-side integration example (offline mock mode, no GUI or LLM dependency).

Demonstrates:
1. Instantiating ToolService with instance-scoped ToolConfig.
2. Exposing ONLY the 7 read/plan functions via service.available_tools to the Agent/LLM.
3. Gathering comparative evidence on case_04_proxy_conflict.
4. Running the model-side plan_action -> host-side approve_action -> host-side execute_action flow,
   including automatic original-target re-verification.
"""

from __future__ import annotations

import json
import sys

from netpet_tools import ToolConfig, ToolService


def run_demo() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    config = ToolConfig(mode="mock", scenario_id="case_04_proxy_conflict", allow_writes=False)
    service = ToolService(config)

    model_tools = service.available_tools
    print("1. Model-exposed tool registry (7 tools):")
    print("  ", list(model_tools.keys()))
    assert "approve_action" not in model_tools
    assert "execute_action" not in model_tools

    target_url = "https://jwxt.campus.edu.test"

    print("\n2. Model invokes diagnostic tools to gather comparative observations:")
    proxy_obs = model_tools["get_proxy_state"]()
    rule_obs = model_tools["check_target_routing_rule"](url_or_domain=target_url)
    direct_probe = model_tools["probe_connectivity"](target_url=target_url, route="direct")
    proxy_probe = model_tools["probe_connectivity"](target_url=target_url, route="configured_proxy")

    print("   - Proxy summary:", proxy_obs["summary"])
    print("   - Routing rule check:", rule_obs["summary"], "| missing_evidence:", rule_obs["data"]["missing_evidence"])
    print("   - Direct probe:", direct_probe["summary"])
    print("   - Configured proxy probe:", proxy_probe["summary"])

    print("\n3. Model proposes a controlled action plan (does NOT modify system):")
    plan_res = model_tools["plan_action"](
        action_name="set_system_proxy",
        params={"enable": False},
        verification_target=target_url,
    )
    plan_id = plan_res["data"]["plan_id"]
    print("   - Planned:", plan_id, "| target:", plan_res["data"]["target"])

    print("\n4. Host confirmation dialog approves plan and executes with single-use token:")
    # NOTE: approval_token stays strictly inside the host UI/controller layer; never pass it to LLM context.
    approval_token = service.approve_action(plan_id)
    exec_res = service.execute_action(plan_id, approval_token=approval_token)

    print("   - Execution summary:", exec_res["summary"])
    print(
        "   - Structured result data:\n",
        json.dumps(exec_res["data"], ensure_ascii=False, indent=2),
    )

    print("\n5. Dynamic campus registration (user inputs university name -> LLM infers domain):")
    reg = service.set_campus_domain("bnuz.edu.cn", alias_name="北京师范大学珠海校区")
    print("   - Dynamic profile status:", reg["summary"])
    rule_bnuz = model_tools["check_target_routing_rule"](url_or_domain="https://jwxt.bnuz.edu.cn/home")
    print(
        "   - Target 'jwxt.bnuz.edu.cn' auto-matched policy:",
        rule_bnuz["data"]["expected_policy"],
        "| confirmed:",
        rule_bnuz["data"]["matched_rule"]["user_confirmed"],
    )


if __name__ == "__main__":
    run_demo()
