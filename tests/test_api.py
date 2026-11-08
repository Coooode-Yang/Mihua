import json
from dataclasses import asdict,replace

import httpx2
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialogButtonBox,QLineEdit

from app import ROOT,PetWindow,SpriteBank
from westie_agent.api_connection import api_error_message,cloud_tool_result,test_connection as check_connection
from westie_agent.api_smoke import run_protocol_smoke
from westie_agent.bridge import ToolBridge
from westie_agent.controller import AgentController
from westie_agent.credentials import SecretStore,validate_api_key
from westie_agent.engine import LocalAgent,AgentReply
from westie_agent.runtime import SetupRequired
from westie_agent.settings import Settings,normalize_api_url

FAKE_KEY='westie-unit-test-key-not-real'


def completion(message,reason='stop'):
    return {'id':'test','object':'chat.completion','created':1,'model':'deepseek-flash',
            'choices':[{'index':0,'message':message,'finish_reason':reason}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}


def test_default_cloud_configuration_and_custom_model_roundtrip(tmp_path):
    settings=Settings()
    assert settings.model_backend=='api' and settings.model=='deepseek-flash' and settings.api_base_url=='https://api.deepseek.com'
    settings=replace(settings,api_provider='compatible',model='user-model',api_base_url='https://provider.example/v1/chat/completions')
    settings.save(tmp_path)
    assert Settings.load(tmp_path)==settings and settings.api_base_url=='https://provider.example/v1'
    assert 'api_key' not in (tmp_path/'data/settings.json').read_text()


@pytest.mark.parametrize('url',['http://provider.example/v1','https://user:pass@example.com/v1','https://example.com/v1?key=secret',
                               'https://[bad','file:///C:/test','https://example.com/v1#key'])
def test_invalid_api_endpoint_cannot_expose_credentials(url):
    with pytest.raises(ValueError):normalize_api_url(url)


def test_legacy_local_config_is_explicit_not_default(tmp_path):
    (tmp_path/'data').mkdir();(tmp_path/'data/settings.json').write_text('{"model":"qwen3:4b"}')
    assert Settings.load(tmp_path).model_backend=='local'
    assert Settings().model_backend=='api'


def test_dpapi_roundtrip_endpoint_binding_and_no_plaintext_file(tmp_path):
    store=SecretStore(tmp_path)
    store.save('https://api.deepseek.com',FAKE_KEY,True)
    assert FAKE_KEY not in store.path.read_text(encoding='utf-8')
    assert SecretStore(tmp_path).load('https://api.deepseek.com/')==FAKE_KEY
    assert SecretStore(tmp_path).load('https://another.example/v1')==''
    store.clear();assert not store.path.exists() and not store.load('https://api.deepseek.com')


def test_session_only_key_does_not_remain_on_disk(tmp_path):
    store=SecretStore(tmp_path);store.save('https://api.deepseek.com',FAKE_KEY,True)
    store.save('https://api.deepseek.com',FAKE_KEY,False)
    assert not store.path.exists() and store.load('https://api.deepseek.com')==FAKE_KEY
    assert not SecretStore(tmp_path).load('https://api.deepseek.com')
    with pytest.raises(ValueError):validate_api_key('key\nwith-header-injection')


def test_environment_key_support_is_official_endpoint_only_and_never_written(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY',FAKE_KEY)
    store=SecretStore(tmp_path)
    assert store.load('https://api.deepseek.com')==FAKE_KEY
    assert store.load('https://api.deepseek.com/v1')==FAKE_KEY
    assert store.load('https://other.example/v1')==''
    assert not store.path.exists()


def test_saved_and_session_keys_take_priority_over_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','environment-dummy-not-real')
    store=SecretStore(tmp_path);store.save('https://api.deepseek.com',FAKE_KEY,True)
    assert SecretStore(tmp_path).load('https://api.deepseek.com')==FAKE_KEY
    store.save('https://api.deepseek.com','session-dummy-not-real',False)
    assert store.load('https://api.deepseek.com')=='session-dummy-not-real'


def test_invalid_environment_key_does_not_echo_secret(tmp_path,monkeypatch):
    invalid='invalid-key\nprivate-value';monkeypatch.setenv('DEEPSEEK_API_KEY',invalid)
    with pytest.raises(SetupRequired) as error:SecretStore(tmp_path).load('https://api.deepseek.com')
    assert invalid not in str(error.value) and 'private-value' not in str(error.value)


def test_cloud_tool_data_hides_identifiers_without_breaking_gateway_selection():
    original={'data':{'ssid':'PrivateNetwork','bssid':'AA:BB:CC:DD:EE:FF','approval_token':'private-token',
                      'default_gateways_ipv4':['10.24.18.1']},'summary':'PrivateNetwork AA:BB:CC:DD:EE:FF private-token'}
    result=cloud_tool_result(original)
    content=json.dumps(result,ensure_ascii=False)
    assert 'PrivateNetwork' not in content and 'private-token' not in content and 'AA:BB' not in content
    assert result['data']['default_gateways_ipv4']==['10.24.18.1'] and original['data']['ssid']=='PrivateNetwork'


def test_outgoing_cloud_tool_redaction_preserves_local_evidence():
    class Service:
        def snapshot(self)->dict:
            return {'mode':'mock','success':True,'data':{'ssid':'SecretCampus'}}
    service=Service();service.available_tools={'get_network_snapshot':service.snapshot}
    bridge=ToolBridge(Settings(tools_mode='mock'),service)
    result=bridge.functions(lambda _:None,for_cloud=True)[0]()
    assert result['data']['ssid']=='[已隐藏]' and bridge.traces[0]['result']['data']['ssid']=='SecretCampus'


def test_deepseek_wire_payload_has_correct_endpoint_and_no_ollama_parameters():
    requests=[]
    def handler(request):
        assert str(request.url)=='https://api.deepseek.com/chat/completions'
        assert request.headers['authorization']=='Bearer '+FAKE_KEY
        body=json.loads(request.content);requests.append(body)
        assert body['model']=='deepseek-flash' and body['thinking']=={'type':'disabled'}
        assert not any(k in body for k in ['think','keep_alive','options','reasoning_effort'])
        assert all(t['function'].get('strict') is not True for t in body.get('tools',[]))
        return httpx2.Response(200,json=completion({'role':'assistant','content':'早上好，今天也陪着你。'}))
    settings=Settings(tools_mode='mock')
    reply=LocalAgent(settings,ToolBridge(settings)).reply('给我一句早安问候',settings.api_base_url,lambda _:None,
        api_key=FAKE_KEY,api_transport=httpx2.MockTransport(handler))
    assert '早上好' in reply.text and len(requests)==1


def test_api_auth_error_is_safe_and_network_diagnosis_falls_back_locally():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx2.Response(401,json={'error':{'message':'server echoed '+FAKE_KEY,'type':'authentication_error'}})
    settings=Settings(tools_mode='mock',scenario_id='case_02_portal')
    agent=LocalAgent(settings,ToolBridge(settings))
    reply=agent.reply('检查校园网认证',settings.api_base_url,lambda _:None,api_key=FAKE_KEY,api_transport=httpx2.MockTransport(handler))
    assert reply.diagnosis['model_error'] and 'API Key 没有通过验证' in reply.text and '校园账号登录' in reply.text
    assert FAKE_KEY not in json.dumps(reply.diagnosis) and len(requests)==1  # SDK retries disabled
    with pytest.raises(SetupRequired) as error:
        agent.reply('给我一句早安问候',settings.api_base_url,lambda _:None,api_key=FAKE_KEY,api_transport=httpx2.MockTransport(handler))
    assert FAKE_KEY not in str(error.value) and '验证' in str(error.value)


def test_remote_redirect_is_not_followed_with_api_key():
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(307,headers={'Location':'https://unrelated.example/steal'},json={})
    settings=Settings()
    with pytest.raises(SetupRequired):
        LocalAgent(settings,ToolBridge(settings)).reply('给我一句问候',settings.api_base_url,lambda _:None,
            api_key=FAKE_KEY,api_transport=httpx2.MockTransport(handler))
    assert seen==['https://api.deepseek.com/chat/completions']


def test_complete_chat_and_agent_tool_roundtrip_via_loopback_protocol_fixture():
    report=run_protocol_smoke()
    assert report['connection_test']['tool_call_ok'] and report['agent_tool_calls']==2
    assert len(report['requests'])==5 and not report['external_api_called'] and not report['real_user_key_used']


def test_connection_test_rejects_chat_only_model():
    transport=httpx2.MockTransport(lambda request:httpx2.Response(200,json=completion({'role':'assistant','content':'hello'})))
    with pytest.raises(SetupRequired,match='没有完成工具调用'):check_connection(Settings(),FAKE_KEY,transport)


@pytest.fixture(scope='module')
def qt_app():return QApplication.instance() or QApplication([])


def wait(controller):
    for _ in range(300):
        QTest.qWait(10)
        if not controller.busy:return
    pytest.fail('worker did not finish')


def test_cloud_chat_does_not_start_local_runtime(qt_app,tmp_path,monkeypatch):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    controller.credentials.save(controller.settings.api_base_url,FAKE_KEY,False)
    monkeypatch.setattr(controller.runtime,'ensure_ready',lambda *a:pytest.fail('API mode must never load local GGUF'))
    received=[]
    class Agent:
        def reply(self,text,url,callback,api_key):
            received.append((url,api_key));return AgentReply('在线测试回复',[],[],'real',True)
    controller.agent=Agent()
    try:
        controller.submit('说一句欢迎语');wait(controller)
        assert received==[('https://api.deepseek.com',FAKE_KEY)]
        assert '在线测试回复' in pet.chat.history.toPlainText()
    finally:controller.close();pet.timer.stop();pet.chat.close();pet.close()


def test_settings_ui_tests_without_saving_then_saves_without_plaintext_key(qt_app,tmp_path,monkeypatch):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    received=[]
    def fake_test(options,key):
        received.append((options.model,key));return {'message':'连接成功，聊天和工具调用都通过了检查。'}
    monkeypatch.setattr('westie_agent.controller.test_connection',fake_test)
    try:
        pet.chat.settings_button.click();dialog=controller.dialog
        assert dialog.key.echoMode()==QLineEdit.EchoMode.Password
        dialog.model.setText('custom-deepseek-model');dialog.key.setText(FAKE_KEY);dialog.remember.setChecked(False)
        dialog.test_button.click();wait(controller)
        assert received==[('custom-deepseek-model',FAKE_KEY)] and '测试不保存' in dialog.status.text()
        assert controller.settings.model=='deepseek-flash' and not controller.credentials.path.exists()
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()
        assert controller.settings.model=='custom-deepseek-model' and controller.model_ready()
        assert not controller.credentials.path.exists() and not dialog.key.text()
        assert FAKE_KEY not in (tmp_path/'data/settings.json').read_text()
        assert not (tmp_path/'data/models').exists()
    finally:
        if controller.dialog:controller.dialog.close()
        controller.close();pet.timer.stop();pet.chat.close();pet.close()
