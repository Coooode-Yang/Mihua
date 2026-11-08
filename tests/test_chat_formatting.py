import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app import ChatPanel, PetWindow, ROOT, SpriteBank
from westie_agent.controller import AgentController
from westie_agent.engine import AgentReply


@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    widget = ChatPanel()
    widget.history.clear()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def test_model_bold_and_spaced_markers_are_rendered(panel):
    panel.add_message('米花', '**DOP** 是精度衰减因子。\n\n**一句话： **它描述几何布局对误差的放大。', markdown=True)
    plain = panel.history.toPlainText()
    assert '**' not in plain and '一句话：' in plain
    cursor = panel.history.document().find('DOP')
    assert cursor.charFormat().fontWeight() >= QFont.Weight.DemiBold


def test_headings_lists_and_following_messages_do_not_leak_format(panel):
    panel.add_message('米花', '## 如何理解\n\n- 布局分散\n- 误差放大较小', markdown=True)
    assert '##' not in panel.history.toPlainText()
    assert panel.history.document().find('布局分散').block().textList() is not None
    heading_font = panel.history.document().find('如何理解').charFormat().font()
    assert heading_font.pixelSize() == panel.history.font().pixelSize()
    panel.add_message('你', '继续解释')
    cursor = panel.history.document().find('继续解释')
    assert cursor.block().textList() is None
    assert cursor.charFormat().fontWeight() < QFont.Weight.DemiBold


def test_user_and_local_messages_stay_literal(panel):
    text = '**原样保留** <b>文字</b> [链接](file:///private)'
    panel.add_message('你', text)
    panel.add_message('米花', '记好啦：' + text)
    assert panel.history.toPlainText().count(text) == 2
    assert not panel.history.document().find('原样保留').charFormat().isAnchor()


def test_code_keeps_asterisks_and_indentation(panel):
    panel.add_message('米花', '内联 `**literal**`\n\n```python\n    value = 2 ** 3\n    text = "**literal**"\n```', markdown=True)
    plain = panel.history.toPlainText()
    assert '**literal**' in plain and '    value = 2 ** 3' in plain
    assert '```' not in plain


def test_model_html_is_literal_and_images_cannot_load_resources(panel):
    panel.add_message('米花', '<b>原始HTML</b>\n\n![说明](file:///private.png)\n\n[网页](https://example.com)', markdown=True)
    assert '<b>原始HTML</b>' in panel.history.toPlainText()
    assert not panel.history.openLinks() and not panel.history.openExternalLinks()
    block = panel.history.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            assert not iterator.fragment().charFormat().isImageFormat()
            iterator += 1
        block = block.next()


def test_controller_applies_format_only_to_model_answers(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    controller.personal.timer.stop()
    try:
        pet.chat.history.clear()
        controller.finished('chat', AgentReply('**简洁结论**\n\n下一段解释。', [], [], 'real', False))
        assert '**' not in pet.chat.history.toPlainText()
        controller.submit('添加待办：**原样标题**')
        assert '**原样标题**' in pet.chat.history.toPlainText()
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close();app.processEvents()
