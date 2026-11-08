"""Windows proxy settings inspection, optional loopback Mihomo read-only adapter, and routing rule checks."""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import socket
import sys
import urllib.parse
from typing import Any, Literal

import httpx

from netpet_tools.base import (
    ErrorCode,
    ToolExecutionError,
    domain_matches_pattern,
    normalize_and_validate_domain,
    redact_proxy_string,
    run_windows_command,
    validate_http_url,
    validate_safe_text,
)
from netpet_tools.config import MihomoControllerConfig, ProbeConfig, ProxyConfig

INTERNET_SETTINGS_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
CONNECTIONS_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings\Connections"

_ENV_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def read_real_hkcu_internet_settings() -> dict[str, Any]:
    """Read raw and normalized HKCU WinINet proxy settings without modifying the registry."""
    if sys.platform != "win32":
        return {
            "proxy_enable_existed": False,
            "proxy_enable_raw": None,
            "proxy_enable": None,
            "proxy_server_existed": False,
            "proxy_server_raw": None,
            "proxy_server": None,
            "proxy_override_existed": False,
            "proxy_override_raw": None,
            "proxy_override": None,
            "auto_config_url_existed": False,
            "auto_config_url_raw": None,
            "auto_config_url": None,
            "auto_detect": None,
        }

    import winreg

    out: dict[str, Any] = {
        "proxy_enable_existed": False,
        "proxy_enable_raw": None,
        "proxy_enable": None,
        "proxy_server_existed": False,
        "proxy_server_raw": None,
        "proxy_server": None,
        "proxy_override_existed": False,
        "proxy_override_raw": None,
        "proxy_override": None,
        "auto_config_url_existed": False,
        "auto_config_url_raw": None,
        "auto_config_url": None,
        "auto_detect": None,
    }

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_KEY_PATH, 0, winreg.KEY_READ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "ProxyEnable")
                out["proxy_enable_existed"] = True
                out["proxy_enable_raw"] = val
                out["proxy_enable"] = bool(int(val))
            except FileNotFoundError:
                out["proxy_enable"] = False
            except (ValueError, TypeError):
                out["proxy_enable"] = None

            for reg_name, prefix in (
                ("ProxyServer", "proxy_server"),
                ("ProxyOverride", "proxy_override"),
                ("AutoConfigURL", "auto_config_url"),
            ):
                try:
                    s_val, _ = winreg.QueryValueEx(key, reg_name)
                    out[f"{prefix}_existed"] = True
                    out[f"{prefix}_raw"] = s_val
                    out[prefix] = str(s_val) if s_val is not None else None
                except FileNotFoundError:
                    pass
    except OSError:
        return out

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY_PATH, 0, winreg.KEY_READ) as conn_key:
            blob, _ = winreg.QueryValueEx(conn_key, "DefaultConnectionSettings")
            if isinstance(blob, (bytes, bytearray)) and len(blob) >= 9:
                # Byte offset 8 stores WinINet connection flags; 0x08 = PROXY_TYPE_AUTO_DETECT
                flags = blob[8]
                out["auto_detect"] = bool(flags & 0x08)
    except OSError:
        pass

    return out


def format_public_hkcu_settings(raw_hkcu: dict[str, Any]) -> dict[str, Any]:
    """Format HKCU proxy snapshot for public output with credential redaction."""
    return {
        "proxy_enable": raw_hkcu.get("proxy_enable"),
        "proxy_server": redact_proxy_string(raw_hkcu.get("proxy_server")),
        "proxy_override": raw_hkcu.get("proxy_override"),
        "auto_config_url": redact_proxy_string(raw_hkcu.get("auto_config_url")),
        "auto_detect": raw_hkcu.get("auto_detect"),
        "scope_note": (
            "HKCU Internet Settings apply only to the current user and WinINet-aware applications; "
            "they do not represent all process or TUN-level routing."
        ),
    }


def read_real_env_proxies() -> dict[str, str]:
    """Read proxy environment variables with embedded credentials redacted."""
    found: dict[str, str] = {}
    for var in _ENV_PROXY_VARS:
        val = os.environ.get(var)
        if val is not None and val.strip():
            found[var] = redact_proxy_string(val.strip()) or ""
    return found


def parse_netsh_winhttp_proxy(stdout: str) -> dict[str, Any]:
    """Parse Chinese or English `netsh winhttp show proxy` output."""
    text = (stdout or "").strip()
    if not text:
        return {"direct_access": None, "proxy_server": None, "bypass_list": None}

    lower = text.lower()
    if "direct access" in lower or "直接访问" in text:
        return {"direct_access": True, "proxy_server": None, "bypass_list": None}

    proxy_srv: str | None = None
    bypass: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if ":" not in line and "：" not in line:
            continue
        sep = ":" if ":" in line else "："
        k, v = line.split(sep, 1)
        k_low = k.strip().lower()
        val = v.strip()
        if "proxy server" in k_low or "代理服务器" in k_low:
            proxy_srv = redact_proxy_string(val) if val else None
        elif "bypass list" in k_low or "绕过列表" in k_low:
            bypass = val if val and val.lower() not in ("(none)", "(无)") else None

    return {
        "direct_access": False if proxy_srv else None,
        "proxy_server": proxy_srv,
        "bypass_list": bypass,
    }


def probe_local_candidate_ports(ports: list[int], timeout_s: float) -> list[dict[str, Any]]:
    """Check TCP listener presence on configured loopback ports only."""
    results: list[dict[str, Any]] = []
    for port in ports:
        detected = False
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout_s):
                detected = True
        except OSError:
            detected = False
        results.append(
            {
                "host": "127.0.0.1",
                "port": int(port),
                "listener_detected": detected,
            }
        )
    return results


def query_mihomo_controller_readonly(cfg: MihomoControllerConfig) -> dict[str, Any]:
    """Query loopback Mihomo controller read-only endpoints without logging secrets."""
    if not cfg.enabled:
        return {
            "adapter": "none",
            "controller_reachable": None,
            "client_type": "unknown",
            "version": None,
            "mode": "unknown",
            "tun_state": "unknown",
        }

    headers: dict[str, str] = {}
    secret = os.environ.get(cfg.secret_env_var)
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    try:
        with httpx.Client(trust_env=False, verify=True, timeout=httpx.Timeout(cfg.timeout_s)) as client:
            ver_resp = client.get(f"{cfg.base_url}/version", headers=headers)
            if ver_resp.status_code in (401, 403):
                return {
                    "adapter": "mihomo",
                    "controller_reachable": True,
                    "auth_error": True,
                    "client_type": "mihomo_compatible",
                    "version": None,
                    "mode": "unknown",
                    "tun_state": "unknown",
                }
            ver_resp.raise_for_status()
            ver_json = ver_resp.json() if ver_resp.content else {}

            cfg_resp = client.get(f"{cfg.base_url}/configs", headers=headers)
            cfg_resp.raise_for_status()
            cfg_json = cfg_resp.json() if cfg_resp.content else {}

            raw_mode = str(cfg_json.get("mode") or "").strip().lower()
            mode_norm = raw_mode if raw_mode in ("rule", "global", "direct") else "unknown"

            tun_obj = cfg_json.get("tun")
            if isinstance(tun_obj, dict) and isinstance(tun_obj.get("enable"), bool):
                tun_state = "enabled" if tun_obj["enable"] else "disabled"
            else:
                tun_state = "unknown"

            is_meta = bool(ver_json.get("meta"))
            return {
                "adapter": "mihomo",
                "controller_reachable": True,
                "auth_error": False,
                "client_type": "mihomo" if is_meta else "clash_compatible",
                "version": ver_json.get("version"),
                "mode": mode_norm,
                "tun_state": tun_state,
            }
    except Exception:
        return {
            "adapter": "mihomo",
            "controller_reachable": False,
            "client_type": "unknown",
            "version": None,
            "mode": "unknown",
            "tun_state": "unknown",
        }


def assemble_proxy_state_payload(
    *,
    public_hkcu: dict[str, Any],
    env_proxies: dict[str, str],
    winhttp_info: dict[str, Any],
    port_probes: list[dict[str, Any]],
    configured_client: dict[str, Any],
    configured_proxy_url: str | None,
    data_origin: str,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Assemble get_proxy_state payload and conservative summary."""
    open_ports = [p["port"] for p in port_probes if p["listener_detected"]]
    hkcu_enabled = public_hkcu.get("proxy_enable")

    summary = (
        f"HKCU 系统代理={'开启' if hkcu_enabled else ('关闭' if hkcu_enabled is False else '未知')}，"
        f"候选本地监听端口开放数={len(open_ports)}，"
        f"客户端模式={configured_client.get('mode', 'unknown')}"
    )

    data: dict[str, Any] = {
        "data_origin": data_origin,
        "hkcu_internet_settings": public_hkcu,
        "env_proxies": env_proxies,
        "winhttp_proxy": winhttp_info,
        "local_port_probes": port_probes,
        "local_port_probe_note": (
            "An open local TCP port only confirms a listener on 127.0.0.1; it does not identify the client "
            "or prove that system/application traffic traverses it."
        ),
        "configured_proxy_url": redact_proxy_string(configured_proxy_url),
        "configured_client": configured_client,
    }
    return data, "ok", [], summary


def collect_real_proxy_state(
    proxy_cfg: ProxyConfig,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Collect real proxy configuration and observable local listener/controller state."""
    raw_hkcu = read_real_hkcu_internet_settings()
    pub_hkcu = format_public_hkcu_settings(raw_hkcu)
    env_p = read_real_env_proxies()

    winhttp_cmd = run_windows_command(["netsh.exe", "winhttp", "show", "proxy"], timeout_s=3.0)
    winhttp_info = (
        parse_netsh_winhttp_proxy(winhttp_cmd.stdout)
        if not winhttp_cmd.timed_out and winhttp_cmd.returncode == 0
        else {"direct_access": None, "proxy_server": None, "bypass_list": None}
    )

    ports = probe_local_candidate_ports(proxy_cfg.candidate_local_ports, proxy_cfg.local_port_timeout_s)
    client_info = query_mihomo_controller_readonly(proxy_cfg.mihomo_controller)

    return assemble_proxy_state_payload(
        public_hkcu=pub_hkcu,
        env_proxies=env_p,
        winhttp_info=winhttp_info,
        port_probes=ports,
        configured_client=client_info,
        configured_proxy_url=proxy_cfg.configured_proxy_url,
        data_origin="real_host",
    )


def collect_mock_proxy_state(
    scenario_state: dict[str, Any],
    proxy_cfg: ProxyConfig,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Collect deterministic mock proxy state from scenario state."""
    proxy_sec = scenario_state.get("proxy")
    if not isinstance(proxy_sec, dict):
        raise ToolExecutionError(ErrorCode.INVALID_SCENARIO, "Scenario is missing 'proxy' section.")

    raw_hkcu = proxy_sec.get("hkcu") or {}
    pub_hkcu = format_public_hkcu_settings(raw_hkcu)
    env_p = {str(k): redact_proxy_string(str(v)) or "" for k, v in (proxy_sec.get("env_proxies") or {}).items()}
    winhttp_info = dict(proxy_sec.get("winhttp") or {"direct_access": None, "proxy_server": None, "bypass_list": None})

    raw_ports = proxy_sec.get("local_ports") or {}
    port_probes = [
        {
            "host": "127.0.0.1",
            "port": int(p),
            "listener_detected": bool(raw_ports.get(str(p), False)),
        }
        for p in proxy_cfg.candidate_local_ports
    ]
    client_info = dict(
        proxy_sec.get("configured_client")
        or {
            "adapter": "none",
            "controller_reachable": None,
            "client_type": "unknown",
            "mode": "unknown",
            "tun_state": "unknown",
        }
    )

    return assemble_proxy_state_payload(
        public_hkcu=pub_hkcu,
        env_proxies=env_p,
        winhttp_info=winhttp_info,
        port_probes=port_probes,
        configured_client=client_info,
        configured_proxy_url=proxy_cfg.configured_proxy_url,
        data_origin=str(scenario_state.get("data_origin") or "synthetic"),
    )


# ---------------------------------------------------------------------------
# Routing Rule Matching & Conflict Evaluation (check_target_routing_rule)
# ---------------------------------------------------------------------------


def extract_target_hostname(
    url_or_domain: Any,
    probe_cfg: ProbeConfig,
) -> tuple[str, str]:
    """Extract (ascii_hostname, display_hostname) from a URL or bare domain name."""
    raw = validate_safe_text(url_or_domain, "url_or_domain", max_len=2048)
    if "://" in raw:
        validated_url = validate_http_url(
            raw,
            allowed_private_targets=probe_cfg.allowed_private_targets + ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
            field_name="url_or_domain",
        )
        if validated_url.ip_literal is not None:
            return validated_url.hostname_ascii, validated_url.hostname_ascii
        parts = urllib.parse.urlsplit(raw)
        _, disp = normalize_and_validate_domain(parts.hostname or "")
        return validated_url.hostname_ascii, disp

    return normalize_and_validate_domain(raw)


def does_proxy_override_bypass(hostname_ascii: str, proxy_override: str | None) -> bool:
    """Check whether a Windows ProxyOverride semicolon-separated list bypasses proxy for hostname."""
    if not proxy_override:
        return False
    host = hostname_ascii.lower().rstrip(".")
    entries = [item.strip().lower() for item in proxy_override.split(";") if item.strip()]
    for entry in entries:
        if entry == "<local>":
            if "." not in host:
                return True
            continue
        if "*" in entry:
            if fnmatch.fnmatchcase(host, entry):
                return True
        elif host == entry:
            return True
    return False


def _extract_local_proxy_port(proxy_server: str | None) -> int | None:
    if not proxy_server:
        return None
    text = proxy_server.strip()
    # Handle Windows protocol=host:port;... format or simple host:port
    first_part = text.split(";")[0].strip()
    if "=" in first_part and "://" not in first_part:
        first_part = first_part.split("=", 1)[1].strip()
    if "://" not in first_part:
        first_part = "http://" + first_part
    try:
        parts = urllib.parse.urlsplit(first_part)
        if parts.hostname in ("127.0.0.1", "localhost", "::1") and parts.port:
            return int(parts.port)
    except ValueError:
        return None
    return None


def evaluate_target_routing_rule(
    *,
    url_or_domain: str,
    probe_cfg: ProbeConfig,
    proxy_cfg: ProxyConfig,
    proxy_state_data: dict[str, Any],
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Match target against explicit routing rules and evaluate conflict strictly on sufficient evidence."""
    hostname_ascii, display_host = extract_target_hostname(url_or_domain, probe_cfg)

    matched_rule_dict: dict[str, Any] | None = None
    expected_policy: Literal["direct", "proxy", "unknown"] = "unknown"

    try:
        ipaddress.ip_address(hostname_ascii)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False

    if not is_ip_literal:
        for rule in proxy_cfg.routing_rules:
            if domain_matches_pattern(hostname_ascii, rule.pattern, rule.match_type):
                expected_policy = rule.policy
                matched_rule_dict = {
                    "rule_id": rule.rule_id,
                    "match_type": rule.match_type,
                    "pattern": rule.pattern,
                    "policy": rule.policy,
                    "source": rule.source,
                    "user_confirmed": rule.user_confirmed,
                    "description": rule.description,
                }
                break

    hkcu = proxy_state_data.get("hkcu_internet_settings") or {}
    env_p = proxy_state_data.get("env_proxies") or {}
    client = proxy_state_data.get("configured_client") or {}
    port_probes = proxy_state_data.get("local_port_probes") or []
    port_map = {int(p["port"]): bool(p["listener_detected"]) for p in port_probes}

    hkcu_on = hkcu.get("proxy_enable") is True
    pac_on = bool(hkcu.get("auto_config_url"))
    bypassed = does_proxy_override_bypass(hostname_ascii, hkcu.get("proxy_override"))
    client_mode = client.get("mode", "unknown")
    tun_state = client.get("tun_state", "unknown")

    conflict: bool | None = None
    potential_conflict: bool = False
    conflict_reason: str | None = None
    missing_evidence: list[str] = []

    if expected_policy == "unknown" or matched_rule_dict is None:
        conflict = None
        missing_evidence.append("no_explicit_routing_rule_configured_for_target")
    elif not matched_rule_dict["user_confirmed"]:
        conflict = None
        missing_evidence.append("matched_routing_rule_is_unconfirmed_example")
    elif expected_policy == "direct":
        if client_mode == "global" and ((hkcu_on and not bypassed) or tun_state == "enabled"):
            conflict = True
            potential_conflict = True
            conflict_reason = "confirmed_direct_rule_conflicts_with_client_global_proxy_mode"
        elif hkcu_on and not bypassed:
            local_port = _extract_local_proxy_port(hkcu.get("proxy_server"))
            if local_port is not None and local_port in port_map and port_map[local_port] is False:
                conflict = True
                potential_conflict = True
                conflict_reason = f"hkcu_system_proxy_enabled_toward_closed_local_port_{local_port}"
            else:
                # ProxyEnable=True alone cannot prove conflict if local proxy client uses rule-based split routing!
                conflict = None
                potential_conflict = True
                missing_evidence.extend(
                    [
                        "proxy_client_internal_routing_mode_unknown",
                        "compare_probe_connectivity_direct_vs_configured_proxy",
                    ]
                )
        elif pac_on:
            conflict = None
            potential_conflict = True
            missing_evidence.extend(
                [
                    "pac_script_routing_decision_unobserved",
                    "compare_probe_connectivity_direct_vs_configured_proxy",
                ]
            )
        elif not hkcu_on and not pac_on and not env_p:
            if tun_state == "disabled" or client_mode == "direct":
                conflict = False
            else:
                conflict = None
                potential_conflict = False
                missing_evidence.append("tun_state_unknown_without_client_controller")
        else:
            conflict = None
            missing_evidence.append("effective_application_proxy_path_unverified")
    elif expected_policy == "proxy":
        if client_mode == "direct" and tun_state in ("disabled", "unknown"):
            conflict = True
            potential_conflict = True
            conflict_reason = "confirmed_proxy_rule_conflicts_with_client_direct_mode"
        elif not hkcu_on and not pac_on and not env_p and tun_state == "disabled":
            conflict = True
            potential_conflict = True
            conflict_reason = "confirmed_proxy_rule_but_system_proxy_and_tun_are_disabled"
        elif not hkcu_on and not pac_on and not env_p and tun_state == "unknown":
            conflict = None
            potential_conflict = True
            missing_evidence.extend(
                [
                    "tun_or_vpn_state_unknown",
                    "compare_probe_connectivity_direct_vs_configured_proxy",
                ]
            )
        elif hkcu_on and not bypassed:
            local_port = _extract_local_proxy_port(hkcu.get("proxy_server"))
            if local_port is not None and local_port in port_map and port_map[local_port] is False:
                conflict = True
                potential_conflict = True
                conflict_reason = f"hkcu_system_proxy_points_to_closed_local_port_{local_port}"
            elif client_mode in ("global",):
                conflict = False
            else:
                conflict = None
                missing_evidence.append("proxy_client_rule_match_unverified")

    summary = (
        f"目标 '{display_host}' 显式策略={expected_policy}，"
        f"conflict={conflict}，缺失证据数={len(missing_evidence)}"
    )

    data: dict[str, Any] = {
        "data_origin": proxy_state_data.get("data_origin", "real_host"),
        "target_input": url_or_domain if "://" not in url_or_domain else f"host:{display_host}",
        "hostname": display_host,
        "ascii_hostname": hostname_ascii,
        "expected_policy": expected_policy,
        "matched_rule": matched_rule_dict,
        "hkcu_proxy_enabled": hkcu_on,
        "bypassed_by_proxy_override": bypassed if hkcu_on else None,
        "pac_configured": pac_on,
        "client_mode": client_mode,
        "tun_state": tun_state,
        "conflict": conflict,
        "potential_conflict": potential_conflict,
        "conflict_reason": conflict_reason,
        "missing_evidence": missing_evidence,
    }
    return data, "ok", [], summary
