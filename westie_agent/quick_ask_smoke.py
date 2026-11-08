"""Exercise the real shared Qt chat with temporary data and a fake completion."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def run_quick_ask_smoke(pet_factory, *, preview_dir=None):
    from .controller import AgentController
    from .engine import AgentReply
    with TemporaryDirectory(prefix='bai-quick-ask-') as folder:
        pet = pet_factory()
        agent = AgentController(pet, Path(folder))
        quick = agent.quick_ask
        panel = pet.chat
        window = quick.window
        queued = []
        agent.work = lambda kind, function: queued.append((kind, function))
        try:
            assert not window.isVisible()
            assert not panel.history.messages and not panel.history.toPlainText()
            quick.show()
            QTest.qWait(20)
            assert panel.window() is window and panel.quick_mode
            assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
            assert 'Esc 退出随手问' in panel.input.placeholderText()
            assert not agent.personal.window.isVisible()
            window.resize(300, 240)
            QTest.qWait(30)
            assert window.width() == 300 and window.height() == 240, (window.size(), window.minimumSizeHint())
            assert panel.input.isVisible() and panel.history.height() >= 30
            assert window.hint.isHidden()
            for width in (300, 360, 676):
                window.resize(width, 300)
                QTest.qWait(20)
                controls = (panel.attach_button, panel.input, panel.send_button)
                assert {control.height() for control in controls} == {36}
                assert len({control.mapTo(window, control.rect().topLeft()).y() for control in controls}) == 1
                assert window.new_button.mapTo(window, window.new_button.rect().topLeft()).y() == window.lookup.mapTo(window, window.lookup.rect().topLeft()).y()
                assert window.new_button.mapTo(window, window.new_button.rect().topRight()).x() < window.lookup.mapTo(window, window.lookup.rect().topLeft()).x()
                for button in (window.clipboard_button, window.new_button, window.lookup, window.pin, window.expand, window.more):
                    assert window.rect().contains(button.mapTo(window, button.rect().topLeft()))
                    assert window.rect().contains(button.mapTo(window, button.rect().bottomRight()))
            panel.input.setText('较长的问题\n第二行\n第三行\n第四行')
            assert panel.input.height() == 36
            window.resize(300, 240)
            panel.input.setText('正在写的问题')
            QApplication.clipboard().setText('MEO')
            window.hide();quick.show()
            assert panel.input.text() == '正在写的问题'  # Summoning never reads clipboard.
            window.clipboard_button.click()
            assert '正在写的问题' in panel.input.text() and 'MEO' in panel.input.text() and not queued
            panel.input.setText('添加待办：只是需要解释的文字')
            panel.send()
            assert len(queued) == 1 and not agent.personal.store.tasks()
            panel.add_message('米花', '这是演示回答：MEO 指中地球轨道。', markdown=True)
            panel.input.setText('它和 LEO 有什么区别？')
            agent.busy = True;panel.set_busy(True)
            quick.expand()
            assert panel.window() is agent.personal.window and not panel.quick_mode
            assert 'MEO 指中地球轨道' in panel.history.toPlainText() and not panel.input.isEnabled()
            quick.show()
            assert not panel.input.isEnabled()
            agent.reset_busy()
            assert panel.input.text() == '它和 LEO 有什么区别？'
            panel.input.setText('MEO 是什么？')
            panel.send()
            agent.failed('模拟失败')
            assert panel.input.text() == 'MEO 是什么？'
            before = panel.history.toPlainText()
            QTest.keyClick(panel.input, Qt.Key.Key_Escape)
            assert not window.isVisible() and panel.history.toPlainText() == before
            quick.show()
            mime = QMimeData()
            image = QImage(60, 40, QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.green)
            mime.setImageData(image)
            QApplication.clipboard().setMimeData(mime)
            window.clipboard_button.click()
            assert len(panel.attachments) == 1 and not agent.personal.store.tasks()
            old_generation = agent.conversation_generation
            quick.new_question()
            assert not panel.attachments and not panel.history.questions and not panel.input.text()
            agent.receive_finished('chat', AgentReply('已过期的回答', [], [], 'conversation', False), old_generation)
            assert '已过期' not in panel.history.toPlainText()
            window.pin.setChecked(False)
            assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            window.pin.setChecked(True)
            assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            quick.show()
            panel.add_message('你', 'MEO 是什么意思？')
            panel.add_message('米花', 'MEO 是中地球轨道，位于低地球轨道与地球同步轨道之间。\n\n导航卫星常使用这类轨道。这里按卫星通信的语境解释。', markdown=True)
            window.refresh_menu()
            assert window.capture_action.isEnabled() and window.search_action.isEnabled()
            window.model_action.trigger();QTest.qWait(20)
            assert agent.dialog.isVisible() and agent.dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            assert window.hint.text() == '模型设置已打开'
            agent.dialog.reject();QTest.qWait(10)
            window.shortcut_action.trigger();QTest.qWait(20)
            assert quick.shortcut_dialog.isVisible() and quick.shortcut_dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            quick.shortcut_dialog.reject();QTest.qWait(10)
            window.capture_action.trigger();QTest.qWait(20)
            editor = agent.journal.editors[-1]
            assert editor.isVisible() and '草稿已打开' in window.hint.text()
            editor.save();QTest.qWait(10)
            assert window.hint.text() == '已保存到科研日志'
            window.restore_hint()
            assert window.hint.isHidden() and not window.hint.text()
            if preview_dir:
                output = Path(preview_dir);output.mkdir(parents=True, exist_ok=True)
                panel.input.clear()
                for size in ((676, 240), (360, 400), (300, 240)):
                    window.resize(*size);QTest.qWait(40)
                    window.grab().save(str(output/f'quick-ask-{size[0]}x{size[1]}.png'))
                quick.expand();QTest.qWait(40)
                agent.personal.window.grab().save(str(output/'expanded-chat.png'))
            return {'ok': True, 'external_api_called': False, 'personal_data_accessed': False,
                    'minimum_300x240_ok': True, 'shared_chat_and_draft_ok': True,
                    'explicit_clipboard_staging_ok': True, 'copied_commands_not_executed_ok': True,
                    'busy_expand_and_restore_ok': True, 'escape_preserves_conversation_ok': True,
                    'failed_question_restored_ok': True, 'clipboard_image_staging_ok': True,
                    'new_question_rejects_stale_reply_ok': True, 'pin_toggle_ok': True,
                    'quiet_chat_start_ok': True, 'menu_dialogs_visible_feedback_ok': True,
                    'compact_composer_aligned_36px_ok': True, 'lookup_after_new_question_ok': True,
                    'no_permanent_footer_ok': True, 'toolbar_fits_narrow_window_ok': True}
        finally:
            QApplication.clipboard().clear()
            agent.close();pet.timer.stop();pet.chat.close();pet.close();QApplication.processEvents()
