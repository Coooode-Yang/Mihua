import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from westie_agent.bridge import ToolBridge
from westie_agent.diagnostics import diagnostic_request
from westie_agent.engine import LocalAgent
from westie_agent.settings import Settings


@pytest.mark.parametrize('text', ['解释表见代理', '网络侵权的构成要件是什么', '民法中的代理权是什么意思', '法学论文怎么安排阅读', '给我一句鼓励'])
def test_personal_conversation_has_no_network_tools(text):
    assert diagnostic_request(text) is None
    settings = Settings(tools_mode='mock')
    bridge = ToolBridge(settings)
    def model(messages, info):
        assert not info.function_tools
        return ModelResponse(parts=[TextPart('一起慢慢来。')])
    answer = LocalAgent(settings, bridge).reply(text, 'http://127.0.0.1:1', lambda _: None, model=FunctionModel(model))
    assert answer.text and not answer.traces and not answer.diagnosis


def test_real_network_fault_still_uses_diagnosis():
    assert diagnostic_request('法学数据库网站打不开，帮我检查 https://example.com') is not None


def test_personal_product_never_registers_network_tools_for_network_questions(monkeypatch):
    settings = Settings(tools_mode='mock')
    bridge = ToolBridge(settings)
    def forbidden(*args, **kwargs):
        pytest.fail('The personal product must not register network tools')
    monkeypatch.setattr(bridge, 'functions', forbidden)
    def model(messages, info):
        assert not info.function_tools
        return ModelResponse(parts=[TextPart('可以在浏览器里确认学校的访问方式。')])
    answer = LocalAgent(settings, bridge, allow_network=False).reply(
        '法学数据库网站打不开，帮我检查 https://example.com', 'http://127.0.0.1:1', lambda _: None,
        model=FunctionModel(model))
    assert not answer.traces and not answer.diagnosis and not answer.plans
