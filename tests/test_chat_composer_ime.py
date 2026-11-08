import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent, QInputMethodEvent
from PySide6.QtWidgets import QApplication

from westie_agent.chat_composer import ChatInput


@pytest.fixture
def composer():
    application = QApplication.instance() or QApplication([])
    widget = ChatInput()
    widget.setPlaceholderText('输入消息…\nEnter 发送')
    widget.show()
    yield widget
    widget.close();widget.deleteLater();application.processEvents()


def test_preedit_hides_hint_before_text_is_committed_and_clear_restores_it(composer):
    QApplication.sendEvent(composer, QInputMethodEvent('dian', []))
    assert composer.text() == '' and composer.placeholderText() == ''
    commit = QInputMethodEvent()
    commit.setCommitString('点')
    QApplication.sendEvent(composer, commit)
    assert composer.text() == '点' and composer.placeholderText() == ''
    composer.clear()
    assert composer.placeholderText() == '输入消息…\nEnter 发送'


def test_cancel_and_focus_loss_restore_empty_hint_without_overlapping_preedit(composer):
    QApplication.sendEvent(composer, QInputMethodEvent('zhong', []))
    assert not composer.placeholderText()
    QApplication.sendEvent(composer, QInputMethodEvent())
    assert composer.placeholderText()
    QApplication.sendEvent(composer, QInputMethodEvent('wen', []))
    QApplication.sendEvent(composer, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
    assert composer.placeholderText()


def test_changing_compact_hint_during_composition_does_not_show_it_early(composer):
    QApplication.sendEvent(composer, QInputMethodEvent('gai', []))
    composer.set_compact(True)
    composer.setPlaceholderText('问个概念…')
    assert not composer.placeholderText()
    QApplication.sendEvent(composer, QInputMethodEvent())
    assert composer.placeholderText() == '问个概念…'
