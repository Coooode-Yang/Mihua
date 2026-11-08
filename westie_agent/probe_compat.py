"""App-only probe compatibility. Does not alter OS settings or tool IP guards."""
from __future__ import annotations

import copy
import ipaddress
import re
import socket
import sys
from urllib.parse import urlsplit

FAKE_IP_POOL = ipaddress.ip_network("198.18.0.0/15")


def normalize_web_target(value: str | None) -> str | None:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "`\"'":
        text = text[1:-1].strip()
    # Accept a plain, unambiguous website name. Other schemes and malformed
    # inputs still go through the original strict tool validation.
    if "://" not in text and not any(c.isspace() for c in text):
        try:
            parsed = urlsplit("//" + text)
            if parsed.hostname and "." in parsed.hostname and not parsed.username and not parsed.password:
                if ":" not in text.split("/", 1)[0] or parsed.port:
                    return "https://" + text
        except ValueError:
            pass
    return text


def is_probe_policy_denial(result: dict) -> bool:
    error = result.get("error") or {}
    return (error.get("code") == "PERMISSION_DENIED"
            and "Private network target" in str(error.get("message", ""))
            and "blocked unless" in str(error.get("message", "")))


def fake_ip_guard_failure(result: dict, target: str | None) -> bool:
    if not is_probe_policy_denial(result):
        return False
    # Only a domain name resolved into the synthetic pool qualifies. Literal
    # private IPs/localhost/metadata and other private subnets never qualify.
    if target:
        try:
            host = urlsplit(target).hostname
            ipaddress.ip_address(host or "")
            return False
        except ValueError:
            pass
    message = str((result.get("error") or {}).get("message", ""))
    match = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", message)
    if not match:
        return False
    try:
        return ipaddress.ip_address(match.group(1)) in FAKE_IP_POOL
    except ValueError:
        return False


def explain_probe_failure(result: dict) -> dict:
    """Do not let a local validator error masquerade as a site/OS denial."""
    result = copy.deepcopy(result)
    error = result.get("error") or {}
    if is_probe_policy_denial(result):
        message = "小鸡的探测范围检查停止了请求，尚未向该网站发送网页请求；这不是网站封禁或 Windows 权限被更改的证据。"
        note = {"category": "probe_policy_restriction", "http_request_sent": False,
                "network_permission_conclusion": "not_tested", "message": message}
        if fake_ip_guard_failure(result, None):
            note["fake_ip_suspected"] = True
            note["message"] += "域名解析落在常见代理 Fake-IP 地址段，可能需要兼容现有代理。"
        result["host_note"] = note
        result["error"] = {"code": "PROBE_POLICY_RESTRICTION", "message": note["message"], "retryable": False}
        result["summary"] = note["message"]
    elif error.get("code") == "INVALID_INPUT":
        result["host_note"] = {"category": "probe_input_error", "http_request_sent": False,
                               "network_permission_conclusion": "not_tested",
                               "message": "探测参数不符合工具要求，尚未验证目标网站是否可访问；不能据此判断用户权限或网络封禁。"}
    return result


def loopback_http_proxy_from_settings(values: dict, target: str | None) -> str | None:
    """Read a declared HTTP proxy, never guess one from arbitrary open ports."""
    if values.get("AutoConfigURL"):
        return None  # A PAC cannot safely be treated as a fixed manual proxy.
    raw = values.get("ProxyServer")
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if "=" in raw and "://" not in raw:
        entries = {}
        for entry in raw.split(";"):
            if "=" in entry:
                key, value = entry.split("=", 1)
                entries[key.strip().lower()] = value.strip()
        preferred = "https" if target and urlsplit(target).scheme == "https" else "http"
        raw = entries.get(preferred) or entries.get("http") or ""
        if not raw:
            return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not parsed.port:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        if parsed.hostname == "localhost":
            host = "127.0.0.1"
        else:
            host = parsed.hostname or ""
            if not ipaddress.ip_address(host).is_loopback:
                return None
        host_part = f"[{host}]" if ":" in host else host
        return f"{parsed.scheme}://{host_part}:{parsed.port}"
    except ValueError:
        return None


def discover_local_http_proxy(target: str | None) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    values = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings", 0, winreg.KEY_READ) as key:
            for name in ("ProxyServer", "AutoConfigURL"):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
    except OSError:
        return None
    candidate = loopback_http_proxy_from_settings(values, target)
    if candidate:
        parsed = urlsplit(candidate)
        try:
            with socket.create_connection((parsed.hostname, parsed.port), timeout=.3):
                return candidate
        except OSError:
            pass
    return None


def grounded_probe_text(traces: list[dict]) -> str | None:
    """Deterministic UI text for local-policy/Fake-IP compatibility outcomes."""
    relevant = [trace for trace in traces if trace.get("tool") == "probe_connectivity"]
    if not any((t.get("result", {}).get("host_note") or {}).get("category")
               in {"fake_ip_proxy_compatibility", "probe_policy_restriction", "probe_input_error"} for t in relevant):
        return None
    lines = []
    compat = False
    restricted = False
    for trace in relevant:
        result = trace.get("result", {})
        data = result.get("data") or {}
        note = result.get("host_note") or {}
        category = note.get("category")
        if category == "fake_ip_proxy_compatibility":
            compat = True
        if category in {"probe_policy_restriction", "probe_input_error"}:
            restricted = True
            lines.append("这次检查被小鸡自身的探测范围或参数检查停止，没有获得网站拒绝访问的证据。")
            if note.get("fake_ip_suspected"):
                lines.append("检测到疑似代理 Fake-IP 解析，当前探测方式尚未兼容；不能据此说你的访问权限被禁止。")
        attempts = data.get("attempts") or []
        if not attempts and data.get("http_status") is not None:
            attempts = [{"probed_url": (trace.get("arguments") or {}).get("target_url"),
                         "http_status": data.get("http_status")}]
        for attempt in attempts:
            target = attempt.get("probed_url") or "受测网站"
            status = attempt.get("http_status")
            if status is not None:
                suffix = "，该路径获得了网页响应" if 200 <= status < 400 else "，需结合网站响应进一步判断"
                lines.append(f"{target}：HTTP {status}{suffix}。")
            elif attempt.get("outcome"):
                lines.append(f"{target}：本次访问未获得网页响应（{attempt['outcome']}），尚不能确定原因。")
        if category == "fake_ip_proxy_compatibility" and not attempts:
            lines.append("代理兼容重试未获得有效网页响应，暂时无法确认小鸡能否访问目标。")
    if compat:
        lines.insert(0, "原来的提示来自小鸡自身的探测范围检查，不能据此说 Windows 权限被更改。已使用你现有的本地 HTTP 代理重试，结果如下。")
        lines.append("这次只调整了小鸡自身请求的访问方式，没有修改系统代理、VPN、防火墙或网站权限。受测网站可达不代表所有网站都正常。")
    elif restricted:
        lines.append("如果浏览器能正常访问，可以继续使用；小鸡暂时无法凭这次检查判断网络是否正常。")
    return "\n\n".join(dict.fromkeys(lines))
