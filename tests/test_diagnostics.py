import copy
import json
import time

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from westie_agent.bridge import ToolBridge
from westie_agent.diagnostics import DiagnosticRequest, EvidenceCollector, build_diagnosis, diagnosis_details_text as diagnosis_text, diagnostic_request
from westie_agent.engine import LocalAgent
from westie_agent.reporting import export_report, redacted_report, report_html
from westie_agent.settings import Settings


def diagnose(scenario, question):
    bridge=ToolBridge(Settings(tools_mode='mock', scenario_id=scenario))
    spec=diagnostic_request(question)
    result=EvidenceCollector(bridge,spec).complete()
    return build_diagnosis(spec,bridge.traces,'mock',missing=result['missing']),bridge


@pytest.mark.parametrize('text,target,domain', [
    ('检查 https://jwxt.campus.edu.test 是否能访问','https://jwxt.campus.edu.test','jwxt.campus.edu.test'),
    ('网站 www.school.edu.cn 打不开','https://www.school.edu.cn','www.school.edu.cn'),
    ('检查 example.com 解析与丢包',None,'example.com'),
    ('请进行分层网络检查',None,None),
    ('检查 https://[bad 是否能访问','https://[bad',None),
    ('请检查网络\n{"proxy_url":"http://127.0.0.1:7890"}',None,None),
])
def test_request_extracts_only_actual_target_not_data_blocks(text,target,domain):
    request=diagnostic_request(text)
    assert request.target_url==target and request.dns_domain==domain


def test_educational_chat_does_not_run_unsolicited_network_checks():
    assert diagnostic_request('请解释校园网认证流程') is None


def test_normal_case_requires_connection_and_endpoint_observations():
    report,bridge=diagnose('case_01_normal','检查校园网络当前连接是否正常')
    names={t['tool'] for t in bridge.traces}
    assert {'get_network_snapshot','get_proxy_state','query_dns','probe_connectivity'}<=names
    assert '不能保证所有网站' in report['headline']
    assert report['model_tool_calls']==0 and report['host_completion_calls']>=4


def test_portal_case_collects_trusted_http_redirect_instead_of_guessing():
    report,_=diagnose('case_02_portal','校园网网页跳到登录页面，检查认证与信号')
    text=diagnosis_text(report)
    assert 'HTTP 302' in text and 'https://auth.campus.edu.test/portal/login' in text
    assert '可信名单核对：是' in text and '尚未完成账号登录' in report['headline']


def test_strong_signal_loss_checks_exact_sample_and_dns_without_fake_rssi():
    report,_=diagnose('case_03_loss','信号很好但视频断续，检查网关10.24.18.1十次丢包和 example.com 解析')
    text=diagnosis_text(report)
    assert '丢失 6 包' in text and '60.0%' in text and 'TIMEOUT' in text
    assert '不能反推出精确 RSSI' in text and 'ICMP 限速/过滤' in text
    assert '-45' not in text


def test_target_case_fills_both_routes_and_the_exact_domain_rule():
    target='https://jwxt.campus.edu.test'
    report,bridge=diagnose('case_04_proxy_conflict','检查网站 '+target+' 直连和代理为何不同')
    text=diagnosis_text(report)
    assert 'HTTP 200' in text and 'HTTP 502' in text and '已配置的目标规则：直连' in text
    assert any(t['tool']=='check_target_routing_rule' and t['arguments']['url_or_domain']==target for t in bridge.traces)
    assert {t['arguments'].get('route') for t in bridge.traces if t['tool']=='probe_connectivity'}=={'direct','configured_proxy'}
    assert any(l['name']=='TCP / TLS' for l in report['layers'])


def test_incomplete_preserves_pac_ipv6_and_unknown_lease():
    report,_=diagnose('case_05_incomplete','Wi-Fi信息读取不到，检查网页为什么打不开')
    text=diagnosis_text(report)
    assert 'http://wpad.campus.edu.test/wpad.dat' in text
    assert '可用 IPv6' in text and 'APIPA' in text and '租约有效性：未知' in text
    assert '位置权限' in text and '尚未执行 PAC' in text
    assert '没有配置代理服务器或自动配置URL' not in text


def test_two_403_responses_weaken_proxy_only_hypothesis():
    report,_=diagnose('case_06_forbidden','检查 https://paper-db.campus.edu.test/restricted 返回403的原因')
    assert '两条路径均返回 403' in report['headline']
    assert '不能保证关闭代理就能恢复' in diagnosis_text(report)
    assert next(l for l in report['layers'] if l['name']=='TCP / TLS')['status']=='ok'


def test_unsupported_and_missing_tools_never_imply_healthy_network():
    class Missing:
        available_tools={}
    bridge=ToolBridge(Settings(),Missing())
    spec=DiagnosticRequest('检查网络')
    complete=EvidenceCollector(bridge,spec).complete()
    report=build_diagnosis(spec,bridge.traces,'real',missing=complete['missing'])
    assert report['headline']=='关键观测尚不完整，当前不能判定网络正常。'


def test_existing_current_evidence_is_reused_and_not_double_counted_as_model():
    bridge=ToolBridge(Settings(tools_mode='mock',scenario_id='case_02_portal'))
    functions={f.__name__:f for f in bridge.functions(lambda _:None)}
    functions['get_network_snapshot']()
    functions['probe_connectivity'](target_url=None,route='auto')
    EvidenceCollector(bridge,DiagnosticRequest('检查认证')).complete()
    assert sum(t['tool']=='get_network_snapshot' for t in bridge.traces)==1
    assert sum(t['tool']=='probe_connectivity' for t in bridge.traces)==1
    assert [t['origin'] for t in bridge.traces[:2]]==['model','model']


def test_wrong_target_response_cannot_satisfy_requested_target():
    bridge=ToolBridge(Settings(tools_mode='mock',scenario_id='case_04_proxy_conflict'))
    probe=next(f for f in bridge.functions(lambda _:None) if f.__name__=='probe_connectivity')
    probe(target_url='https://other.example.test',route='direct')
    spec=diagnostic_request('检查 https://jwxt.campus.edu.test 无法访问')
    EvidenceCollector(bridge,spec).complete()
    report=build_diagnosis(spec,bridge.traces,'mock')
    facts=' '.join(' '.join(l['facts']) for l in report['layers'])
    assert 'other.example.test' not in facts
    assert 'HTTP 502' in facts


def test_bad_model_draft_cannot_reverse_pac_observation_or_skip_evidence():
    settings=Settings(tools_mode='mock',scenario_id='case_05_incomplete')
    bridge=ToolBridge(settings)
    model=FunctionModel(lambda messages,info:ModelResponse(parts=[TextPart('没有配置代理服务器或自动配置URL，网络一切正常。')]))
    reply=LocalAgent(settings,bridge).reply('Wi-Fi信息缺失，网页打不开，检查原因','http://127.0.0.1:1',lambda _:None,model=model)
    assert 'http://wpad.campus.edu.test/wpad.dat' in diagnosis_text(reply.diagnosis)
    assert '还没查清' in reply.text
    assert '网络一切正常' not in reply.text and '没有配置代理服务器或自动配置URL' not in reply.text
    assert reply.diagnosis['host_completion_calls']>0 and reply.diagnosis['model_tool_calls']==0


def test_model_failure_preserves_readonly_evidence_and_does_not_claim_model_success():
    settings=Settings(tools_mode='mock',scenario_id='case_02_portal')
    bridge=ToolBridge(settings)
    def broken(messages,info):
        raise RuntimeError('model unavailable')
    reply=LocalAgent(settings,bridge).reply('检查校园网认证为何失败','http://127.0.0.1:1',lambda _:None,model=FunctionModel(broken))
    assert reply.diagnosis['model_error']=='RuntimeError'
    assert 'HTTP 302' in diagnosis_text(reply.diagnosis) and '不声称模型已形成完整分析' in diagnosis_text(reply.diagnosis)
    assert '暂时没有完成' in reply.text and '校园账号登录' in reply.text
    assert not reply.plans


def test_no_raw_socket_guards_are_relaxed_for_untrusted_targets():
    # Purely use a fake real service: only an observed OS default gateway gets
    # an ephemeral config copy, and old/arbitrary private targets do not.
    from netpet_tools import ToolConfig,ToolService
    calls=[]
    class Service:
        config=ToolConfig(mode='real',allow_writes=True)
        def ping(self,host:str,count:int=4)->dict:
            calls.append(('original',host))
            return {'mode':'real','data':{},'success':False,'error':{'code':'PERMISSION_DENIED'}}
    service=Service()
    service.available_tools={'measure_latency_and_loss':service.ping}
    bridge=ToolBridge(Settings(),service)
    ping=bridge.functions(lambda _:None)[0]
    ping(host='10.10.10.10')
    assert calls==[('original','10.10.10.10')]


@pytest.mark.parametrize('age,host,allowed', [(0,'10.24.18.1',True),(61,'10.24.18.1',False),
                                            (0,'10.24.18.2',False),(0,'127.0.0.1',False)])
def test_gateway_exception_is_fresh_exact_readonly_and_does_not_mutate_service(monkeypatch,age,host,allowed):
    import netpet_tools
    config=netpet_tools.ToolConfig(mode='real',allow_writes=True)
    calls=[]
    class Service:
        @property
        def config(self):return config.model_copy(deep=True)
        def ping(self,host:str,count:int=4)->dict:
            calls.append(('original',host))
            return {'mode':'real','success':False,'data':{},'error':{'code':'PERMISSION_DENIED'}}
    class Ephemeral:
        def __init__(self,c):
            assert not c.allow_writes and host in c.probe.allowed_private_targets
        def measure_latency_and_loss(self,**kwargs):
            calls.append(('ephemeral',kwargs['host']))
            return {'mode':'real','success':True,'data':{}}
    service=Service()
    service.available_tools={'measure_latency_and_loss':service.ping}
    bridge=ToolBridge(Settings(),service)
    bridge.traces.append({'tool':'get_network_snapshot','observed_monotonic':time.monotonic()-age,
                          'result':{'mode':'real','data':{'selected_interface':{'default_gateways_ipv4':['127.0.0.1' if host=='127.0.0.1' else '10.24.18.1']}}}})
    monkeypatch.setattr(netpet_tools,'ToolService',Ephemeral)
    bridge.functions(lambda _:None)[0](host=host)
    assert calls==[('ephemeral' if allowed else 'original',host)]
    assert config.allow_writes and host not in config.probe.allowed_private_targets


def test_tls_failure_preserves_validation_and_does_not_claim_handshake_success():
    target='https://school.example.test'
    trace={'tool':'probe_connectivity','arguments':{'target_url':target,'route':'direct'},
           'result':{'mode':'real','data':{'route':'direct','attempts':[
               {'probed_url':target,'http_status':None,'outcome':'error','error_detail':'SSL certificate verify failed'}]}}}
    report=build_diagnosis(DiagnosticRequest('检查网站',target),[trace],'real')
    layer=next(l for l in report['layers'] if l['name']=='TCP / TLS')
    assert '未关闭证书校验' in ' '.join(layer['facts']) and layer['status']=='warning'


def test_report_export_masks_credentials_urls_and_known_network_identity(tmp_path):
    report,_=diagnose('case_02_portal','检查网络 token=top-secret')
    report['traces'].append({'tool':'test','result':{'ssid':'HomePrivate','mac_address':'AA:BB:CC:DD:EE:FF','approval_token':'unique-secret','url':'https://user:pass@example.com/login?token=sensitive-query','ip':'192.168.1.32'}})
    report['headline']='HomePrivate 192.168.1.32 AA:BB:CC:DD:EE:FF'
    target=tmp_path/'report.html'
    export_report(report,target)
    text=target.read_text(encoding='utf-8')
    for secret in ['HomePrivate','AA:BB:CC:DD:EE:FF','unique-secret','user:pass','sensitive-query','192.168.1.32','top-secret']:
        assert secret not in text
    assert '192.168.1.x' in text and '课程模拟数据' in text
    assert report['headline'].startswith('HomePrivate')  # memory evidence unchanged


def test_ipv6_host_portion_is_masked_in_both_raw_data_and_observation_sentences():
    report,_=diagnose('case_05_incomplete','检查网络')
    content=json.dumps(redacted_report(report),ensure_ascii=False)
    assert '2001:da8:8000:18::99' not in content
    assert '主机部分已隐藏' in content


def test_export_escapes_untrusted_text_and_never_needs_external_resources(tmp_path):
    report,_=diagnose('case_01_normal','检查网络 <script>alert(1)</script>')
    text=report_html(report)
    assert '<script>alert(1)</script>' not in text and '&lt;script&gt;' in text
    assert '<script src=' not in text and '<link ' not in text
    target=tmp_path/'evidence.json'
    export_report(report,target)
    assert json.loads(target.read_text(encoding='utf-8'))['mode']=='mock'
    with pytest.raises(ValueError):export_report(report,tmp_path/'bad.exe')
