"""Capture real Qt conversations and verify durable images without model calls."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def sample_image(color, name):
    from .chat_attachments import image_attachment
    image = QImage(360, 140, QImage.Format.Format_RGB32)
    image.fill(QColor('#ffffff'))
    painter = QPainter(image)
    painter.setPen(QPen(QColor(color), 4))
    painter.drawLine(24, 112, 336, 112)
    painter.drawLine(24, 112, 24, 24)
    painter.drawPolyline([QPoint(x,y) for x,y in ((24, 100), (100, 70), (180, 90), (260, 30), (336, 50))])
    painter.end()
    return image_attachment(image, name)


def run_chat_capture_smoke(pet_factory, *, preview_dir=None):
    from .controller import AgentController
    from .journal_store import JournalStore
    with TemporaryDirectory(prefix='bai-chat-journal-') as folder:
        base = Path(folder)
        pet = pet_factory()
        agent = AgentController(pet, base)
        journal = agent.journal
        agent.personal.timer.stop()
        history = pet.chat.history
        try:
            history.clear_conversation()
            red = sample_image('#cb4d3e', 'curve-red.png')
            blue = sample_image('#447eaa', 'curve-blue.png')
            pet.chat.add_message('你', '请解释第一张曲线图。\n附件：curve-red.png', attachments=(red,))
            pet.chat.add_message('米花', '红色曲线显示了随横轴变化的趋势。', markdown=True)
            menu = history.conversation_menu()
            next(a for a in menu.actions() if a.text() == '连同问题收藏到科研日志').trigger()
            menu.deleteLater()
            pair_editor = journal.editors[-1]
            assert len(pair_editor.pending) == 1 and pair_editor.image_previews.count() == 1
            pair_editor.save()
            pair = JournalStore(base).get(pair_editor.saved['id'])
            assert len(pair['attachments']) == 1 and '第一张曲线图' in pair['body']
            assert journal.store.asset(pair['attachments'][0]).read_bytes() == red.data
            pet.chat.add_message('你', '第二张图与第一张有什么区别？\n附件：curve-blue.png', attachments=(blue,))
            pet.chat.add_message('米花', '两张图可比较曲线的位置与变化幅度。', markdown=True)
            pet.chat.save_conversation_button.click()
            whole_editor = journal.editors[-1]
            assert len(whole_editor.pending) == 2 and whole_editor.image_previews.count() == 2
            frozen_body = whole_editor.body.toPlainText()
            pet.chat.add_message('你', '这条新消息不应出现在刚才的快照里。')
            assert whole_editor.body.toPlainText() == frozen_body
            whole_editor.stash_draft()
            draft = journal.store.drafts()[0]
            assert len(draft['_pending']) == 2
            history.clear_conversation()
            assert not history.message_images and all(Path(p).is_file() for p in whole_editor.pending)
            whole_editor.save()
            whole = JournalStore(base).get(whole_editor.saved['id'])
            assert whole['body'].index('第一张曲线图') < whole['body'].index('红色曲线') < whole['body'].index('第二张图')
            assert '不应出现在' not in whole['body'] and len(whole['attachments']) == 2
            assert all(journal.store.asset(p).is_file() for p in whole['attachments'])
            assert not journal.store.drafts()
            journal.show(all_dates=True)
            window = journal.window
            for index in range(window.list.count()):
                if window.list.item(index).data(Qt.ItemDataRole.UserRole)['id'] == whole['id']:
                    window.list.setCurrentRow(index)
                    break
            QTest.qWait(30)
            for relative in whole['attachments']:
                rendered = window.browser.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(relative))
                assert isinstance(rendered, QImage) and not rendered.isNull(), (relative, rendered)
            exported = journal.store.export(date.today().isoformat(), base/'conversation.md')
            text = exported.read_text(encoding='utf-8')
            assert text.count('![对话配图]') == 3  # one pair image, plus the two-turn snapshot
            assert text.count('![日志图片]') == 0
            assert len(list((base/'conversation-assets').glob('*.png'))) == 2
            assert '](conversation-assets/' in text and '](assets/' not in text
            if preview_dir:
                output = Path(preview_dir);output.mkdir(parents=True, exist_ok=True)
                window.resize(1100, 820);QTest.qWait(30)
                window.grab().save(str(output/'conversation-with-images.png'))
            return {'chat_question_images_preserved_ok': True, 'complete_chat_snapshot_ok': True,
                    'chat_images_survive_clear_and_reload_ok': True, 'journal_images_rendered_ok': True,
                    'conversation_export_images_ok': True, 'snapshot_excludes_future_messages_ok': True,
                    'captured_images_visible_in_editor_ok': True}
        finally:
            agent.close();pet.timer.stop();pet.chat.close();pet.close();QApplication.processEvents()
