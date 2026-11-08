"""Exercise all dialog families with temporary data and no external requests."""
import ctypes
from ctypes import wintypes
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLabel, QMessageBox


def run_dialog_chrome_smoke(pet_factory, preview_dir=None):
    from .controller import AgentController
    from .campus_ui import EntryEditor
    from .journal_ui import SnapshotDialog
    from .personal_ui import ReminderPicker
    from .reader_ui import ReaderDialog
    from .zotero import ZoteroLibrary

    app = QApplication.instance()
    native = app.platformName() == 'windows'
    output = Path(preview_dir) if preview_dir else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    checked = []
    if native:
        user = ctypes.WinDLL('user32')
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW.restype = ctypes.c_ssize_t

    def inspect(name, dialog):
        dialog.move(80, 45)
        dialog.show()
        QTest.qWait(30)
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint, name
        assert dialog.title_bar.isVisible(), name
        assert dialog.title_bar.geometry().bottom() < dialog.contentsRect().top(), name
        assert dialog.title_bar.rect().contains(dialog.title_bar.close_button.geometry()), name
        if native:
            handle = int(dialog.winId())
            outer, client = wintypes.RECT(), wintypes.RECT()
            assert user.GetWindowRect(handle, ctypes.byref(outer))
            assert user.GetClientRect(handle, ctypes.byref(client))
            assert outer.right - outer.left == client.right and outer.bottom - outer.top == client.bottom, name
            point = dialog.title_bar.close_button.mapTo(dialog, dialog.title_bar.close_button.rect().center())
            origin = wintypes.POINT()
            user.ClientToScreen(handle, ctypes.byref(origin))
            scale = dialog.devicePixelRatioF()
            x, y = origin.x + round(point.x() * scale), origin.y + round(point.y() * scale)
            assert user.SendMessageW(handle, 0x84, 0, (x & 0xffff) | ((y & 0xffff) << 16)) == 1, name
        if output:
            assert dialog.grab().save(str(output/(name+'.png')))
        checked.append(name)

    with TemporaryDirectory(prefix='bai-dialog-chrome-') as folder:
        base = Path(folder)
        pet = pet_factory()
        agent = AgentController(pet, base)
        agent.personal.timer.stop()
        agent.personal.sedentary.pulse.stop()
        agent.personal.zotero_library = ZoteroLibrary(base, home=base/'home', roaming=base/'roaming')
        extra = []
        try:
            main = agent.personal.window
            agent.personal.show_page('study')
            agent.personal.show_spelling()
            words = agent.personal.growth.dialog
            inspect('spelling-idle', words)
            assert words.height() <= 550, words.height()
            margins = words.contentsMargins()
            words.hide(); words.show(); QTest.qWait(5)
            assert words.contentsMargins() == margins
            words.next_card()
            inspect('spelling-learn', words)
            words.begin_recall()
            words.answer_input.setText(words.card['word'])
            words.submit_answer()
            inspect('spelling-answer', words)
            assert words.word_feedback.geometry().bottom() < words.height() - 10
            words.title_bar.close_button.click()
            assert not words.isVisible()

            agent.show_settings(); inspect('model-settings', agent.dialog)
            texts = [label.text() for label in agent.dialog.findChildren(QLabel)]
            assert not any('使用在线模型' in text or '聊天文字及主动发送' in text for text in texts)
            agent.dialog.close()
            agent.conversion.show(); inspect('file-conversion', agent.conversion.window)
            assert not any('转换支持' in label.text() for label in agent.conversion.window.findChildren(QLabel))
            agent.conversion.window.close()
            agent.show_campus(); inspect('web-shortcuts', agent.campus_dialog)
            entry = EntryEditor(parent=agent.campus_dialog); extra.append(entry)
            inspect('new-web-entry', entry)
            assert not hasattr(entry, 'policy')
            entry.title_bar.close_button.click()
            assert entry.result() == QDialog.DialogCode.Rejected
            agent.campus_dialog.close()

            reminders = ReminderPicker([], main); extra.append(reminders)
            inspect('task-reminders', reminders); reminders.close()
            agent.personal.show_sedentary_settings()
            inspect('break-settings', agent.personal.sedentary.dialog)
            agent.personal.sedentary.dialog.close()
            agent.personal.sedentary.show_reminder(preview=True, mode='popup', minutes=1)
            inspect('break-popup', agent.personal.sedentary.windows[0])
            agent.personal.sedentary.close_windows()
            agent.quick_ask.configure_shortcut()
            inspect('quick-ask-shortcut', agent.quick_ask.shortcut_dialog)
            agent.quick_ask.shortcut_dialog.close()

            agent.journal.show(); inspect('research-journal', agent.journal.window)
            agent.journal.window.close()
            editor = agent.journal.edit()
            inspect('journal-editor', editor); editor.close()
            record = agent.journal.store.save(day=date.today().isoformat(), kind='discovery', body='本地界面检查')
            snapshot = SnapshotDialog(agent.journal, record, main); extra.append(snapshot)
            inspect('journal-snapshot', snapshot); snapshot.close()
            reader = ReaderDialog(SimpleNamespace(versions=lambda *args: []), base, SimpleNamespace(title='示例文献'), main)
            extra.append(reader)
            inspect('paper-reader', reader); reader.close()

            box = QMessageBox(main); box.setWindowTitle('删除入口'); box.setText('确认删除这个入口？')
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            extra.append(box); inspect('confirmation', box)
            QTimer.singleShot(0, lambda: box.button(QMessageBox.StandardButton.Yes).click())
            assert box.exec() == QMessageBox.StandardButton.Yes
            prompt = QInputDialog(main); prompt.setWindowTitle('恢复草稿'); prompt.setLabelText('选择草稿')
            prompt.setComboBoxItems(['示例草稿']); extra.append(prompt)
            inspect('input-dialog', prompt); prompt.close()
            files = QFileDialog(main, '添加文件', str(base)); extra.append(files)
            inspect('file-picker', files); files.close()

            main.show_page('daily')
            main.show_toolbox(); QTest.qWait(60)
            menu = main.toolbox_menu
            pixels = menu.grab().toImage()
            assert pixels.hasAlphaChannel()
            for x, y in ((0, 0), (pixels.width()-1, 0), (0, pixels.height()-1), (pixels.width()-1, pixels.height()-1)):
                assert pixels.pixelColor(x, y).alpha() == 0, (x, y, pixels.pixelColor(x, y).name())
            if output:
                menu.grab().save(str(output/'toolbox-popup.png'))
            QTest.keyClick(menu, Qt.Key.Key_Escape)
            assert not menu.isVisible()
            return dict(ok=True, platform=app.platformName(), checked_dialogs=checked,
                        native_caption_removed=native, modal_result_preserved=True,
                        spelling_height=words.height(), menu_corners_transparent=True,
                        external_api_called=False, real_user_data_accessed=False)
        finally:
            for dialog in extra:
                try:
                    dialog.close()
                except RuntimeError:
                    pass
            agent.close(); pet.timer.stop(); pet.chat.close(); pet.close()
            app.processEvents()
