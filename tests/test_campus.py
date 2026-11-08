import copy
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMessageBox,QDialogButtonBox

from app import ROOT,PetWindow,SpriteBank
from westie_agent.campus import CampusCatalog,default_catalog,validated_url,validate_catalog
from westie_agent.campus_ui import CampusDialog,EntryEditor
from westie_agent.controller import AgentController


@pytest.fixture(scope='module')
def qt_app():
    return QApplication.instance() or QApplication([])


def web_entry(**changes):
    return dict(id='mail',name='邮箱',url='https://mail.example.org/',note='',aliases=['邮箱'])|changes


@pytest.mark.parametrize('url',['file:///C:/Windows/notepad.exe','javascript:alert(1)','ms-settings:network',
                              'https://user:pass@example.com','https://example.com:bad','https://[broken',
                              'https://example.com\\@evil.test','https://example.com\n/'])
def test_campus_rejects_non_web_or_credential_urls(url):
    with pytest.raises(ValueError):validated_url(url)


def test_any_website_can_be_saved_without_a_school_and_reloaded(tmp_path):
    catalog=CampusCatalog(tmp_path)
    assert catalog.data=={'schema_version':2,'entries':[]}
    custom=copy.deepcopy(catalog.data)
    custom['entries']=[web_entry()]
    catalog.save(custom)
    restored=CampusCatalog(tmp_path)
    assert 'school' not in restored.data and restored.entry('mail')['url']=='https://mail.example.org/'
    assert restored.match('打开邮箱')['id']=='mail'
    assert restored.match('访问邮箱')['id']=='mail'
    assert restored.match('检查邮箱打不开') is None
    assert restored.match('网络断了帮我联网') is None


def test_corrupt_catalog_is_not_overwritten_and_bad_save_preserves_previous(tmp_path):
    path=tmp_path/'data/campus.json';path.parent.mkdir();path.write_text('{broken',encoding='utf-8')
    catalog=CampusCatalog(tmp_path)
    assert catalog.problem and path.read_text()=='{broken'
    bad=default_catalog();bad['entries']=[web_entry(url='file:///test')]
    with pytest.raises(ValueError):catalog.save(bad)
    assert path.read_text()=='{broken'


def test_no_duplicate_ids_and_no_mutable_reference_to_live_entry(tmp_path):
    catalog=CampusCatalog(tmp_path)
    catalog.save(dict(schema_version=2,entries=[web_entry()]))
    selected=catalog.entry('mail');selected['url']='https://other.test'
    assert catalog.entry('mail')['url']=='https://mail.example.org/'
    bad=default_catalog();bad['entries']=[web_entry(),web_entry()]
    with pytest.raises(ValueError):validate_catalog(bad)


@pytest.mark.parametrize('policy',['normal','prefer_direct','school_vpn','proxy','unknown'])
def test_legacy_access_reminders_are_ignored_without_losing_websites(tmp_path,policy):
    path=tmp_path/'data/campus.json';path.parent.mkdir()
    path.write_text(json.dumps(dict(schema_version=2,entries=[web_entry(policy=policy)])),encoding='utf-8')
    catalog=CampusCatalog(tmp_path)
    assert not catalog.problem and catalog.entry('mail')==web_entry()
    catalog.save(catalog.data)
    assert 'policy' not in json.loads(path.read_text(encoding='utf-8'))['entries'][0]


def test_gui_edit_any_website_without_school_controls(qt_app,tmp_path):
    catalog=CampusCatalog(tmp_path)
    dialog=CampusDialog(catalog)
    editor=EntryEditor()
    try:
        editor.name.setText('文献工作台');editor.url.setText('https://papers.example.org/');editor.aliases.setText('文献，论文')
        editor.validate_and_accept()
        assert editor.result()==editor.DialogCode.Accepted
        data=copy.deepcopy(catalog.data);data['entries']=[editor.entry]
        assert dialog.save_data(data,editor.entry['id'])
        restored=CampusCatalog(tmp_path)
        assert 'school' not in restored.data and restored.match('打开文献')['url']=='https://papers.example.org/'
        assert dialog.selected()['name']=='文献工作台' and not hasattr(dialog,'school')
        assert not hasattr(editor,'policy') and 'policy' not in editor.entry
    finally:dialog.close();editor.close()


def test_campus_opens_exact_url_without_model_or_network_write(qt_app,tmp_path,monkeypatch):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    controller.catalog.save(dict(schema_version=2,entries=[web_entry()]))
    opened=[]
    monkeypatch.setattr('westie_agent.controller.QDesktopServices.openUrl',lambda url:opened.append(url.toString()) or True)
    monkeypatch.setattr(controller.runtime,'ensure_ready',lambda *a:pytest.fail('navigation must not load model'))
    try:
        pet.handle_text('打开邮箱')
        assert opened==['https://mail.example.org/']
        assert not controller.busy and '请求默认浏览器' in pet.chat.history.toPlainText()
        pet.chat.campus_button.click()
        assert controller.campus_dialog and controller.campus_dialog.entries.count()==1
        menu = pet.make_menu()
        toolbox = next(a.menu() for a in menu.actions() if a.text() == '工具箱')
        assert any(a.text() == '网页快捷入口 · 带路小鸡' for a in toolbox.actions())
    finally:
        if controller.campus_dialog:controller.campus_dialog.close()
        controller.close();pet.timer.stop();pet.chat.close();pet.close()


def test_old_proxy_entry_opens_directly_without_an_access_prompt(qt_app,tmp_path,monkeypatch):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    opened=[]
    monkeypatch.setattr('westie_agent.controller.QDesktopServices.openUrl',lambda url:opened.append(url.toString()) or True)
    monkeypatch.setattr(QMessageBox,'exec',lambda self:pytest.fail('access reminders have been removed'))
    try:
        controller.catalog.save(dict(schema_version=2,entries=[web_entry(policy='proxy')]))
        controller.open_campus_entry('mail')
        assert opened==['https://mail.example.org/'] and not controller.busy
        assert '代理提醒' not in pet.chat.history.toPlainText()
    finally:controller.close();pet.timer.stop();pet.chat.close();pet.close()


def test_legacy_migration_removes_examples_and_school_but_keeps_custom_urls_with_backup(tmp_path):
    path=tmp_path/'data/campus.json';path.parent.mkdir()
    custom=web_entry(id='portal',name='我的网站',url='https://mis.bjtu.edu.cn/')
    legacy={'schema_version':1,'school':'旧学校','verified_on':'2026-10-31',
            'sources':[{'title':'旧资料','url':'https://www.bnuzh.edu.cn/'}],
            'entries':[custom,web_entry(id='library',url='https://library.bnuzh.edu.cn/')]}
    original=json.dumps(legacy,ensure_ascii=False).encode('utf-8');path.write_bytes(original)
    catalog=CampusCatalog(tmp_path)
    assert not catalog.problem and catalog.data=={'schema_version':2,'entries':[custom]}
    assert json.loads(path.read_text(encoding='utf-8'))==catalog.data
    backups=list(path.parent.glob('campus.before-web-shortcuts-v2-*.json'))
    assert len(backups)==1 and backups[0].read_bytes()==original
    assert CampusCatalog(tmp_path).data==catalog.data
    assert len(list(path.parent.glob('campus.before-web-shortcuts-v2-*.json')))==1


def test_a_user_can_explicitly_save_any_website_in_v2_even_a_former_example(tmp_path):
    catalog=CampusCatalog(tmp_path)
    entry=web_entry(url='https://www.bnuzh.edu.cn/')
    catalog.save({'schema_version':2,'entries':[entry]})
    assert CampusCatalog(tmp_path).data['entries']==[entry]


def test_removed_network_features_have_no_widgets_or_guardian(qt_app,tmp_path):
    from PySide6.QtWidgets import QPushButton
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    try:
        assert not hasattr(controller, 'guardian')
        assert not any(b.text() in {'网络守护','网络检查','导出排障报告','排查原因'} for b in pet.chat.findChildren(QPushButton))
        controller.show_campus()
        assert not hasattr(controller.campus_dialog, 'check_button')
    finally:
        if controller.campus_dialog: controller.campus_dialog.close()
        controller.close();pet.timer.stop();pet.chat.close();pet.close()
