"""Verify focus rendering and keyboard activation using isolated application data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication,
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget, QPushButton,
    QRadioButton, QSpinBox, QStyle, QStyleOptionFocusRect, QTabBar, QToolButton, QVBoxLayout, QWidget)


def assert_no_focus_frame(widget):
    image = QImage(128, 32, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    empty = image.copy()
    option = QStyleOptionFocusRect()
    option.initFrom(widget)
    option.rect = QRect(2, 2, 120, 25)
    option.state |= QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_KeyboardFocusChange
    option.backgroundColor = widget.palette().window().color()
    painter = QPainter(image)
    widget.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, option, painter, widget)
    painter.end()
    assert image == empty, (type(widget).__name__, widget.objectName())


def run_focus_style_smoke(pet_factory, preview_dir=None):
    from .controller import AgentController
    from .zotero import ZoteroLibrary
    output = Path(preview_dir) if preview_dir else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='bai-focus-style-') as folder:
        base = Path(folder)
        pet = pet_factory()
        agent = AgentController(pet, base)
        agent.personal.timer.stop()
        agent.personal.sedentary.pulse.stop()
        agent.personal.zotero_library = ZoteroLibrary(base, home=base/'home', roaming=base/'roaming')
        gallery = QDialog(agent.personal.window)
        gallery.setWindowTitle('按键与焦点检查')
        gallery.resize(560, 360)
        layout = QVBoxLayout(gallery)
        button = QPushButton('普通按钮')
        tool = QToolButton(); tool.setText('工具按钮')
        check = QCheckBox('复选框')
        radio = QRadioButton('单选项')
        combo = QComboBox(); combo.addItems(['选项一', '选项二'])
        spin = QSpinBox()
        edit = QLineEdit()
        items = QListWidget(); items.addItems(['列表项目一', '列表项目二'])
        for widget in (button, tool, check, radio, combo, spin, edit, items):
            layout.addWidget(widget)
        gallery.show(); QTest.qWait(20)
        hits = []
        button.clicked.connect(lambda: hits.append('button'))
        tool.clicked.connect(lambda: hits.append('tool'))
        try:
            button.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert hits == ['button'] and button.hasFocus()
            QTest.keyClick(button, Qt.Key.Key_Tab)
            assert tool.hasFocus()
            QTest.keyClick(tool, Qt.Key.Key_Space)
            assert hits == ['button', 'tool']
            check.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.keyClick(check, Qt.Key.Key_Space)
            assert check.isChecked()
            radio.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.keyClick(radio, Qt.Key.Key_Space)
            assert radio.isChecked()
            edit.setFocus(); QTest.keyClicks(edit, 'hello')
            assert edit.text() == 'hello'
            items.setCurrentRow(0); items.setFocus()
            QTest.keyClick(items, Qt.Key.Key_Down)
            assert items.currentRow() == 1
            if output:
                gallery.grab().save(str(output/'controls.png'))
            gallery.hide()

            main = agent.personal.window
            main.show_page('daily')
            main.toolbox_button.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.keyClick(main.toolbox_button, Qt.Key.Key_Space)
            assert main.toolbox_menu.isVisible()
            QTest.keyClick(main.toolbox_menu, Qt.Key.Key_Escape)
            assert not main.toolbox_menu.isVisible()
            main.toolbox_button.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.qWait(20)
            if output:
                main.toolbox_button.grab().save(str(output/'toolbox-focused.png'))
            checked = 0
            for show, window in (
                (agent.show_settings, lambda: agent.dialog),
                (agent.personal.show_spelling, lambda: agent.personal.growth.dialog),
                (agent.personal.show_sedentary_settings, lambda: agent.personal.sedentary.dialog),
                (agent.show_campus, lambda: agent.campus_dialog),
                (agent.conversion.show, lambda: agent.conversion.window),
            ):
                show()
                QTest.qWait(10)
            controls = (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QTabBar)
            for window in (main, gallery, agent.dialog, agent.personal.growth.dialog,
                           agent.personal.sedentary.dialog, agent.campus_dialog,
                           agent.conversion.window, main.toolbox_menu):
                assert_no_focus_frame(window)
                for widget in window.findChildren(QWidget):
                    if isinstance(widget, controls):
                        assert_no_focus_frame(widget)
                        checked += 1
            return dict(ok=True, checked_controls=checked,
                        dotted_focus_frames_removed=True, keyboard_tab_preserved=True,
                        keyboard_space_preserved=True, text_input_preserved=True,
                        item_navigation_preserved=True, toolbox_keyboard_toggle_preserved=True,
                        platform=QApplication.platformName(), external_api_called=False,
                        real_user_data_accessed=False)
        finally:
            gallery.close()
            agent.close(); pet.timer.stop(); pet.chat.close(); pet.close()
            QApplication.processEvents()
