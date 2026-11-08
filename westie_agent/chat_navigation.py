"""Question markers, previews and local conversation clearing for Qt chat."""
from html import escape
import weakref

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QTextCursor
from PySide6.QtWidgets import QScrollBar, QTextBrowser, QToolTip


class QuestionScrollBar(QScrollBar):
    def __init__(self, history):
        super().__init__(Qt.Orientation.Vertical, history)
        self.history = weakref.proxy(history)
        self.setFixedWidth(21)
        self.setMouseTracking(True)
        self.setStyleSheet('QToolTip { background: #f4f1e8; color: #443c36; border: 1px solid #dcdace; border-radius: 7px; padding: 9px; font: 13px "Microsoft YaHei"; }')
        self.drag_offset = None
        self.setAccessibleName('对话问题导航')
        self.valueChanged.connect(self.update)
        self.rangeChanged.connect(self.update)

    def marker_positions(self):
        count = len(self.history.questions)
        if not count:
            return []
        # Give each question its own target, even when two short turns are adjacent.
        span = max(0, self.height()-24)
        return [12 + span*(i+0.5)/count for i in range(count)]

    def marker_at(self, point):
        positions = self.marker_positions()
        if point.x() < 9 or not positions:
            return None
        index = min(range(len(positions)), key=lambda i: abs(positions[i]-point.y()))
        tolerance = max(4, min(8, self.height()/max(1, len(positions))/2))
        return index if abs(positions[index]-point.y()) <= tolerance else None

    def thumb(self):
        height = max(20, self.height()*self.pageStep()/max(1, self.maximum()+self.pageStep()))
        height = min(self.height(), height)
        top = (self.height()-height)*self.value()/max(1, self.maximum())
        return QRectF(3, top, 3, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.maximum() > 0:
            painter.setBrush(QColor('#c6c4b8'))
            painter.drawRoundedRect(self.thumb(), 1.5, 1.5)
        for y in self.marker_positions():
            painter.setBrush(QColor('#7e9683'))
            painter.drawRoundedRect(QRectF(11, y-1, 6, 2), 1, 1)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.marker_at(event.pos())
            if index is not None:
                text = self.history.questions[index][0]
                preview = text[:500] + ('…' if len(text) > 500 else '')
                content = escape(preview).replace('\n', '<br>')
                QToolTip.showText(event.globalPos(), '<table width="300"><tr><td style="font-size:13px">'+content+'</td></tr></table>', self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        index = self.marker_at(event.position().toPoint())
        if index is not None:
            self.history.jump_to_question(index)
            return
        thumb = self.thumb()
        self.drag_offset = event.position().y()-thumb.top() if thumb.contains(event.position()) else thumb.height()/2
        self.move_thumb(event.position().y())

    def move_thumb(self, y):
        span = self.height()-self.thumb().height()
        if span > 0:
            self.setValue(round((y-self.drag_offset)/span*self.maximum()))

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            self.move_thumb(event.position().y())
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.marker_at(event.position().toPoint()) is not None else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_offset = None
        super().mouseReleaseEvent(event)


class ChatHistory(QTextBrowser):
    conversation_cleared = Signal()
    capture_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.questions = []
        self.messages = []
        self.message_images = []
        self.compact = False
        self.setVerticalScrollBar(QuestionScrollBar(self))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.document().contentsChanged.connect(self.contents_changed)

    def loadResource(self, resource_type, url):
        return None

    def resizeEvent(self, event):
        bar = self.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 8
        super().resizeEvent(event)
        if at_bottom:
            QTimer.singleShot(0, self, self.reveal_latest)

    def scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def reveal_latest(self):
        if self.compact and self.messages:
            # In a short viewport, show the start of the answer, not its tail.
            cursor = self.messages[-1][2]
            top = self.document().documentLayout().blockBoundingRect(cursor.block()).top()
            self.verticalScrollBar().setValue(max(0, round(top)-8))
        else:
            self.scroll_to_bottom()

    def contents_changed(self):
        self.verticalScrollBar().update()

    def clear(self):
        # Release anchored cursors before Qt removes their document blocks.
        self.questions.clear()
        self.messages.clear()
        self.message_images.clear()
        super().clear()

    def add_question(self, text, cursor):
        anchor = QTextCursor(cursor)
        anchor.setKeepPositionOnInsert(True)
        self.questions.append((text, anchor))
        self.verticalScrollBar().update()

    def jump_to_question(self, index):
        if 0 <= index < len(self.questions):
            cursor = self.questions[index][1]
            self.setTextCursor(cursor)
            top = self.document().documentLayout().blockBoundingRect(cursor.block()).top()
            self.verticalScrollBar().setValue(round(top)-8)

    def remember_message(self,speaker,text,start,end,*,attachments=()):
        anchor=QTextCursor(start);anchor.setKeepPositionOnInsert(True)
        finish=QTextCursor(end);finish.setKeepPositionOnInsert(True)
        self.messages.append((speaker,text,anchor,finish))
        self.message_images.append(tuple(a for a in attachments if a.is_image))

    def capture_message(self, index, *, include_question=False):
        if not 0 <= index < len(self.messages):
            return
        speaker, text, _, _ = self.messages[index]
        question_index = next((i for i in range(index, -1, -1) if self.messages[i][0] == '你'), None)
        question = self.messages[question_index][1] if question_index is not None else ''
        images = list(self.message_images[index])
        if question_index is not None and question_index != index:
            images = list(self.message_images[question_index]) + images
        self.capture_requested.emit({'body': text, 'question': question,
                                     'include_question': include_question, 'images': tuple(images)})

    def capture_conversation(self):
        # Snapshot at activation, including any question currently waiting for a reply.
        messages = tuple({'speaker': m[0], 'text': m[1], 'images': self.message_images[i]}
                         for i, m in enumerate(self.messages))
        if messages:
            self.capture_requested.emit({'scope': 'conversation', 'messages': messages})

    def conversation_menu(self,position=None):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        selected=self.textCursor().selectedText().replace('\u2029','\n')
        point=self.cursorForPosition(position).position() if position is not None else self.textCursor().position()
        index=next((i for i,m in reversed(list(enumerate(self.messages))) if m[2].position()<=point<=m[3].position()),None)
        if index is None and position is None and self.messages:index=len(self.messages)-1
        if selected:
            collect=menu.addAction('收藏选中文字到科研日志')
            collect.triggered.connect(lambda:self.capture_requested.emit({'body':selected}))
        if index is not None:
            speaker,text,_,_=self.messages[index]
            question=next((m[1] for m in reversed(self.messages[:index+1]) if m[0]=='你'),'')
            collect=menu.addAction('收藏'+('问题' if speaker=='你' else '回答')+'到科研日志')
            collect.triggered.connect(lambda:self.capture_message(index))
            if question and speaker!='你':
                pair=menu.addAction('连同问题收藏到科研日志')
                pair.triggered.connect(lambda:self.capture_message(index, include_question=True))
            menu.addSeparator()
        entire = menu.addAction('收藏截至目前的全部对话到科研日志')
        entire.setEnabled(bool(self.messages))
        entire.triggered.connect(self.capture_conversation)
        menu.addSeparator()
        action = menu.addAction('清空对话记录')
        action.setEnabled(not self.document().isEmpty())
        action.triggered.connect(self.clear_conversation)
        return menu

    def contextMenuEvent(self, event):
        menu = self.conversation_menu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def clear_conversation(self):
        self.clear()
        self.questions.clear()
        self.conversation_cleared.emit()
