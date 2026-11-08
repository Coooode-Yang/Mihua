"""Isolated per-instance mock scenario loader and state container."""

from __future__ import annotations

import copy
import importlib.resources
import json
import re
from pathlib import Path
from typing import Any

from netpet_tools.base import ErrorCode, ToolExecutionError
from netpet_tools.config import RoutingRuleConfig, ToolConfig

_SCENARIO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_VALID_ORIGINS = frozenset({"synthetic", "captured_replay"})
_REQUIRED_SECTIONS = frozenset({"network_snapshot", "dns", "http_probes", "icmp", "proxy"})


def load_raw_scenario(scenario_id: str | None, scenarios_dir: str | None = None) -> dict[str, Any]:
    """Load and validate a scenario JSON payload without sharing mutable references."""
    if not scenario_id or not isinstance(scenario_id, str):
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            "Mock mode requires a non-empty scenario_id; refusing to fall back to real environment.",
        )
    cleaned_id = scenario_id.strip()
    if not _SCENARIO_ID_RE.match(cleaned_id):
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Invalid scenario_id '{scenario_id}'. Only alphanumeric, underscore, and hyphen characters are allowed.",
        )

    filename = f"{cleaned_id}.json"
    raw_text: str | None = None

    if scenarios_dir is not None:
        candidate = Path(scenarios_dir) / filename
        if not candidate.is_file():
            raise ToolExecutionError(
                ErrorCode.INVALID_SCENARIO,
                f"Scenario '{cleaned_id}' was not found in configured scenarios_dir.",
            )
        try:
            raw_text = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolExecutionError(
                ErrorCode.INVALID_SCENARIO,
                f"Failed to read scenario '{cleaned_id}': {exc}",
            ) from exc
    else:
        repo_candidate = Path(__file__).resolve().parents[2] / "scenarios" / filename
        if repo_candidate.is_file():
            try:
                raw_text = repo_candidate.read_text(encoding="utf-8")
            except OSError as exc:
                raise ToolExecutionError(
                    ErrorCode.INVALID_SCENARIO,
                    f"Failed to read scenario '{cleaned_id}': {exc}",
                ) from exc
        else:
            try:
                pkg_res = importlib.resources.files("netpet_tools._builtin_scenarios").joinpath(filename)
                if pkg_res.is_file():
                    raw_text = pkg_res.read_text(encoding="utf-8")
            except (ModuleNotFoundError, FileNotFoundError, OSError):
                raw_text = None

    if raw_text is None:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario '{cleaned_id}' does not exist; refusing to fall back to real environment.",
        )

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario '{cleaned_id}' contains invalid JSON: {exc}",
        ) from exc

    if not isinstance(payload, dict):
        raise ToolExecutionError(ErrorCode.INVALID_SCENARIO, f"Scenario '{cleaned_id}' root must be a JSON object.")

    if payload.get("scenario_id") != cleaned_id:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario file '{filename}' has mismatched internal scenario_id '{payload.get('scenario_id')}'.",
        )
    origin = payload.get("data_origin")
    if origin not in _VALID_ORIGINS:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario '{cleaned_id}' must declare data_origin as 'synthetic' or 'captured_replay'.",
        )
    missing = _REQUIRED_SECTIONS - set(payload.keys())
    if missing:
        raise ToolExecutionError(
            ErrorCode.INVALID_SCENARIO,
            f"Scenario '{cleaned_id}' is missing required sections: {sorted(missing)}.",
        )

    return copy.deepcopy(payload)


class MockEnvironment:
    """Per-ToolService isolated mock environment state."""

    def __init__(self, config: ToolConfig) -> None:
        self._scenario_id = config.scenario_id
        self._scenarios_dir = config.scenarios_dir
        self._state: dict[str, Any] | None = None
        self._effective_config: ToolConfig | None = None
        self._dns_flushed_count: int = 0
        self._opened_urls: list[str] = []

    def ensure_loaded(self, base_config: ToolConfig) -> tuple[dict[str, Any], ToolConfig]:
        """Lazily load and isolate scenario state and scenario-scoped default config."""
        if self._state is None:
            raw = load_raw_scenario(self._scenario_id, self._scenarios_dir)
            # Strip any accidental evaluation-only fields so Agent never receives root-cause hints
            raw.pop("_evaluation", None)
            raw.pop("expected_conclusion", None)
            self._state = raw
            self._effective_config = self._merge_scenario_defaults(base_config, raw.get("scenario_config_defaults") or {})
        assert self._state is not None and self._effective_config is not None
        return self._state, self._effective_config

    @staticmethod
    def _merge_scenario_defaults(base_config: ToolConfig, defaults: dict[str, Any]) -> ToolConfig:
        """Apply scenario defaults only where caller kept empty/unset defaults."""
        cfg = base_config.model_copy(deep=True)
        if not cfg.probe.trusted_portal_hosts and defaults.get("trusted_portal_hosts"):
            cfg.probe.trusted_portal_hosts = list(defaults["trusted_portal_hosts"])
        if not cfg.probe.allowed_private_targets and defaults.get("allowed_private_targets"):
            cfg.probe.allowed_private_targets = list(defaults["allowed_private_targets"])
        if not cfg.probe.allowed_browser_hosts and defaults.get("allowed_browser_hosts"):
            cfg.probe.allowed_browser_hosts = list(defaults["allowed_browser_hosts"])
        if cfg.proxy.configured_proxy_url is None and defaults.get("configured_proxy_url") is not None:
            cfg.proxy.configured_proxy_url = defaults["configured_proxy_url"]
        if not cfg.proxy.routing_rules and defaults.get("routing_rules"):
            cfg.proxy.routing_rules = [RoutingRuleConfig.model_validate(item) for item in defaults["routing_rules"]]
        return cfg

    def update_campus_domain(self, domain_ascii: str, rule: RoutingRuleConfig) -> None:
        """Dynamically update effective mock config with new campus domain and routing rule."""
        if self._effective_config is not None:
            if domain_ascii not in self._effective_config.probe.trusted_portal_hosts:
                self._effective_config.probe.trusted_portal_hosts.append(domain_ascii)
            if domain_ascii not in self._effective_config.probe.allowed_browser_hosts:
                self._effective_config.probe.allowed_browser_hosts.append(domain_ascii)
            if domain_ascii not in self._effective_config.probe.allowed_private_targets:
                self._effective_config.probe.allowed_private_targets.append(domain_ascii)
            self._effective_config.proxy.routing_rules = [
                r for r in self._effective_config.proxy.routing_rules if r.pattern != domain_ascii
            ] + [rule]

    @property
    def dns_flushed_count(self) -> int:
        return self._dns_flushed_count

    def record_dns_flush(self) -> None:
        self._dns_flushed_count += 1

    @property
    def opened_urls(self) -> list[str]:
        return list(self._opened_urls)

    def record_browser_open(self, url: str) -> None:
        self._opened_urls.append(url)
