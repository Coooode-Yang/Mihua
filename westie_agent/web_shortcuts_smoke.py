"""Generic bookmark navigation and legacy migration using only temporary data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication, QLabel, QPushButton


def run_web_shortcuts_smoke(pet_factory, *, preview_dir=None):
    from . import controller as host
    from .campus import CampusCatalog
    from .campus_ui import EntryEditor
    original_browser = host.QDesktopServices
    opened = []
    class FakeBrowser:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())
            return True
    with TemporaryDirectory(prefix='bai-web-shortcuts-') as folder:
        base = Path(folder)
        custom = dict(id='mail',name='邮箱',url='https://mail.example.org/',note='',aliases=['邮箱'])
        legacy = dict(schema_version=1,school='旧学校',verified_on='2026-10-31',sources=[],entries=[
            dict(id='library',name='旧示例',url='https://library.bnuzh.edu.cn/',policy='normal',note='',aliases=[]),custom])
        path = base/'data/campus.json';path.parent.mkdir()
        original = json.dumps(legacy,ensure_ascii=False).encode('utf-8');path.write_bytes(original)
        pet = pet_factory();agent = host.AgentController(pet,base)
        agent.personal.timer.stop()
        host.QDesktopServices = FakeBrowser
        try:
            assert agent.catalog.data == {'schema_version':2,'entries':[custom]}
            backups = list(path.parent.glob('campus.before-web-shortcuts-v2-*.json'))
            assert len(backups)==1 and backups[0].read_bytes()==original
            agent.show_campus();window=agent.campus_dialog
            assert window.entries.count()==1 and '校园' not in window.windowTitle()
            assert not hasattr(window,'school') and not hasattr(window,'save_school')
            assert not any('北师珠' in b.text() for b in window.findChildren(QPushButton))
            assert not any('我的学校' in label.text() or '选择入口后' in label.text() for label in window.findChildren(QLabel))
            editor=EntryEditor(parent=window)
            assert editor.status.isHidden() and not editor.status.text()
            editor.name.setText('文献工作台');editor.url.setText('https://papers.example.org/');editor.aliases.setText('文献，论文')
            editor.validate_and_accept()
            assert editor.result()==EntryEditor.DialogCode.Accepted
            agent.catalog.save({'schema_version':2,'entries':[custom,editor.entry]})
            window.refresh();window.open_button.click()
            assert opened==['https://mail.example.org/'] and not agent.busy
            agent.submit('访问文献')
            assert opened[-1]=='https://papers.example.org/' and not agent.busy
            assert CampusCatalog(base).data['entries']==[custom,editor.entry]
            if preview_dir:
                from PySide6.QtTest import QTest
                output=Path(preview_dir);output.mkdir(parents=True,exist_ok=True)
                window.resize(650,600);QTest.qWait(20)
                window.grab().save(str(output/'web-shortcuts.png'))
                fresh=EntryEditor(parent=window);fresh.show();QTest.qWait(20)
                fresh.grab().save(str(output/'new-web-entry.png'));fresh.close()
            editor.close()
            return {'ok':True,'external_api_called':False,'real_browser_opened':False,
                    'generic_web_shortcuts_ui_ok':True,'legacy_examples_removed_ok':True,
                    'custom_urls_preserved_and_backed_up_ok':True,'no_school_controls_or_fixed_editor_note_ok':True,
                    'exact_url_and_keyword_navigation_ok':True}
        finally:
            host.QDesktopServices=original_browser
            agent.close();pet.timer.stop();pet.chat.close();pet.close();QApplication.processEvents()
