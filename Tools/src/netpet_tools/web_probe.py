"""HTTP/HTTPS connectivity probing, captive portal evidence grading, and ICMP loss/RTT measurement."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.parse
from typing import Any, Literal

import httpx

from netpet_tools.base import (
    ErrorCode,
    ToolExecutionError,
    ValidatedUrl,
    is_target_explicitly_allowed,
    normalize_and_validate_domain,
    run_windows_command,
    sanitize_url_for_log,
    validate_http_url,
    validate_ip_for_network_probe,
    validate_safe_text,
)
from netpet_tools.config import ProbeConfig, ProbeEndpointConfig, ProxyConfig

PortalState = Literal["confirmed", "suspected", "not_detected", "unknown"]

DIRECT_ROUTE_LIMITATION = (
    "route='direct' disables explicit HTTP client proxy settings (trust_env=False), "
    "but traffic may still traverse OS-level VPNs, TUN virtual interfaces, or transparent gateway proxies."
)

ICMP_INTERPRETATION_LIMITS = (
    "ICMP Echo loss or timeout may result from ICMP filtering or rate-limiting rather than data-plane failure; "
    "small-sample gateway loss alone cannot prove link congestion or isolate the fault hop."
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_PORTAL_BODY_CLUES = ("portal", "wlanuserip", "wlanacname", "captive", "authentication", "校园网", "认证", "<form")


def validate_probe_args(
    route: Any,
    timeout_s: Any,
    probe_cfg: ProbeConfig,
) -> tuple[Literal["direct", "configured_proxy"], float]:
    """Validate route and timeout_s arguments for probe_connectivity."""
    route_str = validate_safe_text(route, "route", max_len=32)
    if route_str not in ("direct", "configured_proxy"):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported route '{route}'; must be 'direct' or 'configured_proxy'.",
        )
    route_typed: Literal["direct", "configured_proxy"] = (
        "direct" if route_str == "direct" else "configured_proxy"
    )

    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            "Parameter 'timeout_s' must be a number.",
        )
    t_val = float(timeout_s)
    if t_val < 0.2 or t_val > probe_cfg.http_total_budget_s:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter 'timeout_s' must be between 0.2 and {probe_cfg.http_total_budget_s} seconds.",
        )
    return route_typed, t_val


def resolve_configured_proxy_for_httpx(
    proxy_cfg: ProxyConfig,
    *,
    pac_url_hint: str | None = None,
) -> str:
    """Validate and return explicit HTTP/HTTPS proxy URL for route='configured_proxy', or raise UNSUPPORTED."""
    proxy_url = proxy_cfg.configured_proxy_url
    if not proxy_url:
        extra = f" (PAC AutoConfigURL '{pac_url_hint}' is present, which cannot be directly emulated)" if pac_url_hint else ""
        raise ToolExecutionError(
            ErrorCode.UNSUPPORTED,
            f"route='configured_proxy' is unavailable because no explicit configured_proxy_url is set{extra}; "
            "refusing to silently fall back to direct.",
        )
    parts = urllib.parse.urlsplit(proxy_url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ToolExecutionError(
            ErrorCode.UNSUPPORTED,
            f"Configured proxy scheme '{scheme}' is not supported without explicit HTTP/HTTPS proxy transport; "
            "refusing to fall back to direct.",
        )
    if parts.username is not None or parts.password is not None:
        raise ToolExecutionError(
            ErrorCode.UNSUPPORTED,
            "Proxy URLs containing embedded credentials are not supported.",
        )
    return proxy_url


def _check_url_host_trusted_portal(url: str | None, trusted_portal_hosts: list[str]) -> bool:
    if not url or not trusted_portal_hosts:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        if not host:
            return False
        ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
        return is_target_explicitly_allowed(host, ip_obj, trusted_portal_hosts)
    except Exception:
        return False


def _sanitize_candidate_url(base_url: str, raw_location: str | None) -> str | None:
    if not raw_location:
        return None
    joined = urllib.parse.urljoin(base_url, raw_location.strip())
    try:
        parts = urllib.parse.urlsplit(joined)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        return joined
    except ValueError:
        return None


def evaluate_portal_evidence(
    *,
    probed_url: str,
    is_known_check_endpoint: bool,
    expected_status: int | None,
    expected_body_prefix: str | None,
    expected_header_key: str | None,
    expected_header_value: str | None,
    outcome: Literal["response", "timeout", "connect_error", "tls_error"],
    status_code: int | None,
    headers: dict[str, str],
    body_snippet: str,
    trusted_portal_hosts: list[str],
) -> dict[str, Any]:
    """Grade captive-portal evidence conservatively without executing scripts or opening browsers."""
    if outcome != "response" or status_code is None:
        return {
            "portal_state": "unknown",
            "expected_signature_matched": None,
            "candidate_login_url": None,
            "login_url_trusted": False,
            "portal_clues": [f"probe_outcome={outcome}"],
        }

    headers_lower = {k.lower(): v for k, v in headers.items()}
    raw_location = headers_lower.get("location")
    candidate_url = _sanitize_candidate_url(probed_url, raw_location) if raw_location else None
    body_lower = (body_snippet or "").lower()
    body_clues = [kw for kw in _PORTAL_BODY_CLUES if kw in body_lower]

    if is_known_check_endpoint and expected_status is not None:
        status_ok = status_code == expected_status
        body_ok = True
        if expected_body_prefix is not None:
            if expected_body_prefix == "":
                body_ok = len(body_snippet.strip()) == 0
            else:
                body_ok = body_snippet.startswith(expected_body_prefix)
        hdr_ok = True
        if expected_header_key:
            actual_hdr = headers_lower.get(expected_header_key.lower())
            hdr_ok = actual_hdr is not None and (
                expected_header_value is None or actual_hdr == expected_header_value
            )

        if status_ok and body_ok and hdr_ok:
            return {
                "portal_state": "not_detected",
                "expected_signature_matched": True,
                "candidate_login_url": None,
                "login_url_trusted": False,
                "portal_clues": [],
            }

        # Known connectivity endpoint deviated from expected signature!
        effective_candidate = candidate_url or (probed_url if status_code in (200, 511) and body_clues else None)
        trusted = _check_url_host_trusted_portal(effective_candidate, trusted_portal_hosts)

        clues: list[str] = [f"expected_status={expected_status}_got={status_code}"]
        if raw_location:
            clues.append("redirect_on_known_check_endpoint")
        if body_clues:
            clues.append(f"body_keywords={','.join(body_clues[:4])}")

        if status_code == 511 and trusted:
            state: PortalState = "confirmed"
        elif (status_code in _REDIRECT_STATUSES or status_code == 200) and trusted:
            state = "confirmed"
        elif status_code in _REDIRECT_STATUSES or status_code == 511 or (status_code == 200 and not body_ok):
            state = "suspected"
        else:
            state = "unknown"

        return {
            "portal_state": state,
            "expected_signature_matched": False,
            "candidate_login_url": effective_candidate,
            "login_url_trusted": trusted,
            "portal_clues": clues,
        }

    # Custom target URL without expected content baseline:
    # 200 does NOT prove portal absence; 403 does NOT mean network disconnection; ordinary 302/login is only a clue.
    clues_custom: list[str] = ["custom_target_no_expected_signature"]
    if status_code in _REDIRECT_STATUSES and candidate_url:
        trusted_redir = _check_url_host_trusted_portal(candidate_url, trusted_portal_hosts)
        orig_host = (urllib.parse.urlsplit(probed_url).hostname or "").lower()
        redir_host = (urllib.parse.urlsplit(candidate_url).hostname or "").lower()
        if trusted_redir and redir_host != orig_host:
            clues_custom.append("redirected_to_configured_portal_host")
            return {
                "portal_state": "suspected",
                "expected_signature_matched": None,
                "candidate_login_url": candidate_url,
                "login_url_trusted": True,
                "portal_clues": clues_custom,
            }
        clues_custom.append("ordinary_http_redirect_clue_only")
        return {
            "portal_state": "unknown",
            "expected_signature_matched": None,
            "candidate_login_url": None,
            "login_url_trusted": False,
            "portal_clues": clues_custom,
        }

    if status_code == 511:
        trusted_511 = _check_url_host_trusted_portal(candidate_url, trusted_portal_hosts)
        return {
            "portal_state": "confirmed" if trusted_511 else "suspected",
            "expected_signature_matched": None,
            "candidate_login_url": candidate_url,
            "login_url_trusted": trusted_511,
            "portal_clues": ["http_511_network_authentication_required"],
        }

    if body_clues:
        clues_custom.append("page_contains_login_or_portal_terms_clue_only")

    return {
        "portal_state": "unknown",
        "expected_signature_matched": None,
        "candidate_login_url": None,
        "login_url_trusted": False,
        "portal_clues": clues_custom,
    }


def _precheck_resolved_ips_for_direct(
    validated: ValidatedUrl,
    probe_cfg: ProbeConfig,
) -> list[str]:
    """Resolve hostname and enforce SSRF IP rules before direct HTTP connection."""
    if validated.ip_literal is not None:
        return [str(validated.ip_literal)]
    port = validated.port or (443 if validated.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(validated.hostname_ascii, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    resolved: list[str] = []
    for info in infos:
        ip_str = str(info[4][0]).split("%")[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        validate_ip_for_network_probe(
            ip_obj,
            validated.hostname_ascii,
            allowed_private_targets=probe_cfg.allowed_private_targets,
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
        )
        if str(ip_obj) not in resolved:
            resolved.append(str(ip_obj))
    return resolved


def _execute_single_real_http_probe(
    validated: ValidatedUrl,
    *,
    route: Literal["direct", "configured_proxy"],
    proxy_url: str | None,
    timeout_s: float,
    probe_cfg: ProbeConfig,
    endpoint_meta: ProbeEndpointConfig | None,
) -> dict[str, Any]:
    if route == "direct":
        _precheck_resolved_ips_for_direct(validated, probe_cfg)

    timings: dict[str, float] = {}

    def _trace(event_name: str, _info: dict[str, Any]) -> None:
        now = time.monotonic()
        if event_name == "connection.connect_tcp.started":
            timings["tcp_start"] = now
        elif event_name == "connection.connect_tcp.complete" and "tcp_start" in timings:
            timings["tcp_ms"] = round((now - timings["tcp_start"]) * 1000.0, 2)
        elif event_name == "connection.start_tls.started":
            timings["tls_start"] = now
        elif event_name == "connection.start_tls.complete" and "tls_start" in timings:
            timings["tls_ms"] = round((now - timings["tls_start"]) * 1000.0, 2)

    timeout_obj = httpx.Timeout(timeout_s)
    client_kwargs: dict[str, Any] = {
        "trust_env": False,
        "verify": True,
        "follow_redirects": False,
        "timeout": timeout_obj,
    }
    if route == "configured_proxy" and proxy_url is not None:
        client_kwargs["proxy"] = proxy_url

    t_req_start = time.monotonic()
    outcome: Literal["response", "timeout", "connect_error", "tls_error"] = "response"
    status_code: int | None = None
    headers_dict: dict[str, str] = {}
    body_text = ""
    error_detail: str | None = None
    http_response_ms: float | None = None

    try:
        with httpx.Client(**client_kwargs) as client:
            req = client.build_request(
                "GET",
                validated.url,
                headers={"User-Agent": "netpet-tools/0.1"},
                extensions={"trace": _trace},
            )
            resp = client.send(req, stream=True)
            try:
                http_response_ms = round((time.monotonic() - t_req_start) * 1000.0, 2)
                status_code = int(resp.status_code)
                headers_dict = {str(k): str(v) for k, v in resp.headers.items()}
                chunks: list[bytes] = []
                total_read = 0
                for chunk in resp.iter_bytes():
                    remaining = probe_cfg.max_body_bytes - total_read
                    if remaining <= 0:
                        break
                    piece = chunk[:remaining]
                    chunks.append(piece)
                    total_read += len(piece)
                    if total_read >= probe_cfg.max_body_bytes:
                        break
                body_text = b"".join(chunks).decode("utf-8", errors="replace")
            finally:
                resp.close()
    except httpx.TimeoutException as exc:
        outcome = "timeout"
        error_detail = f"HTTP probe timed out ({type(exc).__name__})."
    except httpx.ConnectError as exc:
        msg = str(exc)
        if "SSL" in msg or "TLS" in msg or "certificate" in msg.lower():
            outcome = "tls_error"
        else:
            outcome = "connect_error"
        error_detail = f"{type(exc).__name__}: {msg}"
    except httpx.HTTPError as exc:
        outcome = "connect_error"
        error_detail = f"{type(exc).__name__}: {exc}"

    portal_eval = evaluate_portal_evidence(
        probed_url=validated.url,
        is_known_check_endpoint=endpoint_meta is not None,
        expected_status=endpoint_meta.expected_status if endpoint_meta else None,
        expected_body_prefix=endpoint_meta.expected_body_prefix if endpoint_meta else None,
        expected_header_key=endpoint_meta.expected_header_key if endpoint_meta else None,
        expected_header_value=endpoint_meta.expected_header_value if endpoint_meta else None,
        outcome=outcome,
        status_code=status_code,
        headers=headers_dict,
        body_snippet=body_text,
        trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
    )

    raw_loc = headers_dict.get("location") or headers_dict.get("Location")
    return {
        "endpoint_name": endpoint_meta.name if endpoint_meta else "custom_target",
        "probed_url": sanitize_url_for_log(validated.url) if "?" in validated.url else validated.url,
        "outcome": outcome,
        "http_status": status_code,
        "redirect_location": sanitize_url_for_log(raw_loc) if raw_loc and "?" in raw_loc else raw_loc,
        "tcp_connect_time_ms": timings.get("tcp_ms"),
        "tls_handshake_time_ms": timings.get("tls_ms"),
        "http_response_time_ms": http_response_ms,
        "error_detail": error_detail,
        **portal_eval,
    }


def _finalize_probe_payload(
    *,
    route: Literal["direct", "configured_proxy"],
    is_custom_target: bool,
    attempts: list[dict[str, Any]],
    warnings: list[str],
    data_origin: str,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    primary = attempts[-1]
    notes: list[str] = []
    if is_custom_target:
        if primary["http_status"] == 200:
            notes.append(
                "Custom target returned HTTP 200 without an expected content signature; "
                "use probe_connectivity(target_url=None) if captive portal verification is needed."
            )
        elif primary["http_status"] == 403:
            notes.append(
                "Target HTTP server responded with 403 Forbidden; TCP/TLS/HTTP transport succeeded, "
                "so 403 indicates server-side access policy rather than link disconnection."
            )

    summary_parts = [f"路径 {route} 探测结果：{primary['outcome']}"]
    if primary["http_status"] is not None:
        summary_parts.append(f"HTTP {primary['http_status']}")
    summary_parts.append(f"portal_state={primary['portal_state']}")
    summary = "，".join(summary_parts)

    data: dict[str, Any] = {
        "data_origin": data_origin,
        "route": route,
        "target_kind": "custom_url" if is_custom_target else "configured_check_endpoint",
        "outcome": primary["outcome"],
        "http_status": primary["http_status"],
        "redirect_location": primary["redirect_location"],
        "tcp_connect_time_ms": primary["tcp_connect_time_ms"],
        "tls_handshake_time_ms": primary.get("tls_handshake_time_ms"),
        "http_response_time_ms": primary["http_response_time_ms"],
        "portal_state": primary["portal_state"],
        "expected_signature_matched": primary["expected_signature_matched"],
        "candidate_login_url": primary["candidate_login_url"],
        "login_url_trusted": primary["login_url_trusted"],
        "portal_clues": primary["portal_clues"],
        "error_detail": primary["error_detail"],
        "attempts": attempts,
        "interpretation_notes": notes,
        "route_limitation": DIRECT_ROUTE_LIMITATION
        if route == "direct"
        else "route='configured_proxy' uses only the explicit configured_proxy_url with trust_env=False.",
    }
    return data, "ok", warnings, summary


def collect_real_probe_connectivity(
    probe_cfg: ProbeConfig,
    proxy_cfg: ProxyConfig,
    *,
    target_url: str | None = None,
    route: str = "direct",
    timeout_s: float = 3.0,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Perform real HTTP/HTTPS connectivity probing with explicit route and safety bounds."""
    route_typed, validated_timeout = validate_probe_args(route, timeout_s, probe_cfg)
    proxy_url = (
        resolve_configured_proxy_for_httpx(proxy_cfg)
        if route_typed == "configured_proxy"
        else None
    )

    warnings: list[str] = []
    deadline = time.monotonic() + probe_cfg.http_total_budget_s
    attempts: list[dict[str, Any]] = []

    if target_url is not None:
        validated = validate_http_url(
            target_url,
            allowed_private_targets=probe_cfg.allowed_private_targets,
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
            field_name="target_url",
        )
        eff_timeout = min(validated_timeout, max(0.2, deadline - time.monotonic()))
        res = _execute_single_real_http_probe(
            validated,
            route=route_typed,
            proxy_url=proxy_url,
            timeout_s=eff_timeout,
            probe_cfg=probe_cfg,
            endpoint_meta=None,
        )
        attempts.append(res)
        return _finalize_probe_payload(
            route=route_typed,
            is_custom_target=True,
            attempts=attempts,
            warnings=warnings,
            data_origin="real_host",
        )

    if not probe_cfg.probe_endpoints:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            "No connectivity probe_endpoints are configured and target_url was None.",
        )

    for ep in probe_cfg.probe_endpoints:
        remaining = deadline - time.monotonic()
        if remaining < 0.25:
            warnings.append("HTTP total budget reached before probing remaining fallback check endpoints.")
            break
        validated_ep = validate_http_url(
            ep.url,
            allowed_private_targets=probe_cfg.allowed_private_targets,
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
            field_name="probe_endpoint.url",
        )
        attempt = _execute_single_real_http_probe(
            validated_ep,
            route=route_typed,
            proxy_url=proxy_url,
            timeout_s=min(validated_timeout, remaining),
            probe_cfg=probe_cfg,
            endpoint_meta=ep,
        )
        attempts.append(attempt)
        # Stop immediately if connectivity succeeded or captive portal was detected
        if attempt["portal_state"] in ("not_detected", "confirmed", "suspected"):
            break

    return _finalize_probe_payload(
        route=route_typed,
        is_custom_target=False,
        attempts=attempts,
        warnings=warnings,
        data_origin="real_host",
    )


def collect_mock_probe_connectivity(
    scenario_state: dict[str, Any],
    probe_cfg: ProbeConfig,
    proxy_cfg: ProxyConfig,
    *,
    target_url: str | None = None,
    route: str = "direct",
    timeout_s: float = 3.0,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Perform deterministic mock HTTP/HTTPS connectivity probing from scenario state."""
    route_typed, _ = validate_probe_args(route, timeout_s, probe_cfg)

    hkcu_state = (scenario_state.get("proxy") or {}).get("hkcu") or {}
    pac_hint = hkcu_state.get("auto_config_url")
    if route_typed == "configured_proxy":
        resolve_configured_proxy_for_httpx(proxy_cfg, pac_url_hint=pac_hint)

    probes_sec = (scenario_state.get("http_probes") or {}).get(route_typed)
    if not isinstance(probes_sec, dict):
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario is missing http_probes section for route='{route_typed}'.",
        )

    is_custom = target_url is not None
    endpoint_meta: ProbeEndpointConfig | None = None
    if is_custom:
        validated = validate_http_url(
            target_url,
            allowed_private_targets=probe_cfg.allowed_private_targets,
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
            field_name="target_url",
        )
    else:
        if not probe_cfg.probe_endpoints:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                "No connectivity probe_endpoints are configured and target_url was None.",
            )
        endpoint_meta = probe_cfg.probe_endpoints[0]
        validated = validate_http_url(
            endpoint_meta.url,
            allowed_private_targets=probe_cfg.allowed_private_targets,
            trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
            field_name="probe_endpoint.url",
        )

    entry = probes_sec.get(validated.url)
    if entry is None:
        # Also try stripping trailing slash if exact URL not found
        entry = probes_sec.get(validated.url.rstrip("/"))
    if entry is None:
        entry = probes_sec.get("*")
    if entry is None:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario lacks mock HTTP observation for '{validated.url}' on route='{route_typed}'.",
        )

    outcome: Literal["response", "timeout", "connect_error", "tls_error"] = entry.get("outcome", "response")
    status_code: int | None = entry.get("status_code") if outcome == "response" else None
    headers_dict: dict[str, str] = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
    body_text = str(entry.get("body") or "")[: probe_cfg.max_body_bytes]

    portal_eval = evaluate_portal_evidence(
        probed_url=validated.url,
        is_known_check_endpoint=endpoint_meta is not None,
        expected_status=endpoint_meta.expected_status if endpoint_meta else None,
        expected_body_prefix=endpoint_meta.expected_body_prefix if endpoint_meta else None,
        expected_header_key=endpoint_meta.expected_header_key if endpoint_meta else None,
        expected_header_value=endpoint_meta.expected_header_value if endpoint_meta else None,
        outcome=outcome,
        status_code=status_code,
        headers=headers_dict,
        body_snippet=body_text,
        trusted_portal_hosts=probe_cfg.trusted_portal_hosts,
    )

    raw_loc = headers_dict.get("location") or headers_dict.get("Location")
    attempt = {
        "endpoint_name": endpoint_meta.name if endpoint_meta else "custom_target",
        "probed_url": validated.url,
        "outcome": outcome,
        "http_status": status_code,
        "redirect_location": raw_loc,
        "tcp_connect_time_ms": entry.get("tcp_connect_time_ms"),
        "tls_handshake_time_ms": entry.get("tls_handshake_time_ms"),
        "http_response_time_ms": entry.get("http_response_time_ms"),
        "error_detail": entry.get("error_detail"),
        **portal_eval,
    }

    return _finalize_probe_payload(
        route=route_typed,
        is_custom_target=is_custom,
        attempts=[attempt],
        warnings=[],
        data_origin=str(scenario_state.get("data_origin") or "synthetic"),
    )


# ---------------------------------------------------------------------------
# ICMP Latency & Loss (measure_latency_and_loss)
# ---------------------------------------------------------------------------


def validate_icmp_inputs(host: Any, count: Any) -> tuple[str, int]:
    """Validate host and packet count (1..10) for ICMP echo probing."""
    if isinstance(count, bool) or not isinstance(count, int) or not (1 <= count <= 10):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter 'count' must be an integer between 1 and 10 (got {count!r}).",
        )

    host_str = validate_safe_text(host, "host", max_len=253)
    if host_str.startswith("-") or host_str.startswith("/") or any(ch.isspace() for ch in host_str):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Invalid host '{host_str}': option flags and whitespace are prohibited.",
        )
    if "://" in host_str or "/" in host_str or "@" in host_str:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Invalid host '{host_str}': pass a hostname or IP address, not a URL.",
        )
    if host_str.lower() == "localhost" or host_str.lower().endswith(".localhost"):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            "Loopback target 'localhost' is not permitted for measure_latency_and_loss.",
        )

    try:
        ip_obj = ipaddress.ip_address(host_str)
        if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"Address '{ip_obj}' (loopback/link-local/multicast/unspecified) is not permitted for ICMP probing.",
            )
        return str(ip_obj), count
    except ValueError:
        ascii_domain, _ = normalize_and_validate_domain(host_str)
        return ascii_domain, count


def compute_icmp_metrics(
    host: str,
    sent: int,
    rtt_samples_ms: list[float],
    *,
    sub_ms_count: int = 0,
    data_origin: str = "real_host",
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Compute exact packet loss and ICMP RTT statistics from observed samples."""
    received = len(rtt_samples_ms)
    lost = max(0, sent - received)
    loss_rate_pct = round((lost / sent) * 100.0, 2) if sent > 0 else 0.0

    if received > 0:
        rtt_min = round(min(rtt_samples_ms), 2)
        rtt_max = round(max(rtt_samples_ms), 2)
        rtt_avg = round(sum(rtt_samples_ms) / received, 2)
    else:
        rtt_min = None
        rtt_max = None
        rtt_avg = None

    data: dict[str, Any] = {
        "data_origin": data_origin,
        "host": host,
        "sent": sent,
        "received": received,
        "lost": lost,
        "loss_rate_pct": loss_rate_pct,
        "icmp_rtt_samples_ms": [round(x, 2) for x in rtt_samples_ms],
        "icmp_rtt_min_ms": rtt_min,
        "icmp_rtt_avg_ms": rtt_avg,
        "icmp_rtt_max_ms": rtt_max,
        "sub_ms_samples_count": sub_ms_count,
        "interpretation_limits": ICMP_INTERPRETATION_LIMITS,
    }

    if received > 0:
        summary = (
            f"ICMP 探测 {host}：发送 {sent}，接收 {received}，丢失 {lost} "
            f"（丢包率 {loss_rate_pct}%，平均 ICMP RTT {rtt_avg} ms）"
        )
    else:
        summary = f"ICMP 探测 {host}：发送 {sent}，接收 0，丢失 {sent}（100.0% 无回应）"

    return data, "ok", [], summary


def parse_windows_ping_output(stdout: str, requested_count: int) -> tuple[int, list[float], int]:
    """Parse Chinese and English Windows `ping.exe` output into (sent, valid_rtt_samples_ms, sub_ms_count)."""
    samples: list[float] = []
    sub_ms_count = 0

    # Matches English `time=12ms`, `time<1ms` and Chinese `时间=12ms`, `时间<1ms`, `时间=12毫秒`
    rtt_re = re.compile(r"(?:time|时间)\s*([=<])\s*(\d+(?:\.\d+)?)\s*(?:ms|毫秒)", re.IGNORECASE)
    for raw_line in (stdout or "").splitlines():
        m = rtt_re.search(raw_line)
        if m:
            op = m.group(1)
            val = float(m.group(2))
            if op == "<" and val <= 1.0:
                sub_ms_count += 1
                samples.append(0.5)
            else:
                samples.append(val)

    sent = requested_count
    sent_re = re.compile(r"(?:Sent|已发送)\s*=\s*(\d+)", re.IGNORECASE)
    m_sent = sent_re.search(stdout or "")
    if m_sent:
        parsed_sent = int(m_sent.group(1))
        if parsed_sent > 0:
            sent = parsed_sent

    if len(samples) > sent:
        samples = samples[:sent]

    return sent, samples, sub_ms_count


def collect_real_icmp(
    probe_cfg: ProbeConfig,
    *,
    host: str,
    count: int = 4,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Run real Windows `ping.exe` safely and compute ICMP RTT and loss."""
    validated_host, validated_count = validate_icmp_inputs(host, count)

    # Check DNS-resolved IP safety against loopback/link-local before invoking ping.exe
    try:
        ipaddress.ip_address(validated_host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(validated_host, None)
            for info in infos:
                ip_obj = ipaddress.ip_address(str(info[4][0]).split("%")[0])
                if ip_obj.is_loopback or ip_obj.is_link_local:
                    raise ToolExecutionError(
                        ErrorCode.INVALID_INPUT,
                        f"Host '{validated_host}' resolves to prohibited loopback/link-local address '{ip_obj}'.",
                    )
        except socket.gaierror:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"Host '{validated_host}' could not be resolved by DNS prior to ICMP probing.",
                retryable=True,
            )

    cmd = [
        "ping.exe",
        "-n",
        str(validated_count),
        "-w",
        str(probe_cfg.ping_per_packet_timeout_ms),
        validated_host,
    ]
    budget = min(
        probe_cfg.ping_total_budget_s,
        max(3.0, validated_count * (probe_cfg.ping_per_packet_timeout_ms / 1000.0) + 2.5),
    )
    out = run_windows_command(cmd, timeout_s=budget)
    if out.timed_out:
        raise ToolExecutionError(
            ErrorCode.TIMEOUT,
            f"ping.exe exceeded total budget ({round(budget, 1)}s) for host '{validated_host}'.",
            retryable=True,
        )

    sent, samples, sub_ms = parse_windows_ping_output(out.stdout, validated_count)
    return compute_icmp_metrics(
        validated_host,
        sent,
        samples,
        sub_ms_count=sub_ms,
        data_origin="real_host",
    )


def collect_mock_icmp(
    scenario_state: dict[str, Any],
    *,
    host: str,
    count: int = 4,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Compute deterministic mock ICMP RTT and loss from scenario packet sequence."""
    validated_host, validated_count = validate_icmp_inputs(host, count)
    icmp_sec = scenario_state.get("icmp")
    if not isinstance(icmp_sec, dict):
        raise ToolExecutionError(ErrorCode.INVALID_SCENARIO, "Scenario is missing 'icmp' section.")

    pattern = icmp_sec.get(validated_host)
    if pattern is None:
        pattern = icmp_sec.get("*")
    if not isinstance(pattern, list) or not pattern:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario lacks mock ICMP sample sequence for '{validated_host}'.",
        )

    selected_slots: list[float | None] = [
        pattern[i % len(pattern)] for i in range(validated_count)
    ]
    valid_samples = [float(x) for x in selected_slots if x is not None]
    return compute_icmp_metrics(
        validated_host,
        validated_count,
        valid_samples,
        sub_ms_count=0,
        data_origin=str(scenario_state.get("data_origin") or "synthetic"),
    )
