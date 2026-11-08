"""Primary host-facing ToolService and model-safe tool registry."""

from __future__ import annotations

import hmac
import threading
import time
from typing import Any, Callable, Literal

from netpet_tools.base import (
    AuditLogger,
    ErrorCode,
    ToolExecutionError,
    build_error_result,
    build_ok_result,
    normalize_and_validate_domain,
    validate_safe_text,
)
from netpet_tools.config import RoutingRuleConfig, ToolConfig
from netpet_tools.dns_diagnostics import collect_mock_dns, collect_real_dns
from netpet_tools.mock_environment import MockEnvironment
from netpet_tools.network_snapshot import (
    collect_mock_network_snapshot,
    collect_real_network_snapshot,
)
from netpet_tools.proxy_manager import (
    collect_mock_proxy_state,
    collect_real_proxy_state,
    evaluate_target_routing_rule,
)
from netpet_tools.safe_actions import (
    ActionPlanRecord,
    _hash_token,
    build_not_executed_payload,
    execute_approved_plan,
    format_plan_for_public_output,
    issue_host_approval_token,
    validate_and_create_plan,
)
from netpet_tools.web_probe import (
    collect_mock_icmp,
    collect_mock_probe_connectivity,
    collect_real_icmp,
    collect_real_probe_connectivity,
)


class ToolService:
    """Instance-scoped network diagnostic and controlled-action service.

    Each instance holds isolated configuration, action plans, single-use host
    approval tokens, and (in mock mode) isolated scenario state.
    """

    def __init__(self, config: ToolConfig | None = None) -> None:
        if config is not None and not isinstance(config, ToolConfig):
            raise TypeError("config must be a ToolConfig instance or None")
        self._config: ToolConfig = (config or ToolConfig()).model_copy(deep=True)
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._audit = AuditLogger(jsonl_path=self._config.audit_log_path)
        self._mock_env: MockEnvironment | None = (
            MockEnvironment(self._config) if self._config.mode == "mock" else None
        )
        self._plans: dict[str, ActionPlanRecord] = {}

    @property
    def config(self) -> ToolConfig:
        """Return an isolated copy of this service's configuration."""
        return self._config.model_copy(deep=True)

    @property
    def mode(self) -> Literal["mock", "real"]:
        """Return active execution mode ('mock' or 'real')."""
        return self._config.mode

    @property
    def scenario_id(self) -> str | None:
        """Return scenario_id when in mock mode, else None."""
        return self._config.scenario_id if self._config.mode == "mock" else None

    @property
    def available_tools(self) -> dict[str, Callable[..., dict[str, Any]]]:
        """Return the exact v1 registry of 7 model-callable tools (excludes approve/execute)."""
        return {
            "get_network_snapshot": self.get_network_snapshot,
            "query_dns": self.query_dns,
            "probe_connectivity": self.probe_connectivity,
            "measure_latency_and_loss": self.measure_latency_and_loss,
            "get_proxy_state": self.get_proxy_state,
            "check_target_routing_rule": self.check_target_routing_rule,
            "plan_action": self.plan_action,
        }

    def get_audit_records(self) -> list[dict[str, Any]]:
        """Return sanitized recent tool invocation audit records for this service instance."""
        return self._audit.get_recent_records()

    def set_campus_domain(
        self,
        domain: str,
        alias_name: str | None = None,
        *,
        auto_verify_dns: bool = False,
        enforce_resolution: bool = False,
    ) -> dict[str, Any]:
        """Host-facing API: dynamically register or update active campus domain and routing policies.

        Called when user provides school name and LLM resolves candidate campus domain (e.g. 'bnuz.edu.cn').

        Args:
            domain: University/campus domain name (e.g. 'bnuz.edu.cn' or 'bnu.edu.cn').
            alias_name: Optional human-readable school name (e.g. '北京师范大学珠海校区').
            auto_verify_dns: When True, queries DNS to check if the domain resolves to IP(s).
            enforce_resolution: When True, refuses registration if DNS resolution returns NXDOMAIN/TIMEOUT.

        Returns:
            Dict containing registration status, validated domain, DNS verification result, and summary.
        """
        clean_domain = validate_safe_text(domain, "domain", max_len=255)
        ascii_dom, disp_dom = normalize_and_validate_domain(clean_domain)
        clean_alias = (
            validate_safe_text(alias_name, "alias_name", max_len=128)
            if alias_name
            else None
        )

        dns_verified: bool | None = None
        dns_status: str | None = None
        dns_records: list[str] = []

        if auto_verify_dns:
            dns_res = self.query_dns(disp_dom, compare_public=False)
            prim = (dns_res.get("data") or {}).get("primary_result") or {}
            dns_status = prim.get("status")
            dns_records = list(prim.get("records") or [])
            dns_verified = bool(dns_status == "NOERROR" and dns_records)

            if enforce_resolution and not dns_verified:
                return {
                    "success": False,
                    "domain": disp_dom,
                    "ascii_domain": ascii_dom,
                    "alias_name": clean_alias,
                    "dns_verified": False,
                    "dns_primary_status": dns_status,
                    "dns_records": dns_records,
                    "summary": f"域名 '{disp_dom}' 未能通过 DNS 解析验证（状态={dns_status}），未生效。",
                }

        rule_id = f"campus_direct_{ascii_dom.replace('.', '_')}"
        new_rule = RoutingRuleConfig(
            rule_id=rule_id,
            match_type="domain_suffix",
            pattern=ascii_dom,
            policy="direct",
            source=f"user_campus_input:{clean_alias}" if clean_alias else "user_campus_input",
            user_confirmed=True,
            description=f"Direct routing rule for {clean_alias or disp_dom} campus network",
        )

        with self._lock:
            if ascii_dom not in self._config.probe.trusted_portal_hosts:
                self._config.probe.trusted_portal_hosts.append(ascii_dom)
            if ascii_dom not in self._config.probe.allowed_browser_hosts:
                self._config.probe.allowed_browser_hosts.append(ascii_dom)
            if ascii_dom not in self._config.probe.allowed_private_targets:
                self._config.probe.allowed_private_targets.append(ascii_dom)

            self._config.proxy.routing_rules = [
                r for r in self._config.proxy.routing_rules if r.pattern != ascii_dom
            ] + [new_rule]

            if self._mock_env is not None:
                self._mock_env.update_campus_domain(ascii_dom, new_rule)

        dns_note = ""
        if auto_verify_dns:
            dns_note = "，DNS解析核实有效" if dns_verified else f"，DNS解析状态为 {dns_status}"

        summary = (
            f"已成功设置校园主域名 '{disp_dom}'"
            + (f"（{clean_alias}）" if clean_alias else "")
            + dns_note
            + "；认证白名单与直连策略已动态生效。"
        )

        return {
            "success": True,
            "domain": disp_dom,
            "ascii_domain": ascii_dom,
            "alias_name": clean_alias,
            "dns_verified": dns_verified,
            "dns_primary_status": dns_status,
            "dns_records": dns_records,
            "summary": summary,
        }

    def get_campus_profile(self) -> dict[str, Any]:
        """Host-facing API: return currently registered campus domains and active direct routing rules."""
        with self._lock:
            rules = [
                r.model_dump(mode="json")
                for r in self._config.proxy.routing_rules
                if "campus" in r.source.lower() or "campus" in r.rule_id.lower()
            ]
            return {
                "trusted_portal_hosts": list(self._config.probe.trusted_portal_hosts),
                "allowed_browser_hosts": list(self._config.probe.allowed_browser_hosts),
                "allowed_private_targets": list(self._config.probe.allowed_private_targets),
                "campus_routing_rules": rules,
            }

    # ------------------------------------------------------------------
    # Internal Runner & State Helpers
    # ------------------------------------------------------------------

    def _get_mock_context(self) -> tuple[dict[str, Any], ToolConfig]:
        assert self._mock_env is not None
        with self._lock:
            return self._mock_env.ensure_loaded(self._config)

    def _run_tool(
        self,
        tool_name: str,
        params_for_log: dict[str, Any],
        fn: Callable[[], tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]],
        *,
        record_audit: bool = True,
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            data, status, warnings, summary = fn()
            res = build_ok_result(
                tool=tool_name,
                mode=self._config.mode,
                scenario_id=self._config.scenario_id,
                started_monotonic=t0,
                data=data,
                summary=summary,
                status=status,
                warnings=warnings,
            )
        except ToolExecutionError as exc:
            res = build_error_result(
                tool=tool_name,
                mode=self._config.mode,
                scenario_id=self._config.scenario_id,
                started_monotonic=t0,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                data=exc.data,
                warnings=exc.warnings,
            )
        except Exception as exc:
            res = build_error_result(
                tool=tool_name,
                mode=self._config.mode,
                scenario_id=self._config.scenario_id,
                started_monotonic=t0,
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Unexpected error in {tool_name}: {type(exc).__name__}: {exc}",
                retryable=False,
            )

        if record_audit:
            self._audit.record(
                tool=tool_name,
                mode=self._config.mode,
                scenario_id=self._config.scenario_id,
                params=params_for_log,
                result=res,
            )
        return res

    # ------------------------------------------------------------------
    # Public Model-Callable Diagnostic & Planning Tools (7 tools)
    # ------------------------------------------------------------------

    def get_network_snapshot(
        self,
        interface: str = "auto",
        include_recent_events: bool = False,
    ) -> dict[str, Any]:
        """Read local network interface state, IPv4/IPv6 addresses, default routes, DNS, DHCP, and Wi-Fi metrics.

        Args:
            interface: Adapter name/description or 'auto' to select the active egress candidate.
            include_recent_events: Whether to query recent Windows WLAN AutoConfig events.

        Returns:
            Unified v1 ToolResult dictionary containing candidate and selected interface observations.
        """

        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if not isinstance(include_recent_events, bool):
                raise ToolExecutionError(
                    ErrorCode.INVALID_INPUT,
                    "Parameter 'include_recent_events' must be a boolean.",
                )
            if self._config.mode == "mock":
                with self._lock:
                    state, _ = self._get_mock_context()
                    return collect_mock_network_snapshot(
                        state,
                        interface=interface,
                        include_recent_events=include_recent_events,
                    )
            return collect_real_network_snapshot(
                interface=interface,
                include_recent_events=include_recent_events,
            )

        return self._run_tool(
            "get_network_snapshot",
            {"interface": interface, "include_recent_events": include_recent_events},
            _impl,
        )

    def query_dns(
        self,
        domain: str,
        dns_server: str | None = None,
        record_type: str = "A",
        compare_public: bool = False,
    ) -> dict[str, Any]:
        """Resolve a domain name (A or AAAA) via system or specified DNS server, optionally comparing public resolvers.

        Args:
            domain: Bare domain name to query (e.g. 'www.example.com').
            dns_server: Optional IPv4/IPv6 resolver address; None queries the system resolver.
            record_type: DNS record type, 'A' or 'AAAA'.
            compare_public: When True, also queries configured public resolvers for comparison.

        Returns:
            Unified v1 ToolResult dictionary with per-resolver records, RCODE statuses, and comparison notes.
        """

        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                with self._lock:
                    state, eff_cfg = self._get_mock_context()
                    return collect_mock_dns(
                        state,
                        eff_cfg.dns,
                        domain=domain,
                        dns_server=dns_server,
                        record_type=record_type,
                        compare_public=compare_public,
                    )
            return collect_real_dns(
                self._config.dns,
                domain=domain,
                dns_server=dns_server,
                record_type=record_type,
                compare_public=compare_public,
            )

        return self._run_tool(
            "query_dns",
            {
                "domain": domain,
                "dns_server": dns_server,
                "record_type": record_type,
                "compare_public": compare_public,
            },
            _impl,
        )

    def probe_connectivity(
        self,
        target_url: str | None = None,
        route: str = "direct",
        timeout_s: float = 3.0,
    ) -> dict[str, Any]:
        """Probe HTTP/HTTPS reachability and captive-portal redirect evidence without auto-following redirects.

        Args:
            target_url: Optional http/https URL to probe; None probes configured connectivity-check endpoints.
            route: Explicit HTTP transport route: 'direct' or 'configured_proxy'.
            timeout_s: Per-probe timeout in seconds (0.2 up to http_total_budget_s).

        Returns:
            Unified v1 ToolResult dictionary with HTTP status, separate TCP/HTTP timings, and portal_state.
        """
        return self._probe_connectivity_internal(
            target_url=target_url,
            route=route,
            timeout_s=timeout_s,
            record_audit=True,
        )

    def _probe_connectivity_internal(
        self,
        *,
        target_url: str | None = None,
        route: str = "direct",
        timeout_s: float = 3.0,
        record_audit: bool = True,
    ) -> dict[str, Any]:
        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                with self._lock:
                    state, eff_cfg = self._get_mock_context()
                    return collect_mock_probe_connectivity(
                        state,
                        eff_cfg.probe,
                        eff_cfg.proxy,
                        target_url=target_url,
                        route=route,
                        timeout_s=timeout_s,
                    )
            return collect_real_probe_connectivity(
                self._config.probe,
                self._config.proxy,
                target_url=target_url,
                route=route,
                timeout_s=timeout_s,
            )

        return self._run_tool(
            "probe_connectivity",
            {"target_url": target_url, "route": route, "timeout_s": timeout_s},
            _impl,
            record_audit=record_audit,
        )

    def measure_latency_and_loss(
        self,
        host: str,
        count: int = 4,
    ) -> dict[str, Any]:
        """Measure ICMP Echo packet loss and RTT samples (1 to 10 packets) toward a gateway or public host.

        Args:
            host: Target IPv4/IPv6 address or domain name (non-loopback, non-link-local).
            count: Number of echo requests to send, integer from 1 to 10.

        Returns:
            Unified v1 ToolResult dictionary with sent/received/lost counts and ICMP RTT statistics.
        """

        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                with self._lock:
                    state, _ = self._get_mock_context()
                    return collect_mock_icmp(state, host=host, count=count)
            return collect_real_icmp(self._config.probe, host=host, count=count)

        return self._run_tool(
            "measure_latency_and_loss",
            {"host": host, "count": count},
            _impl,
        )

    def get_proxy_state(self) -> dict[str, Any]:
        """Read current-user HKCU proxy settings, PAC status, proxy env vars, WinHTTP, local ports, and client state.

        Returns:
            Unified v1 ToolResult dictionary separating HKCU, PAC, env, WinHTTP, port probes, and client controller.
        """
        return self._get_proxy_state_internal(record_audit=True)

    def _get_proxy_state_internal(self, *, record_audit: bool = True) -> dict[str, Any]:
        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                with self._lock:
                    state, eff_cfg = self._get_mock_context()
                    return collect_mock_proxy_state(state, eff_cfg.proxy)
            return collect_real_proxy_state(self._config.proxy)

        return self._run_tool("get_proxy_state", {}, _impl, record_audit=record_audit)

    def check_target_routing_rule(self, url_or_domain: str) -> dict[str, Any]:
        """Query explicit configured routing policy for a target domain/URL and check for confirmed conflicts.

        Args:
            url_or_domain: Target domain name or http/https URL.

        Returns:
            Unified v1 ToolResult dictionary with matched rule, expected policy, conflict status, and missing evidence.
        """

        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                with self._lock:
                    state, eff_cfg = self._get_mock_context()
                    proxy_data, _, _, _ = collect_mock_proxy_state(state, eff_cfg.proxy)
                    return evaluate_target_routing_rule(
                        url_or_domain=url_or_domain,
                        probe_cfg=eff_cfg.probe,
                        proxy_cfg=eff_cfg.proxy,
                        proxy_state_data=proxy_data,
                    )
            proxy_data, _, _, _ = collect_real_proxy_state(self._config.proxy)
            return evaluate_target_routing_rule(
                url_or_domain=url_or_domain,
                probe_cfg=self._config.probe,
                proxy_cfg=self._config.proxy,
                proxy_state_data=proxy_data,
            )

        return self._run_tool(
            "check_target_routing_rule",
            {"url_or_domain": url_or_domain},
            _impl,
        )

    def plan_action(
        self,
        action_name: str,
        params: dict[str, Any] | None = None,
        verification_target: str | None = None,
    ) -> dict[str, Any]:
        """Validate and register a read-only controlled action plan awaiting host user confirmation.

        Supported action_name values:
        - 'flush_dns': params={}
        - 'open_browser_url': params={'url': '<allowlisted http/https URL>'}
        - 'reconnect_wifi': params={'interface': str, 'profile_name': str, 'ssid': Optional[str]}
        - 'set_system_proxy': params={'enable': bool}

        Args:
            action_name: Whitelisted controlled action identifier.
            params: Action parameter dictionary.
            verification_target: Optional original fault URL or domain to re-verify after execution.

        Returns:
            Unified v1 ToolResult dictionary containing plan_id, normalized target, impact, reversibility, and expiry.
        """

        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            with self._lock:
                if self._config.mode == "mock":
                    state, eff_cfg = self._get_mock_context()
                else:
                    state = None
                    eff_cfg = self._config

                plan = validate_and_create_plan(
                    action_name=action_name,
                    params=params,
                    verification_target=verification_target,
                    config=eff_cfg,
                    mock_state=state,
                )
                self._plans[plan.plan_id] = plan
                public_plan = format_plan_for_public_output(plan)
                summary = (
                    f"已创建待授权操作计划 '{plan.plan_id}' ({plan.action_name})；"
                    "尚未执行系统修改，需宿主确认后调用执行"
                )
                return public_plan, "ok", [], summary

        return self._run_tool(
            "plan_action",
            {
                "action_name": action_name,
                "params": params,
                "verification_target": verification_target,
            },
            _impl,
        )

    # ------------------------------------------------------------------
    # Host-Only Authorization & Execution Boundary (NOT in available_tools)
    # ------------------------------------------------------------------

    def approve_action(self, plan_id: str) -> str:
        """Host-only method: issue a single-use approval token for a planned action.

        Raises:
            ActionApprovalError: If plan_id is invalid, unknown, expired, or already approved/executed.
        """
        if not isinstance(plan_id, str) or not plan_id.strip():
            from netpet_tools.base import ActionApprovalError

            raise ActionApprovalError(ErrorCode.INVALID_INPUT, "plan_id must be a non-empty string.")
        with self._lock:
            plan = self._plans.get(plan_id.strip())
            return issue_host_approval_token(plan)

    def execute_action(self, plan_id: str, *, approval_token: str) -> dict[str, Any]:
        """Host-only method: execute a previously planned and host-approved action, then re-verify target."""
        t0 = time.monotonic()
        params_log = {"plan_id": plan_id, "approval_token": "<redacted>"}

        with self._write_lock:
            try:
                clean_plan_id = validate_safe_text(plan_id, "plan_id", max_len=128)
            except ToolExecutionError as exc:
                res = build_error_result(
                    tool="execute_action",
                    mode=self._config.mode,
                    scenario_id=self._config.scenario_id,
                    started_monotonic=t0,
                    code=exc.code,
                    message=exc.message,
                    data=build_not_executed_payload(str(plan_id), None, "invalid_plan_id"),
                )
                self._audit.record(
                    tool="execute_action",
                    mode=self._config.mode,
                    scenario_id=self._config.scenario_id,
                    params=params_log,
                    result=res,
                )
                return res

            with self._lock:
                plan = self._plans.get(clean_plan_id)
                if plan is None:
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.INVALID_INPUT,
                        message=f"Unknown plan_id '{clean_plan_id}'.",
                        data=build_not_executed_payload(clean_plan_id, None, "unknown_plan_id"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                if not isinstance(approval_token, str) or not approval_token.strip():
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.PERMISSION_DENIED,
                        message="Missing host approval_token; action was not executed.",
                        data=build_not_executed_payload(plan.plan_id, plan.action_name, "missing_approval_token"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                if plan.state == "executed":
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.PERMISSION_DENIED,
                        message=f"Plan '{plan.plan_id}' was already executed; single-use approval replay rejected.",
                        data=build_not_executed_payload(plan.plan_id, plan.action_name, "replay_rejected"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                if time.monotonic() > plan.expires_monotonic or plan.state == "expired":
                    plan.state = "expired"
                    plan.approval_token_sha256 = None
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.INVALID_INPUT,
                        message=f"Plan '{plan.plan_id}' has expired and was not executed.",
                        data=build_not_executed_payload(plan.plan_id, plan.action_name, "plan_expired"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                token_digest = _hash_token(approval_token)
                if (
                    plan.state != "approved"
                    or plan.approval_token_sha256 is None
                    or not hmac.compare_digest(token_digest, plan.approval_token_sha256)
                ):
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.PERMISSION_DENIED,
                        message="Invalid or unapproved approval_token; action was not executed.",
                        data=build_not_executed_payload(plan.plan_id, plan.action_name, "invalid_approval_token"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                if self._config.mode == "real" and not self._config.allow_writes:
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.PERMISSION_DENIED,
                        message="Real write operations are disabled in ToolConfig (allow_writes=False); action was not executed.",
                        data=build_not_executed_payload(plan.plan_id, plan.action_name, "allow_writes_disabled"),
                    )
                    self._audit.record(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        params=params_log,
                        result=res,
                    )
                    return res

                # Consume single-use token before running action so it can never be replayed
                plan.state = "executed"
                plan.approval_token_sha256 = None

                if self._config.mode == "mock":
                    mock_state, eff_cfg = self._get_mock_context()
                else:
                    mock_state = None
                    eff_cfg = self._config

            # Run action (under _write_lock; mock state protected via _lock when accessed)
            try:
                with self._lock if self._config.mode == "mock" else _null_context():
                    data, status, warnings, summary = execute_approved_plan(
                        plan,
                        config=eff_cfg,
                        mock_env=self._mock_env,
                        mock_state=mock_state,
                        probe_fn=lambda **kw: self._probe_connectivity_internal(record_audit=False, **kw),
                        dns_fn=lambda **kw: self._run_dns_internal(**kw),
                        proxy_state_fn=lambda: self._get_proxy_state_internal(record_audit=False),
                        snapshot_fn=lambda **kw: self._run_snapshot_internal(**kw),
                    )
                if data.get("execution_status") == "failed":
                    res = build_error_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        code=ErrorCode.INTERNAL_ERROR,
                        message=summary,
                        retryable=True,
                        data=data,
                        summary=summary,
                        warnings=warnings,
                    )
                else:
                    res = build_ok_result(
                        tool="execute_action",
                        mode=self._config.mode,
                        scenario_id=self._config.scenario_id,
                        started_monotonic=t0,
                        data=data,
                        summary=summary,
                        status=status,
                        warnings=warnings,
                    )
            except ToolExecutionError as exc:
                err_data = build_not_executed_payload(plan.plan_id, plan.action_name, exc.message)
                err_data.update(exc.data)
                res = build_error_result(
                    tool="execute_action",
                    mode=self._config.mode,
                    scenario_id=self._config.scenario_id,
                    started_monotonic=t0,
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                    data=err_data,
                    warnings=exc.warnings,
                )
            except Exception as exc:
                res = build_error_result(
                    tool="execute_action",
                    mode=self._config.mode,
                    scenario_id=self._config.scenario_id,
                    started_monotonic=t0,
                    code=ErrorCode.INTERNAL_ERROR,
                    message=f"Unexpected execution failure: {type(exc).__name__}: {exc}",
                    data={
                        "plan_id": plan.plan_id,
                        "action_name": plan.action_name,
                        "execution_status": "failed",
                        "recovery_status": "unknown",
                        "before": None,
                        "after": None,
                        "rollback_status": "unknown",
                    },
                )

            self._audit.record(
                tool="execute_action",
                mode=self._config.mode,
                scenario_id=self._config.scenario_id,
                params=params_log,
                result=res,
            )
            return res

    def _run_dns_internal(
        self,
        *,
        domain: str,
        dns_server: str | None = None,
        record_type: str = "A",
        compare_public: bool = False,
    ) -> dict[str, Any]:
        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                state, eff_cfg = self._get_mock_context()
                return collect_mock_dns(
                    state,
                    eff_cfg.dns,
                    domain=domain,
                    dns_server=dns_server,
                    record_type=record_type,
                    compare_public=compare_public,
                )
            return collect_real_dns(
                self._config.dns,
                domain=domain,
                dns_server=dns_server,
                record_type=record_type,
                compare_public=compare_public,
            )

        return self._run_tool("query_dns", {}, _impl, record_audit=False)

    def _run_snapshot_internal(
        self,
        *,
        interface: str = "auto",
        include_recent_events: bool = False,
    ) -> dict[str, Any]:
        def _impl() -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
            if self._config.mode == "mock":
                state, _ = self._get_mock_context()
                return collect_mock_network_snapshot(
                    state,
                    interface=interface,
                    include_recent_events=include_recent_events,
                )
            return collect_real_network_snapshot(
                interface=interface,
                include_recent_events=include_recent_events,
            )

        return self._run_tool("get_network_snapshot", {}, _impl, record_audit=False)


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None
