"""Core protocol models, validation helpers, audit logging, and safe command execution."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import logging
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("netpet_tools")
logger.addHandler(logging.NullHandler())

SCHEMA_VERSION: Literal["1.0"] = "1.0"

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE_PARAM_KEYS = frozenset(
    {
        "approval_token",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "proxy_auth",
        "ticket",
        "session",
    }
)


class ErrorCode(str, Enum):
    """Stable v1 error code enumeration."""

    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    LOCATION_PERMISSION_REQUIRED = "LOCATION_PERMISSION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INVALID_SCENARIO = "INVALID_SCENARIO"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ActionApprovalError(ValueError):
    """Raised when host-side approve_action cannot issue a token for a plan."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolExecutionError(Exception):
    """Structured exception converted to ToolResult(status='error') at service boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.data = data or {}
        self.warnings = warnings or []


class ToolError(BaseModel):
    """Structured error payload for ToolResult."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    """Unified v1 return envelope for all netpet_tools public tool methods."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    tool: str
    success: bool
    status: Literal["ok", "partial", "error"]
    timestamp: str
    duration_ms: float = Field(ge=0.0)
    mode: Literal["mock", "real"]
    scenario_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> ToolResult:
        if self.status == "error":
            if self.success:
                raise ValueError("ToolResult with status='error' must have success=False")
            if self.error is None:
                raise ValueError("ToolResult with status='error' must include error details")
        else:
            if not self.success:
                raise ValueError(f"ToolResult with status='{self.status}' must have success=True")
            if self.error is not None:
                raise ValueError(f"ToolResult with status='{self.status}' must have error=None")
        return self

    def to_dict(self) -> dict[str, Any]:
        """Return a pure JSON-serializable Python dict."""
        sanitized_data = to_json_safe(self.data)
        copy_obj = self.model_copy(update={"data": sanitized_data})
        dumped = copy_obj.model_dump(mode="json")
        # Guarantee strict JSON compatibility with no custom object leakage
        json.dumps(dumped, ensure_ascii=False)
        return dumped


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def to_json_safe(value: Any) -> Any:
    """Recursively convert values into plain JSON-compatible primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return to_json_safe(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in value]
    return str(value)


def build_ok_result(
    *,
    tool: str,
    mode: Literal["mock", "real"],
    scenario_id: str | None,
    started_monotonic: float,
    data: dict[str, Any],
    summary: str,
    status: Literal["ok", "partial"] = "ok",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build and serialize a successful ('ok' or 'partial') ToolResult."""
    elapsed_ms = round(max(0.0, (time.monotonic() - started_monotonic) * 1000.0), 2)
    result = ToolResult(
        tool=tool,
        success=True,
        status=status,
        timestamp=utc_now_iso(),
        duration_ms=elapsed_ms,
        mode=mode,
        scenario_id=scenario_id if mode == "mock" else None,
        data=to_json_safe(data),
        summary=summary,
        warnings=list(warnings or []),
        error=None,
    )
    return result.to_dict()


def build_error_result(
    *,
    tool: str,
    mode: Literal["mock", "real"],
    scenario_id: str | None,
    started_monotonic: float,
    code: ErrorCode,
    message: str,
    retryable: bool = False,
    data: dict[str, Any] | None = None,
    summary: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build and serialize an error ToolResult."""
    elapsed_ms = round(max(0.0, (time.monotonic() - started_monotonic) * 1000.0), 2)
    result = ToolResult(
        tool=tool,
        success=False,
        status="error",
        timestamp=utc_now_iso(),
        duration_ms=elapsed_ms,
        mode=mode,
        scenario_id=scenario_id if mode == "mock" else None,
        data=to_json_safe(data or {}),
        summary=summary or message,
        warnings=list(warnings or []),
        error=ToolError(code=code, message=message, retryable=retryable),
    )
    return result.to_dict()


def sanitize_url_for_log(raw_url: str) -> str:
    """Strip credentials and redact query parameter values from a URL for safe logging."""
    if not isinstance(raw_url, str) or not raw_url:
        return ""
    try:
        parts = urllib.parse.urlsplit(raw_url)
    except ValueError:
        return "<invalid_url>"
    if not parts.scheme or not parts.netloc:
        return raw_url[:120]
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    query = ""
    if parts.query:
        q_items = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        redacted_pairs = [f"{urllib.parse.quote_plus(k)}=<redacted>" for k, _ in q_items]
        query = "&".join(redacted_pairs)
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_proxy_string(proxy_val: str | None) -> str | None:
    """Mask any user:password credentials embedded in a proxy server string."""
    if proxy_val is None:
        return None
    text = str(proxy_val).strip()
    if not text:
        return text
    if "@" not in text:
        return text
    try:
        if "://" in text:
            parts = urllib.parse.urlsplit(text)
            if parts.username is not None or parts.password is not None:
                host = parts.hostname or ""
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                netloc = f"<redacted>@{host}:{parts.port}" if parts.port else f"<redacted>@{host}"
                return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        pass
    # Fallback regex redaction for user:pass@host:port
    return re.sub(r"([^/:\s]+):([^@\s]+)@", "<redacted>@", text)


def summarize_params_for_log(params: dict[str, Any]) -> dict[str, Any]:
    """Produce a redacted parameter summary suitable for audit logs."""
    sanitized: dict[str, Any] = {}
    for key, val in params.items():
        key_lower = key.lower()
        if key_lower in _SENSITIVE_PARAM_KEYS or "token" in key_lower or "secret" in key_lower or "password" in key_lower:
            sanitized[key] = "<redacted>"
        elif isinstance(val, dict):
            sanitized[key] = summarize_params_for_log(val)
        elif isinstance(val, str) and ("http://" in val.lower() or "https://" in val.lower()):
            sanitized[key] = sanitize_url_for_log(val)
        else:
            sanitized[key] = to_json_safe(val)
    return sanitized


class AuditLogger:
    """Thread-safe sanitized tool invocation logger with optional JSONL sink."""

    def __init__(self, jsonl_path: str | None = None, max_memory_records: int = 200) -> None:
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._max_records = max_memory_records
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []

    def record(
        self,
        *,
        tool: str,
        mode: str,
        scenario_id: str | None,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        entry = {
            "call_id": uuid.uuid4().hex[:16],
            "timestamp": result.get("timestamp") or utc_now_iso(),
            "tool": tool,
            "mode": mode,
            "scenario_id": scenario_id if mode == "mock" else None,
            "params": summarize_params_for_log(params),
            "status": result.get("status"),
            "success": result.get("success"),
            "duration_ms": result.get("duration_ms"),
            "error_code": (result.get("error") or {}).get("code") if result.get("error") else None,
        }
        line = json.dumps(entry, ensure_ascii=False)
        logger.info("netpet_tool_call %s", line)
        with self._lock:
            self._records.append(entry)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records :]
            if self._jsonl_path is not None:
                try:
                    self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._jsonl_path.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                except OSError as exc:
                    logger.warning("Failed to write audit JSONL log: %s", exc)
        return entry

    def get_recent_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._records]


def validate_safe_text(value: Any, field_name: str, *, max_len: int = 128, allow_empty: bool = False) -> str:
    """Validate an identifier/argument string, rejecting control characters and type mismatches."""
    if not isinstance(value, str):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter '{field_name}' must be a string.",
        )
    if _CONTROL_CHAR_RE.search(value):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter '{field_name}' contains invalid control characters.",
        )
    stripped = value.strip()
    if not allow_empty and not stripped:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter '{field_name}' must not be empty.",
        )
    if len(stripped) > max_len:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter '{field_name}' exceeds maximum length ({max_len}).",
        )
    return stripped


def normalize_and_validate_domain(raw_domain: Any) -> tuple[str, str]:
    """Validate a DNS domain name (including IDNA) and return (ascii_domain, display_domain)."""
    text = validate_safe_text(raw_domain, "domain", max_len=255)
    if any(ch in text for ch in (" ", "\t", "/", "\\", "?", "#", "@", ":")):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Invalid domain '{text}': pass a bare hostname/domain, not a URL or host:port.",
        )
    normalized_display = text.rstrip(".")
    if not normalized_display:
        raise ToolExecutionError(ErrorCode.INVALID_INPUT, "Domain must not be empty or root dot only.")

    # Reject raw IPv4 / IPv6 literals when a DNS domain name is required
    try:
        ipaddress.ip_address(normalized_display)
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"'{normalized_display}' is an IP literal; query_dns expects a domain name.",
        )
    except ValueError:
        pass

    try:
        ascii_domain = normalized_display.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Invalid IDNA domain '{normalized_display}': {exc}",
        ) from exc

    if len(ascii_domain) > 253:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Domain '{normalized_display}' exceeds 253 ASCII characters.",
        )
    labels = ascii_domain.split(".")
    if len(labels) < 2:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Domain '{normalized_display}' must be a multi-label domain name (e.g. 'example.com').",
        )
    for label in labels:
        if not (1 <= len(label) <= 63) or not _LABEL_RE.match(label):
            raise ToolExecutionError(
                ErrorCode.INVALID_INPUT,
                f"Invalid domain label '{label}' in '{normalized_display}'.",
            )
    if labels[-1].isdigit():
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Invalid numeric top-level label in domain '{normalized_display}'.",
        )
    return ascii_domain, normalized_display.lower()


def domain_matches_pattern(hostname_ascii: str, pattern: str, match_type: Literal["domain_exact", "domain_suffix"]) -> bool:
    """Match normalized ASCII hostname against exact or label-bounded suffix pattern."""
    host = hostname_ascii.rstrip(".").lower()
    pat_raw = pattern.strip().lstrip(".").rstrip(".")
    if not pat_raw:
        return False
    try:
        pat = pat_raw.encode("idna").decode("ascii").lower()
    except UnicodeError:
        pat = pat_raw.lower()
    if match_type == "domain_exact":
        return host == pat
    if match_type == "domain_suffix":
        return host == pat or host.endswith("." + pat)
    return False


def is_target_explicitly_allowed(
    hostname_ascii: str,
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
    allowed_patterns: list[str],
) -> bool:
    """Check whether a private host or IP is explicitly listed in allowed patterns (CIDR, IP, or domain)."""
    host = hostname_ascii.rstrip(".").lower()
    for raw_item in allowed_patterns:
        item = raw_item.strip()
        if not item:
            continue
        # CIDR network check
        if "/" in item:
            if ip_obj is not None:
                try:
                    net = ipaddress.ip_network(item, strict=False)
                    if ip_obj in net:
                        return True
                except ValueError:
                    pass
            continue
        # Exact IP check
        try:
            allowed_ip = ipaddress.ip_address(item)
            if ip_obj is not None and ip_obj == allowed_ip:
                return True
            if host == str(allowed_ip).lower():
                return True
            continue
        except ValueError:
            pass
        # Domain exact or suffix check (leading dot means suffix, otherwise exact or subdomain)
        if item.startswith("."):
            if domain_matches_pattern(host, item[1:], "domain_suffix"):
                return True
        else:
            if domain_matches_pattern(host, item, "domain_suffix"):
                return True
    return False


def validate_ip_for_network_probe(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
    hostname_ascii: str,
    *,
    allowed_private_targets: list[str],
    trusted_portal_hosts: list[str],
) -> None:
    """Enforce SSRF/safety rules on a target or resolved IP address."""
    if ip_obj.is_loopback:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Loopback address '{ip_obj}' is not permitted for network probing.",
        )
    if ip_obj.is_link_local:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Link-local/metadata address '{ip_obj}' is not permitted for network probing.",
        )
    if ip_obj.is_unspecified or ip_obj.is_multicast or ip_obj.is_reserved:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Reserved/multicast/unspecified address '{ip_obj}' is not permitted.",
        )
    if ip_obj.is_private:
        combined_allowed = list(allowed_private_targets) + list(trusted_portal_hosts)
        if not is_target_explicitly_allowed(hostname_ascii, ip_obj, combined_allowed):
            raise ToolExecutionError(
                ErrorCode.PERMISSION_DENIED,
                f"Private network target '{hostname_ascii}' ({ip_obj}) is blocked unless explicitly listed in allowed_private_targets or trusted_portal_hosts.",
            )


@dataclass(frozen=True)
class ValidatedUrl:
    """Validated HTTP/HTTPS URL representation."""

    url: str
    scheme: str
    hostname_ascii: str
    port: int | None
    ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None


def validate_http_url(
    raw_url: Any,
    *,
    allowed_private_targets: list[str],
    trusted_portal_hosts: list[str],
    field_name: str = "url",
) -> ValidatedUrl:
    """Validate an HTTP/HTTPS URL against scheme, credential, character, and SSRF rules."""
    text = validate_safe_text(raw_url, field_name, max_len=2048)
    if any(ch.isspace() for ch in text):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Parameter '{field_name}' must not contain whitespace.",
        )
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise ToolExecutionError(ErrorCode.INVALID_INPUT, f"Malformed URL in '{field_name}': {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported URL scheme '{parts.scheme or ''}' in '{field_name}'; only http and https are allowed.",
        )
    if parts.username is not None or parts.password is not None:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Embedded credentials are not permitted in '{field_name}'.",
        )
    if not parts.hostname:
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"URL in '{field_name}' is missing a valid hostname.",
        )
    try:
        port = parts.port
    except ValueError as exc:
        raise ToolExecutionError(ErrorCode.INVALID_INPUT, f"Invalid port in '{field_name}': {exc}") from exc

    raw_host = parts.hostname.rstrip(".")
    if raw_host.lower() == "localhost" or raw_host.lower().endswith(".localhost"):
        raise ToolExecutionError(
            ErrorCode.INVALID_INPUT,
            f"Localhost target '{raw_host}' is not permitted.",
        )

    ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        ip_literal = ipaddress.ip_address(raw_host)
        hostname_ascii = str(ip_literal).lower()
    except ValueError:
        hostname_ascii, _ = normalize_and_validate_domain(raw_host)

    if ip_literal is not None:
        validate_ip_for_network_probe(
            ip_literal,
            hostname_ascii,
            allowed_private_targets=allowed_private_targets,
            trusted_portal_hosts=trusted_portal_hosts,
        )

    return ValidatedUrl(
        url=text,
        scheme=scheme,
        hostname_ascii=hostname_ascii,
        port=port,
        ip_literal=ip_literal,
    )


@dataclass(frozen=True)
class CommandOutput:
    """Decoded subprocess execution result."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def decode_windows_bytes(raw: bytes) -> str:
    """Decode Windows CLI output bytes (UTF-8 or localized CP936/GBK) cleanly."""
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_windows_command(args: list[str], *, timeout_s: float = 5.0) -> CommandOutput:
    """Execute a fixed argument-list subprocess safely without shell or visible window."""
    if not isinstance(args, list) or not args:
        raise ValueError("args must be a non-empty list of strings")
    clean_args: list[str] = []
    for item in args:
        if not isinstance(item, str) or "\x00" in item:
            raise ValueError("Invalid command argument")
        clean_args.append(item)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        completed = subprocess.run(
            clean_args,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_s,
            creationflags=creationflags,
            check=False,
        )
        return CommandOutput(
            returncode=completed.returncode,
            stdout=decode_windows_bytes(completed.stdout),
            stderr=decode_windows_bytes(completed.stderr),
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        out_bytes = exc.stdout if isinstance(exc.stdout, bytes) else b""
        err_bytes = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return CommandOutput(
            returncode=-1,
            stdout=decode_windows_bytes(out_bytes),
            stderr=decode_windows_bytes(err_bytes),
            timed_out=True,
        )
