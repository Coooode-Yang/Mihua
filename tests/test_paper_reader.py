import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import threading

import pytest
from PySide6.QtWidgets import QApplication

from app import ROOT,PetWindow,SpriteBank
from westie_agent.paper_reader import (ReaderCancelled,ReaderGenerator,ReaderModel,batches,is_reader_command,validate_translation)
from westie_agent.reader_extract import extract_pdf
from westie_agent.reader_smoke import mock_reader_transport,prepare_reader_library,run_reader_smoke
from westie_agent.reader_store import ReaderStore
from westie_agent.reader_ui import ReaderBrowser
from westie_agent.settings import Settings
from westie_agent.zotero import RecentPaper,ZoteroLibrary


@pytest.fixture(scope='module')
def qt_app():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.mark.parametrize('text',[
    '使用naturre reader阅读这篇文献','使用 nature-reader 阅读这篇论文','请使用 nature reader 解读这篇文献',
    '帮我用 NATURE_READER 精读这篇论文',
])
def test_explicit_reader_commands(text): assert is_reader_command(text)


@pytest.mark.parametrize('text',['nature是指什么','如何使用 nature-reader 阅读文献？','解释reader工具','阅读这篇文献'])
def test_normal_questions_do_not_upload_papers(text): assert not is_reader_command(text)


@pytest.fixture
def extracted(tmp_path):
    extract_pdf(ROOT/'assets/reader-samples/sample-paper.pdf',tmp_path)
    return tmp_path,json.loads((tmp_path/'source_map.json').read_text(encoding='utf-8'))


def test_pdf_extraction_covers_both_pages_and_real_crops(extracted):
    directory,data=extracted
    assert len(data['pages'])==2 and data['equations'] and len(data['figures'])>=2
    assert 'Methods and limitations' in ' '.join(b['original_text'] for b in data['blocks'])
    assert 'Data availability' in ' '.join(b['original_text'] for b in data['blocks'])
    ids=[b['id'] for b in data['blocks']]
    assert len(ids)==len(set(ids))
    from PIL import Image
    for item in data['figures']+data['equations']:
        image=directory/item['image_path']
        assert image.is_file()
        with Image.open(image) as raster:
            assert raster.width>5 and raster.height>5
            assert raster.height<1000  # Crops are not substituted with full pages.


def test_scanned_pdf_is_marked_without_fabricating_text(tmp_path):
    extract_pdf(ROOT/'assets/reader-samples/scanned-paper.pdf',tmp_path)
    data=json.loads((tmp_path/'source_map.json').read_text(encoding='utf-8'))
    assert not data['blocks'] and len(data['pages'])==1
    assert any('OCR' in note for note in data['notes'])


def test_corrupt_pdf_is_rejected(tmp_path):
    source=tmp_path/'bad.pdf';source.write_bytes(b'not a PDF')
    with pytest.raises(ValueError):extract_pdf(source,tmp_path/'output')


@pytest.mark.parametrize('reply',[
    {},{'translations':[]},{'translations':[{'id':'wrong','translation':'译文'}]},
    {'translations':[{'id':'S001','translation':''}]},
    {'translations':[{'id':'S001','translation':'译文'},{'id':'S001','translation':'重复'}]},
    {'translations':[{'id':'S001','translation':'![读取](file:///private)'}]},
])
def test_translation_results_must_match_every_source_block(reply):
    with pytest.raises(ValueError):validate_translation(reply,[{'id':'S001','original_text':'Source paragraph.'}])


def test_api_mock_carries_skill_and_source_without_tools():
    settings=Settings(api_provider='compatible',api_base_url='https://reader.test',model='reader-mock-only')
    gateway=ReaderModel(settings,'test-key-reader',ReaderGenerator(ROOT).guidance(),mock_reader_transport())
    payload={'blocks':[{'id':'S001','page':1,'text':'A controlled experiment.'}]}
    reply=gateway.request(payload,threading.Event())
    assert set(validate_translation(reply,[{'id':'S001','original_text':'A controlled experiment.'}]))=={'S001'}
    assert gateway.requests==1


def test_cancel_during_network_await_does_not_wait_for_timeout():
    import httpx2
    cancel=threading.Event()
    async def handle(request):
        cancel.set();await asyncio.sleep(10)
        return httpx2.Response(500)
    gateway=ReaderModel(Settings(),'test-key-reader','reviewed skill',httpx2.MockTransport(handle))
    with pytest.raises(ReaderCancelled):gateway.request({'blocks':[]},cancel)


def test_failure_and_cancel_preserve_grounded_draft(tmp_path):
    library,directory,pdf=prepare_reader_library(tmp_path,ROOT/'assets')
    paper=library.recent().papers[0]
    class Failing:
        requests=1
        def request(self,*args):raise ValueError('测试中的模型错误')
    generator=ReaderGenerator(tmp_path)
    first=generator.run(pdf,paper,directory,Failing(),threading.Event())
    assert first.error and first.artifact.metadata['status']=='failed'
    assert '待翻译' in first.artifact.markdown.read_text(encoding='utf-8')
    class Cancelled:
        requests=0
        def request(self,payload,cancel):
            self.requests+=1
            if self.requests>1:raise ReaderCancelled()
            return {'translations':[{'id':b['id'],'translation':'本段是软件测试的已完成译文，用来验证用户取消之后，程序仍然保留已经完成的原文对应段落和完整的来源编号。'+b['text']} for b in payload['blocks']],
                    'insight':'测试解读提示。'}
    second=generator.run(pdf,paper,directory,Cancelled(),threading.Event())
    assert second.cancelled and second.artifact.metadata['status']=='cancelled'
    assert 0<second.artifact.metadata['translated_blocks']<second.artifact.metadata['total_blocks']
    assert len(generator.store.versions(directory,paper))==2
    assert first.artifact.markdown.is_file()


def test_store_does_not_confuse_libraries_or_versions(tmp_path):
    paper=RecentPaper('title','ATTACH01','PARENT01',1,None,'application/pdf',1,'test')
    store=ReaderStore(tmp_path)
    assert store.document_root(tmp_path/'library-a',paper)!=store.document_root(tmp_path/'library-b',paper)
    assert store.document_root(tmp_path/'a',paper)!=store.document_root(tmp_path/'a',replace(paper,library_id=2))
    with pytest.raises(ValueError):store.document_root(tmp_path,replace(paper,item_key='../../private'))


def test_zotero_resolution_is_read_only_and_blocks_storage_escape(tmp_path):
    from contextlib import closing
    import sqlite3
    library,directory,pdf=prepare_reader_library(tmp_path,ROOT/'assets')
    paper=library.recent().papers[0]
    before=(directory/'zotero.sqlite').read_bytes()
    assert library.resolve_pdf(paper)==pdf
    assert before==(directory/'zotero.sqlite').read_bytes()
    with closing(sqlite3.connect(directory/'zotero.sqlite')) as db:
        db.execute("UPDATE itemAttachments SET path='storage:../../private.pdf' WHERE itemID=11");db.commit()
    with pytest.raises(ValueError,match='路径'):library.resolve_pdf(paper)


def test_zotero_absolute_and_relative_linked_attachments(tmp_path):
    from contextlib import closing
    import sqlite3
    library,directory,pdf=prepare_reader_library(tmp_path,ROOT/'assets')
    paper=library.recent().papers[0]
    with closing(sqlite3.connect(directory/'zotero.sqlite')) as db:
        db.execute('UPDATE itemAttachments SET path=?,linkMode=2 WHERE itemID=11',(str(pdf),));db.commit()
    assert library.resolve_pdf(paper)==pdf
    profile=library.roaming/'Zotero/Zotero/Profiles/test';profile.mkdir(parents=True)
    (profile.parent.parent/'profiles.ini').write_text('[Profile0]\nPath=Profiles/test\nIsRelative=1\n',encoding='utf-8')
    (profile/'prefs.js').write_text('user_pref("extensions.zotero.dataDir", '+json.dumps(str(directory))+');\n'+
        'user_pref("extensions.zotero.baseAttachmentPath", '+json.dumps(str(pdf.parent))+');',encoding='utf-8')
    with closing(sqlite3.connect(directory/'zotero.sqlite')) as db:
        db.execute("UPDATE itemAttachments SET path='attachments:sample.pdf',linkMode=2 WHERE itemID=11");db.commit()
    assert library.resolve_pdf(paper)==pdf


def test_viewer_cannot_load_external_or_other_local_resources(qt_app,tmp_path):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage,QTextDocument
    browser=ReaderBrowser();browser.root=tmp_path
    assets=tmp_path/'assets';assets.mkdir()
    image=QImage(20,20,QImage.Format.Format_RGB32);image.fill(0xffffffff)
    image.save(str(assets/'safe.png'));image.save(str(tmp_path/'private.png'))
    kind=QTextDocument.ResourceType.ImageResource
    assert browser.loadResource(kind,QUrl('https://example.test/image.png')) is None
    assert browser.loadResource(kind,QUrl.fromLocalFile(str(tmp_path/'private.png'))) is None
    assert not browser.loadResource(kind,QUrl.fromLocalFile(str(assets/'safe.png'))).isNull()
    browser.close()


def test_full_selected_paper_command_and_history_workflow(qt_app):
    report=run_reader_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False),ROOT/'assets')
    assert report['ok'] and not report['external_api_called'] and not report['real_zotero_accessed']


def test_selected_article_cannot_change_during_generation(qt_app,tmp_path):
    import time
    from PySide6.QtTest import QTest
    from westie_agent.controller import AgentController
    from westie_agent.workspace_refresh_smoke import wait_reading
    from westie_agent.reader_smoke import wait_reader
    library,directory,pdf=prepare_reader_library(tmp_path,ROOT/'assets')
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    controller.personal.zotero_library=library;controller.personal.timer.stop()
    entered,release=threading.Event(),threading.Event()
    class Holding:
        requests=0
        def request(self,payload,cancel):
            entered.set();assert release.wait(5)
            self.requests+=1
            return {'translations':[{'id':b['id'],'translation':'这是测试接口提供的逐段中文对照。测试需要保留全部段落、稳定来源编号和已经生成的内容，确保在用户切换选择时解读仍属于原始文献。'+b['text']} for b in payload['blocks']],
                    'insight':'测试中的阅读提示。'}
    controller.reader.model_factory=lambda *args:Holding()
    try:
        controller.personal.show_page('research');page=controller.personal.window.research;wait_reading(page)
        first=page.selected_paper();page.generate_reader.click()
        deadline=time.monotonic()+5
        while not entered.is_set() and time.monotonic()<deadline:QTest.qWait(5);time.sleep(.01)
        assert entered.is_set()
        page.list.setCurrentRow(1)
        second=page.selected_paper();release.set();wait_reader(controller)
        assert controller.reader.store.latest(directory,first)
        assert not controller.reader.store.latest(directory,second) and not page.view_reader.isEnabled()
        page.list.setCurrentRow(0);assert page.view_reader.isEnabled()
    finally:
        release.set();controller.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()


def test_missing_key_never_reads_pdf_or_calls_model(qt_app,tmp_path,monkeypatch):
    from westie_agent.controller import AgentController
    from westie_agent.reader_smoke import wait_reader
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path);controller.personal.timer.stop()
    monkeypatch.setattr(controller.credentials,'load',lambda *args:'')
    monkeypatch.setattr(controller.personal.zotero_library,'resolve_pdf',lambda *args:pytest.fail('No PDF access before a configured key'))
    try:
        controller.submit('使用nature-reader阅读这篇文献');wait_reader(controller)
        assert 'API Key' in controller.reader.message and not (tmp_path/'data/paper-readers').exists()
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()
