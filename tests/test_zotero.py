import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from westie_agent.zotero import RecentPaper, ZoteroLibrary
from westie_agent.zotero_ui import ResearchPage

NOW = 1_800_000_000


def create_library(path, modern=True):
    path.mkdir(parents=True)
    db = sqlite3.connect(path/'zotero.sqlite')
    db.executescript('''
        CREATE TABLE items(itemID INTEGER PRIMARY KEY, libraryID INT, key TEXT);
        CREATE TABLE libraries(libraryID INT, type TEXT);
        CREATE TABLE groups(libraryID INT, groupID INT);
        CREATE TABLE itemAttachments(itemID INT, parentItemID INT, contentType TEXT);
        CREATE TABLE itemData(itemID INT, fieldID INT, valueID INT);
        CREATE TABLE itemDataValues(valueID INT, value TEXT);
        CREATE TABLE fields(fieldID INT, fieldName TEXT);
        CREATE TABLE deletedItems(itemID INT);
        INSERT INTO libraries VALUES(1,'user'),(2,'group');
        INSERT INTO groups VALUES(2,2468);
        INSERT INTO fields VALUES(1,'title');
    ''')
    if modern: db.execute('ALTER TABLE itemAttachments ADD COLUMN lastRead INT')
    db.commit(); db.close()
    return path


def add_attachment(path, item_id, key, parent=None, library=1, last_read=NOW, title='示例文献', state=None, modern=True, deleted=False):
    with sqlite3.connect(path/'zotero.sqlite') as db:
        db.execute('INSERT INTO items VALUES(?,?,?)', (item_id, library, key))
        if modern: db.execute('INSERT INTO itemAttachments VALUES(?,?,?,?)', (item_id,parent,'application/pdf',last_read))
        else: db.execute('INSERT INTO itemAttachments VALUES(?,?,?)', (item_id,parent,'application/pdf'))
        db.execute('INSERT INTO itemData VALUES(?,1,?)', (item_id,item_id))
        db.execute('INSERT INTO itemDataValues VALUES(?,?)', (item_id,title))
        if deleted: db.execute('INSERT INTO deletedItems VALUES(?)', (item_id,))
    if state is not None:
        folder=path/'storage'/key; folder.mkdir(parents=True,exist_ok=True)
        file=folder/'.zotero-reader-state'; file.write_text(json.dumps(state),encoding='utf-8')
        import os
        os.utime(file,(NOW-600,NOW-600))


@pytest.fixture
def library(tmp_path):
    root = create_library(tmp_path/'home/Zotero')
    reader = ZoteroLibrary(tmp_path, home=tmp_path/'home', roaming=tmp_path/'roaming', clock=lambda: NOW)
    return root, reader


def test_recent_read_uses_open_time_dedups_parent_and_filters_trash(library):
    root, reader=library
    with sqlite3.connect(root/'zotero.sqlite') as db:
        db.execute("INSERT INTO items VALUES(100,1,'PARENT01')")
        db.execute('INSERT INTO itemData VALUES(100,1,100)')
        db.execute("INSERT INTO itemDataValues VALUES(100,'论文标题')")
    add_attachment(root,1,'ATTACH01',100,last_read=NOW-3*86400,state={'pageIndex':4})
    add_attachment(root,2,'ATTACH02',100,last_read=NOW-2*86400)
    add_attachment(root,3,'ATTACH03',last_read=NOW-20*86400)
    add_attachment(root,4,'ATTACH04',deleted=True)
    add_attachment(root,5,'ATTACH05',last_read=None)
    before=hashlib.sha256((root/'zotero.sqlite').read_bytes()).digest()
    result=reader.recent()
    assert len(result.papers)==2 and result.week_count==1 and result.month_count==2
    assert result.papers[0].title=='论文标题' and result.papers[0].attachment_key=='ATTACH02'
    assert before==hashlib.sha256((root/'zotero.sqlite').read_bytes()).digest()


def test_group_and_saved_page_generate_correct_zotero_link(library):
    root, reader=library
    add_attachment(root,1,'ATTACH01',library=2,state={'pageIndex':7})
    paper=reader.recent().papers[0]
    assert paper.open_url()=='zotero://open-pdf/groups/2468/items/ATTACH01?page=8'
    bad=RecentPaper('bad','../../x','ITEM0001',1,None,'application/pdf',NOW,'test')
    with pytest.raises(ValueError): bad.open_url()


def test_old_schema_uses_only_reader_state_not_metadata_date(tmp_path):
    root=create_library(tmp_path/'home/Zotero',modern=False)
    add_attachment(root,1,'ATTACH01',state={'pageIndex':0},modern=False)
    add_attachment(root,2,'ATTACH02',modern=False)
    reader=ZoteroLibrary(tmp_path,home=tmp_path/'home',roaming=tmp_path/'none',clock=lambda:NOW)
    result=reader.recent()
    assert len(result.papers)==1 and result.approximate_count==1
    assert result.papers[0].evidence=='阅读位置更新时间' and result.papers[0].page==1


def test_deleted_parent_excludes_children(library):
    root, reader=library
    with sqlite3.connect(root/'zotero.sqlite') as db:
        db.execute("INSERT INTO items VALUES(100,1,'PARENT01')")
        db.execute('INSERT INTO deletedItems VALUES(100)')
    add_attachment(root,1,'ATTACH01',100)
    assert not reader.recent().papers


def test_detects_custom_data_directory_from_profile_prefs(tmp_path):
    root=create_library(tmp_path/'custom library')
    roaming=tmp_path/'roaming'
    profile=roaming/'Zotero/Zotero/Profiles/main';profile.mkdir(parents=True)
    (profile.parent.parent/'profiles.ini').write_text('[Profile0]\nPath=Profiles/main\nIsRelative=1\nDefault=1\n',encoding='utf-8')
    (profile/'prefs.js').write_text('user_pref("extensions.zotero.useDataDir", true);\nuser_pref("extensions.zotero.dataDir", '+json.dumps(str(root))+');',encoding='utf-8')
    reader=ZoteroLibrary(tmp_path/'app',home=tmp_path/'home',roaming=roaming)
    assert reader.discover_directory()==root


def test_path_setting_reset_and_bad_configuration(library,tmp_path):
    root,reader=library
    reader.select_directory(root)
    assert reader.discover_directory()==root
    with pytest.raises(ValueError): reader.select_directory(tmp_path)
    reader.config.write_text('broken',encoding='utf-8')
    with pytest.raises(ValueError,match='设置'): reader.discover_directory()
    reader.reset_directory()
    assert reader.discover_directory()==root


def test_missing_and_corrupt_database_are_truthful(tmp_path):
    reader=ZoteroLibrary(tmp_path,home=tmp_path/'home',roaming=tmp_path/'none')
    with pytest.raises(ValueError,match='未找到'): reader.recent()
    directory=tmp_path/'home/Zotero'; directory.mkdir(parents=True)
    (directory/'zotero.sqlite').write_bytes(b'not database')
    with pytest.raises(ValueError,match='暂时无法读取'): reader.recent()


def test_research_page_loads_asynchronously_and_opens_selected_item(library, monkeypatch):
    root,reader=library
    add_attachment(root,1,'ATTACH01',state={'pageIndex':2})
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    page=ResearchPage(reader);page.show()
    opened=[]
    monkeypatch.setattr('westie_agent.zotero_ui.QDesktopServices.openUrl',lambda url: opened.append(url.toString()) or True)
    try:
        page.activate()
        deadline=time.monotonic()+5
        while page.worker is not None and time.monotonic()<deadline: QTest.qWait(10)
        assert page.worker is None and page.list.count()==1
        assert '最近 7 天 1 篇' in page.summary.text()
        page.continue_button.click()
        assert opened==['zotero://open-pdf/library/items/ATTACH01?page=3']
        assert '已请求' in page.status.text()
    finally:
        page.shutdown();page.close();app.processEvents()
