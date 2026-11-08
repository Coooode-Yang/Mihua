"""Packaged attachment verification with synthetic data and a mock API only."""
import base64
import json
import tempfile
from pathlib import Path

import httpx2
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QImage, QInputMethodEvent
from PySide6.QtWidgets import QApplication

from .chat_attachments import ChatAttachment, document_text
from .bridge import ToolBridge
from .engine import LocalAgent
from .settings import Settings


def run_attachments_smoke(pet_factory):
    pet = pet_factory()
    panel = pet.chat
    try:
        placeholder = panel.input.placeholderText()
        QApplication.sendEvent(panel.input, QInputMethodEvent('dian', []))
        assert panel.input.text() == '' and panel.input.placeholderText() == ''
        commit = QInputMethodEvent();commit.setCommitString('点')
        QApplication.sendEvent(panel.input, commit)
        assert panel.input.text() == '点' and panel.input.placeholderText() == ''
        panel.input.clear()
        assert panel.input.placeholderText() == placeholder
        image = QImage(80, 40, QImage.Format.Format_RGB32)
        image.fill(0xffd4e8d8)
        mime = QMimeData(); mime.setImageData(image)
        panel.add_mime_attachments(mime)
        assert len(panel.attachments) == 1
        panel.stage_attachment(ChatAttachment('提纲.md', '附件测试内容'.encode(), 'text'))
        panel.input.setText('解释图片\n参考提纲')
        sent = []
        panel.attachments_submitted.connect(lambda text, files: sent.append((text,files)))
        panel.send()
        assert len(sent)==1 and len(sent[0][1])==2 and '\n' in sent[0][0]
        assert '<img' in panel.history.toHtml()
        panel.set_busy(True)
        assert not panel.attach_button.isEnabled()
        panel.set_busy(False)
        panel.attachment_send_finished(False)
        assert panel.input.text()==sent[0][0] and len(panel.attachments)==2
        requests=[]
        def handler(request):
            payload=json.loads(request.content);requests.append(payload)
            parts=next(m['content'] for m in payload['messages'] if m['role']=='user')
            image_url=next(p['image_url']['url'] for p in parts if p['type']=='image_url')
            assert base64.b64decode(image_url.split(',',1)[1])==sent[0][1][0].data
            assert any('附件测试内容' in p.get('text','') for p in parts)
            return httpx2.Response(200,json={'id':'smoke','object':'chat.completion','created':1,
                'model':'deepseek-flash','choices':[{'index':0,'message':{'role':'assistant','content':'已读到图片和提纲。'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':5,'completion_tokens':5,'total_tokens':10}})
        settings=Settings(tools_mode='mock')
        agent=LocalAgent(settings,ToolBridge(settings),allow_network=False)
        transport=httpx2.MockTransport(handler)
        agent.reply(sent[0][0],settings.api_base_url,lambda _:None,api_key='smoke-dummy-key',api_transport=transport,attachments=sent[0][1])
        agent.reply('继续说明',settings.api_base_url,lambda _:None,api_key='smoke-dummy-key',api_transport=transport)
        assert len(requests)==2
        panel.attachment_send_finished(True)
        assert not panel.attachments
        import pypdfium2 as pdfium
        with tempfile.TemporaryDirectory(prefix='bai-attachments-') as temporary:
            path=Path(temporary)/'blank.pdf'
            with pdfium.PdfDocument.new() as pdf:
                page=pdf.new_page(100,100);page.close();pdf.save(path)
            try:
                document_text(ChatAttachment('blank.pdf',path.read_bytes(),'pdf'))
            except ValueError as exc:
                assert '截图' in str(exc)
            else:
                raise AssertionError('Scanned PDF must report its missing text layer')
        return {'ok':True,'image_paste_preview_ok':True,'multiline_and_attachment_send_ok':True,
                'ime_preedit_hides_placeholder_ok':True,
                'failure_keeps_draft_ok':True,'wire_image_bytes_and_document_text_ok':True,
                'followup_attachment_history_ok':True,'scanned_pdf_hint_ok':True,
                'mock_requests':len(requests),'external_api_called':False}
    finally:
        pet.timer.stop();panel.close();pet.close()
