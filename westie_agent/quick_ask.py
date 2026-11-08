"""A compact host for the existing chat, with explicit clipboard access."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QCursor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QKeySequenceEdit, QLabel, QMenu, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .window_chrome import NativeWindowFrame


class GlobalShortcut(QAbstractNativeEventFilter):
    """Register only one chord; do not hook or record ordinary keyboard input."""
    _next_id = 0xA100

    def __init__(self, callback, *, enabled=True):
        super().__init__()
        self.callback = callback
        self.enabled = enabled and sys.platform == 'win32'
        self.key = None
        self.identifier = GlobalShortcut._next_id
        GlobalShortcut._next_id += 1
        self.api = None
        if self.enabled:
            from ctypes import wintypes
            self.api = ctypes.WinDLL('user32', use_last_error=True)
            self.api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.api.RegisterHotKey.restype = wintypes.BOOL
            self.api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self.api.UnregisterHotKey.restype = wintypes.BOOL
            QApplication.instance().installNativeEventFilter(self)

    @staticmethod
    def chord(sequence):
        if sequence.count() != 1:
            raise ValueError('请选择一个包含 Ctrl 或 Alt 的组合键，例如 Ctrl+Alt+Q。')
        combination = sequence[0]
        modifiers = combination.keyboardModifiers()
        key = combination.key().value
        if (not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
                or modifiers & Qt.KeyboardModifier.MetaModifier or not (ord('A') <= key <= ord('Z') or ord('0') <= key <= ord('9'))):
            raise ValueError('请使用 Ctrl / Alt / Shift 搭配一个字母或数字。')
        native = (2 if modifiers & Qt.KeyboardModifier.ControlModifier else 0)
        native |= 1 if modifiers & Qt.KeyboardModifier.AltModifier else 0
        native |= 4 if modifiers & Qt.KeyboardModifier.ShiftModifier else 0
        return native | 0x4000, key  # MOD_NOREPEAT

    def configure(self, sequence):
        chord = self.chord(sequence)
        if chord == self.key:
            return
        previous = self.key
        if self.api:
            if previous:
                self.api.UnregisterHotKey(None, self.identifier)
            if not self.api.RegisterHotKey(None, self.identifier, *chord):
                if previous:
                    self.api.RegisterHotKey(None, self.identifier, *previous)
                raise ValueError('这个快捷键已被占用，请换一个组合。')
        self.key = chord

    def nativeEventFilter(self, event_type, message):
        if self.api and bytes(event_type) in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            from ctypes import wintypes
            event = wintypes.MSG.from_address(int(message))
            if event.message == 0x0312 and event.wParam == self.identifier:
                QTimer.singleShot(0, self.callback)
                return True, 0
        return False, 0

    def close(self):
        if self.api:
            if self.key:
                self.api.UnregisterHotKey(None, self.identifier)
            QApplication.instance().removeNativeEventFilter(self)
        self.key = None


class QuickAskWindow(NativeWindowFrame, QWidget):
    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setWindowTitle('米花 · 随手问')
        self.setWindowIcon(owner.agent.pet.windowIcon())
        self.setMinimumSize(300, 240)
        self.resize(360, 400)
        self.setStyleSheet('''
            QWidget { background: #fffaf3; color: #443c36; font: 13px "Microsoft YaHei"; }
            QPushButton { background: #eee4d6; border: none; border-radius: 7px; padding: 5px 8px; }
            QPushButton:hover { background: #e5d6c3; }
            QPushButton:checked { background: #d9e7dd; color: #355d44; }
            QPushButton:disabled { color: #aaa197; }
            QToolButton#quickMore { background: #eee4d6; border: none; border-radius: 7px; padding: 5px; }
            QToolButton#quickMore:hover, QToolButton#quickMore:pressed, QToolButton#quickMore:open { background: #d9e7dd; }
            QToolButton#quickMore::menu-indicator { image: none; width: 0; }
        ''')
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 8, 10, 8)
        body.setSpacing(5)
        self.pin = QPushButton('置顶')
        self.pin.setCheckable(True)
        self.pin.setToolTip('固定在其他窗口上方')
        self.pin.toggled.connect(self.set_pinned)
        self.expand = QPushButton('展开')
        self.expand.setToolTip('带着当前对话回到完整聊天')
        self.expand.clicked.connect(owner.expand)
        self.title_bar = QWidget()
        self.toolbar = QGridLayout(self.title_bar)
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.setSpacing(4)
        self.primary_tools = QWidget()
        tools = QHBoxLayout(self.primary_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(4)
        self.clipboard_button = QPushButton('解释复制')
        self.clipboard_button.setToolTip('读取刚复制的文字或图片，放入输入框；点击发送才会提交')
        self.clipboard_button.clicked.connect(owner.stage_clipboard)
        tools.addWidget(self.clipboard_button)
        self.new_button = QPushButton('新问题')
        self.new_button.clicked.connect(owner.new_question)
        tools.addWidget(self.new_button)
        self.lookup = QPushButton('联网查证')
        self.lookup.setCheckable(True)
        self.lookup.setToolTip('发送时检索网页摘要，结合来源回答；仅检索问题文字，不发送附件给搜索引擎')
        self.lookup.toggled.connect(self.set_lookup)
        tools.addWidget(self.lookup)
        self.secondary_tools = QWidget()
        secondary = QHBoxLayout(self.secondary_tools)
        secondary.setContentsMargins(0, 0, 0, 0)
        secondary.setSpacing(4)
        secondary.addWidget(self.pin)
        secondary.addWidget(self.expand)
        self.more = QToolButton()
        self.more.setText('···')
        self.more.setObjectName('quickMore')
        self.more.setFixedSize(32, 29)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setAccessibleName('随手问更多选项')
        self.more.setToolTip('更多选项')
        menu = QMenu(self.more)
        menu.setToolTipsVisible(True)
        menu.setStyleSheet('''
            QMenu { background: #fffdf8; color: #443c36; border: 1px solid #dcd8cc; padding: 5px; }
            QMenu::item { padding: 9px 18px; border-radius: 5px; }
            QMenu::item:selected { background: #d9e7dd; color: #294c37; }
            QMenu::item:disabled { color: #aaa197; }
        ''')
        self.shortcut_action = menu.addAction('设置全局快捷键', lambda: owner.dispatch_menu(owner.configure_shortcut))
        self.model_action = menu.addAction('模型设置', lambda: owner.dispatch_menu(owner.show_model_settings))
        self.capture_action = menu.addAction('收藏截至目前的全部对话到科研日志', lambda: owner.dispatch_menu(owner.capture_conversation))
        self.capture_action.setToolTip('先开始对话，再收藏到科研日志；打开草稿后需要点击保存记录')
        self.search_action = menu.addAction('用浏览器搜索', lambda: owner.dispatch_menu(owner.browser_search))
        self.search_action.setToolTip('搜索输入框中的文字，或最近一次问题')
        menu.aboutToShow.connect(self.refresh_menu)
        self.more.setMenu(menu)
        self.menu = menu
        secondary.addWidget(self.more)
        self.toolbar.addWidget(self.primary_tools, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.toolbar.addWidget(self.secondary_tools, 0, 1, Qt.AlignmentFlag.AlignRight)
        self.toolbar.setColumnStretch(0, 1)
        self._toolbar_wrapped = False
        body.addWidget(self.title_bar)
        self.chat_layout = QVBoxLayout()
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        body.addLayout(self.chat_layout, 1)
        self.hint = QLabel()
        self.hint.setStyleSheet('color: #82776c; font-size: 11px;')
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hint.hide()
        body.addWidget(self.hint)
        self.feedback_timer = QTimer(self)
        self.feedback_timer.setSingleShot(True)
        self.feedback_timer.setInterval(4500)
        self.feedback_timer.timeout.connect(self.restore_hint)
        self.escape = QShortcut(QKeySequence('Esc'), self)
        self.escape.activated.connect(self.hide)
        self.pin.setChecked(owner.preferences.value('pinned', True, type=bool))

    def set_lookup(self, enabled):
        self.owner.chat.lookup_enabled = enabled
        self.restore_hint()

    def restore_hint(self):
        self.feedback_timer.stop()
        self.hint.clear()
        self.hint.hide()

    def show_feedback(self, message):
        self.hint.setText(message)
        self.hint.setToolTip(message)
        self.hint.show()
        self.feedback_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        required = self.primary_tools.sizeHint().width() + self.secondary_tools.sizeHint().width() + 4
        wrapped = self.width() - 20 < required
        if wrapped != self._toolbar_wrapped:
            self.toolbar.removeWidget(self.secondary_tools)
            self.toolbar.addWidget(self.secondary_tools, 1 if wrapped else 0,
                                   0 if wrapped else 1, Qt.AlignmentFlag.AlignRight)
            self._toolbar_wrapped = wrapped

    def refresh_menu(self):
        self.capture_action.setEnabled(bool(self.owner.chat.history.messages))
        self.search_action.setEnabled(bool(self.owner.search_question()))

    def set_pinned(self, enabled):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.owner.preferences.setValue('pinned', enabled)
        if visible:
            self.show()
            self.show_feedback('已置顶' if enabled else '已取消置顶')

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        self.owner.preferences.setValue('geometry', self.saveGeometry())
        super().hideEvent(event)


class QuickAskController(QObject):
    def __init__(self, agent, *, register_shortcut=True):
        super().__init__(agent)
        self.agent = agent
        self.chat = agent.pet.chat
        self.main = agent.personal.window
        self.preferences = QSettings(str(Path(agent.base)/'data/quick-ask.ini'), QSettings.Format.IniFormat)
        self.window = QuickAskWindow(self)
        saved = self.preferences.value('geometry')
        if saved:
            self.window.restoreGeometry(saved)
        else:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            bounds = screen.availableGeometry()
            self.window.move(bounds.right()-self.window.width()-20, bounds.top()+60)
        self.detached = False
        self.closed = False
        self.shortcut_dialog = None
        self.chat.quick_requested.connect(self.show)
        self.chat.quick_submitted.connect(lambda text, files, web: agent.submit_chat(text, files, quick=True, web=web))
        self.chat.quick_controls = (self.window.clipboard_button, self.window.lookup)
        self.shortcut = GlobalShortcut(self.show, enabled=register_shortcut and QApplication.platformName() == 'windows')
        self.shortcut_text = self.preferences.value('shortcut', 'Ctrl+Alt+Q', type=str)
        self.shortcut_problem = ''
        try:
            self.shortcut.configure(QKeySequence(self.shortcut_text))
        except ValueError as exc:
            self.shortcut_problem = str(exc)
        self.chat.quick_button.setToolTip('随手问 · '+self.shortcut_text)

    def show(self):
        if self.closed:
            return
        if not self.detached:
            self.main.chat_layout.removeWidget(self.chat)
            self.window.chat_layout.addWidget(self.chat)
            self.chat.set_compact(True)
            self.detached = True
        self.main.hide()
        self.chat.show()
        self.clamp()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if self.chat.input.isEnabled():
            self.chat.input.setFocus()
        if self.shortcut_problem:
            self.chat.show_attachment_notice(self.shortcut_problem+' 可在右上角菜单修改快捷键。')

    def clamp(self):
        screens = QApplication.screens()
        screen = next((s for s in screens if s.availableGeometry().intersects(self.window.frameGeometry())), None)
        if screen is None:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            bounds = screen.availableGeometry()
            self.window.move(bounds.right()-self.window.width()-20, bounds.top()+60)
        bounds = screen.availableGeometry()
        frame = self.window.frameGeometry()
        self.window.resize(min(self.window.width(), max(300, bounds.width()-16)),
                           min(self.window.height(), max(240, bounds.height()-48)))
        self.window.move(max(bounds.left(), min(self.window.x(), bounds.right()-frame.width()+1)),
                         max(bounds.top(), min(self.window.y(), bounds.bottom()-frame.height()+1)))

    def attach_to_main(self):
        if not self.detached:
            return
        self.window.hide()
        self.window.chat_layout.removeWidget(self.chat)
        self.main.chat_layout.addWidget(self.chat)
        self.chat.set_compact(False)
        self.chat.show()
        self.detached = False

    def expand(self):
        self.attach_to_main()
        self.agent.personal.show_page('chat')

    def stage_clipboard(self):
        if self.agent.busy:
            return
        mime = QApplication.clipboard().mimeData()
        if mime.hasImage() or mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
            self.chat.add_mime_attachments(mime)
            if not self.chat.input.text():
                self.chat.input.setText('请简短解释这些内容。')
            return
        text = mime.text().strip() if mime.hasText() else ''
        if not text:
            self.chat.show_attachment_notice('先复制一个词或一段文字，也可以复制截图后再点这里。')
            return
        if len(text) > 18000:
            self.chat.show_attachment_notice('复制内容过长，请只复制需要解释的一段（最多 18000 字符）。')
            return
        # Never overwrite an existing draft or submit an OS clipboard command.
        prefix = self.chat.input.text()
        self.chat.input.setText(prefix + ('\n\n' if prefix else '') + '请解释以下内容：\n'+text)
        self.chat.input.setFocus()

    def new_question(self):
        self.chat.history.clear_conversation()
        self.chat.input.clear()
        self.chat._quick_draft = ''
        self.chat.show_attachment_notice('')
        self.window.lookup.setChecked(False)
        self.window.show_feedback('已开始新问题')
        if self.chat.input.isEnabled():
            self.chat.input.setFocus()

    def dispatch_menu(self, callback):
        self.window.menu.hide()
        # Let the popup release its mouse/focus grab before activating a dialog.
        QTimer.singleShot(0, self, lambda: callback() if not self.closed else None)

    def present_dialog(self, dialog):
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.window.pin.isChecked())
        dialog.showNormal()
        bounds = self.window.screen().availableGeometry()
        dialog.resize(min(dialog.width(), bounds.width()-24), min(dialog.height(), bounds.height()-24))
        frame = dialog.frameGeometry()
        center = self.window.frameGeometry().center()
        dialog.move(max(bounds.left(), min(center.x()-frame.width()//2, bounds.right()-frame.width()+1)),
                    max(bounds.top(), min(center.y()-frame.height()//2, bounds.bottom()-frame.height()+1)))
        dialog.raise_()
        dialog.activateWindow()

    def show_model_settings(self):
        self.agent.show_settings()
        if self.agent.dialog:
            self.present_dialog(self.agent.dialog)
            self.window.show_feedback('模型设置已打开')

    def capture_conversation(self):
        if not self.chat.history.messages:
            self.window.show_feedback('暂无可收藏的对话')
            return
        journal = self.agent.journal
        count = len(journal.editors)
        self.chat.history.capture_conversation()
        if len(journal.editors) > count:
            editor = journal.editors[-1]
            self.present_dialog(editor)
            self.window.show_feedback('日志草稿已打开，保存后完成收藏')
            editor.finished.connect(lambda _: self.window.show_feedback(
                '已保存到科研日志' if editor.saved else '已取消收藏') if not self.closed else None)
        else:
            self.window.show_feedback(journal.message or '日志草稿未能打开，请重试')

    def search_question(self):
        question = self.chat.input.text().strip()
        if not question and self.chat.history.questions:
            question = self.chat.history.questions[-1][0]
        return question

    def browser_search(self):
        from .web_lookup import browser_search_url
        from PySide6.QtCore import QUrl
        question = self.search_question()
        if question:
            opened = QDesktopServices.openUrl(QUrl(browser_search_url(question)))
            self.window.show_feedback('已请求浏览器搜索' if opened else '未能打开浏览器，请重试')
        else:
            self.window.show_feedback('先输入问题，再用浏览器搜索')

    def configure_shortcut(self):
        if self.shortcut_dialog:
            self.present_dialog(self.shortcut_dialog)
            return
        dialog = QDialog(self.window)
        self.shortcut_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('随手问快捷键')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('在任何应用中唤起随手问：'))
        edit = QKeySequenceEdit(QKeySequence(self.shortcut_text))
        edit.setMaximumSequenceLength(1)
        layout.addWidget(edit)
        notice = QLabel('使用 Ctrl / Alt / Shift 搭配字母或数字。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        def save():
            try:
                self.shortcut.configure(edit.keySequence())
            except ValueError as exc:
                notice.setText(str(exc))
                return
            self.shortcut_text = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            self.preferences.setValue('shortcut', self.shortcut_text)
            self.chat.quick_button.setToolTip('随手问 · '+self.shortcut_text)
            self.shortcut_problem = ''
            self.window.show_feedback('快捷键已保存：'+self.shortcut_text)
            dialog.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda _: setattr(self, 'shortcut_dialog', None))
        self.present_dialog(dialog)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.shortcut_dialog:
            self.shortcut_dialog.close()
        self.shortcut.close()
        self.attach_to_main()
        self.preferences.sync()
        self.window.hide()
        self.window.deleteLater()
