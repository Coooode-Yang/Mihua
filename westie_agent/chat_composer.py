"""Multi-line input with image/file clipboard and local drag-and-drop support."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit


class ChatInput(QPlainTextEdit):
    COMPACT_HEIGHT = 36
    returnPressed = Signal()
    attachment_pasted = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(60)
        self.setTabChangesFocus(True)
        self.setAccessibleName('聊天输入，Enter 发送，Shift+Enter 换行')
        self._compact = False
        self._preediting = False
        self._placeholder_text = ''
        self.textChanged.connect(self.adjust_height)
        self.textChanged.connect(self.sync_placeholder)

    def setPlaceholderText(self, text):
        self._placeholder_text = text
        self.sync_placeholder()

    def sync_placeholder(self):
        # IME preedit text is not part of toPlainText() until it is committed.
        # Hide the hint during composition as well as for committed content.
        visible = not self._preediting and self.document().isEmpty()
        hint = self._placeholder_text
        if self._compact and 'Esc 退出随手问' in hint:
            if self.fontMetrics().horizontalAdvance(hint) > self.viewport().width() - 8:
                hint = 'Enter 发送 · Esc 退出随手问'
        super().setPlaceholderText(hint if visible else '')

    def inputMethodEvent(self, event):
        self._preediting = bool(event.preeditString())
        self.sync_placeholder()
        super().inputMethodEvent(event)
        self.sync_placeholder()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._preediting = False
        self.sync_placeholder()

    def set_compact(self, compact):
        self._compact = compact
        self.setAccessibleName('聊天输入，Enter 发送，Shift+Enter 换行' + ('，Esc 退出随手问' if compact else ''))
        self.adjust_height()
        self.sync_placeholder()

    def adjust_height(self):
        if not self._compact:
            self.setFixedHeight(60)
            return
        # Keep the mini composer aligned; longer drafts scroll within the field.
        self.setFixedHeight(self.COMPACT_HEIGHT)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._compact:
            self.adjust_height()
            self.sync_placeholder()

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)
        self.moveCursor(QTextCursor.MoveOperation.End)

    def keyPressEvent(self, event):
        if self._preediting:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasUrls() or source.hasImage():
            self.attachment_pasted.emit(source)
        else:
            super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            self.attachment_pasted.emit(event.mimeData())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
