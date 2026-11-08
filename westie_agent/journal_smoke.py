"""Isolated installed-app workflow; authored notes, synthetic library, mock API."""
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from .controller import AgentController
from .journal_ai import JournalModel
from .journal_store import JournalStore
from .settings import Settings
from .workspace_refresh_smoke import create_sample_zotero,wait_reading
from .zotero import ZoteroLibrary


def mock_journal_transport(requests=None):
    import httpx2
    def handler(request):
        data=json.loads(request.content)
        assert 'tools' not in data and 'records' in data['messages'][0]['content']
        payload=json.loads(data['messages'][1]['content'])
        if requests is not None:requests.append(payload)
        result={'title':'今日科研回顾（模拟示例）','body':'\n\n'.join(f'[{r["id"]}] {r["title"]}：{r["body"]}' for r in payload['records']),
                'source_ids':[r['id'] for r in payload['records']]}
        return httpx2.Response(200,json={'id':'journal-mock','object':'chat.completion','created':0,'model':data['model'],
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],
            'usage':{'prompt_tokens':20,'completion_tokens':50,'total_tokens':70}})
    return httpx2.MockTransport(handler)


def wait_journal(controller):
    deadline=time.monotonic()+40
    while controller.journal.worker is not None and time.monotonic()<deadline:
        QTest.qWait(5);time.sleep(.01)
    assert controller.journal.worker is None,'journal worker did not finish'


def run_journal_smoke(pet_factory):
    with TemporaryDirectory(prefix='bai-journal-smoke-') as folder:
        base=Path(folder);directory=create_sample_zotero(base/'home/Zotero')
        original=(directory/'zotero.sqlite').read_bytes()
        pet=pet_factory();controller=AgentController(pet,base);controller.personal.timer.stop()
        controller.personal.zotero_library=ZoteroLibrary(base,home=base/'home',roaming=base/'roaming')
        journal=controller.journal;today=date.today().isoformat();requests=[]
        controller.settings=Settings(api_provider='compatible',api_base_url='https://journal.test',model='journal-mock-only')
        journal.model_factory=lambda settings:JournalModel(settings,'journal-test-key',mock_journal_transport(requests))
        try:
            assert not journal.store.database.exists()
            controller.personal.show_page('research');page=controller.personal.window.research;wait_reading(page)
            assert page.journal_shelf and page.note_button.isEnabled()
            page.note_button.click();editor=journal.editors[-1]
            editor.body.setPlainText('模拟文献笔记：这里记录自己的理解与尚未解决的疑问。')
            editor.reflection.setPlainText('下次阅读时核对作者的实验边界。');editor.save_button.click();QTest.qWait(10)
            rows=JournalStore(base).entries(today);assert len(rows)==1 and rows[0]['sources'][0]['url'].startswith('zotero://')
            assert '文献笔记 1 条' in page.journal_shelf.summary.text()
            pet.chat.history.clear();pet.chat.add_message('你','怎样设计对照实验？')
            pet.chat.add_message('米花','模拟回答：先明确变量，再记录条件。',markdown=True)
            cursor=QTextCursor(pet.chat.history.document());cursor.movePosition(QTextCursor.MoveOperation.End);pet.chat.history.setTextCursor(cursor)
            menu=pet.chat.history.conversation_menu()
            next(a for a in menu.actions() if a.text()=='连同问题收藏到科研日志').trigger();menu.deleteLater()
            editor=journal.editors[-1];assert '怎样设计对照实验' in editor.body.toPlainText()
            editor.save_button.click();QTest.qWait(10)
            journal.store.save(day=today,kind='meeting',title='组会记录（模拟）',body='讨论了验证方案。没有形成最终结论。',tags=['组会'])
            journal.changed.emit();assert len(journal.store.entries(today))==3
            pet.chat.history.clear_conversation();assert len(journal.store.entries(today))==3
            journal.show();window=journal.window;assert window.list.count()==3
            window.search.setText('验证方案');QTest.qWait(220);assert window.list.count()==1
            window.search.clear();QTest.qWait(220)
            journal.assist('day',today);wait_journal(controller)
            assert requests and len(requests[0]['records'])==3 and len(journal.store.entries(today))==3
            preview=journal.editors[-1];assert preview.kind.currentData()=='review'
            originals=journal.store.entries(today)
            # Save is explicit and does not edit any original row.
            preview.save_button.click();QTest.qWait(10)
            current=journal.store.entries(today);assert len(current)==4
            assert all(journal.store.get(row['id'])==row for row in originals)
            review=next(r for r in current if r['kind']=='review')
            source=review['sources'][0];before=source['snapshot']['body']
            first=originals[0]
            journal.store.save(**{k:first[k] for k in ('day','kind','title','reflection','tags','sources','attachments')},
                               body='修改后的文字',identifier=first['id'])
            assert journal.store.get(review['id'])['sources'][0]['snapshot']['body']==before
            journal.store.trash(first['id']);assert len(journal.store.entries(today))==3
            journal.store.trash(first['id'],False);assert len(journal.store.entries(today))==4
            output=journal.store.export(today,base/'daily.md');assert '科研日志' in output.read_text(encoding='utf-8')
            try:journal.store.export(today,output)
            except ValueError:pass
            else:raise AssertionError('export replaced existing file')
            draft=journal.edit(seed={'body':'未保存的模拟笔记'});draft.stash_draft();assert journal.store.drafts()
            # Exercise the actual Ctrl+V route in the frozen Windows app as well.
            clipboard=QApplication.clipboard();previous=clipboard.text()
            try:
                image=QImage(32,24,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.green)
                clipboard.setImage(image);draft.body.setFocus()
                QTest.keyClick(draft.body,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
                assert len(draft.pending)==1 and draft.body.toPlainText()=='未保存的模拟笔记'
                staged=Path(draft.pending[0]);draft.stash_draft()
                recovered=journal.store.drafts()[0];assert recovered['_pending']==[str(staged)] and staged.is_file()
                draft.save_button.click();QTest.qWait(10)
                saved=journal.store.get(draft.saved['id']);assert len(saved['attachments'])==1
                assert not staged.exists() and not journal.store.drafts()
                assert QImage(str(journal.store.asset(saved['attachments'][0]))).pixelColor(0,0)==Qt.GlobalColor.green
            finally:
                clipboard.setText(previous)
            assert (directory/'zotero.sqlite').read_bytes()==original
            from .journal_chat_smoke import run_chat_capture_smoke
            capture_checks=run_chat_capture_smoke(pet_factory)
            return {'ok':True,'external_api_called':False,'real_zotero_accessed':False,'mock_model_transport':True,
                **capture_checks,
                'research_shelf_and_paper_capture_ok':True,'chat_capture_survives_chat_clear_ok':True,
                'daily_search_and_restart_persistence_ok':True,'three_note_types_ok':True,
                'ai_preview_explicit_save_preserves_originals_ok':True,'ai_source_snapshot_survives_edit_ok':True,
                'trash_restore_and_no_overwrite_export_ok':True,'unsaved_draft_recovery_ok':True,
                'clipboard_image_ctrl_v_and_save_ok':True,'clipboard_image_draft_and_cleanup_ok':True,
                'zotero_database_unchanged':True,'model_requests':len(requests)}
        finally:
            controller.close();pet.timer.stop();pet.chat.close();pet.close();QTest.qWait(10)
