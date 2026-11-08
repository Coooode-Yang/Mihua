import json

import pytest
from PySide6.QtWidgets import QApplication

from app import ROOT,PetWindow,SpriteBank
from westie_agent.bridge import ToolBridge
from westie_agent.controller import AgentController
from westie_agent.diagnostics import diagnostic_request,EvidenceCollector,build_diagnosis,diagnosis_text,diagnosis_details_text
from westie_agent.reporting import export_report
from westie_agent.settings import Settings


def scenario_report(scenario,question):
    bridge=ToolBridge(Settings(tools_mode='mock',scenario_id=scenario))
    request=diagnostic_request(question)
    complete=EvidenceCollector(bridge,request).complete()
    return build_diagnosis(request,bridge.traces,'mock',missing=complete['missing'])


@pytest.mark.parametrize('scenario,question,action',[
    ('case_01_normal','检查网络','把网址发给我'),
    ('case_02_portal','检查校园网认证','校园账号登录'),
    ('case_03_loss','检查十次丢包和 example.com 解析','暂停正在进行的大文件下载'),
    ('case_04_proxy_conflict','检查 https://jwxt.campus.edu.test','先暂停它，再刷新'),
    ('case_05_incomplete','检查网络','先用浏览器打开'),
    ('case_06_forbidden','检查 https://paper-db.campus.edu.test/restricted','确认已用学校账号登录'),
])
def test_default_guidance_is_short_actionable_and_does_not_dump_analysis(scenario,question,action):
    report=scenario_report(scenario,question)
    before=json.dumps(report,ensure_ascii=False)
    text=diagnosis_text(report)
    assert action in text and '不是你的电脑实测' in text
    for jargon in ['HTTP','DNS','TCP','TLS','DHCP','APIPA','RSSI','PAC','TUN','TIMEOUT','分层检查','尚未确认：','导出排障报告','证据：']:
        assert jargon not in text
    assert len(text)<450 and text.count('\n1.')<=1 and '\n3.' not in text
    assert '已修复' not in text and '已恢复' not in text
    assert json.dumps(report,ensure_ascii=False)==before


def test_professional_evidence_remains_in_export_while_chat_is_plain(tmp_path):
    report=scenario_report('case_04_proxy_conflict','检查 https://jwxt.campus.edu.test')
    export_report(report,tmp_path/'report.html')
    assert 'HTTP 502' in (tmp_path/'report.html').read_text(encoding='utf-8')
    assert 'HTTP 502' in diagnosis_details_text(report)
    assert 'HTTP 502' not in diagnosis_text(report)


def test_no_connection_gives_manual_steps_without_removed_recovery_button():
    traces=[{'tool':'get_network_snapshot','arguments':{},'result':{'mode':'real','success':True,'data':{
        'selected_interface':{'status':'disconnected'}}}}]
    report=build_diagnosis(diagnostic_request('检查网络'),traces,'real')
    text=diagnosis_text(report)
    assert '电脑目前没有连上网络' in text and 'Windows 右下角的网络图标' in text
    assert '帮我联网' not in text
    assert '已打开' not in text


def test_policy_restriction_cannot_be_reported_as_user_access_block():
    traces=[{'tool':'probe_connectivity','arguments':{'target_url':'https://school.test'},
        'result':{'mode':'real','success':False,'data':{},'error':{'code':'PROBE_POLICY_RESTRICTION'}}}]
    report=build_diagnosis(diagnostic_request('检查 https://school.test'),traces,'real')
    text=diagnosis_text(report)
    assert '米花没能完成网页检查' in text and '先用浏览器' in text
    assert '管理员' not in text and '被封禁' not in text


def test_removed_status_areas_stay_hidden_after_updates(tmp_path):
    app=QApplication.instance() or QApplication([])
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    try:
        controller.personal.show_page('chat')
        controller.show_campus()
        controller.campus_dialog.status.setText('已请求浏览器打开；详细访问提示')
        app.processEvents()
        assert not hasattr(pet.chat, 'guardian_status')
        assert controller.campus_dialog.status.isHidden()
        # Generic shortcut feedback is transient; assigning old diagnostic
        # text must not bring back the removed persistent status panel.
        controller.campus_dialog.show_status('已保存')
        assert controller.campus_dialog.status.isVisible()
        controller.campus_dialog.status_timer.timeout.emit()
        assert controller.campus_dialog.status.isHidden()
        assert not hasattr(controller, 'guardian')
    finally:
        if controller.campus_dialog:controller.campus_dialog.close()
        controller.close();pet.timer.stop();pet.chat.close();pet.close()
