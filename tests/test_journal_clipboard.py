from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QObject, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from westie_agent.journal_store import JournalStore
from westie_agent.journal_ui import EntryEditor


class Controller(QObject):
    changed = Signal()

    def __init__(self, base):
        super().__init__()
        self.store = JournalStore(base)


@pytest.fixture
def editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    controller = Controller(tmp_path)
    window = EntryEditor(controller, seed={'body': '实验笔记'})
    yield window
    app.clipboard().clear()
    window.draft_timer.stop()
    window.accept()
    app.processEvents()


def image_mime():
    image = QImage(32, 24, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.green)
    mime = QMimeData()
    mime.setImageData(image)
    return mime


def test_ctrl_v_image_saves_attachment_and_preserves_selected_text(editor):
    editor.show()
    editor.body.setFocus()
    editor.body.selectAll()
    QApplication.clipboard().setMimeData(image_mime())
    QTest.keyClick(editor.body, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert editor.body.toPlainText() == '实验笔记'
    assert len(editor.pending) == 1 and '1 / 6' in editor.images.text()
    staged = Path(editor.pending[0])
    assert QImage(str(staged)).pixelColor(0, 0) == Qt.GlobalColor.green
    editor.save()
    row = editor.controller.store.get(editor.saved['id'])
    asset = editor.controller.store.asset(row['attachments'][0])
    assert QImage(str(asset)).size() == QImage(32, 24, QImage.Format.Format_ARGB32).size()
    assert not staged.exists()


def test_text_paste_keeps_normal_selection_and_plain_text_behavior(editor):
    mime = QMimeData()
    mime.setText('替换后的笔记\n第二行')
    mime.setHtml('<b>替换后的笔记</b><p>第二行</p>')
    QApplication.clipboard().setMimeData(mime)
    editor.body.selectAll()
    editor.body.paste()
    assert editor.body.toPlainText() == '替换后的笔记\n第二行'
    assert not editor.pending


def test_pasted_image_survives_draft_recovery(editor):
    editor.body.clear()
    editor.body.insertFromMimeData(image_mime())
    editor.stash_draft()
    draft = editor.controller.store.drafts()[0]
    restored = EntryEditor(editor.controller, seed=draft)
    try:
        assert len(restored.pending) == 1 and Path(restored.pending[0]).is_file()
        restored.body.setPlainText('恢复的笔记')
        restored.save()
        assert len(restored.saved['attachments']) == 1
        assert not editor.controller.store.drafts()
        assert not Path(editor.pending[0]).exists()
    finally:
        restored.draft_timer.stop()
        restored.accept()


@pytest.mark.parametrize('saved_count,pending_count', [(0, 6), (4, 2), (6, 0)])
def test_image_limit_counts_existing_and_pending_images(editor, saved_count, pending_count):
    editor.attachments = ['existing.png'] * saved_count
    editor.pending = ['chosen.png'] * pending_count
    editor.body.insertFromMimeData(image_mime())
    assert len(editor.attachments) + len(editor.pending) == 6
    assert '最多添加 6 张' in editor.status.text()
    assert not list((editor.controller.store.root / 'drafts').glob('*.png'))


def test_remove_images_and_discard_clean_only_this_drafts_clipboard_files(editor, monkeypatch):
    external = editor.controller.store.base / 'chosen.png'
    image_mime().imageData().save(str(external))
    editor.pending.append(str(external))
    editor.body.insertFromMimeData(image_mime())
    staged = Path(editor.pending[-1])
    editor.remove_images()
    assert not staged.exists() and external.is_file() and not editor.pending
    editor.body.insertFromMimeData(image_mime())
    staged = Path(editor.pending[0])
    editor.stash_draft()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Discard)
    editor.reject()
    assert not staged.exists() and external.is_file() and not editor.controller.store.drafts()


def test_bad_clipboard_image_does_not_change_draft(editor):
    mime = QMimeData()
    mime.setImageData(QImage())
    editor.body.insertFromMimeData(mime)
    assert not editor.pending and editor.body.toPlainText() == '实验笔记'
    assert '剪贴板图片无法读取' in editor.status.text()


def test_disk_error_leaves_no_pending_image(editor, monkeypatch):
    original = Path.open

    def fail_staging(path, *args, **kwargs):
        if args and args[0] == 'xb':
            raise OSError('disk full')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_staging)
    editor.body.insertFromMimeData(image_mime())
    assert not editor.pending and '磁盘空间' in editor.status.text()
