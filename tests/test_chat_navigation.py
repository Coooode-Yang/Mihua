import threading

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app import ChatPanel, PetWindow, ROOT, SpriteBank
from westie_agent.controller import AgentController
from westie_agent.engine import AgentReply


@pytest.fixture
def panel(qt_application):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    widget = ChatPanel(); widget.resize(450, 500); widget.show()
    widget.history.clear()
    yield widget
    widget.close(); app.processEvents()


@pytest.fixture(scope='module')
def qt_application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_panel_simplified_but_pet_interaction_stays_available(panel):
    labels = {b.text() for b in panel.findChildren(QPushButton)}
    assert not {'摸摸','跳一下','睡觉'} & labels
    assert not hasattr(panel, 'focus_summary')
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    try:
        pet.perform('pet'); assert pet.pet_count == 1
        pet.perform('jump'); assert pet.state == 'jump'
        pet.perform('sleep'); assert pet.state == 'sleep'
    finally:
        pet.timer.stop(); pet.chat.close(); pet.close()


def test_every_user_question_has_marker_and_click_jumps(panel):
    for i in range(15):
        panel.add_message('你', f'问题 {i}：请解释这个概念')
        panel.add_message('米花', ('说明内容。\n'*15), markdown=True)
    QApplication.processEvents()
    bar = panel.history.verticalScrollBar()
    assert len(bar.marker_positions()) == 15 and bar.maximum() > 0
    assert len(set(bar.marker_positions())) == 15
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(14, round(bar.marker_positions()[0])))
    assert bar.value() < 100
    assert panel.history.textCursor().block().text() == '你'
    panel.history.jump_to_question(14)
    assert bar.value() > bar.maximum()/2


def test_hover_preview_escapes_html_and_retains_question_text(panel, monkeypatch):
    question = '<b>这不是HTML</b> **用户问题**'
    panel.add_message('你', question)
    QApplication.processEvents()
    bar = panel.history.verticalScrollBar()
    captured = []
    monkeypatch.setattr('westie_agent.chat_navigation.QToolTip.showText', lambda *args: captured.append(args[1]))
    point = QPoint(14, round(bar.marker_positions()[0]))
    QApplication.sendEvent(bar, QHelpEvent(QEvent.Type.ToolTip, point, bar.mapToGlobal(point)))
    assert '&lt;b&gt;' in captured[0] and '**用户问题**' in captured[0]


def test_context_menu_clear_removes_transcript_markers_and_actions(panel):
    panel.add_message('你', '清空前的问题')
    panel.add_message('米花', '**回答**', markdown=True)
    signal = []
    panel.conversation_cleared.connect(lambda: signal.append(True))
    menu = panel.history.conversation_menu()
    action = next(a for a in menu.actions() if a.text() == '清空对话记录')
    assert action.isEnabled()
    action.trigger()
    assert panel.history.toPlainText() == '' and not panel.history.questions and signal
    panel.add_message('你', '新的问题')
    assert len(panel.history.questions) == 1
    menu.deleteLater()


@pytest.mark.parametrize('fail', [False, True])
def test_clear_discards_inflight_response_and_resets_model_context(tmp_path, fail):
    app = QApplication.instance() or QApplication([])
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    controller.personal.timer.stop()
    entered, release = threading.Event(), threading.Event()
    old_agent = controller.agent
    def response():
        entered.set(); assert release.wait(3)
        old_agent.history.append('old conversation')
        if fail: raise ValueError('不应重新出现的旧错误')
        return AgentReply('不应重新出现的旧回答', [], [], 'real', False)
    try:
        pet.chat.add_message('你', '旧问题')
        assert controller.work('chat', response) and entered.wait(1)
        pet.chat.history.clear_conversation()
        assert controller.agent is not old_agent and not controller.agent.history
        release.set()
        for _ in range(150):
            QTest.qWait(10)
            if not controller.busy: break
        assert not controller.busy and pet.chat.history.toPlainText() == ''
        assert not controller.agent.history and pet.chat.input.isEnabled()
    finally:
        release.set(); controller.close(); pet.timer.stop(); pet.chat.close(); pet.close(); app.processEvents()
