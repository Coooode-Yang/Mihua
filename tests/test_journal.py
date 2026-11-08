import asyncio
from datetime import date
import json
from pathlib import Path
import threading

import pytest
from PySide6.QtWidgets import QApplication

from app import ROOT,PetWindow,SpriteBank
from westie_agent.journal_ai import JournalModel,snapshot_payload,validate_result
from westie_agent.journal_smoke import mock_journal_transport,run_journal_smoke,wait_journal
from westie_agent.journal_store import JournalStore,safe_url
from westie_agent.settings import Settings


@pytest.fixture(scope='module')
def qt_app():
    instance=QApplication.instance() or QApplication([]);instance.setQuitOnLastWindowClosed(False);return instance


def add(store,**kwargs):
    return store.save(**(dict(day='2026-11-01',kind='reading',title='文献笔记',body='原始记录：实验还没有完成。')|kwargs))


def test_store_is_lazy_and_isolates_existing_data(tmp_path):
    data=tmp_path/'data';data.mkdir();old=data/'personal.sqlite';old.write_bytes(b'existing-user-data')
    store=JournalStore(tmp_path);assert store.entries()==[] and store.drafts()==[] and not store.root.exists()
    entry=add(store);assert JournalStore(tmp_path).get(entry['id'])==entry
    assert old.read_bytes()==b'existing-user-data'


def test_edit_moves_day_search_is_literal_and_trash_can_restore(tmp_path):
    store=JournalStore(tmp_path);entry=add(store,tags=['对照实验','对照实验'])
    assert len(store.entries(query='实验'))==1 and not store.entries(query='%')
    add(store,day='2026-10-31',kind='meeting',title='讨论',body='尚未确定')
    store.save(day='2026-11-02',kind='reading',body='保留疑问',identifier=entry['id'])
    assert not store.entries('2026-11-01') and store.entries('2026-11-02')[0]['created_at']==entry['created_at']
    store.trash(entry['id']);assert len(store.entries())==1 and len(store.entries(deleted=True))==1
    store.trash(entry['id'],False);assert len(store.entries())==2


@pytest.mark.parametrize('day',['../escape','2026-2-1','2026-02-30',''])
def test_rejects_invalid_dates(tmp_path,day):
    with pytest.raises(ValueError):JournalStore(tmp_path).save(day=day,kind='meeting',body='内容')


@pytest.mark.parametrize('url',['file:///C:/private','javascript:alert(1)','https://user:password@example.com','zotero://select/library/items/INVALID'])
def test_external_links_are_limited_to_web_or_paper(url):assert safe_url(url)==''


def test_image_copy_portable_export_and_no_overwrite(tmp_path):
    from PIL import Image
    source=tmp_path/'picture.png';Image.new('RGB',(20,15),'green').save(source)
    store=JournalStore(tmp_path);image=store.import_image(source)
    add(store,attachments=[image]);source.unlink()
    destination=tmp_path/'日志.md';store.export('2026-11-01',destination)
    assert (tmp_path/'日志-assets'/Path(image).name).read_bytes()==store.asset(image).read_bytes()
    assert '%E6%97%A5%E5%BF%97-assets' in destination.read_text(encoding='utf-8')
    with pytest.raises(ValueError):store.export('2026-11-01',destination)
    with pytest.raises(ValueError):store.asset('../picture.png')
    with pytest.raises(ValueError):store.reader_path('../private/paper.md')


def test_drafts_are_recoverable_and_not_published(tmp_path):
    store=JournalStore(tmp_path);identifier='a'*32
    store.save_draft(identifier,{'day':'2026-11-01','body':'还没保存的想法','title':''})
    assert not store.entries() and JournalStore(tmp_path).drafts()[0]['body']=='还没保存的想法'
    store.remove_draft(identifier);assert not store.drafts()


def test_stale_editor_cannot_overwrite_newer_saved_content(tmp_path):
    store=JournalStore(tmp_path);first=add(store)
    newer=store.save(day=first['day'],kind='reading',body='另一个窗口的修改',identifier=first['id'],expected_updated_at=first['updated_at'])
    with pytest.raises(ValueError,match='其他窗口'):
        store.save(day=first['day'],kind='reading',body='旧窗口的修改',identifier=first['id'],expected_updated_at=first['updated_at'])
    assert store.get(first['id'])==newer


def test_ai_payload_only_contains_selected_text_no_paths_or_images(tmp_path):
    store=JournalStore(tmp_path);row=add(store,sources=[{'title':'原文','directory':'C:/private/library','reader':'private/paper.md','url':'https://example.com/paper'}])
    payload=snapshot_payload([row],'day',row['day'],row['day']);serialized=json.dumps(payload)
    assert 'private' not in serialized and 'attachments' not in serialized and len(payload['records'])==1
    with pytest.raises(ValueError):snapshot_payload([dict(row,body='x'*51000)],'day',row['day'],row['day'])


@pytest.mark.parametrize('result',[{}, {'title':'标题','body':'正文','source_ids':[]},
    {'title':'标题','body':'[R999] 编造来源','source_ids':['R001']},
    {'title':'标题','body':'正文','source_ids':['R001','R001']}])
def test_ai_result_requires_complete_valid_source_ids(tmp_path,result):
    row=add(JournalStore(tmp_path));payload=snapshot_payload([row],'day',row['day'],row['day'])
    with pytest.raises(ValueError):validate_result(result,payload)


def test_mock_api_uses_real_sdk_without_tools(tmp_path):
    row=add(JournalStore(tmp_path));payload=snapshot_payload([row],'tidy',row['day'],row['day']);requests=[]
    result=JournalModel(Settings(api_provider='compatible',api_base_url='https://journal.test',model='mock'),
                        'test-key',mock_journal_transport(requests)).request(payload,threading.Event())
    assert result['source_ids']==['R001'] and requests==[payload]


def test_cancellation_interrupts_api_wait(tmp_path):
    import httpx2
    from westie_agent.paper_reader import ReaderCancelled
    cancel=threading.Event()
    async def handler(request):
        cancel.set();await asyncio.sleep(30)
    model=JournalModel(Settings(api_provider='compatible',api_base_url='https://journal.test',model='mock'),'test-key',httpx2.MockTransport(handler))
    row=add(JournalStore(tmp_path));payload=snapshot_payload([row],'day',row['day'],row['day'])
    with pytest.raises(ReaderCancelled):model.request(payload,cancel)


def test_full_journal_workflow(qt_app):
    report=run_journal_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
    assert report['ok'] and not report['external_api_called']


def test_failed_ai_and_missing_key_do_not_change_notes(qt_app,tmp_path):
    from westie_agent.controller import AgentController
    from westie_agent.runtime import SetupRequired
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False);agent=AgentController(pet,tmp_path)
    agent.personal.timer.stop();journal=agent.journal;row=add(journal.store)
    class Failure:
        def request(self,*args):raise ValueError('模拟模型失败')
    try:
        journal.model_factory=lambda settings:Failure()
        journal.assist('tidy',row['day'],row);wait_journal(agent)
        assert journal.store.get(row['id'])==row and '模拟模型失败' in journal.message and not journal.editors
        journal.model_factory=None;agent.credentials.load=lambda url:None
        journal.assist('day',row['day']);assert journal.worker is None and 'API Key' in journal.message
        assert journal.store.get(row['id'])==row
    finally:agent.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()


def test_reader_excerpt_retains_exact_version_and_source_after_switch(qt_app,tmp_path):
    from PySide6.QtCore import QUrl
    from westie_agent.controller import AgentController
    from westie_agent.reader_store import write_json
    from westie_agent.zotero import RecentPaper
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False);agent=AgentController(pet,tmp_path)
    agent.personal.timer.stop();paper=RecentPaper('测试文献','ATTACH01','PAPER001',1,None,'application/pdf',0,'模拟',3)
    directory=tmp_path/'library'
    def publish(text):
        staging=tmp_path/('staging-'+text);staging.mkdir()
        (staging/'paper.md').write_text('# '+text,encoding='utf-8')
        write_json(staging/'source_map.json',{'blocks':[]})
        return agent.reader.store.publish(staging,directory,paper,{'created_at':'2026-11-01T10:00:00','status':'complete'})
    try:
        first=publish('第一版');editor=agent.journal.capture_reader(directory,paper,first,'值得留下的译文')
        editor.save_button.click();qt_app.processEvents()
        note=agent.journal.store.entries()[0];publish('第二版')
        agent.journal.follow_link(QUrl(f'bai-reader:/{note["id"]}/0'))
        viewer=agent.reader.viewers[-1]
        assert viewer.artifact.markdown.resolve()==first.markdown.resolve() and '第一版' in viewer.browser.toPlainText()
        assert '值得留下的译文' in note['body'] and note['sources'][0]['url'].endswith('?page=3')
    finally:agent.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()


def test_week_scope_excludes_other_days_reviews_and_trash(qt_app,tmp_path):
    from westie_agent.controller import AgentController
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False);agent=AgentController(pet,tmp_path);agent.personal.timer.stop()
    journal=agent.journal;requests=[]
    try:
        for day in ('2026-10-25','2026-10-26','2026-10-28','2026-11-01','2026-11-02'):add(journal.store,day=day)
        add(journal.store,kind='review',body='旧回顾不应再次参与汇总')
        deleted=add(journal.store,title='已移走');journal.store.trash(deleted['id'])
        journal.model_factory=lambda settings:JournalModel(Settings(api_provider='compatible',api_base_url='https://journal.test',model='mock'),
            'test-key',mock_journal_transport(requests))
        journal.assist('week','2026-11-01');wait_journal(agent)
        assert [r['day'] for r in requests[0]['records']]==['2026-11-01','2026-10-28','2026-10-26']
        assert requests[0]['start']=='2026-10-26' and len(journal.editors)==1
    finally:agent.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()


def test_recovered_draft_keeps_text_and_optional_fields(qt_app,tmp_path):
    from westie_agent.controller import AgentController
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False);agent=AgentController(pet,tmp_path);agent.personal.timer.stop()
    try:
        editor=agent.journal.edit(seed={'body':'组会原始记录','kind':'meeting'})
        editor.tags.setText('方法，组会');editor.url.setText('https://example.com/paper');editor.stash_draft()
        draft=agent.journal.store.drafts()[0]
        restored=agent.journal.edit(seed=draft)
        assert restored.body.toPlainText()=='组会原始记录' and restored.url.text()=='https://example.com/paper'
        restored.save_button.click();qt_app.processEvents()
        assert not agent.journal.store.drafts() and len(agent.journal.store.entries())==1
        editor.saved=True;editor.accept()
    finally:agent.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()
