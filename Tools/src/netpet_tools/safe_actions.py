"""Controlled action planning, single-use host authorization, execution, verification, and rollback."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from netpet_tools.base import (
    ActionApprovalError,
    ErrorCode,
    ToolExecutionError,
    is_target_explicitly_allowed,
    normalize_and_validate_domain,
    redact_proxy_string,
    run_windows_command,
    sanitize_url_for_log,
    utc_now_iso,
    validate_http_url,
    validate_safe_text,
)
from netpet_tools.config import ToolConfig
from netpet_tools.proxy_manager import (
    INTERNET_SETTINGS_KEY_PATH,
    format_public_hkcu_settings,
    read_real_hkcu_internet_settings,
)

WHITELISTED_ACTIONS = frozenset(
    {
        "flush_dns",
        "open_browser_url",
        "reconnect_wifi",
        "set_system_proxy",
    }
)

ExecutionStatus = Literal["succeeded", "failed", "not_executed"]
RecoveryStatus = Literal["recovered", "not_recovered", "unknown", "not_checked"]
RollbackStatus = Literal["not_needed", "succeeded", "failed", "unsupported"]


@dataclass
class ActionPlanRecord:
    """Internal server-side stored action plan (never mutated by model parameters)."""

    plan_id: str
    action_name: str
    normalized_params: dict[str, Any]
    target_summary: str
    impact_summary: str
    privilege_required: str
    reversible: bool
    rollback_policy: str
    verification_target: str | None
    verification_kind: Literal["none", "http_url", "dns_domain"]
    verification_method: str
    created_at_iso: str
    expires_at_iso: str
    created_monotonic: float
    expires_monotonic: float
    state: Literal["planned", "approved", "executed", "expired"] = "planned"
    approval_token_sha256: str | None = None
    extra_meta: dict[str, Any] = field(default_factory=dict)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_netsh_wlan_profiles(stdout: str) -> list[str]:
    """Parse Chinese or English `netsh wlan show profiles` output into profile names."""
    profiles: list[str] = []
    profile_re = re.compile(
        r"(?:All User Profile|Current User Profile|Group Policy Profile|所有用户配置文件|当前用户配置文件|组策略配置文件)\s*[:：]\s*(.+)$",
        re.IGNORECASE,
    )
    for raw_line in (stdout or "").splitlines():
        m = profile_re.search(raw_line.strip())
        if m:
            name = m.group(1).strip()
            if name and name not in profiles:
                profiles.append(name)
    return profiles


def _extract_host_port_from_proxy(proxy_value: str | None) -> str | None:
    if not proxy_value:
        return None
    cleaned = proxy_value.strip()
    if not cleaned:
        return None
    if "://" in cleaned:
        parts = urllib.parse.urlsplit(cleaned)
        if parts.hostname and parts.port:
            host = f"[{parts.hostname}]" if ":" in parts.hostname and not parts.hostname.startswith("[") else parts.hostname
            return f"{host}:{parts.port}"
        return None
    # Windows host:port or http=host:port;https=host:port
    first_seg = cleaned.split(";")[0].strip()
    if "=" in first_seg:
        first_seg = first_seg.split("=", 1)[1].strip()
    parts = urllib.parse.urlsplit("http://" + first_seg)
    if parts.hostname and parts.port:
        return cleaned
    return None


def _validate_verification_target(
    verification_target: str | None,
    config: ToolConfig,
) -> tuple[str | None, Literal["none", "http_url", "dns_domain"], str]:
    if verification_target is None:
        return None, "none", "action_state_check_only (no original target provided; recovery_status will be not_checked)"

    raw = validate_safe_text(verification_target, "verification_target", max_len=2048)
    if "://" in raw:
        validated = validate_http_url(
            raw,
            allowed_private_targets=config.probe.allowed_private_targets,
            trusted_portal_hosts=config.probe.trusted_portal_hosts,
            field_name="verification_target",
        )
        return validated.url, "http_url", f"probe_connectivity on original target URL ({sanitize_url_for_log(validated.url)})"

    ascii_dom, disp_dom = normalize_and_validate_domain(raw)
    _ = ascii_dom
    return disp_dom, "dns_domain", f"query_dns on original target domain ({disp_dom})"


def validate_and_create_plan(
    *,
    action_name: Any,
    params: dict[str, Any] | None,
    verification_target: str | None,
    config: ToolConfig,
    mock_state: dict[str, Any] | None,
) -> ActionPlanRecord:
    """Validate whitelisted action, parameters, preconditions, and create an unapproved plan."""
    act = validate_safe_text(action_name, "action_name", max_len=64)
    if act not in WHITELISTED_ACTIONS:
        raise ToolExecutionError(
            ErrorCode.UNSUPPORTED,
            f"Action '{act}' is not in the v1 controlled action whitelist ({sorted(WHITELISTED_ACTIONS)}).",
        )

    if params is not None and not isinstance(params, dict):
        raise ToolExecutionError(ErrorCode.INVALID_INPUT, "Parameter 'params' must be a dict or None.")
    raw_params = dict(params or {})

    ver_target, ver_kind, ver_method = _validate_verification_target(verification_target, config)
    normalized_params: dict[str, Any] = {}
    extra_meta: dict[str, Any] = {}

    if act == "flush_dns":
        if raw_params:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"flush_dns does not accept extra parameters (got keys: {sorted(raw_params.keys())}).",
            )
        target_summary = "Windows local DNS client resolver cache"
        impact_summary = "Flushes cached DNS records locally; does not alter configured DNS server addresses."
        privilege_required = "standard_user"
        reversible = False
        rollback_policy = "unsupported (DNS cache flush is one-way)"

    elif act == "open_browser_url":
        allowed_keys = {"url"}
        unknown_keys = set(raw_params.keys()) - allowed_keys
        if unknown_keys or "url" not in raw_params:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "open_browser_url requires exactly {'url': '<http/https URL>'}.",
            )
        allowed_hosts = list(config.probe.trusted_portal_hosts) + list(config.probe.allowed_browser_hosts)
        validated_url = validate_http_url(
            raw_params["url"],
            allowed_private_targets=config.probe.allowed_private_targets + allowed_hosts,
            trusted_portal_hosts=allowed_hosts,
            field_name="params.url",
        )
        if not allowed_hosts or not is_target_explicitly_allowed(
            validated_url.hostname_ascii,
            validated_url.ip_literal,
            allowed_hosts,
        ):
            raise ToolExecutionError(
                ErrorCode.PERMISSION_DENIED,
                f"URL host '{validated_url.hostname_ascii}' is not in configured trusted_portal_hosts or allowed_browser_hosts.",
            )
        normalized_params = {"url": validated_url.url}
        target_summary = sanitize_url_for_log(validated_url.url)
        impact_summary = (
            "Opens default browser to the allowlisted portal/help URL. "
            "Browser launch does NOT complete portal authentication; user must sign in manually."
        )
        privilege_required = "interactive_desktop_user"
        reversible = False
        rollback_policy = "unsupported (cannot retract launched browser tab or user web actions)"

    elif act == "reconnect_wifi":
        allowed_keys = {"interface", "profile_name", "ssid"}
        unknown_keys = set(raw_params.keys()) - allowed_keys
        if unknown_keys or "interface" not in raw_params or "profile_name" not in raw_params:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "reconnect_wifi requires {'interface': str, 'profile_name': str} and optional 'ssid'.",
            )
        iface = validate_safe_text(raw_params["interface"], "params.interface", max_len=128)
        profile = validate_safe_text(raw_params["profile_name"], "params.profile_name", max_len=128)
        ssid: str | None = None
        if raw_params.get("ssid") is not None:
            ssid = validate_safe_text(raw_params["ssid"], "params.ssid", max_len=64)

        # Verify profile already exists on the specified interface without modifying state
        if config.mode == "mock":
            assert mock_state is not None
            interfaces = (mock_state.get("network_snapshot") or {}).get("interfaces") or []
            matched_if = next((i for i in interfaces if str(i.get("name", "")).lower() == iface.lower()), None)
            if matched_if is None or matched_if.get("adapter_type") != "wifi":
                raise ToolExecutionError(
                    ErrorCode.INVALID_INPUT,
                    f"Wi-Fi interface '{iface}' was not found.",
                )
            wlan_sec = matched_if.get("wlan") or {}
            if wlan_sec.get("state") == "location_restricted":
                raise ToolExecutionError(
                    ErrorCode.LOCATION_PERMISSION_REQUIRED,
                    "Cannot verify existing Wi-Fi profiles while Windows location permission is restricted.",
                )
            existing_profiles = list(matched_if.get("available_profiles") or [])
            if wlan_sec.get("profile_name") and wlan_sec["profile_name"] not in existing_profiles:
                existing_profiles.append(wlan_sec["profile_name"])
            if profile not in existing_profiles:
                raise ToolExecutionError(
                    ErrorCode.UNSUPPORTED,
                    f"Wi-Fi profile '{profile}' does not exist on interface '{iface}'; first-time provisioning is unsupported.",
                )
        else:
            prof_cmd = run_windows_command(
                ["netsh.exe", "wlan", "show", "profiles", f"interface={iface}"],
                timeout_s=4.0,
            )
            if prof_cmd.timed_out or prof_cmd.returncode != 0:
                raise ToolExecutionError(
                    ErrorCode.INVALID_INPUT,
                    f"Could not read existing WLAN profiles for interface '{iface}'.",
                )
            existing_profiles = parse_netsh_wlan_profiles(prof_cmd.stdout)
            if profile not in existing_profiles:
                raise ToolExecutionError(
                    ErrorCode.UNSUPPORTED,
                    f"Wi-Fi profile '{profile}' is not saved on interface '{iface}'; new network provisioning is unsupported.",
                )

        normalized_params = {"interface": iface, "profile_name": profile, "ssid": ssid}
        target_summary = f"interface={iface}, profile_name={profile}" + (f", ssid={ssid}" if ssid else "")
        impact_summary = (
            "Issues `netsh wlan connect` to re-associate with an existing saved WLAN profile without unnecessary prior disconnect."
        )
        privilege_required = "standard_user"
        reversible = True
        rollback_policy = "best_effort (attempts reconnecting prior profile if a different profile was previously connected)"

    elif act == "set_system_proxy":
        allowed_keys = {"enable"}
        unknown_keys = set(raw_params.keys()) - allowed_keys
        if unknown_keys or "enable" not in raw_params or not isinstance(raw_params["enable"], bool):
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "set_system_proxy requires {'enable': bool} with an explicit boolean value.",
            )
        enable_flag: bool = raw_params["enable"]

        hkcu = (
            dict(((mock_state or {}).get("proxy") or {}).get("hkcu") or {})
            if config.mode == "mock"
            else read_real_hkcu_internet_settings()
        )
        if hkcu.get("auto_config_url"):
            raise ToolExecutionError(
                ErrorCode.UNSUPPORTED,
                "HKCU AutoConfigURL (PAC) is active; set_system_proxy refuses to alter PAC-managed configurations.",
            )

        existing_srv = _extract_host_port_from_proxy(hkcu.get("proxy_server"))
        fallback_srv = _extract_host_port_from_proxy(config.proxy.configured_proxy_url)
        if enable_flag and not existing_srv and not fallback_srv:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "Cannot plan set_system_proxy(enable=True): neither HKCU ProxyServer nor configured_proxy_url has a valid host:port.",
            )

        normalized_params = {"enable": enable_flag}
        extra_meta["resolved_proxy_server_if_needed"] = existing_srv or fallback_srv
        target_summary = f"HKCU Internet Settings ProxyEnable -> {enable_flag}"
        impact_summary = (
            "Updates current-user WinINet manual proxy toggle (HKCU ProxyEnable) and broadcasts WinINet refresh. "
            "Does NOT toggle VPN/TUN adapters or edit proxy client rule files."
        )
        privilege_required = "standard_user (HKCU)"
        reversible = True
        rollback_policy = (
            "conditional_restore (restores prior ProxyEnable/ProxyServer values and existence flags only if "
            "HKCU values still match values written by this operation)"
        )

    now_mono = time.monotonic()
    exp_mono = now_mono + float(config.plan_ttl_seconds)
    now_dt = dt.datetime.now(dt.timezone.utc)
    exp_dt = now_dt + dt.timedelta(seconds=config.plan_ttl_seconds)

    return ActionPlanRecord(
        plan_id=f"plan_{uuid.uuid4().hex[:16]}",
        action_name=act,
        normalized_params=normalized_params,
        target_summary=target_summary,
        impact_summary=impact_summary,
        privilege_required=privilege_required,
        reversible=reversible,
        rollback_policy=rollback_policy,
        verification_target=ver_target,
        verification_kind=ver_kind,
        verification_method=ver_method,
        created_at_iso=now_dt.isoformat(timespec="seconds"),
        expires_at_iso=exp_dt.isoformat(timespec="seconds"),
        created_monotonic=now_mono,
        expires_monotonic=exp_mono,
        extra_meta=extra_meta,
    )


def format_plan_for_public_output(plan: ActionPlanRecord) -> dict[str, Any]:
    """Serialize ActionPlanRecord to JSON-safe dict without any token hash."""
    safe_params = dict(plan.normalized_params)
    if "url" in safe_params and isinstance(safe_params["url"], str):
        safe_params["url_redacted_preview"] = sanitize_url_for_log(safe_params["url"])
    return {
        "plan_id": plan.plan_id,
        "action_name": plan.action_name,
        "normalized_params": safe_params,
        "target": plan.target_summary,
        "impact": plan.impact_summary,
        "privilege_required": plan.privilege_required,
        "reversible": plan.reversible,
        "rollback_policy": plan.rollback_policy,
        "requires_host_approval": True,
        "created_at": plan.created_at_iso,
        "expires_at": plan.expires_at_iso,
        "verification_target": (
            sanitize_url_for_log(plan.verification_target)
            if plan.verification_kind == "http_url" and plan.verification_target
            else plan.verification_target
        ),
        "verification_method": plan.verification_method,
        "state": plan.state,
    }


def issue_host_approval_token(plan: ActionPlanRecord | None) -> str:
    """Validate plan state/TTL and issue a single-use approval token for host confirmation."""
    if plan is None:
        raise ActionApprovalError(ErrorCode.INVALID_INPUT, "Unknown plan_id; cannot approve nonexistent plan.")
    if time.monotonic() > plan.expires_monotonic:
        plan.state = "expired"
        plan.approval_token_sha256 = None
        raise ActionApprovalError(ErrorCode.INVALID_INPUT, f"Plan '{plan.plan_id}' has expired.")
    if plan.state == "executed":
        raise ActionApprovalError(
            ErrorCode.PERMISSION_DENIED,
            f"Plan '{plan.plan_id}' was already executed and cannot be re-approved.",
        )
    if plan.state == "approved":
        raise ActionApprovalError(
            ErrorCode.PERMISSION_DENIED,
            f"Plan '{plan.plan_id}' already has an active single-use approval token.",
        )

    token = secrets.token_urlsafe(24)
    plan.approval_token_sha256 = _hash_token(token)
    plan.state = "approved"
    return token


def build_not_executed_payload(plan_id: str, action_name: str | None, reason: str) -> dict[str, Any]:
    """Standardized data payload when execute_action refuses execution."""
    return {
        "plan_id": plan_id,
        "action_name": action_name,
        "execution_status": "not_executed",
        "recovery_status": "not_checked",
        "before": None,
        "after": None,
        "rollback_status": "not_needed",
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Real Windows WinINet Notification & Registry Write / Rollback Helpers
# ---------------------------------------------------------------------------


def notify_wininet_proxy_changed() -> bool:
    """Broadcast INTERNET_OPTION_SETTINGS_CHANGED (39) and INTERNET_OPTION_REFRESH (37)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        wininet = ctypes.windll.wininet
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        ok1 = bool(wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0))
        ok2 = bool(wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0))
        return ok1 and ok2
    except Exception:
        return False


def _apply_real_hkcu_proxy_change(
    enable: bool,
    fallback_proxy_server: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write HKCU ProxyEnable (and ProxyServer if needed), returning (prior_raw, written_values)."""
    import winreg

    prior = read_real_hkcu_internet_settings()
    if prior.get("auto_config_url"):
        raise ToolExecutionError(
            ErrorCode.UNSUPPORTED,
            "PAC (AutoConfigURL) became active prior to execution; refusing to overwrite HKCU proxy.",
        )

    existing_srv = _extract_host_port_from_proxy(prior.get("proxy_server"))
    write_srv: str | None = None
    wrote_proxy_server = False
    if enable:
        if existing_srv:
            write_srv = str(prior.get("proxy_server"))
        elif fallback_proxy_server:
            write_srv = fallback_proxy_server
            wrote_proxy_server = True
        else:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "No valid proxy server address available when enabling HKCU system proxy.",
            )

    dword_val = 1 if enable else 0
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        INTERNET_SETTINGS_KEY_PATH,
        0,
        winreg.KEY_SET_VALUE | winreg.KEY_READ,
    ) as key:
        if wrote_proxy_server and write_srv is not None:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, write_srv)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, dword_val)

    notify_wininet_proxy_changed()
    written = {
        "proxy_enable_raw": dword_val,
        "wrote_proxy_server": wrote_proxy_server,
        "proxy_server_raw": write_srv if wrote_proxy_server else prior.get("proxy_server_raw"),
        "proxy_server_existed_after_write": True if wrote_proxy_server else prior.get("proxy_server_existed"),
    }
    return prior, written


def _rollback_real_hkcu_proxy_change(
    prior: dict[str, Any],
    written: dict[str, Any],
    warnings: list[str],
) -> RollbackStatus:
    """Restore prior HKCU values ONLY if current values still match what this action wrote."""
    if sys.platform != "win32":
        return "unsupported"
    import winreg

    current = read_real_hkcu_internet_settings()
    if (
        current.get("proxy_enable_raw") != written.get("proxy_enable_raw")
        or current.get("proxy_server_existed") != written.get("proxy_server_existed_after_write")
        or current.get("proxy_server_raw") != written.get("proxy_server_raw")
    ):
        warnings.append(
            "Detected concurrent external change in HKCU Internet Settings; aborted rollback to avoid overwriting newer state."
        )
        return "failed"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            INTERNET_SETTINGS_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if prior.get("proxy_enable_existed"):
                winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, int(prior["proxy_enable_raw"]))
            else:
                try:
                    winreg.DeleteValue(key, "ProxyEnable")
                except FileNotFoundError:
                    pass

            if written.get("wrote_proxy_server"):
                if prior.get("proxy_server_existed"):
                    winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, str(prior["proxy_server_raw"]))
                else:
                    try:
                        winreg.DeleteValue(key, "ProxyServer")
                    except FileNotFoundError:
                        pass
        notify_wininet_proxy_changed()
        return "succeeded"
    except OSError as exc:
        warnings.append(f"Failed to restore HKCU Internet Settings during rollback: {exc}")
        return "failed"


def _rollback_mock_hkcu_proxy_change(
    hkcu_dict: dict[str, Any],
    prior: dict[str, Any],
    written: dict[str, Any],
    warnings: list[str],
) -> RollbackStatus:
    """Mock-equivalent conditional rollback verifying no external mutation occurred."""
    if (
        hkcu_dict.get("proxy_enable") != written.get("proxy_enable")
        or hkcu_dict.get("proxy_server_existed") != written.get("proxy_server_existed")
        or hkcu_dict.get("proxy_server") != written.get("proxy_server")
    ):
        warnings.append(
            "Detected concurrent external modification in mock HKCU proxy state; aborted rollback."
        )
        return "failed"

    hkcu_dict.clear()
    hkcu_dict.update(copy.deepcopy(prior))
    return "succeeded"


# ---------------------------------------------------------------------------
# Target Observation & Recovery Evaluation Helpers
# ---------------------------------------------------------------------------


def _capture_verification_observation(
    plan: ActionPlanRecord,
    *,
    probe_fn: Callable[..., dict[str, Any]],
    dns_fn: Callable[..., dict[str, Any]],
    proxy_state_fn: Callable[[], dict[str, Any]],
    snapshot_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    obs: dict[str, Any] = {}
    if plan.action_name == "set_system_proxy":
        p_res = proxy_state_fn()
        obs["hkcu_internet_settings"] = (p_res.get("data") or {}).get("hkcu_internet_settings")
    elif plan.action_name == "reconnect_wifi":
        iface = plan.normalized_params["interface"]
        s_res = snapshot_fn(interface=iface, include_recent_events=False)
        sel = (s_res.get("data") or {}).get("selected_interface") or {}
        obs["interface"] = {
            "name": sel.get("name"),
            "status": sel.get("status"),
            "wlan": sel.get("wlan"),
        }

    if plan.verification_kind == "http_url" and plan.verification_target:
        # Probe along effective system route: if proxy is currently enabled & configured, check configured_proxy, else direct
        route_to_check = "direct"
        if plan.action_name == "set_system_proxy":
            hkcu_now = obs.get("hkcu_internet_settings") or {}
            if hkcu_now.get("proxy_enable") is True:
                route_to_check = "configured_proxy"
        h_res = probe_fn(target_url=plan.verification_target, route=route_to_check, timeout_s=3.0)
        if h_res.get("success"):
            hd = h_res.get("data") or {}
            obs["target_probe"] = {
                "route": route_to_check,
                "outcome": hd.get("outcome"),
                "http_status": hd.get("http_status"),
                "portal_state": hd.get("portal_state"),
                "expected_signature_matched": hd.get("expected_signature_matched"),
            }
        else:
            obs["target_probe"] = {
                "route": route_to_check,
                "error_code": (h_res.get("error") or {}).get("code"),
            }
    elif plan.verification_kind == "dns_domain" and plan.verification_target:
        d_res = dns_fn(domain=plan.verification_target, record_type="A", compare_public=False)
        dd = d_res.get("data") or {}
        prim = dd.get("primary_result") or {}
        obs["target_dns"] = {
            "status": prim.get("status"),
            "records": prim.get("records"),
        }
    return obs


def _evaluate_recovery_status(
    plan: ActionPlanRecord,
    execution_status: ExecutionStatus,
    after_obs: dict[str, Any],
) -> RecoveryStatus:
    if execution_status != "succeeded":
        return "not_checked" if plan.verification_kind == "none" else "not_recovered"
    if plan.verification_kind == "none":
        return "not_checked"

    if plan.action_name == "open_browser_url":
        # Opening browser only launches UI; unless known probe already shows portal cleared, do not claim recovered!
        t_probe = after_obs.get("target_probe") or {}
        if t_probe.get("portal_state") == "not_detected" and t_probe.get("expected_signature_matched") is True:
            return "recovered"
        if t_probe.get("portal_state") in ("confirmed", "suspected") or t_probe.get("outcome") != "response":
            return "not_recovered"
        return "unknown"

    if plan.verification_kind == "http_url":
        t_probe = after_obs.get("target_probe") or {}
        outcome = t_probe.get("outcome")
        status_code = t_probe.get("http_status")
        portal_state = t_probe.get("portal_state")
        if outcome != "response" or status_code is None:
            return "not_recovered"
        if portal_state in ("confirmed", "suspected"):
            return "not_recovered"
        if 200 <= int(status_code) < 400:
            return "recovered"
        return "not_recovered"

    if plan.verification_kind == "dns_domain":
        t_dns = after_obs.get("target_dns") or {}
        if t_dns.get("status") == "NOERROR" and t_dns.get("records"):
            return "recovered"
        return "not_recovered"

    return "unknown"


# ---------------------------------------------------------------------------
# Action Execution Entry Point
# ---------------------------------------------------------------------------


def execute_approved_plan(
    plan: ActionPlanRecord,
    *,
    config: ToolConfig,
    mock_env: Any | None,
    mock_state: dict[str, Any] | None,
    probe_fn: Callable[..., dict[str, Any]],
    dns_fn: Callable[..., dict[str, Any]],
    proxy_state_fn: Callable[[], dict[str, Any]],
    snapshot_fn: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Execute a verified, host-approved action plan, re-check original target, and bound rollback."""
    warnings: list[str] = []
    before_obs = _capture_verification_observation(
        plan,
        probe_fn=probe_fn,
        dns_fn=dns_fn,
        proxy_state_fn=proxy_state_fn,
        snapshot_fn=snapshot_fn,
    )

    execution_status: ExecutionStatus = "succeeded"
    rollback_status: RollbackStatus = "not_needed"
    action_details: dict[str, Any] = {}

    if config.mode == "mock":
        assert mock_env is not None and mock_state is not None
        sim_faults = mock_state.get("simulated_action_faults") or {}

        if plan.action_name == "flush_dns":
            if sim_faults.get("flush_dns_fail"):
                execution_status = "failed"
                rollback_status = "unsupported"
                action_details["error"] = "Simulated ipconfig /flushdns failure"
            else:
                mock_env.record_dns_flush()
                action_details["dns_cache_flushed"] = True
                action_details["flush_count"] = mock_env.dns_flushed_count

        elif plan.action_name == "open_browser_url":
            url = plan.normalized_params["url"]
            if sim_faults.get("open_browser_fail"):
                execution_status = "failed"
                rollback_status = "unsupported"
                action_details["browser_launched"] = False
            else:
                mock_env.record_browser_open(url)
                action_details["browser_launched"] = True
                action_details["authentication_completed"] = False
                action_details["opened_url"] = sanitize_url_for_log(url)

        elif plan.action_name == "reconnect_wifi":
            iface_name = plan.normalized_params["interface"]
            target_prof = plan.normalized_params["profile_name"]
            interfaces = (mock_state.get("network_snapshot") or {}).get("interfaces") or []
            matched_if = next((i for i in interfaces if str(i.get("name", "")).lower() == iface_name.lower()), None)
            if matched_if is None:
                execution_status = "failed"
                rollback_status = "unsupported"
            else:
                wlan_dict = matched_if.setdefault("wlan", {})
                prev_prof = wlan_dict.get("profile_name")
                if sim_faults.get("reconnect_wifi_fail"):
                    execution_status = "failed"
                    if prev_prof and prev_prof != target_prof:
                        wlan_dict["profile_name"] = prev_prof
                        rollback_status = "succeeded"
                    else:
                        rollback_status = "not_needed"
                else:
                    wlan_dict["state"] = "connected"
                    wlan_dict["profile_name"] = target_prof
                    wlan_dict["ssid"] = plan.normalized_params.get("ssid") or target_prof
                    matched_if["status"] = "connected"
                    action_details["connected_profile"] = target_prof

        elif plan.action_name == "set_system_proxy":
            hkcu_dict = ((mock_state.get("proxy") or {}).setdefault("hkcu", {}))
            if hkcu_dict.get("auto_config_url"):
                raise ToolExecutionError(
                    ErrorCode.UNSUPPORTED,
                    "Mock HKCU AutoConfigURL (PAC) is set; refusing to modify system proxy.",
                )
            prior_copy = copy.deepcopy(hkcu_dict)
            enable_flag = bool(plan.normalized_params["enable"])
            wrote_srv = False
            if enable_flag and not hkcu_dict.get("proxy_server"):
                hkcu_dict["proxy_server"] = plan.extra_meta.get("resolved_proxy_server_if_needed")
                hkcu_dict["proxy_server_existed"] = True
                wrote_srv = True
            hkcu_dict["proxy_enable"] = enable_flag
            hkcu_dict["proxy_enable_existed"] = True

            written_snap = {
                "proxy_enable": hkcu_dict["proxy_enable"],
                "proxy_server_existed": hkcu_dict.get("proxy_server_existed"),
                "proxy_server": hkcu_dict.get("proxy_server"),
                "wrote_srv": wrote_srv,
            }

            if sim_faults.get("set_system_proxy_fail_after_write"):
                execution_status = "failed"
                if sim_faults.get("concurrent_external_proxy_mutation"):
                    hkcu_dict["proxy_server"] = "127.0.0.1:19999"
                rollback_status = _rollback_mock_hkcu_proxy_change(
                    hkcu_dict, prior_copy, written_snap, warnings
                )
            else:
                action_details["hkcu_proxy_enable"] = enable_flag
                action_details["hkcu_proxy_server"] = redact_proxy_string(hkcu_dict.get("proxy_server"))

    else:
        # REAL MODE EXECUTION (requires config.allow_writes == True checked by caller)
        if plan.action_name == "flush_dns":
            cmd_out = run_windows_command(["ipconfig.exe", "/flushdns"], timeout_s=5.0)
            if cmd_out.timed_out or cmd_out.returncode != 0:
                execution_status = "failed"
                rollback_status = "unsupported"
                action_details["returncode"] = cmd_out.returncode
                warnings.append("ipconfig /flushdns returned non-zero status or timed out.")
            else:
                action_details["dns_cache_flushed"] = True

        elif plan.action_name == "open_browser_url":
            url = plan.normalized_params["url"]
            try:
                if sys.platform == "win32":
                    os.startfile(url)  # type: ignore[attr-defined]
                else:
                    import webbrowser

                    if not webbrowser.open(url, new=2):
                        raise OSError("webbrowser.open returned False")
                action_details["browser_launched"] = True
                action_details["authentication_completed"] = False
                action_details["opened_url"] = sanitize_url_for_log(url)
            except OSError as exc:
                execution_status = "failed"
                rollback_status = "unsupported"
                action_details["browser_launched"] = False
                warnings.append(f"Browser launch failed: {exc}")

        elif plan.action_name == "reconnect_wifi":
            iface = plan.normalized_params["interface"]
            target_prof = plan.normalized_params["profile_name"]
            ssid = plan.normalized_params.get("ssid")
            prev_wlan = ((before_obs.get("interface") or {}).get("wlan") or {})
            prev_prof = prev_wlan.get("profile_name") if prev_wlan.get("state") == "connected" else None

            cmd = ["netsh.exe", "wlan", "connect", f"name={target_prof}", f"interface={iface}"]
            if ssid:
                cmd.append(f"ssid={ssid}")
            conn_out = run_windows_command(cmd, timeout_s=8.0)
            if conn_out.timed_out or conn_out.returncode != 0:
                execution_status = "failed"
                if prev_prof and prev_prof != target_prof:
                    rb_out = run_windows_command(
                        ["netsh.exe", "wlan", "connect", f"name={prev_prof}", f"interface={iface}"],
                        timeout_s=6.0,
                    )
                    rollback_status = "succeeded" if (not rb_out.timed_out and rb_out.returncode == 0) else "failed"
                else:
                    rollback_status = "not_needed"
                warnings.append("netsh wlan connect failed.")
            else:
                # Brief pause for association state update before post-check
                time.sleep(0.5)
                action_details["connected_profile"] = target_prof

        elif plan.action_name == "set_system_proxy":
            enable_flag = bool(plan.normalized_params["enable"])
            fallback_srv = plan.extra_meta.get("resolved_proxy_server_if_needed")
            prior_raw: dict[str, Any] | None = None
            written_raw: dict[str, Any] | None = None
            try:
                prior_raw, written_raw = _apply_real_hkcu_proxy_change(enable_flag, fallback_srv)
                verify_now = read_real_hkcu_internet_settings()
                if verify_now.get("proxy_enable") != enable_flag:
                    execution_status = "failed"
                    rollback_status = _rollback_real_hkcu_proxy_change(prior_raw, written_raw, warnings)
                else:
                    pub = format_public_hkcu_settings(verify_now)
                    action_details["hkcu_proxy_enable"] = pub["proxy_enable"]
                    action_details["hkcu_proxy_server"] = pub["proxy_server"]
            except ToolExecutionError:
                raise
            except Exception as exc:
                execution_status = "failed"
                warnings.append(f"HKCU proxy update error: {exc}")
                if prior_raw is not None and written_raw is not None:
                    rollback_status = _rollback_real_hkcu_proxy_change(prior_raw, written_raw, warnings)
                else:
                    rollback_status = "not_needed"

    after_obs = _capture_verification_observation(
        plan,
        probe_fn=probe_fn,
        dns_fn=dns_fn,
        proxy_state_fn=proxy_state_fn,
        snapshot_fn=snapshot_fn,
    )
    recovery_status = _evaluate_recovery_status(plan, execution_status, after_obs)

    status_out: Literal["ok", "partial"] = "partial" if warnings or execution_status == "failed" else "ok"
    summary = (
        f"动作 '{plan.action_name}' 执行状态={execution_status}，"
        f"目标复检={recovery_status}，回退状态={rollback_status}"
    )

    data: dict[str, Any] = {
        "data_origin": str((mock_state or {}).get("data_origin") or "real_host"),
        "plan_id": plan.plan_id,
        "action_name": plan.action_name,
        "execution_status": execution_status,
        "recovery_status": recovery_status,
        "before": before_obs,
        "after": after_obs,
        "rollback_status": rollback_status,
        "action_details": action_details,
    }
    return data, status_out, warnings, summary
