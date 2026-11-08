"""Isolated Qt and Zotero-reading checks, with no user's library or paid API."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from .controller import AgentController
from .zotero import ZoteroLibrary


def create_sample_zotero(directory, now=None):
    now = now or time.time()
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory/'zotero.sqlite')) as db:
        db.executescript('''
            CREATE TABLE items(itemID INTEGER PRIMARY KEY, libraryID INT, key TEXT);
            CREATE TABLE libraries(libraryID INT, type TEXT);
            CREATE TABLE groups(libraryID INT, groupID INT);
            CREATE TABLE itemAttachments(itemID INT, parentItemID INT, contentType TEXT, lastRead INT);
            CREATE TABLE itemData(itemID INT, fieldID INT, valueID INT);
            CREATE TABLE itemDataValues(valueID INT, value TEXT);
            CREATE TABLE fields(fieldID INT, fieldName TEXT);
            CREATE TABLE deletedItems(itemID INT);
            INSERT INTO libraries VALUES(1,'user'),(2,'group');
            INSERT INTO groups VALUES(2,2468);
            INSERT INTO fields VALUES(1,'title');
        ''')
        for index, title, days, library in [(1,'平台责任与个人信息保护：研究笔记',0,1),
                                            (2,'合同解释中的信赖保护',3,1),
                                            (3,'Comparative Perspectives on Legal Reasoning',20,2)]:
            db.execute('INSERT INTO items VALUES(?,?,?)',(index,library,f'PAPER00{index}'))
            db.execute('INSERT INTO items VALUES(?,?,?)',(index+10,library,f'ATTACH0{index}'))
            db.execute('INSERT INTO itemData VALUES(?,1,?)',(index,index))
            db.execute('INSERT INTO itemDataValues VALUES(?,?)',(index,title))
            db.execute('INSERT INTO itemAttachments VALUES(?,?,?,?)',
                       (index+10,index,'application/pdf',int(now-days*86400)))
            folder = directory/'storage'/f'ATTACH0{index}'; folder.mkdir(parents=True)
            (folder/'.zotero-reader-state').write_text(json.dumps({'pageIndex':index*3-1}),encoding='utf-8')
        db.commit()
    return directory


def wait_reading(page):
    deadline = time.monotonic()+8
    while page.worker is not None and time.monotonic()<deadline:
        QTest.qWait(10)
    assert page.worker is None


def run_workspace_refresh_smoke(pet_factory):
    with TemporaryDirectory(prefix='bai-workspace-refresh-') as folder:
        base = Path(folder)
        now = time.time()
        directory = create_sample_zotero(base/'home/Zotero', now)
        library = ZoteroLibrary(base, home=base/'home', roaming=base/'roaming', clock=lambda:now)
        original = (directory/'zotero.sqlite').read_bytes()
        pet = pet_factory()
        controller = AgentController(pet, base)
        controller.personal.zotero_library = library
        controller.personal.timer.stop()
        try:
            pet.chat.history.clear()
            for index in range(12):
                pet.chat.add_message('你', f'示例问题 {index+1}')
                pet.chat.add_message('米花', '简短解释。\n'*10)
            controller.personal.show_page('chat'); QTest.qWait(20)
            bar = pet.chat.history.verticalScrollBar()
            assert len(bar.marker_positions()) == 12 and bar.maximum()>0
            pet.chat.history.jump_to_question(0)
            assert bar.value()<100
            old = controller.agent
            old.history.append('synthetic old context')
            menu = pet.chat.history.conversation_menu()
            next(a for a in menu.actions() if a.text()=='清空对话记录').trigger()
            assert not pet.chat.history.toPlainText() and not pet.chat.history.questions
            assert controller.agent is not old and not controller.agent.history
            menu.deleteLater()
            controller.personal.show_page('research')
            window = controller.personal.window
            wait_reading(window.research)
            assert window.research.list.count()==3 and '最近 7 天 2 篇' in window.research.summary.text()
            paper = window.research.list.item(0).data(Qt.ItemDataRole.UserRole)
            assert paper.open_url()=='zotero://open-pdf/library/items/ATTACH01?page=3'
            assert not hasattr(window,'research_task') and window.status.isHidden()
            assert window.category.findText('研究') == -1
            window.show_page('study')
            assert not hasattr(window,'task_combo') and not hasattr(window,'task_focus_button')
            window.start_button.click()
            assert controller.personal.focus.snapshot()['task_id'] is None
            assert (directory/'zotero.sqlite').read_bytes()==original
            return {'ok':True,'external_api_called':False,'real_zotero_accessed':False,
                    'question_markers_and_jump_ok':True,'clear_transcript_and_context_ok':True,
                    'research_recent_stats_ok':True,'zotero_resume_uri_and_page_ok':True,
                    'zotero_database_unchanged':True,'research_todo_removed_ok':True,'focus_unlinked_ok':True,
                    'chat_controls_removed_ok':not hasattr(pet.chat,'focus_summary')}
        finally:
            controller.close(); pet.timer.stop(); pet.chat.close(); pet.close()
