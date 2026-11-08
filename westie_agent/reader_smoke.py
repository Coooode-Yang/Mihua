"""Synthetic PDF, mocked model transport, real subprocess extraction and Qt UI."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from tempfile import TemporaryDirectory
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from .controller import AgentController
from .paper_reader import ReaderGenerator, ReaderModel
from .settings import Settings
from .workspace_refresh_smoke import create_sample_zotero,wait_reading
from .zotero import ZoteroLibrary


def mock_reader_transport():
    import httpx2
    sentences={
        'A Synthetic Study of Signal Gain':'信号增益的模拟研究',
        'This authored sample tests bilingual reading.':'本自制示例用于测试中英文对照阅读。',
        'It is not a real research publication.':'这不是真实的研究出版物。',
        'We preserve source locations and report uncertainty.':'我们保留来源位置，并标明不确定性。',
        'We measure signal gain in a controlled experiment.':'我们在受控实验中测量信号增益。',
        'The input power is 2 watts.':'输入功率为 2 瓦。',
        'The output power is 6 watts.':'输出功率为 6 瓦。',
        'The result supports a gain of 3 for this setup.':'对于这一实验装置，结果支持增益为 3。',
        'This observation does not establish performance under other conditions.':'这一观察并不能证明其他条件下的性能。',
        'The method compares the input and output under the same conditions.':'该方法比较相同条件下的输入与输出。',
        'Figure 1 shows the measured signal.':'图 1 展示所测信号。',
        'Table 1 records the two power measurements.':'表 1 记录两次功率测量。',
        'The equation defines the ratio.':'该公式定义二者的比值。',
        'No additional experiments are claimed.':'这里并未声称进行了其他实验。',
        'Fig. 1. Signal measured in the authored example.':'图 1. 自制示例中测得的信号。',
        'Table 1. Power measurements.':'表 1. 功率测量。',
        'The sample is designed to exercise figure crops, table crops, equation images and stable page anchors.':'该示例用于验证插图裁切、表格裁切、公式原图和稳定的页码锚点。',
        'Readers should inspect the original evidence before drawing conclusions.':'读者应在得出结论之前检查原始证据。',
        'Methods and limitations':'方法与局限',
        'The evidence is limited to an illustrative example.':'证据仅限于一个演示示例。',
        'All text and graphics were authored for testing.':'全部文字和图形均为测试而创作。',
        'This page checks that the reader does not stop after the first page.':'本页用于检查阅读器不会只处理第一页。',
        'The original source file must remain unchanged after processing.':'处理后，原始文件必须保持不变。',
        'Data availability: This synthetic sample contains no personal data.':'数据可用性：本合成示例不包含个人数据。',
        'Code availability: The extraction and validation workflow is part of the desktop application.':'代码可用性：提取与验证流程是桌面程序的一部分。',
        'Competing interests: This is a software test fixture.':'利益冲突：这是一个软件测试样本。',
        'Quantity':'物理量','Watts':'瓦','Input':'输入','Output':'输出',
    }
    def handle(request):
        payload=json.loads(request.content)
        assert 'nature-reader' in payload['messages'][0]['content']
        assert 'tools' not in payload and len(payload['messages'])==2
        source=json.loads(payload['messages'][1]['content'])
        assert source['blocks'] and all(b['id'] and b['text'] for b in source['blocks'])
        def translate(text):
            for english,chinese in sentences.items():text=text.replace(english,chinese)
            return text
        answer={'translations':[{'id':b['id'],'translation':translate(b['text'])} for b in source['blocks']],
            'terms':[{'term':'signal gain','translation':'信号增益'}],
            'insight':'测试接口说明：本例用于检查逐段来源映射和文献绑定，不代表实际模型的阅读效果。'}
        return httpx2.Response(200,json={'id':'reader-mock','object':'chat.completion','created':1,'model':'reader-mock-only',
            'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':200,'completion_tokens':200,'total_tokens':400}})
    return httpx2.MockTransport(handle)


def prepare_reader_library(base,assets):
    directory=create_sample_zotero(Path(base)/'home/Zotero')
    with closing(sqlite3.connect(directory/'zotero.sqlite')) as db:
        db.execute('ALTER TABLE itemAttachments ADD COLUMN path TEXT')
        db.execute('ALTER TABLE itemAttachments ADD COLUMN linkMode INT')
        db.execute("UPDATE itemAttachments SET path='storage:sample.pdf',linkMode=0 WHERE itemID=11")
        db.execute("UPDATE itemDataValues SET value='Nature-reader 集成测试文献（模拟数据）' WHERE valueID=1")
        db.commit()
    pdf=directory/'storage/ATTACH01/sample.pdf'
    shutil.copy2(Path(assets)/'reader-samples/sample-paper.pdf',pdf)
    library=ZoteroLibrary(base,home=Path(base)/'home',roaming=Path(base)/'roaming')
    return library,directory,pdf


def wait_reader(controller,timeout=60):
    deadline=time.monotonic()+timeout
    while controller.reader.worker is not None and time.monotonic()<deadline:
        QTest.qWait(5)
        time.sleep(.01)  # Let a first SDK import acquire the GIL between Qt test waits.
    assert controller.reader.worker is None


def run_reader_smoke(pet_factory,assets):
    with TemporaryDirectory(prefix='bai-reader-smoke-') as folder:
        base=Path(folder)
        library,directory,pdf=prepare_reader_library(base,assets)
        pdf_hash=hashlib.sha256(pdf.read_bytes()).hexdigest()
        db_hash=hashlib.sha256((directory/'zotero.sqlite').read_bytes()).hexdigest()
        pet=pet_factory();controller=AgentController(pet,base)
        controller.personal.zotero_library=library;controller.personal.timer.stop()
        controller.settings=Settings(model='reader-mock-only',api_provider='compatible',api_base_url='https://reader.test')
        controller.reader.model_factory=lambda settings,guidance:ReaderModel(settings,'test-reader-key-only',guidance,mock_reader_transport())
        try:
            controller.personal.show_page('research');page=controller.personal.window.research;wait_reading(page)
            page.list.setCurrentRow(0)
            controller.submit('使用naturre reader阅读这篇文献')
            wait_reader(controller)
            paper=page.selected_paper()
            artifact=controller.reader.store.latest(directory,paper)
            assert artifact and artifact.markdown.is_file(),controller.reader.message
            data=json.loads((artifact.directory/'source_map.json').read_text(encoding='utf-8'))
            assert len(data['pages'])==2 and data['figures'] and data['equations']
            assert all(b['translation'] for b in data['blocks'] if not b.get('image_path'))
            assert artifact.metadata['translated_blocks']==artifact.metadata['total_blocks']
            assert '**Original:**' in artifact.markdown.read_text(encoding='utf-8') and '**中文:**' in artifact.markdown.read_text(encoding='utf-8')
            assert page.view_reader.isEnabled() and '已有解读' in page.list.currentItem().text()
            assert not controller.reader.store.latest(directory,page.list.item(1).data(Qt.ItemDataRole.UserRole))
            page.view_reader.click();QTest.qWait(20)
            viewer=controller.reader.viewers[-1]
            assert viewer.artifact.markdown==artifact.markdown and '已翻译' in viewer.status.text()
            assert 'Original:' in viewer.browser.toPlainText()
            assert not any(x['severity']=='FAIL' for x in json.loads((artifact.directory/'validation.json').read_text(encoding='utf-8')))
            # Regeneration creates another version and keeps the old file byte-for-byte.
            old_hash=hashlib.sha256(artifact.markdown.read_bytes()).hexdigest()
            page.generate_reader.click();wait_reader(controller)
            assert len(controller.reader.store.versions(directory,paper))==2
            assert hashlib.sha256(artifact.markdown.read_bytes()).hexdigest()==old_hash
            assert hashlib.sha256(pdf.read_bytes()).hexdigest()==pdf_hash
            assert hashlib.sha256((directory/'zotero.sqlite').read_bytes()).hexdigest()==db_hash
            return {'ok':True,'external_api_called':False,'real_zotero_accessed':False,'mock_model_transport':True,
                    'skill_version':'2.1.1','all_pages_and_bilingual_blocks_ok':True,'figure_table_equation_assets_ok':True,
                    'upstream_math_validation_ok':True,'chat_typo_command_and_selected_binding_ok':True,
                    'research_md_viewer_and_badges_ok':True,'version_history_preserves_old_md_ok':True,
                    'zotero_database_and_pdf_unchanged':True,'model_requests':artifact.metadata['api_requests']}
        finally:
            controller.close();pet.timer.stop();pet.chat.close();pet.close()
