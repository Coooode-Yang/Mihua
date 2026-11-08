"""DNS diagnostics with dnspython, multi-resolver comparison, and strict non-overclaiming notes."""

from __future__ import annotations

import ipaddress
import time
from typing import Any, Literal

import dns.exception
import dns.rcode
import dns.resolver

from netpet_tools.base import (
    ErrorCode,
    ToolExecutionError,
    normalize_and_validate_domain,
    validate_safe_text,
)
from netpet_tools.config import DnsConfig

DnsStatus = Literal[
    "NOERROR",
    "NOERROR_NODATA",
    "NXDOMAIN",
    "SERVFAIL",
    "REFUSED",
    "TIMEOUT",
    "UNREACHABLE",
    "ERROR",
]

ROUTING_LIMITATION_NOTE = (
    "DNS queries use OS UDP/TCP port 53 sockets and remain subject to system routing tables, "
    "active VPN/TUN adapters, or transparent gateway DNS interception."
)


def validate_dns_inputs(
    domain: Any,
    dns_server: Any = None,
    record_type: Any = "A",
    compare_public: Any = False,
) -> tuple[str, str, str | None, Literal["A", "AAAA"], bool]:
    """Validate and normalize all query_dns arguments."""
    ascii_domain, display_domain = normalize_and_validate_domain(domain)

    rec_str = validate_safe_text(record_type, "record_type", max_len=10).upper()
    if rec_str not in ("A", "AAAA"):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported record_type '{record_type}'; only 'A' and 'AAAA' are supported in v1.",
        )
    rec_typed: Literal["A", "AAAA"] = "A" if rec_str == "A" else "AAAA"

    validated_server: str | None = None
    if dns_server is not None:
        srv_text = validate_safe_text(dns_server, "dns_server", max_len=64)
        try:
            validated_server = str(ipaddress.ip_address(srv_text))
        except ValueError as exc:
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"Invalid dns_server '{dns_server}'; provide a valid IPv4 or IPv6 literal without port.",
            ) from exc

    if not isinstance(compare_public, bool):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            "Parameter 'compare_public' must be a boolean.",
        )

    return ascii_domain, display_domain, validated_server, rec_typed, compare_public


def classify_no_nameservers_error(exc: dns.resolver.NoNameservers) -> tuple[DnsStatus, str]:
    """Distinguish REFUSED, SERVFAIL, TIMEOUT, and UNREACHABLE inside dnspython NoNameservers."""
    kw = getattr(exc, "kwargs", None)
    errors = (kw.get("errors") if isinstance(kw, dict) else None) or getattr(exc, "errors", None) or []
    saw_refused = False
    saw_servfail = False
    saw_timeout = False
    saw_unreachable = False

    for entry in errors:
        # dnspython error tuple format: (server, tcp, port, err, response)
        err_obj = entry[3] if len(entry) > 3 else None
        resp_obj = entry[4] if len(entry) > 4 else None
        if resp_obj is not None and hasattr(resp_obj, "rcode"):
            rc = resp_obj.rcode()
            if rc == dns.rcode.REFUSED:
                saw_refused = True
            elif rc == dns.rcode.SERVFAIL:
                saw_servfail = True
        if isinstance(err_obj, dns.exception.Timeout):
            saw_timeout = True
        elif isinstance(err_obj, OSError):
            saw_unreachable = True
        elif isinstance(err_obj, str):
            low = err_obj.lower()
            if "refused" in low:
                saw_refused = True
            elif "servfail" in low:
                saw_servfail = True

    if saw_refused:
        return "REFUSED", "Resolver refused the DNS query (RCODE=REFUSED)."
    if saw_servfail:
        return "SERVFAIL", "Resolver returned server failure (RCODE=SERVFAIL)."
    if saw_timeout:
        return "TIMEOUT", "Resolver timed out."
    if saw_unreachable:
        return "UNREACHABLE", "Network or socket unreachable when contacting resolver."
    return "SERVFAIL", str(exc)


def query_single_real_resolver(
    ascii_domain: str,
    record_type: Literal["A", "AAAA"],
    *,
    resolver_label: Literal["system", "specified", "public"],
    nameservers: list[str] | None,
    timeout_s: float,
) -> dict[str, Any]:
    """Execute a single DNS query via dnspython and return structured observation."""
    t0 = time.monotonic()
    try:
        if nameservers is None:
            resolver = dns.resolver.Resolver(configure=True)
            effective_ns = [str(ns) for ns in resolver.nameservers if isinstance(ns, str)]
        else:
            resolver = dns.resolver.Resolver(configure=False)
            resolver.nameservers = list(nameservers)
            effective_ns = list(nameservers)
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": list(nameservers or []),
            "status": "UNREACHABLE",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": f"System resolver configuration unavailable: {exc}",
        }

    if not effective_ns:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": [],
            "status": "UNREACHABLE",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": "No nameservers configured on system.",
        }

    per_try = max(0.2, min(timeout_s, 2.0))
    resolver.timeout = per_try
    resolver.lifetime = max(0.2, timeout_s)

    try:
        answer = resolver.resolve(ascii_domain, record_type, raise_on_no_answer=True)
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        records = [rdata.to_text() for rdata in answer]
        ttl = int(answer.rrset.ttl) if answer.rrset is not None else None
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "NOERROR",
            "records": records,
            "ttl": ttl,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": None,
        }
    except dns.resolver.NXDOMAIN:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "NXDOMAIN",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": "Domain name does not exist on queried resolver (NXDOMAIN).",
        }
    except dns.resolver.NoAnswer:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "NOERROR_NODATA",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": f"Domain exists (NOERROR) but has no {record_type} records.",
        }
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "TIMEOUT",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": f"DNS query exceeded {round(timeout_s, 2)}s lifetime.",
        }
    except dns.resolver.NoNameservers as exc:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        status_code, detail = classify_no_nameservers_error(exc)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": status_code,
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": detail,
        }
    except OSError as exc:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "UNREACHABLE",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": f"Socket error: {exc}",
        }
    except dns.exception.DNSException as exc:
        elapsed = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            "resolver_label": resolver_label,
            "nameservers": effective_ns,
            "status": "ERROR",
            "records": [],
            "ttl": None,
            "elapsed_ms": elapsed,
            "transport": "udp_53_with_tcp_fallback",
            "detail": f"DNS protocol error: {type(exc).__name__}",
        }


def build_dns_comparison(queries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compare primary vs secondary/public DNS observations without overclaiming root cause."""
    if len(queries) < 2:
        return None

    primary = queries[0]
    others = queries[1:]
    p_status = primary["status"]
    other_statuses = [q["status"] for q in others]

    all_noerror = p_status == "NOERROR" and all(s == "NOERROR" for s in other_statuses)
    status_differ = any(s != p_status for s in other_statuses)

    records_differ: bool | None = None
    noerror_sets = [frozenset(q["records"]) for q in queries if q["status"] == "NOERROR"]
    if len(noerror_sets) >= 2:
        first_set = noerror_sets[0]
        records_differ = any(s != first_set for s in noerror_sets[1:])

    notes: list[str] = []
    if records_differ:
        notes.append(
            "Resolved addresses differ across resolvers; differences commonly arise from CDN geo/anycast routing, "
            "split-horizon campus DNS, or TTL caching, and do not alone prove DNS hijacking."
        )

    failure_statuses = {"TIMEOUT", "SERVFAIL", "REFUSED", "UNREACHABLE", "ERROR"}
    if p_status in failure_statuses and any(s == "NOERROR" for s in other_statuses):
        notes.append(
            "Primary resolver query failed while at least one public resolver succeeded; the primary/system resolver "
            "path is worth checking (this alone does not prove the campus DNS server is down)."
        )
    elif p_status == "NOERROR" and all(s == "NXDOMAIN" for s in other_statuses):
        notes.append(
            "Domain resolved on primary resolver but returned NXDOMAIN on public resolvers; this is normal for "
            "campus intranet-only domains under split-horizon DNS."
        )
    elif p_status == "NOERROR" and all(s in ("TIMEOUT", "UNREACHABLE", "REFUSED") for s in other_statuses):
        notes.append(
            "Primary resolver succeeded while public resolvers were unreachable or timed out; campus networks often "
            "restrict external port 53 without indicating whole-machine disconnection."
        )

    return {
        "all_noerror": all_noerror,
        "status_differ": status_differ,
        "records_differ": records_differ,
        "observation_notes": notes,
    }


def finalize_dns_payload(
    *,
    display_domain: str,
    ascii_domain: str,
    record_type: Literal["A", "AAAA"],
    compare_public: bool,
    queries: list[dict[str, Any]],
    warnings: list[str],
    partial: bool,
    data_origin: str,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Assemble final DNS data dictionary and concise factual summary."""
    comparison = build_dns_comparison(queries)
    primary = queries[0]
    status: Literal["ok", "partial"] = "partial" if partial else "ok"

    summary = (
        f"域名 '{display_domain}' ({record_type}) 主查询状态为 {primary['status']}"
        + (f"，返回 {len(primary['records'])} 条记录" if primary["records"] else "")
    )
    if compare_public and len(queries) > 1:
        pub_summary = ", ".join(f"{q['nameservers'][0] if q['nameservers'] else 'public'}={q['status']}" for q in queries[1:])
        summary += f"；公共解析对比：{pub_summary}"

    data = {
        "data_origin": data_origin,
        "domain": display_domain,
        "ascii_domain": ascii_domain,
        "record_type": record_type,
        "compare_public": compare_public,
        "primary_result": primary,
        "queries": queries,
        "comparison": comparison,
        "routing_limitation": ROUTING_LIMITATION_NOTE,
    }
    return data, status, warnings, summary


def collect_real_dns(
    config: DnsConfig,
    *,
    domain: str,
    dns_server: str | None = None,
    record_type: str = "A",
    compare_public: bool = False,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Execute real DNS lookup(s) within configured timeout and total budget."""
    ascii_domain, display_domain, validated_server, rec_type, cmp_pub = validate_dns_inputs(
        domain, dns_server, record_type, compare_public
    )

    warnings: list[str] = []
    partial = False
    deadline = time.monotonic() + config.total_budget_s

    primary_label: Literal["system", "specified"] = "specified" if validated_server else "system"
    primary_ns = [validated_server] if validated_server else None
    first_timeout = min(config.timeout_s, max(0.2, deadline - time.monotonic()))

    queries: list[dict[str, Any]] = [
        query_single_real_resolver(
            ascii_domain,
            rec_type,
            resolver_label=primary_label,
            nameservers=primary_ns,
            timeout_s=first_timeout,
        )
    ]

    if cmp_pub:
        for pub_ip in config.public_resolvers:
            if validated_server and pub_ip == validated_server:
                continue
            remaining = deadline - time.monotonic()
            if remaining < 0.25:
                partial = True
                warnings.append(
                    f"Skipped remaining public resolver '{pub_ip}' because total DNS budget ({config.total_budget_s}s) was exhausted."
                )
                break
            q_res = query_single_real_resolver(
                ascii_domain,
                rec_type,
                resolver_label="public",
                nameservers=[pub_ip],
                timeout_s=min(config.timeout_s, remaining),
            )
            queries.append(q_res)

    return finalize_dns_payload(
        display_domain=display_domain,
        ascii_domain=ascii_domain,
        record_type=rec_type,
        compare_public=cmp_pub,
        queries=queries,
        warnings=warnings,
        partial=partial,
        data_origin="real_host",
    )


def _lookup_mock_resolver_entry(
    resolver_map: dict[str, Any],
    ascii_domain: str,
    record_type: Literal["A", "AAAA"],
    nameservers: list[str],
    resolver_label: Literal["system", "specified", "public"],
) -> dict[str, Any]:
    exact_key = f"{ascii_domain}:{record_type}"
    entry = resolver_map.get(exact_key)
    if entry is None:
        # If domain exists for opposite record type (e.g. A exists, AAAA queried) and no wildcard overrides:
        other_type = "AAAA" if record_type == "A" else "A"
        other_entry = resolver_map.get(f"{ascii_domain}:{other_type}")
        if other_entry is not None and other_entry.get("status") == "NOERROR":
            return {
                "resolver_label": resolver_label,
                "nameservers": list(nameservers),
                "status": "NOERROR_NODATA",
                "records": [],
                "ttl": None,
                "elapsed_ms": float(other_entry.get("elapsed_ms", 8.0)),
                "transport": "udp_53_with_tcp_fallback",
                "detail": f"Domain exists (NOERROR) but has no {record_type} records in scenario.",
            }
        entry = resolver_map.get("*")

    if entry is None:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Mock DNS section lacks response mapping for '{exact_key}' on resolver '{resolver_label}'.",
        )

    status_val: DnsStatus = entry.get("status", "NXDOMAIN")
    return {
        "resolver_label": resolver_label,
        "nameservers": list(nameservers),
        "status": status_val,
        "records": list(entry.get("records") or []),
        "ttl": entry.get("ttl"),
        "elapsed_ms": float(entry.get("elapsed_ms", 10.0)),
        "transport": "udp_53_with_tcp_fallback",
        "detail": entry.get("detail"),
    }


def collect_mock_dns(
    scenario_state: dict[str, Any],
    config: DnsConfig,
    *,
    domain: str,
    dns_server: str | None = None,
    record_type: str = "A",
    compare_public: bool = False,
) -> tuple[dict[str, Any], Literal["ok", "partial"], list[str], str]:
    """Execute deterministic mock DNS lookup(s) from scenario state."""
    ascii_domain, display_domain, validated_server, rec_type, cmp_pub = validate_dns_inputs(
        domain, dns_server, record_type, compare_public
    )

    dns_sec = scenario_state.get("dns") or {}
    responses_all = dns_sec.get("responses") or {}
    sys_ns = list(dns_sec.get("system_nameservers") or [])
    warnings: list[str] = []

    queries: list[dict[str, Any]] = []
    if validated_server is None:
        sys_map = responses_all.get("system")
        if not isinstance(sys_map, dict):
            raise ToolExecutionError(ErrorCode.INVALID_SCENARIO, "Scenario is missing 'dns.responses.system'.")
        queries.append(
            _lookup_mock_resolver_entry(sys_map, ascii_domain, rec_type, sys_ns, "system")
        )
    else:
        srv_map = responses_all.get(validated_server)
        if srv_map is None and validated_server in sys_ns:
            srv_map = responses_all.get("system")
        if srv_map is None:
            # Unconfigured custom server in mock: fall back to wildcard if present or report TIMEOUT observation
            default_map = responses_all.get("*")
            if isinstance(default_map, dict):
                srv_map = default_map
            else:
                queries.append(
                    {
                        "resolver_label": "specified",
                        "nameservers": [validated_server],
                        "status": "TIMEOUT",
                        "records": [],
                        "ttl": None,
                        "elapsed_ms": round(config.timeout_s * 1000.0, 1),
                        "transport": "udp_53_with_tcp_fallback",
                        "detail": f"No mock DNS response configured for server {validated_server}; simulated timeout.",
                    }
                )
        if isinstance(srv_map, dict):
            queries.append(
                _lookup_mock_resolver_entry(srv_map, ascii_domain, rec_type, [validated_server], "specified")
            )

    if cmp_pub:
        for pub_ip in config.public_resolvers:
            if validated_server and pub_ip == validated_server:
                continue
            pub_map = responses_all.get(pub_ip)
            if isinstance(pub_map, dict):
                queries.append(
                    _lookup_mock_resolver_entry(pub_map, ascii_domain, rec_type, [pub_ip], "public")
                )
            else:
                warnings.append(f"Public resolver {pub_ip} had no explicit mock table; skipped.")

    return finalize_dns_payload(
        display_domain=display_domain,
        ascii_domain=ascii_domain,
        record_type=rec_type,
        compare_public=cmp_pub,
        queries=queries,
        warnings=warnings,
        partial=False,
        data_origin=str(scenario_state.get("data_origin") or "synthetic"),
    )
