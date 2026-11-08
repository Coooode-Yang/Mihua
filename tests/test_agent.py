import hashlib
import json
import threading
import zipfile

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from westie_agent.bridge import ToolBridge
from westie_agent.engine import LocalAgent
from westie_agent.portable import PortableRuntime, verified_download
from westie_agent.runtime import SetupRequired, safe_extract
from westie_agent.settings import Settings


class FakeService:
    def __init__(self):
        self.approvals = 0
        self.executions = 0
        self.available_tools = {"query_dns": self.query_dns, "plan_action": self.plan_action,
                                "execute_action": self.execute_action, "approve_action": self.approve_action}

    def query_dns(self, domain: str) -> dict:
        """Read DNS observations for the requested domain."""
        return {"mode": "mock", "success": True, "status": "ok", "data": {"domain": domain, "status": "timeout"}}

    def plan_action(self, action_name: str, params: dict | None = None) -> dict:
        """Prepare an action plan without executing it."""
        return {"mode": "mock", "success": True, "data": {"plan_id": "plan-1", "action_name": action_name}}

    def approve_action(self, plan_id):
        self.approvals += 1
        return "host-secret-never-to-model"

    def execute_action(self, plan_id, *, approval_token):
        assert approval_token == "host-secret-never-to-model"
        self.executions += 1
        return {"success": True, "data": {"recovery_status": "unknown"}}


def test_model_selects_tool_and_uses_return_without_host_approval():
    service = FakeService()
    settings = Settings(tools_mode="mock")
    bridge = ToolBridge(settings, service)
    names = [f.__name__ for f in bridge.functions(lambda _: None)]
    assert "execute_action" not in names and "approve_action" not in names
    calls = []
    def model(messages, info):
        calls.append(messages)
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not results:
            return ModelResponse(parts=[ToolCallPart("query_dns", {"domain": "school.example.test"})])
        assert results[-1].content["data"]["status"] == "timeout"
        return ModelResponse(parts=[TextPart("域名解析超时，但还需要排查认证和访问路径。")])
    answer = LocalAgent(settings, bridge).reply("学校网站打不开", "http://127.0.0.1:1", lambda _: None,
                                               model=FunctionModel(model))
    assert len(calls) == 2 and answer.traces[0]["tool"] == "query_dns"
    assert "课程演示" in answer.text
    assert service.approvals == service.executions == 0


def test_plan_is_not_executed_until_explicit_host_call():
    service = FakeService()
    bridge = ToolBridge(Settings(tools_mode="mock"), service)
    plan = next(fn for fn in bridge.functions(lambda _: None) if fn.__name__ == "plan_action")
    plan(action_name="flush_dns")
    assert service.executions == 0
    bridge.execute_approved("plan-1")
    assert service.approvals == service.executions == 1
    with pytest.raises(ValueError):
        bridge.execute_approved("plan-1")


def test_mismatched_mock_result_never_looks_like_real_observation():
    bridge = ToolBridge(Settings(tools_mode="real"), FakeService())
    tool = next(fn for fn in bridge.functions(lambda _: None) if fn.__name__ == "query_dns")
    result = tool("school.example.test")
    assert result["success"] is False and result["error"]["code"] == "TOOL_ERROR"


def test_bad_plan_id_never_calls_host_approval():
    service = FakeService()
    bridge = ToolBridge(Settings(), service)
    with pytest.raises(ValueError):
        bridge.execute_approved("forged")
    assert service.approvals == 0


def test_missing_runtime_has_clear_setup_error_and_no_subprocess(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a missing runtime"))
    with pytest.raises(SetupRequired):
        PortableRuntime(tmp_path).start("qwen3:4b")


def test_archive_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.txt", "bad")
    with pytest.raises(ValueError):
        safe_extract(archive, tmp_path / "runtime")
    assert not (tmp_path / "outside.txt").exists()


def test_corrupt_download_is_not_marked_verified(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"corrupted")
    with pytest.raises(SetupRequired):
        verified_download("https://unused.invalid/", target, "0" * 64, 9, lambda _: None, threading.Event())
    assert not target.exists() and not target.with_suffix(".gguf.verified.json").exists()


def test_verified_local_file_needs_no_network(tmp_path, monkeypatch):
    import urllib.request
    target = tmp_path / "model.gguf"
    target.write_bytes(b"test")
    digest = hashlib.sha256(b"test").hexdigest()
    target.with_suffix(".gguf.verified.json").write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("must work offline"))
    verified_download("https://unused.invalid/", target, digest, 4, lambda _: None, threading.Event())


def test_settings_roundtrip_and_invalid_model(tmp_path):
    settings = Settings(model="qwen3:1.7b", model_backend='local', tools_mode="mock")
    settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings
    path = tmp_path / "data/settings.json"
    path.write_text('{"model_backend":"local","model":"some-cloud-model"}', encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.load(tmp_path)


@pytest.mark.parametrize('failure,expected_calls', [(b'ErrorOutOfDeviceMemory',2),(b'invalid model file',1)])
def test_gpu_memory_fallback_is_cpu_only_once_and_only_for_current_allocation_failure(tmp_path,monkeypatch,failure,expected_calls):
    import io
    import westie_agent.portable as portable
    runtime=PortableRuntime(tmp_path)
    monkeypatch.setattr(runtime,'ready_on_disk',lambda _:True)
    log=tmp_path/'data/logs/model-runtime.log'
    log.parent.mkdir(parents=True)
    log.write_bytes(b'old error: ErrorOutOfDeviceMemory\n')
    calls=[]
    class Process:
        pid=123456
        def __init__(self,failed):self.exit=1 if failed else None
        def poll(self):return self.exit
        def wait(self,timeout):self.exit=0
    def popen(command,**kwargs):
        calls.append(command)
        if len(calls)==1:
            kwargs['stdout'].write(failure+b'\n')
            kwargs['stdout'].flush()
        return Process(len(calls)==1)
    class Job:
        handle=1
        def __init__(self,p):pass
        def close(self):self.handle=None
    monkeypatch.setattr(portable.subprocess,'Popen',popen)
    monkeypatch.setattr(portable,'WindowsJob',Job)
    monkeypatch.setattr(portable.LOCAL_OPENER,'open',lambda *a,**k:io.BytesIO(b'{"status":"ok"}'))
    try:
        if expected_calls==1:
            with pytest.raises(SetupRequired):runtime.start('qwen3:4b')
        else:
            runtime.start('qwen3:4b')
            assert runtime.process.poll() is None
            assert calls[1][calls[1].index('--device')+1]=='none'
            assert calls[1][calls[1].index('--n-gpu-layers')+1]=='0'
            assert '--no-op-offload' in calls[1] and '--no-kv-offload' in calls[1]
        assert len(calls)==expected_calls
    finally:runtime.close()
