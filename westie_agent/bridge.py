from __future__ import annotations

import functools
import inspect
import json
import threading
import ipaddress
import time
from typing import Callable, Literal

from .settings import Settings
from .probe_compat import (discover_local_http_proxy, explain_probe_failure,
                           fake_ip_guard_failure, normalize_web_target)

MODEL_TOOLS = frozenset({"get_network_snapshot", "query_dns", "probe_connectivity",
                        "measure_latency_and_loss", "get_proxy_state",
                        "check_target_routing_rule", "plan_action"})


class ToolBridge:
    """Only this allowlist reaches the model; approval never reaches it."""
    def __init__(self, settings: Settings, service=None):
        self.settings = settings
        self.traces: list[dict] = []
        self.plans: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.problem = ""
        if service is None:
            try:
                from netpet_tools import ToolConfig, ToolService
                # Host enables the capability; a one-use approval is still required
                # and neither the approval nor execution methods reach the model.
                config = ToolConfig(mode=settings.tools_mode,
                                    scenario_id=settings.scenario_id if settings.tools_mode == "mock" else None,
                                    allow_writes=True)
                service = ToolService(config)
            except ImportError:
                self.problem = "联网检查组件尚未接入，不能读取本机网络状态。"
            except Exception:
                self.problem = "联网检查组件无法初始化，请检查工具配置。"
        self.service = service

    def functions(self, on_event: Callable[[str], None], *, for_cloud=False) -> list[Callable]:
        if self.service is None:
            return []
        registry = self.service.available_tools
        if not isinstance(registry, dict):
            raise TypeError("Tools.available_tools 必须是函数映射字典。")
        result = []
        for name, function in registry.items():
            if name not in MODEL_TOOLS or not callable(function):
                continue
            if name == "probe_connectivity":
                function = self._compatible_probe(function)
            if name == "measure_latency_and_loss":
                function = self._gateway_probe(function)
            result.append(self._wrap(name, function, on_event,for_cloud=for_cloud))
        return result

    def _gateway_probe(self, function):
        def measure_latency_and_loss(host: str, count: int = 4) -> dict:
            """Measure bounded ICMP loss/RTT. An OS-observed default gateway is
            permitted narrowly; arbitrary private hosts keep the Tools guards.
            Strong Wi-Fi signal does not rule out loss; ICMP loss alone does not
            prove congestion or the failure of application traffic.
            """
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                return function(host=host, count=count)
            if (self.settings.tools_mode == 'real' and address.is_private and not
                    (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast)):
                recent = next((t for t in reversed(self.traces) if t['tool']=='get_network_snapshot'
                               and t['result'].get('mode')=='real' and not t['result'].get('error')
                               and time.monotonic()-t.get('observed_monotonic',0)<60), None)
                iface = ((recent or {}).get('result',{}).get('data') or {}).get('selected_interface') or {}
                if host in (iface.get('default_gateways_ipv4') or []):
                    from netpet_tools import ToolService
                    config = self.service.config
                    config.allow_writes = False
                    config.probe.allowed_private_targets.append(host)
                    return ToolService(config).measure_latency_and_loss(host=host, count=count)
            return function(host=host, count=count)
        return measure_latency_and_loss

    def _probe_with_proxy(self, proxy_url: str, **kwargs) -> dict:
        from netpet_tools import ToolService
        # config is a deep copy supplied by Tools. Never mutate the main service
        # or its pending plans, and never change Windows/third-party settings.
        config = self.service.config
        config.allow_writes = False
        config.proxy.configured_proxy_url = proxy_url
        return ToolService(config).probe_connectivity(route="configured_proxy", **kwargs)

    def _compatible_probe(self, function: Callable):
        def probe_connectivity(target_url: str | None = None,
                               route: Literal["auto", "direct", "configured_proxy"] = "auto",
                               timeout_s: float = 5.0) -> dict:
            """Read website reachability without changing network settings.

            Use auto normally: try direct, and ONLY when the local guard rejects
            a domain resolved into the synthetic Fake-IP pool, try an existing
            declared loopback HTTP proxy. The actual route is always reported.
            Explicit direct never switches route. A returned tool/input policy
            error is not evidence of website/Windows denial. None checks known
            connectivity endpoints; a bare website name is accepted as HTTPS.
            """
            target_url = normalize_web_target(target_url)
            kwargs = {"target_url": target_url, "timeout_s": timeout_s}
            if route == "configured_proxy" and self.settings.tools_mode == "real":
                configured = getattr(getattr(getattr(self.service, "config", None), "proxy", None),
                                     "configured_proxy_url", None)
                proxy = None if configured else discover_local_http_proxy(target_url)
                if proxy:
                    response = self._probe_with_proxy(proxy, **kwargs)
                else:
                    response = function(route=route, **kwargs)
            else:
                response = function(route="direct" if route == "auto" else route, **kwargs)
            if (route == "auto" and self.settings.tools_mode == "real"
                    and fake_ip_guard_failure(response, target_url)):
                configured = getattr(getattr(getattr(self.service, "config", None), "proxy", None),
                                     "configured_proxy_url", None)
                proxy = configured or discover_local_http_proxy(target_url)
                if proxy:
                    initial = response
                    response = self._probe_with_proxy(proxy, **kwargs)
                    response["host_note"] = {
                        "category": "fake_ip_proxy_compatibility", "requested_route": "auto",
                        "actual_route": "configured_proxy", "system_settings_changed": False,
                        "initial_check": {"http_request_sent": False,
                                          "reason": "synthetic_ip_rejected_by_local_guard",
                                          "original_error": initial.get("error")},
                        "message": "初次检查被小鸡自身的 Fake-IP 范围检查停止，未向网站发送请求；已改用现有本地代理进行只读探测。请按本次实际 HTTP 响应判断，不能声称直连成功或权限被禁止。",
                    }
            return response
        return probe_connectivity

    def _wrap(self, name: str, function: Callable, on_event: Callable[[str], None], *, for_cloud=False):
        @functools.wraps(function)
        def invoke(*args, **kwargs):
            on_event({"get_network_snapshot": "检查网络连接状态…", "query_dns": "检查域名解析…",
                      "probe_connectivity": "检查网站访问…", "measure_latency_and_loss": "检查延迟与丢包…",
                      "get_proxy_state": "检查代理配置…", "check_target_routing_rule": "检查网站访问策略…",
                      "plan_action": "整理可采取的恢复措施…"}[name])
            try:
                arguments=dict(inspect.signature(function).bind(*args,**kwargs).arguments)
                if name=='plan_action' and arguments.get('action_name')=='reconnect_wifi':
                    response={'schema_version':'1.0','tool':name,'success':False,'status':'error','mode':self.settings.tools_mode,
                              'data':{},'summary':'已移除自动联网功能，请在 Windows 网络菜单手动连接。',
                              'error':{'code':'ACTION_REMOVED','message':'reconnect_wifi is unavailable; provide manual Windows connection steps.'}}
                else:response = function(*args, **kwargs)
                if hasattr(response, "model_dump"):
                    response = response.model_dump(mode="json")
                if not isinstance(response, dict):
                    raise TypeError("工具返回值不是字典")
                response = json.loads(json.dumps(response, ensure_ascii=False, allow_nan=False))
                if response.get("mode") != self.settings.tools_mode:
                    raise ValueError("工具返回的数据模式与宿主配置不一致")
            except Exception as exc:
                response = {"schema_version": "1.0", "tool": name, "success": False, "status": "error",
                            "mode": self.settings.tools_mode, "data": {}, "summary": "工具未能完成本次检查",
                            "warnings": [], "error": {"code": "TOOL_ERROR", "message": type(exc).__name__}}
            raw_response = response
            if name == "probe_connectivity":
                response = explain_probe_failure(response)
            with self.lock:
                try:
                    arguments = dict(inspect.signature(function).bind(*args, **kwargs).arguments)
                except TypeError:
                    arguments = kwargs
                if name == 'probe_connectivity' and 'target_url' in arguments:
                    arguments['target_url'] = normalize_web_target(arguments['target_url'])
                trace = {"tool": name, "arguments": arguments, "result": response,
                         "origin": "model", "observed_monotonic": time.monotonic()}
                if raw_response != response:
                    trace["raw_result"] = raw_response
                self.traces.append(trace)
                data = response.get("data", {})
                if name == "plan_action" and response.get("success") and isinstance(data, dict):
                    plan_id = data.get("plan_id")
                    if isinstance(plan_id, str) and plan_id:
                        self.plans[plan_id] = data
            if for_cloud:
                from .api_connection import cloud_tool_result
                response=cloud_tool_result(response)
            serialized = json.dumps(response, ensure_ascii=False)
            if len(serialized) > 20000:
                return {"tool": name, "mode": self.settings.tools_mode, "status": "partial",
                        "summary": response.get("summary"), "data_preview": serialized[:16000],
                        "warnings": ["结果超过长度限制，仅能据部分数据分析。"]}
            return response
        # Resolve postponed annotations in the original function's namespace.
        signature = inspect.signature(function, eval_str=True)
        invoke.__signature__ = signature
        invoke.__annotations__ = {key: p.annotation for key, p in signature.parameters.items()
                                  if p.annotation is not inspect.Parameter.empty}
        invoke.__annotations__["return"] = dict
        return invoke

    def execute_approved(self, plan_id: str) -> dict:
        """Called exclusively by the host after the actual confirmation dialog."""
        if not self.service or plan_id not in self.plans:
            raise ValueError("操作计划已失效，请重新检查后再操作。")
        if self.plans[plan_id].get('action_name')=='reconnect_wifi':
            raise ValueError('自动联网功能已移除，请在 Windows 网络菜单手动连接。')
        token = self.service.approve_action(plan_id)
        result = self.service.execute_action(plan_id, approval_token=token)
        self.plans.pop(plan_id, None)
        if not isinstance(result, dict):
            raise TypeError("执行结果格式不正确")
        return result
