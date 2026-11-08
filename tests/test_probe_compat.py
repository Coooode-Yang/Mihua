import copy

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

import westie_agent.bridge as bridge_module
from westie_agent.bridge import ToolBridge
from westie_agent.engine import LocalAgent
from westie_agent.probe_compat import (
    fake_ip_guard_failure, grounded_probe_text, loopback_http_proxy_from_settings,
    normalize_web_target,
)
from westie_agent.settings import Settings


def denial(ip="198.18.0.14"):
    return {"mode": "real", "success": False, "status": "error", "data": {},
            "error": {"code": "PERMISSION_DENIED", "message":
                      f"Private network target 'www.google.com' ({ip}) is blocked unless explicitly listed in allowed_private_targets or trusted_portal_hosts."}}


def http_result(status=200):
    return {"mode": "real", "success": True, "status": "ok", "data": {
        "route": "configured_proxy", "http_status": status,
        "attempts": [{"probed_url": "https://www.google.com", "http_status": status, "outcome": "response"}]},
        "error": None}


class ProbeService:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.available_tools = {"probe_connectivity": self.probe_connectivity}

    def probe_connectivity(self, target_url: str | None = None, route: str = "direct", timeout_s: float = 3.0) -> dict:
        self.calls.append((target_url, route))
        return copy.deepcopy(self.result)


def make_bridge(monkeypatch, result=None, proxy="http://127.0.0.1:7897", proxy_result=None):
    service = ProbeService(denial() if result is None else result)
    bridge = ToolBridge(Settings(tools_mode="real"), service)
    discoveries, proxy_calls = [], []

    def discover(target):
        discoveries.append(target)
        return proxy

    def probe_via_proxy(proxy_url, **kwargs):
        proxy_calls.append((proxy_url, kwargs))
        return copy.deepcopy(http_result() if proxy_result is None else proxy_result)

    monkeypatch.setattr(bridge_module, "discover_local_http_proxy", discover)
    monkeypatch.setattr(bridge, "_probe_with_proxy", probe_via_proxy)
    return bridge, service, discoveries, proxy_calls


def test_auto_fake_ip_uses_existing_proxy_and_reports_actual_route(monkeypatch):
    bridge, service, discoveries, proxy_calls = make_bridge(monkeypatch)
    result = bridge.functions(lambda _: None)[0](target_url="www.google.com")
    assert service.calls == [("https://www.google.com", "direct")]
    assert len(discoveries) == len(proxy_calls) == 1
    assert result["data"]["http_status"] == 200
    note = result["host_note"]
    assert note["actual_route"] == "configured_proxy"
    assert note["initial_check"]["http_request_sent"] is False
    assert note["system_settings_changed"] is False
    assert "HTTP 200" in grounded_probe_text(bridge.traces)


def test_explicit_direct_does_not_switch_route(monkeypatch):
    bridge, service, discoveries, proxy_calls = make_bridge(monkeypatch)
    result = bridge.functions(lambda _: None)[0](target_url="https://www.google.com", route="direct")
    assert not discoveries and not proxy_calls
    assert result["error"]["code"] == "PROBE_POLICY_RESTRICTION"
    assert result["host_note"]["http_request_sent"] is False
    assert bridge.traces[0]["raw_result"]["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("ip,target", [
    ("192.168.1.1", "http://router.example.test"),
    ("10.0.0.1", "http://campus.example.test"),
    ("169.254.169.254", "http://metadata.example.test"),
    ("127.0.0.1", "http://localhost"),
    ("198.18.0.14", "http://198.18.0.14"),
])
def test_other_private_or_literal_targets_never_gain_auto_proxy(monkeypatch, ip, target):
    bridge, service, discoveries, proxy_calls = make_bridge(monkeypatch, result=denial(ip))
    bridge.functions(lambda _: None)[0](target_url=target)
    assert not discoveries and not proxy_calls


def test_http_403_is_not_a_local_policy_error_and_never_retried(monkeypatch):
    response = http_result(403)
    response["data"]["route"] = "direct"
    bridge, service, discoveries, proxy_calls = make_bridge(monkeypatch, result=response)
    result = bridge.functions(lambda _: None)[0](target_url="https://www.google.com")
    assert result == response
    assert not discoveries and not proxy_calls
    assert grounded_probe_text(bridge.traces) is None


def test_no_proxy_reports_tool_limitation_not_network_denial(monkeypatch):
    bridge, service, discoveries, proxy_calls = make_bridge(monkeypatch, proxy=None)
    result = bridge.functions(lambda _: None)[0](target_url="https://www.google.com")
    assert result["host_note"]["fake_ip_suspected"]
    assert not proxy_calls
    text = grounded_probe_text(bridge.traces)
    assert "不能据此说你的访问权限被禁止" in text
    assert "暂时无法" in text


def test_failed_proxy_retry_is_not_reported_as_recovery(monkeypatch):
    response = http_result()
    response["data"]["attempts"][0].update(http_status=None, outcome="timeout")
    response["data"]["http_status"] = None
    bridge, *_ = make_bridge(monkeypatch, proxy_result=response)
    bridge.functions(lambda _: None)[0](target_url="https://www.google.com")
    text = grounded_probe_text(bridge.traces)
    assert "未获得网页响应" in text and "HTTP 200" not in text


@pytest.mark.parametrize("raw,expected", [
    ("www.google.com", "https://www.google.com"),
    (" `www.google.com` ", "https://www.google.com"),
    ("http://connect.rom.miui.com/generate_204", "http://connect.rom.miui.com/generate_204"),
    ("example.com:443/path", "https://example.com:443/path"),
    ("file:///etc/passwd", "file:///etc/passwd"),
    ("javascript:alert(1)", "javascript:alert(1)"),
])
def test_plain_domain_normalization_preserves_original_validator(raw, expected):
    assert normalize_web_target(raw) == expected


@pytest.mark.parametrize("values,expected", [
    ({"ProxyServer": "127.0.0.1:7897", "ProxyEnable": 0}, "http://127.0.0.1:7897"),
    ({"ProxyServer": "http=127.0.0.1:7897;https=127.0.0.1:7898"}, "http://127.0.0.1:7898"),
    ({"ProxyServer": "[::1]:7897"}, "http://[::1]:7897"),
    ({"ProxyServer": "remote.example.test:7897"}, None),
    ({"ProxyServer": "http://user:secret@127.0.0.1:7897"}, None),
    ({"ProxyServer": "socks=127.0.0.1:7897"}, None),
    ({"ProxyServer": "127.0.0.1:7897", "AutoConfigURL": "http://localhost/proxy.pac"}, None),
    ({"ProxyServer": "http://127.0.0.1:7897/path"}, None),
])
def test_only_declared_loopback_http_proxies_are_used(values, expected):
    assert loopback_http_proxy_from_settings(values, "https://www.google.com") == expected


def test_input_error_is_not_website_denial(monkeypatch):
    bridge, *_ = make_bridge(monkeypatch, result={"mode": "real", "success": False, "data": {},
        "error": {"code": "INVALID_INPUT", "message": "bad URL"}})
    result = bridge.functions(lambda _: None)[0](target_url="bad URL")
    assert result["host_note"]["http_request_sent"] is False
    assert result["host_note"]["category"] == "probe_input_error"


def test_grounded_ui_and_history_override_model_misinterpretation(monkeypatch):
    bridge, *_ = make_bridge(monkeypatch)

    def model(messages, info):
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart("probe_connectivity", {"target_url": "https://www.google.com"})])
        return ModelResponse(parts=[TextPart("管理员封禁了你的电脑，请关闭防火墙。")])

    agent = LocalAgent(Settings(tools_mode="real"), bridge)
    reply = agent.reply("检查网站 https://www.google.com，浏览器正常，只有小鸡说受限", "http://127.0.0.1:1", lambda _: None,
                        model=FunctionModel(model))
    assert '收到了这个网站的回复' in reply.text
    assert any(t['result'].get('data',{}).get('http_status')==200 for t in reply.traces)
    assert "管理员封禁" not in reply.text and "关闭防火墙" not in reply.text
    saved_text = [p.content for m in agent.history for p in m.parts if isinstance(p, TextPart)]
    assert all("管理员封禁" not in text for text in saved_text)


def test_real_service_proxy_clone_does_not_mutate_host_config(monkeypatch):
    from netpet_tools import ToolConfig, ToolService
    original = ToolService(ToolConfig(mode="real", allow_writes=True))
    bridge = ToolBridge(Settings(tools_mode="real"), original)
    observed = []

    def probe(self, **kwargs):
        observed.append((self.config, kwargs))
        return http_result()

    monkeypatch.setattr(ToolService, "probe_connectivity", probe)
    bridge._probe_with_proxy("http://127.0.0.1:7897", target_url="https://www.google.com", timeout_s=3)
    assert observed[0][0].allow_writes is False
    assert observed[0][0].proxy.configured_proxy_url == "http://127.0.0.1:7897"
    assert original.config.allow_writes is True
    assert original.config.proxy.configured_proxy_url is None
