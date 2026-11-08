"""Network interface snapshot collection, Wi-Fi metrics normalization, and CLI fallback parsers."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
from typing import Any, Literal

from netpet_tools.base import (
    ErrorCode,
    ToolExecutionError,
    run_windows_command,
    validate_safe_text,
)

_VIRTUAL_KEYWORDS = (
    "virtual",
    "vethernet",
    "wsl",
    "hyper-v",
    "vmware",
    "virtualbox",
    "vbox",
    "loopback",
    "tap-",
    "tap windows",
    "wintun",
    "wireguard",
    "tailscale",
    "clash",
    "mihomo",
    "sing-box",
    "zerotier",
    "bluetooth",
    "teredo",
    "isatap",
    "6to4",
    "npcap",
    "wan miniport",
    "wi-fi direct virtual",
    "hosted network virtual",
)

_WIFI_KEYWORDS = (
    "wi-fi",
    "wifi",
    "wlan",
    "wireless",
    "802.11",
    "无线",
)

_ETHERNET_KEYWORDS = (
    "ethernet",
    "gbe",
    "gigabit",
    "realtek pcie",
    "intel(r) ethernet",
    "以太网",
)

_LOCATION_DENIED_HINTS = (
    "location",
    "privacy",
    "ms-settings:privacy-location",
    "位置",
    "隐私",
    "access is denied",
    "拒绝访问",
    "elevation",
    "permission",
)

_WLANSVC_DOWN_HINTS = (
    "wlansvc",
    "wireless autoconfig service",
    "无线自动配置服务",
    "there is no wireless interface on the system",
    "系统上没有无线接口",
)

# Fixed read-only PowerShell script (zero user string interpolation).
_PS_SNAPSHOT_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$adapters = @(Get-NetAdapter -IncludeHidden:$false)
$cimMap = @{}
foreach ($c in @(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter "IPEnabled=TRUE")) {
    $cimMap[[int]$c.InterfaceIndex] = $c
}
$result = @()
foreach ($a in $adapters) {
    $idx = [int]$a.ifIndex
    $ipcfg = Get-NetIPConfiguration -InterfaceIndex $idx
    $v4 = @()
    foreach ($addr in @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4)) {
        if ($addr.IPAddress) {
            $v4 += @{ ip = [string]$addr.IPAddress; prefix_length = [int]$addr.PrefixLength }
        }
    }
    $v6 = @()
    foreach ($addr in @(Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv6)) {
        if ($addr.IPAddress) {
            $v6 += @{ ip = [string]$addr.IPAddress; prefix_length = [int]$addr.PrefixLength }
        }
    }
    $gw4 = @()
    if ($ipcfg -and $ipcfg.IPv4DefaultGateway) {
        foreach ($g in @($ipcfg.IPv4DefaultGateway)) {
            if ($g.NextHop) { $gw4 += [string]$g.NextHop }
        }
    }
    $gw6 = @()
    if ($ipcfg -and $ipcfg.IPv6DefaultGateway) {
        foreach ($g in @($ipcfg.IPv6DefaultGateway)) {
            if ($g.NextHop) { $gw6 += [string]$g.NextHop }
        }
    }
    $dnsList = @()
    foreach ($d in @(Get-DnsClientServerAddress -InterfaceIndex $idx)) {
        foreach ($srv in @($d.ServerAddresses)) {
            if ($srv -and -not ($dnsList -contains [string]$srv)) {
                $dnsList += [string]$srv
            }
        }
    }
    $routes = @(Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0','::/0')
    $metric = $null
    if ($routes.Count -gt 0) {
        $minRoute = $routes | Sort-Object -Property RouteMetric | Select-Object -First 1
        $metric = [int]($minRoute.RouteMetric + $minRoute.InterfaceMetric)
    }
    $dhcpIf = Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4
    $dhcpEnabled = $null
    if ($dhcpIf) {
        $dhcpEnabled = ([string]$dhcpIf.Dhcp -eq 'Enabled')
    }
    $leaseObtained = $null
    $leaseExpires = $null
    if ($cimMap.ContainsKey($idx)) {
        $cim = $cimMap[$idx]
        if ($null -ne $cim.DHCPEnabled) { $dhcpEnabled = [bool]$cim.DHCPEnabled }
        if ($cim.DHCPLeaseObtained) { $leaseObtained = ([datetime]$cim.DHCPLeaseObtained).ToString('o') }
        if ($cim.DHCPLeaseExpires) { $leaseExpires = ([datetime]$cim.DHCPLeaseExpires).ToString('o') }
    }
    $result += @{
        name = [string]$a.Name
        description = [string]$a.InterfaceDescription
        if_type = [int]$a.InterfaceType
        status_raw = [string]$a.Status
        mac_address = [string]$a.MacAddress
        ipv4_addresses = $v4
        ipv6_addresses = $v6
        default_gateways_ipv4 = $gw4
        default_gateways_ipv6 = $gw6
        route_metric = $metric
        dns_servers = $dnsList
        dhcp_enabled = $dhcpEnabled
        dhcp_lease_obtained = $leaseObtained
        dhcp_lease_expires = $leaseExpires
    }
}
ConvertTo-Json -InputObject @{ interfaces = $result } -Depth 6 -Compress
"""

_PS_WLAN_EVENTS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$events = @(Get-WinEvent -LogName 'Microsoft-Windows-WLAN-AutoConfig/Operational' -MaxEvents 8 | ForEach-Object {
    @{
        event_id = [int]$_.Id
        time_created = $_.TimeCreated.ToString('o')
        level = [string]$_.LevelDisplayName
        summary = ([string]$_.Message -split "`r?`n")[0]
    }
})
ConvertTo-Json -InputObject @{ events = $events } -Depth 4 -Compress
"""


def classify_adapter_type(
    name: str,
    description: str,
    if_type: int | None = None,
) -> Literal["wifi", "ethernet", "virtual", "other"]:
    """Classify an adapter into wifi, ethernet, virtual, or other."""
    combined = f"{name} {description}".lower()
    if any(kw in combined for kw in _VIRTUAL_KEYWORDS):
        return "virtual"
    if if_type == 24 or if_type == 131:
        return "virtual"
    if if_type == 71 or any(kw in combined for kw in _WIFI_KEYWORDS):
        return "wifi"
    if if_type == 6 or any(kw in combined for kw in _ETHERNET_KEYWORDS):
        return "ethernet"
    return "other"


def compute_wlan_signal_fields(
    signal_quality_pct: int | None,
    native_rssi_dbm: int | None = None,
) -> dict[str, Any]:
    """Normalize Wi-Fi signal quality and RSSI without fabricating endpoint dBm values."""
    if native_rssi_dbm is not None:
        return {
            "signal_quality_pct": signal_quality_pct,
            "rssi_dbm": int(native_rssi_dbm),
            "rssi_estimated_dbm": None,
            "rssi_source": "native_rssi",
            "rssi_boundary_note": None,
        }

    if signal_quality_pct is None:
        return {
            "signal_quality_pct": None,
            "rssi_dbm": None,
            "rssi_estimated_dbm": None,
            "rssi_source": "unavailable",
            "rssi_boundary_note": None,
        }

    pct = max(0, min(100, int(signal_quality_pct)))
    if 1 <= pct <= 99:
        return {
            "signal_quality_pct": pct,
            "rssi_dbm": None,
            "rssi_estimated_dbm": round(pct / 2.0 - 100.0, 1),
            "rssi_source": "estimated_from_quality",
            "rssi_boundary_note": None,
        }
    if pct == 100:
        return {
            "signal_quality_pct": 100,
            "rssi_dbm": None,
            "rssi_estimated_dbm": None,
            "rssi_source": "boundary_clamped",
            "rssi_boundary_note": "Signal quality 100% is a clamped boundary (>= -50 dBm); exact RSSI above -50 dBm cannot be inferred.",
        }
    return {
        "signal_quality_pct": 0,
        "rssi_dbm": None,
        "rssi_estimated_dbm": None,
        "rssi_source": "boundary_clamped",
        "rssi_boundary_note": "Signal quality 0% is a clamped boundary (<= -100 dBm); exact RSSI below -100 dBm cannot be inferred.",
    }


def _parse_iso_datetime(value: str | None) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def evaluate_dhcp_fields(
    dhcp_enabled: bool | None,
    lease_obtained: str | None,
    lease_expires: str | None,
    *,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    """Evaluate DHCP configuration, lease data presence, and lease validity independently."""
    current = now_utc or dt.datetime.now(dt.timezone.utc)
    has_lease_timestamps = bool(lease_obtained or lease_expires)

    if dhcp_enabled is False:
        lease_present: bool | None = False
        lease_valid: bool | None = None
    elif has_lease_timestamps:
        lease_present = True
        exp_dt = _parse_iso_datetime(lease_expires)
        lease_valid = (exp_dt > current) if exp_dt is not None else None
    else:
        # Never infer DHCP lease validity merely from holding a private IP address!
        lease_present = None
        lease_valid = None

    return {
        "dhcp_enabled": dhcp_enabled,
        "dhcp_lease_present": lease_present,
        "dhcp_lease_obtained": lease_obtained,
        "dhcp_lease_expires": lease_expires,
        "dhcp_lease_valid": lease_valid,
    }


def normalize_interface_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw adapter fields into canonical schema."""
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    adapter_type = raw.get("adapter_type") or classify_adapter_type(name, description, raw.get("if_type"))

    status_raw = str(raw.get("status") or raw.get("status_raw") or "unknown").strip().lower()
    if status_raw in ("up", "connected", "已连接"):
        status = "connected"
    elif status_raw in ("disconnected", "media disconnected", "已断开连接", "媒体已断开连接", "disconnected "):
        status = "disconnected"
    elif status_raw in ("down", "disabled", "not present"):
        status = "down"
    else:
        status = "unknown"

    ipv4_out: list[dict[str, Any]] = []
    apipa_net = ipaddress.IPv4Network("169.254.0.0/16")
    for item in raw.get("ipv4_addresses") or []:
        ip_text = str(item.get("ip") or "").strip()
        if not ip_text:
            continue
        try:
            ip_obj = ipaddress.IPv4Address(ip_text)
        except ValueError:
            continue
        is_apipa = ip_obj in apipa_net
        ipv4_out.append(
            {
                "ip": str(ip_obj),
                "prefix_length": item.get("prefix_length"),
                "is_apipa": is_apipa,
                "is_private": bool(ip_obj.is_private and not is_apipa and not ip_obj.is_loopback),
            }
        )

    ipv6_out: list[dict[str, Any]] = []
    ula_net = ipaddress.IPv6Network("fc00::/7")
    for item in raw.get("ipv6_addresses") or []:
        ip_text = str(item.get("ip") or "").split("%")[0].strip()
        if not ip_text:
            continue
        try:
            ip6_obj = ipaddress.IPv6Address(ip_text)
        except ValueError:
            continue
        is_ll = bool(ip6_obj.is_link_local)
        is_usable = bool((ip6_obj.is_global or ip6_obj in ula_net) and not is_ll and not ip6_obj.is_loopback)
        ipv6_out.append(
            {
                "ip": str(ip6_obj),
                "prefix_length": item.get("prefix_length"),
                "is_link_local": is_ll,
                "is_global_or_ula": is_usable,
            }
        )

    has_apipa_ipv4 = any(x["is_apipa"] for x in ipv4_out)
    has_routable_ipv4 = any(not x["is_apipa"] and not ipaddress.IPv4Address(x["ip"]).is_loopback for x in ipv4_out)
    has_usable_ipv6 = any(x["is_global_or_ula"] for x in ipv6_out)

    dhcp_info = evaluate_dhcp_fields(
        raw.get("dhcp_enabled"),
        raw.get("dhcp_lease_obtained"),
        raw.get("dhcp_lease_expires"),
    )

    wlan_out: dict[str, Any] | None = None
    raw_wlan = raw.get("wlan")
    if adapter_type == "wifi" or raw_wlan is not None:
        w = raw_wlan or {}
        sig_fields = compute_wlan_signal_fields(w.get("signal_quality_pct"), w.get("native_rssi_dbm"))
        wlan_out = {
            "state": w.get("state") or ("connected" if status == "connected" else "disconnected"),
            "permission_issue": w.get("permission_issue"),
            "ssid": w.get("ssid"),
            "profile_name": w.get("profile_name"),
            "bssid": w.get("bssid"),
            "radio_type": w.get("radio_type"),
            "channel": w.get("channel"),
            **sig_fields,
        }

    return {
        "name": name,
        "description": description,
        "adapter_type": adapter_type,
        "status": status,
        "mac_address": raw.get("mac_address") or None,
        "ipv4_addresses": ipv4_out,
        "ipv6_addresses": ipv6_out,
        "has_apipa_ipv4": has_apipa_ipv4,
        "has_routable_ipv4": has_routable_ipv4,
        "has_usable_ipv6": has_usable_ipv6,
        "default_gateways_ipv4": list(raw.get("default_gateways_ipv4") or []),
        "default_gateways_ipv6": list(raw.get("default_gateways_ipv6") or []),
        "route_metric": raw.get("route_metric"),
        "dns_servers": list(raw.get("dns_servers") or []),
        **dhcp_info,
        "wlan": wlan_out,
    }


def select_snapshot_interface(
    interfaces: list[dict[str, Any]],
    requested_interface: str = "auto",
) -> tuple[dict[str, Any] | None, str]:
    """Select target interface explicitly or by active default-egress ranking."""
    req = validate_safe_text(requested_interface, "interface", max_len=128)
    if not interfaces:
        if req.lower() != "auto":
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"Requested interface '{req}' was not found (host reported zero network interfaces).",
                data={"available_interfaces": []},
            )
        return None, "no_interfaces_detected"

    if req.lower() != "auto":
        req_lower = req.lower()
        for iface in interfaces:
            if iface["name"].lower() == req_lower or iface["description"].lower() == req_lower:
                return iface, f"explicit_match:{iface['name']}"
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Interface '{req}' was not found among host adapters.",
            data={"available_interfaces": [i["name"] for i in interfaces]},
        )

    def _sort_key(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
        is_connected = 0 if item["status"] == "connected" else 1
        has_gw = 0 if (item["default_gateways_ipv4"] or item["default_gateways_ipv6"]) else 1
        is_physical = 0 if item["adapter_type"] in ("wifi", "ethernet") else 1
        has_ip = 0 if (item["has_routable_ipv4"] or item["has_usable_ipv6"]) else 1
        metric = int(item["route_metric"]) if item["route_metric"] is not None else 99999
        return (is_connected, has_gw, is_physical, has_ip, metric)

    ranked = sorted(interfaces, key=_sort_key)
    chosen = ranked[0]
    reasons: list[str] = [f"status={chosen['status']}", f"type={chosen['adapter_type']}"]
    if chosen["default_gateways_ipv4"] or chosen["default_gateways_ipv6"]:
        reasons.append(f"has_default_gateway(metric={chosen['route_metric']})")
    else:
        reasons.append("no_default_gateway_fallback")
    return chosen, ", ".join(reasons)


def parse_netsh_wlan_interfaces(output: str, returncode: int = 0) -> dict[str, Any]:
    """Parse Chinese or English `netsh wlan show interfaces` text output."""
    text = (output or "").strip()
    lower = text.lower()

    if any(hint in lower for hint in _LOCATION_DENIED_HINTS) and (returncode != 0 or "ssid" not in lower):
        return {
            "location_restricted": True,
            "wlansvc_unavailable": False,
            "interfaces": {},
            "raw_notice": text.splitlines()[0] if text else "Location permission denied for WLAN API.",
        }

    if any(hint in lower for hint in _WLANSVC_DOWN_HINTS):
        return {
            "location_restricted": False,
            "wlansvc_unavailable": True,
            "interfaces": {},
            "raw_notice": text.splitlines()[0] if text else "WLAN AutoConfig unavailable or no wireless adapter.",
        }

    interfaces: dict[str, dict[str, Any]] = {}
    current_name: str | None = None
    current: dict[str, Any] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key_raw, val_raw = line.split(":", 1)
        key = key_raw.strip().lower()
        val = val_raw.strip()

        if key in ("name", "名称"):
            if current_name and current:
                interfaces[current_name] = current
            current_name = val
            current = {
                "state": "unknown",
                "permission_issue": None,
                "ssid": None,
                "profile_name": None,
                "bssid": None,
                "radio_type": None,
                "channel": None,
                "signal_quality_pct": None,
                "native_rssi_dbm": None,
            }
        elif current_name is not None:
            if key in ("state", "状态"):
                v_low = val.lower()
                if v_low in ("connected", "已连接"):
                    current["state"] = "connected"
                elif "disconnect" in v_low or "断开" in v_low:
                    current["state"] = "disconnected"
                else:
                    current["state"] = v_low
            elif key == "ssid":
                current["ssid"] = val or None
            elif key in ("bssid", "ap bssid"):
                current["bssid"] = val or None
            elif key in ("radio type", "无线电类型"):
                current["radio_type"] = val or None
            elif key in ("channel", "频道", "信道"):
                m_ch = re.search(r"(\d+)", val)
                if m_ch:
                    current["channel"] = int(m_ch.group(1))
            elif key in ("signal", "信号"):
                m_sig = re.search(r"(\d+)\s*%", val)
                if m_sig:
                    current["signal_quality_pct"] = int(m_sig.group(1))
            elif key in ("rssi",):
                m_rssi = re.search(r"(-?\d+)", val)
                if m_rssi:
                    current["native_rssi_dbm"] = int(m_rssi.group(1))
            elif key in ("profile", "配置文件"):
                current["profile_name"] = val or None

    if current_name and current:
        interfaces[current_name] = current

    return {
        "location_restricted": False,
        "wlansvc_unavailable": False,
        "interfaces": interfaces,
        "raw_notice": None,
    }


def parse_ipconfig_all(output: str) -> list[dict[str, Any]]:
    """Parse Chinese or English `ipconfig /all` output as a compatibility fallback."""
    adapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_list_field: str | None = None

    header_re = re.compile(
        r"^(?:Ethernet adapter|Wireless LAN adapter|Unknown adapter|PPP adapter|以太网适配器|无线局域网适配器|未知适配器|隧道适配器)\s+(.+?)\s*[:：]\s*$",
        re.IGNORECASE,
    )

    for raw_line in (output or "").splitlines():
        if not raw_line.strip():
            continue
        m_hdr = header_re.match(raw_line.strip())
        if m_hdr:
            if current is not None:
                adapters.append(current)
            hdr_prefix = raw_line.strip().lower()
            name = m_hdr.group(1).strip()
            hint_iftype = 71 if ("wireless" in hdr_prefix or "无线" in hdr_prefix) else None
            current = {
                "name": name,
                "description": "",
                "if_type": hint_iftype,
                "status_raw": "connected",
                "mac_address": None,
                "ipv4_addresses": [],
                "ipv6_addresses": [],
                "default_gateways_ipv4": [],
                "default_gateways_ipv6": [],
                "route_metric": None,
                "dns_servers": [],
                "dhcp_enabled": None,
                "dhcp_lease_obtained": None,
                "dhcp_lease_expires": None,
            }
            last_list_field = None
            continue

        if current is None:
            continue

        stripped = raw_line.strip()
        if ":" in stripped and (raw_line.startswith("   ") and ("." in stripped.split(":", 1)[0] or any(k in stripped.lower() for k in ("ip", "dns", "dhcp", "description", "描述", "physical", "物理", "media", "媒体", "gateway", "网关", "lease", "租约")))):
            key_part, val_part = stripped.split(":", 1)
            key = re.sub(r"[\.\s]+$", "", key_part).strip().lower()
            val = val_part.strip()
            last_list_field = None

            if "description" in key or "描述" in key:
                current["description"] = val
            elif "physical address" in key or "物理地址" in key:
                current["mac_address"] = val or None
            elif "media state" in key or "媒体状态" in key:
                if "disconnected" in val.lower() or "已断开" in val:
                    current["status_raw"] = "disconnected"
            elif "dhcp enabled" in key or "dhcp 已启用" in key:
                v_low = val.lower()
                if v_low in ("yes", "是"):
                    current["dhcp_enabled"] = True
                elif v_low in ("no", "否"):
                    current["dhcp_enabled"] = False
            elif "ipv4" in key or "ip address" in key:
                cleaned_ip = re.sub(r"\(.*?\)|（.*?）", "", val).strip()
                try:
                    ipaddress.IPv4Address(cleaned_ip)
                    current["ipv4_addresses"].append({"ip": cleaned_ip, "prefix_length": None})
                except ValueError:
                    pass
            elif "ipv6" in key and "iaid" not in key and "duid" not in key:
                cleaned_ip6 = re.sub(r"\(.*?\)|（.*?）", "", val).split("%")[0].strip()
                try:
                    ipaddress.IPv6Address(cleaned_ip6)
                    current["ipv6_addresses"].append({"ip": cleaned_ip6, "prefix_length": None})
                except ValueError:
                    pass
            elif "default gateway" in key or "默认网关" in key:
                last_list_field = "gateway"
                _append_gateway_ip(current, val)
            elif "dns servers" in key or "dns 服务器" in key:
                last_list_field = "dns"
                _append_dns_ip(current, val)
            elif "lease obtained" in key or "获得租约的时间" in key:
                # Keep raw timestamp notice if non-ISO; evaluate_dhcp_fields handles parseable ISO
                current["dhcp_lease_obtained"] = val or None
            elif "lease expires" in key or "租约过期的时间" in key:
                current["dhcp_lease_expires"] = val or None
        elif last_list_field == "gateway":
            _append_gateway_ip(current, stripped)
        elif last_list_field == "dns":
            _append_dns_ip(current, stripped)

    if current is not None:
        adapters.append(current)
    return adapters


def _append_gateway_ip(adapter: dict[str, Any], raw_val: str) -> None:
    candidate = raw_val.split("%")[0].strip()
    if not candidate:
        return
    try:
        ip_obj = ipaddress.ip_address(candidate)
        if isinstance(ip_obj, ipaddress.IPv4Address):
            adapter["default_gateways_ipv4"].append(str(ip_obj))
        else:
            adapter["default_gateways_ipv6"].append(str(ip_obj))
    except ValueError:
        pass


def _append_dns_ip(adapter: dict[str, Any], raw_val: str) -> None:
    candidate = raw_val.split("%")[0].strip()
    if not candidate:
        return
    try:
        ip_obj = ipaddress.ip_address(candidate)
        ip_str = str(ip_obj)
        if ip_str not in adapter["dns_servers"]:
            adapter["dns_servers"].append(ip_str)
    except ValueError:
        pass


def assemble_snapshot_payload(
    raw_interfaces: list[dict[str, Any]],
    *,
    requested_interface: str = "auto",
    include_recent_events: bool = False,
    recent_wlan_events: list[dict[str, Any]] | None = None,
    extra_warnings: list[str] | None = None,
    data_origin: str = "real_host",
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Normalize interfaces, select target interface, and compute snapshot warnings/status."""
    warnings: list[str] = list(extra_warnings or [])
    normalized = [normalize_interface_record(item) for item in raw_interfaces]
    selected, selection_basis = select_snapshot_interface(normalized, requested_interface)

    status: Literal["ok", "partial"] = "ok"
    if selected is None:
        status = "partial"
        warnings.append("No active or configured network interfaces were detected.")
    else:
        wlan_info = selected.get("wlan")
        if isinstance(wlan_info, dict) and wlan_info.get("state") == "location_restricted":
            status = "partial"
            warnings.append(
                "Windows location privacy settings restricted Wi-Fi SSID/BSSID/signal details; IP and routing data were preserved."
            )
        if selected.get("dhcp_enabled") is True and selected.get("dhcp_lease_present") is None and selected.get("status") == "connected":
            warnings.append("DHCP is enabled on the selected interface, but exact lease timestamps were unavailable.")

    active_virtual = [
        iface["name"]
        for iface in normalized
        if iface["adapter_type"] == "virtual"
        and iface["status"] == "connected"
        and (iface["default_gateways_ipv4"] or iface["default_gateways_ipv6"] or iface["has_routable_ipv4"])
    ]
    other_active = [
        iface["name"]
        for iface in normalized
        if (selected is None or iface["name"] != selected["name"])
        and iface["status"] == "connected"
        and (iface["has_routable_ipv4"] or iface["has_usable_ipv6"])
    ]

    host_notes: list[str] = []
    if selected is not None and selected["has_apipa_ipv4"]:
        host_notes.append(
            "Selected interface holds an IPv4 APIPA address (169.254.0.0/16); check IPv6 usability and other active interfaces before concluding whole-machine offline."
        )
    if active_virtual:
        host_notes.append(
            f"Active virtual/tunnel interfaces detected ({', '.join(active_virtual)}); OS traffic may be steered by virtual routes."
        )

    candidates_summary = [
        {
            "name": iface["name"],
            "adapter_type": iface["adapter_type"],
            "status": iface["status"],
            "has_default_gateway": bool(iface["default_gateways_ipv4"] or iface["default_gateways_ipv6"]),
            "route_metric": iface["route_metric"],
            "has_routable_ipv4": iface["has_routable_ipv4"],
            "has_apipa_ipv4": iface["has_apipa_ipv4"],
            "has_usable_ipv6": iface["has_usable_ipv6"],
        }
        for iface in normalized
    ]

    data: dict[str, Any] = {
        "data_origin": data_origin,
        "requested_interface": requested_interface,
        "selection_basis": selection_basis,
        "selected_interface": selected,
        "candidate_interfaces": candidates_summary,
        "active_virtual_interfaces": active_virtual,
        "other_active_interfaces": other_active,
        "host_notes": host_notes,
        "recent_wlan_events": recent_wlan_events if include_recent_events else None,
    }

    if include_recent_events and recent_wlan_events is None:
        status = "partial"

    if selected is None:
        summary = "未检测到可用网络接口"
    else:
        summary = (
            f"已读取接口 '{selected['name']}' ({selected['adapter_type']}, 状态={selected['status']}) 的网络快照"
        )
        if status == "partial":
            summary += "（部分无线或事件字段受限）"

    return data, status, warnings, summary


def collect_real_network_snapshot(
    interface: str = "auto",
    include_recent_events: bool = False,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Read network snapshot from real Windows host using PowerShell JSON + netsh fallback."""
    validate_safe_text(interface, "interface", max_len=128)
    warnings: list[str] = []
    raw_interfaces: list[dict[str, Any]] = []

    ps_out = run_windows_command(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SNAPSHOT_SCRIPT],
        timeout_s=6.0,
    )
    if not ps_out.timed_out and ps_out.returncode == 0 and ps_out.stdout.strip():
        try:
            parsed_ps = json.loads(ps_out.stdout.strip())
            if isinstance(parsed_ps, dict) and isinstance(parsed_ps.get("interfaces"), list):
                raw_interfaces = parsed_ps["interfaces"]
        except json.JSONDecodeError:
            warnings.append("PowerShell adapter JSON parse failed; falling back to ipconfig /all.")

    if not raw_interfaces:
        ipcfg_out = run_windows_command(["ipconfig.exe", "/all"], timeout_s=5.0)
        if ipcfg_out.timed_out or (ipcfg_out.returncode != 0 and not ipcfg_out.stdout.strip()):
            raise ToolExecutionError(
                ErrorCode.INTERNAL_ERROR,
                "Failed to read host network adapter configuration via PowerShell and ipconfig.",
                retryable=True,
            )
        raw_interfaces = parse_ipconfig_all(ipcfg_out.stdout)

    # Supplement Wi-Fi interfaces with netsh wlan details
    has_wifi_candidate = any(
        classify_adapter_type(str(i.get("name", "")), str(i.get("description", "")), i.get("if_type")) == "wifi"
        for i in raw_interfaces
    )
    if has_wifi_candidate:
        wlan_cmd = run_windows_command(["netsh.exe", "wlan", "show", "interfaces"], timeout_s=4.0)
        if wlan_cmd.timed_out:
            warnings.append("Timed out querying `netsh wlan show interfaces`; Wi-Fi radio details omitted.")
        else:
            parsed_wlan = parse_netsh_wlan_interfaces(wlan_cmd.stdout + "\n" + wlan_cmd.stderr, wlan_cmd.returncode)
            if parsed_wlan["location_restricted"]:
                for iface in raw_interfaces:
                    if classify_adapter_type(str(iface.get("name", "")), str(iface.get("description", "")), iface.get("if_type")) == "wifi":
                        iface["wlan"] = {
                            "state": "location_restricted",
                            "permission_issue": ErrorCode.LOCATION_PERMISSION_REQUIRED.value,
                            "ssid": None,
                            "profile_name": None,
                            "bssid": None,
                            "radio_type": None,
                            "channel": None,
                            "signal_quality_pct": None,
                            "native_rssi_dbm": None,
                        }
            elif parsed_wlan["wlansvc_unavailable"]:
                warnings.append("WLAN AutoConfig service (wlansvc) is inactive or unavailable.")
            else:
                wlan_map = parsed_wlan["interfaces"]
                for iface in raw_interfaces:
                    if classify_adapter_type(str(iface.get("name", "")), str(iface.get("description", "")), iface.get("if_type")) == "wifi":
                        name_key = str(iface.get("name", ""))
                        matched_wlan = wlan_map.get(name_key)
                        if matched_wlan is None:
                            for k, v in wlan_map.items():
                                if k.lower() == name_key.lower():
                                    matched_wlan = v
                                    break
                        if matched_wlan is not None:
                            iface["wlan"] = matched_wlan

    recent_events: list[dict[str, Any]] | None = None
    if include_recent_events:
        ev_cmd = run_windows_command(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_WLAN_EVENTS_SCRIPT],
            timeout_s=4.0,
        )
        if not ev_cmd.timed_out and ev_cmd.returncode == 0 and ev_cmd.stdout.strip():
            try:
                ev_json = json.loads(ev_cmd.stdout.strip())
                if isinstance(ev_json, dict) and isinstance(ev_json.get("events"), list):
                    recent_events = ev_json["events"]
            except json.JSONDecodeError:
                warnings.append("Failed to parse recent WLAN AutoConfig events.")
        else:
            warnings.append("Recent WLAN Operational events could not be read (log empty, timed out, or permission restricted).")

    return assemble_snapshot_payload(
        raw_interfaces,
        requested_interface=interface,
        include_recent_events=include_recent_events,
        recent_wlan_events=recent_events,
        extra_warnings=warnings,
        data_origin="real_host",
    )


def collect_mock_network_snapshot(
    scenario_state: dict[str, Any],
    *,
    interface: str = "auto",
    include_recent_events: bool = False,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Assemble network snapshot from isolated mock scenario state."""
    validate_safe_text(interface, "interface", max_len=128)
    snap_sec = scenario_state.get("network_snapshot") or {}
    raw_interfaces = snap_sec.get("interfaces") or []
    warnings: list[str] = []
    recent_events: list[dict[str, Any]] | None = None
    if include_recent_events:
        recent_events = snap_sec.get("recent_wlan_events")
        if recent_events is None and snap_sec.get("recent_wlan_events_warning"):
            warnings.append(str(snap_sec["recent_wlan_events_warning"]))

    return assemble_snapshot_payload(
        raw_interfaces,
        requested_interface=interface,
        include_recent_events=include_recent_events,
        recent_wlan_events=recent_events,
        extra_warnings=warnings,
        data_origin=str(scenario_state.get("data_origin") or "synthetic"),
    )
