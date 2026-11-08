"""Configuration schemas for netpet_tools."""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DnsConfig(BaseModel):
    """DNS diagnostic configuration."""

    model_config = ConfigDict(extra="forbid")

    public_resolvers: list[str] = Field(
        default_factory=lambda: ["223.5.5.5", "119.29.29.29"],
        description="Configurable public DNS resolvers queried only when compare_public=True.",
    )
    timeout_s: float = Field(default=2.5, ge=0.2, le=10.0)
    total_budget_s: float = Field(default=6.0, ge=0.5, le=15.0)

    @field_validator("public_resolvers")
    @classmethod
    def _validate_resolvers(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            addr = str(ipaddress.ip_address(item.strip()))
            cleaned.append(addr)
        return cleaned


class ProbeEndpointConfig(BaseModel):
    """Known connectivity-check endpoint with explicit expected HTTP signature."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    expected_status: int = Field(default=204, ge=100, le=599)
    expected_body_prefix: str | None = ""
    expected_header_key: str | None = None
    expected_header_value: str | None = None


class ProbeConfig(BaseModel):
    """HTTP connectivity, portal detection, and ICMP probe settings."""

    model_config = ConfigDict(extra="forbid")

    probe_endpoints: list[ProbeEndpointConfig] = Field(
        default_factory=lambda: [
            ProbeEndpointConfig(
                name="miui_generate_204",
                url="http://connect.rom.miui.com/generate_204",
                expected_status=204,
                expected_body_prefix="",
            ),
            ProbeEndpointConfig(
                name="hicloud_generate_204",
                url="http://connectivitycheck.platform.hicloud.com/generate_204",
                expected_status=204,
                expected_body_prefix="",
            ),
        ]
    )
    trusted_portal_hosts: list[str] = Field(
        default_factory=list,
        description="Known campus authentication hostnames/IPs configured by user/school.",
    )
    allowed_private_targets: list[str] = Field(
        default_factory=list,
        description="Explicitly allowed campus private CIDR ranges, IPs, or internal hostnames.",
    )
    allowed_browser_hosts: list[str] = Field(
        default_factory=list,
        description="Additional allowed hostnames for open_browser_url besides trusted_portal_hosts.",
    )
    max_redirects_inspect: int = Field(default=3, ge=0, le=5)
    max_body_bytes: int = Field(default=8192, ge=512, le=65536)
    http_total_budget_s: float = Field(default=8.0, ge=1.0, le=10.0)
    ping_total_budget_s: float = Field(default=12.0, ge=1.0, le=15.0)
    ping_per_packet_timeout_ms: int = Field(default=1000, ge=200, le=5000)


class MihomoControllerConfig(BaseModel):
    """Optional read-only Mihomo/Clash-compatible loopback controller configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str = "http://127.0.0.1:9090"
    secret_env_var: str = "NETPET_MIHOMO_SECRET"
    timeout_s: float = Field(default=1.5, ge=0.2, le=5.0)

    @field_validator("base_url")
    @classmethod
    def _validate_loopback_controller(cls, value: str) -> str:
        parts = urllib.parse.urlsplit(value.strip())
        if parts.scheme not in ("http", "https"):
            raise ValueError("Mihomo controller base_url must use http or https")
        if parts.username is not None or parts.password is not None:
            raise ValueError("Mihomo controller base_url must not contain credentials")
        host = (parts.hostname or "").lower()
        if host != "localhost":
            try:
                ip_obj = ipaddress.ip_address(host)
                if not ip_obj.is_loopback:
                    raise ValueError("Mihomo controller base_url must be a loopback address")
            except ValueError as exc:
                raise ValueError("Mihomo controller base_url must be localhost or a loopback IP") from exc
        return value.strip().rstrip("/")


class RoutingRuleConfig(BaseModel):
    """Explicit domain routing policy configured by host/user."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    match_type: Literal["domain_exact", "domain_suffix"] = "domain_suffix"
    pattern: str
    policy: Literal["direct", "proxy"]
    source: str = "user_config"
    user_confirmed: bool = True
    description: str = ""


class ProxyConfig(BaseModel):
    """Proxy observation and explicit routing rule configuration."""

    model_config = ConfigDict(extra="forbid")

    configured_proxy_url: str | None = Field(
        default=None,
        description="Trusted explicit HTTP/HTTPS proxy URL for route='configured_proxy' and proxy enablement.",
    )
    candidate_local_ports: list[int] = Field(
        default_factory=lambda: [7890, 7897, 10808, 10809],
        description="Local loopback TCP ports checked for listener presence.",
    )
    local_port_timeout_s: float = Field(default=0.15, ge=0.05, le=1.0)
    mihomo_controller: MihomoControllerConfig = Field(default_factory=MihomoControllerConfig)
    routing_rules: list[RoutingRuleConfig] = Field(default_factory=list)

    @field_validator("candidate_local_ports")
    @classmethod
    def _validate_ports(cls, value: list[int]) -> list[int]:
        for port in value:
            if isinstance(port, bool) or not (1 <= port <= 65535):
                raise ValueError(f"Invalid TCP port in candidate_local_ports: {port}")
        return list(dict.fromkeys(value))

    @field_validator("configured_proxy_url")
    @classmethod
    def _validate_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parts = urllib.parse.urlsplit(cleaned)
        if parts.username is not None or parts.password is not None:
            raise ValueError("configured_proxy_url must not contain embedded credentials")
        if not parts.scheme or not parts.hostname or parts.port is None:
            raise ValueError("configured_proxy_url must include scheme, host, and port (e.g. 'http://127.0.0.1:7890')")
        return cleaned


class ToolConfig(BaseModel):
    """Top-level instance configuration for ToolService."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["mock", "real"] = "mock"
    scenario_id: str | None = "case_01_normal"
    scenarios_dir: str | None = None
    allow_writes: bool = False
    audit_log_path: str | None = None
    plan_ttl_seconds: int = Field(default=300, ge=10, le=3600)

    dns: DnsConfig = Field(default_factory=DnsConfig)
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

    @classmethod
    def from_json_file(cls, path: str | Path) -> ToolConfig:
        """Load and validate ToolConfig from a UTF-8 JSON file."""
        file_path = Path(path)
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return cls.model_validate(payload)

    @classmethod
    def with_campus(
        cls,
        domain: str,
        alias_name: str | None = None,
        *,
        mode: Literal["mock", "real"] = "mock",
        scenario_id: str | None = "case_01_normal",
        **kwargs: Any,
    ) -> ToolConfig:
        """Create a ToolConfig pre-configured with a campus domain and direct routing rules."""
        from netpet_tools.base import normalize_and_validate_domain

        ascii_dom, disp_dom = normalize_and_validate_domain(domain)
        cfg = cls(mode=mode, scenario_id=scenario_id, **kwargs)
        if ascii_dom not in cfg.probe.trusted_portal_hosts:
            cfg.probe.trusted_portal_hosts.append(ascii_dom)
        if ascii_dom not in cfg.probe.allowed_browser_hosts:
            cfg.probe.allowed_browser_hosts.append(ascii_dom)
        if ascii_dom not in cfg.probe.allowed_private_targets:
            cfg.probe.allowed_private_targets.append(ascii_dom)

        rule = RoutingRuleConfig(
            rule_id=f"campus_direct_{ascii_dom.replace('.', '_')}",
            match_type="domain_suffix",
            pattern=ascii_dom,
            policy="direct",
            source=f"user_campus:{alias_name}" if alias_name else "user_campus",
            user_confirmed=True,
            description=f"Direct campus network routing rule for {alias_name or disp_dom}",
        )
        cfg.proxy.routing_rules = [r for r in cfg.proxy.routing_rules if r.pattern != ascii_dom] + [rule]
        return cfg
