"""Read-only command-line runner for netpet_tools diagnostics and action planning."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from netpet_tools.config import ToolConfig
from netpet_tools.service import ToolService

BUILTIN_SCENARIOS = (
    "case_01_normal",
    "case_02_portal",
    "case_03_loss",
    "case_04_proxy_conflict",
    "case_05_incomplete",
    "case_06_forbidden",
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m netpet_tools.cli",
        description="Run read-only netpet_tools diagnostics or action planning (writes are never executed via CLI).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Optional path to ToolConfig JSON file.",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "real"),
        default=None,
        help="Execution mode ('mock' by default).",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Mock scenario ID (e.g. case_02_portal).",
    )
    parser.add_argument(
        "--tool",
        type=str,
        default=None,
        help=(
            "Model-facing tool name to invoke: get_network_snapshot, query_dns, "
            "probe_connectivity, measure_latency_and_loss, get_proxy_state, "
            "check_target_routing_rule, or plan_action."
        ),
    )
    parser.add_argument(
        "--params",
        type=str,
        default=None,
        help="JSON object string of keyword arguments passed to the selected tool.",
    )
    # Convenient optional shorthand flags for common single-tool CLI invocations
    parser.add_argument("--interface", type=str, default=None, help="Shorthand parameter for get_network_snapshot.")
    parser.add_argument("--domain", type=str, default=None, help="Shorthand parameter for query_dns.")
    parser.add_argument("--compare-public", action="store_true", help="Shorthand compare_public=True for query_dns.")
    parser.add_argument("--target-url", type=str, default=None, help="Shorthand target_url for probe_connectivity or check_target_routing_rule.")
    parser.add_argument("--route", type=str, default=None, help="Shorthand route ('direct' or 'configured_proxy') for probe_connectivity.")
    parser.add_argument("--host", type=str, default=None, help="Shorthand host for measure_latency_and_loss.")
    parser.add_argument("--count", type=int, default=None, help="Shorthand packet count (1-10) for measure_latency_and_loss.")
    parser.add_argument("--list-tools", action="store_true", help="List available read/plan tools and exit.")
    parser.add_argument("--list-scenarios", action="store_true", help="List built-in offline scenarios and exit.")
    return parser


def _build_tool_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if args.params:
        parsed = json.loads(args.params)
        if not isinstance(parsed, dict):
            raise ValueError("--params must decode to a JSON object (dictionary).")
        kwargs.update(parsed)

    if args.tool == "get_network_snapshot" and args.interface is not None:
        kwargs.setdefault("interface", args.interface)
    elif args.tool == "query_dns":
        if args.domain is not None:
            kwargs.setdefault("domain", args.domain)
        if args.compare_public:
            kwargs.setdefault("compare_public", True)
    elif args.tool == "probe_connectivity":
        if args.target_url is not None:
            kwargs.setdefault("target_url", args.target_url)
        if args.route is not None:
            kwargs.setdefault("route", args.route)
    elif args.tool == "measure_latency_and_loss":
        if args.host is not None:
            kwargs.setdefault("host", args.host)
        if args.count is not None:
            kwargs.setdefault("count", args.count)
    elif args.tool == "check_target_routing_rule":
        if args.target_url is not None:
            kwargs.setdefault("url_or_domain", args.target_url)
        elif args.domain is not None:
            kwargs.setdefault("url_or_domain", args.domain)

    return kwargs


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print(json.dumps({"builtin_scenarios": list(BUILTIN_SCENARIOS)}, ensure_ascii=False, indent=2))
        return 0

    if args.config:
        cfg = ToolConfig.from_json_file(args.config)
    else:
        cfg = ToolConfig()

    if args.mode is not None:
        cfg.mode = args.mode
    if args.scenario is not None:
        cfg.scenario_id = args.scenario

    # Enforce strict CLI safety: CLI never enables write operations
    cfg.allow_writes = False

    service = ToolService(cfg)
    tools = service.available_tools

    if args.list_tools:
        print(json.dumps({"available_tools": list(tools.keys())}, ensure_ascii=False, indent=2))
        return 0

    if not args.tool:
        parser.error("--tool is required unless --list-tools or --list-scenarios is specified.")

    if args.tool not in tools:
        err_out = {
            "error": f"Unsupported or non-CLI tool '{args.tool}'. Allowed CLI tools: {list(tools.keys())}. "
            "Write execution is restricted to host programmatic confirmation."
        }
        print(json.dumps(err_out, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    try:
        kwargs = _build_tool_kwargs(args)
    except Exception as exc:
        print(json.dumps({"error": f"Invalid CLI parameters: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    try:
        result = tools[args.tool](**kwargs)
    except TypeError as exc:
        print(json.dumps({"error": f"Tool parameter mismatch: {exc}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
