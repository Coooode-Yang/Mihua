import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QKeySequenceEdit

from app import ROOT, PetWindow, SpriteBank
from westie_agent.controller import AgentController


@pytest.fixture
def agent(tmp_path):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    controller.personal.timer.stop()
    yield controller
    controller.close();pet.timer.stop();pet.chat.close();pet.close();application.processEvents()


def test_whole_more_button_opens_menu_and_empty_actions_are_disabled(agent):
    quick = agent.quick_ask
    assert not quick.chat.history.messages and not quick.chat.history.toPlainText()
    quick.show()
    window = quick.window
    opened = []
    def dismiss():
        opened.append(window.menu.isVisible())
        window.menu.setActiveAction(window.model_action)
        assert window.menu.activeAction() is window.model_action
        QTest.keyClick(window.menu, Qt.Key.Key_Escape)
    QTimer.singleShot(30, window.menu, dismiss)
    QTest.mouseClick(window.more, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    assert opened == [True] and window.isVisible()
    assert not window.capture_action.isEnabled() and not window.search_action.isEnabled()
    assert window.model_action.isEnabled() and window.shortcut_action.isEnabled()


def test_model_settings_open_above_pinned_window_and_repeat_reuses_dialog(agent):
    quick = agent.quick_ask
    quick.show()
    QTimer.singleShot(30, quick.window.menu, lambda: QTest.mouseClick(
        quick.window.menu, Qt.MouseButton.LeftButton,
        pos=quick.window.menu.actionGeometry(quick.window.model_action).center()))
    QTest.mouseClick(quick.window.more, Qt.MouseButton.LeftButton);QTest.qWait(50)
    dialog = agent.dialog
    assert dialog and dialog.isVisible() and dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert '模型设置已打开' in quick.window.hint.text()
    quick.window.model_action.trigger();QTest.qWait(20)
    assert agent.dialog is dialog and dialog.isVisible()
    dialog.reject();QTest.qWait(10)


def test_shortcut_dialog_is_nonblocking_and_saved_choice_has_feedback(agent):
    quick = agent.quick_ask
    quick.show()
    quick.window.shortcut_action.trigger();QTest.qWait(20)
    dialog = quick.shortcut_dialog
    assert dialog and dialog.isVisible() and dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    quick.window.shortcut_action.trigger();QTest.qWait(10)
    assert quick.shortcut_dialog is dialog
    dialog.findChild(QKeySequenceEdit).setKeySequence(QKeySequence('Ctrl+Alt+J'))
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    QTest.qWait(10)
    assert quick.shortcut_dialog is None and quick.shortcut_text == 'Ctrl+Alt+J'
    assert '快捷键已保存' in quick.window.hint.text()
    assert quick.preferences.value('shortcut') == 'Ctrl+Alt+J'


def test_collect_opens_visible_draft_and_reports_success_only_after_save(agent):
    quick = agent.quick_ask
    quick.show()
    quick.chat.add_message('你', '菜单收藏测试')
    quick.chat.add_message('米花', '这是回答。')
    quick.window.refresh_menu()
    assert quick.window.capture_action.isEnabled()
    quick.window.capture_action.trigger();QTest.qWait(20)
    editor = agent.journal.editors[-1]
    assert editor.isVisible() and editor.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert '草稿已打开' in quick.window.hint.text() and agent.journal.store.entries() == []
    editor.save_button.click();QTest.qWait(10)
    assert '已保存到科研日志' in quick.window.hint.text() and len(agent.journal.store.entries()) == 1


@pytest.mark.parametrize('opened', [True, False])
def test_browser_action_reports_open_result_and_empty_query_does_not_launch(agent, monkeypatch, opened):
    quick = agent.quick_ask
    quick.show()
    requests = []
    def open_url(url):
        requests.append(url.toString())
        return opened
    monkeypatch.setattr('westie_agent.quick_ask.QDesktopServices.openUrl', open_url)
    quick.browser_search()
    assert not requests and '先输入问题' in quick.window.hint.text()
    quick.chat.input.setText('中地球轨道')
    quick.window.refresh_menu()
    assert quick.window.search_action.isEnabled()
    quick.window.search_action.trigger();QTest.qWait(20)
    assert len(requests) == 1
    assert ('已请求浏览器搜索' if opened else '未能打开浏览器') in quick.window.hint.text()
