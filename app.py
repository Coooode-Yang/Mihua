"""Windows chick desktop agent with local inference and approved networking.

Chat/UI stay on Qt's main thread; diagnosis and confirmed actions run in workers.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from pathlib import Path

if any(flag in sys.argv for flag in ("--update-self-test", "--chick-self-test", "--focus-style-self-test", "--dialog-chrome-self-test", "--sedentary-self-test", "--web-shortcuts-self-test", "--quick-ask-self-test", "--self-test", "--model-self-test", "--diagnosis-self-test", "--api-self-test", "--offline-self-test", "--personal-self-test", "--vocabulary-self-test", "--conversion-self-test", "--workspace-self-test", "--reader-self-test", "--journal-self-test", "--unified-self-test", "--attachments-self-test", "--growth-self-test", "--iphone-sync-self-test")):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLockFile, QPoint, QPointF, QRectF, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QRegion, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocumentFragment, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PET_SIZE = 140
PET_SIZES = (140, 190, 240)
if '--growth-self-test' in sys.argv:
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')


def application_icon():
    return QIcon(str(ROOT/'assets/ui/chick-head.ico'))


from westie_agent.chat_navigation import ChatHistory
from westie_agent.chat_composer import ChatInput
from westie_agent.chat_attachment_ui import AttachmentComposer


class SpriteBank:
    """Read the 3-column x 2-row sheet, aligning each pose to a common floor.

    The source OBJ is rendered offline into transparent PNG sheets. Runtime QPixmaps are just
    render resources, allowing a future 12/24-frame sheet to use the same UI.
    """

    def __init__(self, path: Path):
        import json
        metadata = json.loads(path.with_name('chick-assets.json').read_text(encoding='utf-8'))
        fixed = metadata.get('fixed_canvas', False)
        self.frames = self._load_grid(path, columns=3, rows=2, fixed_canvas=fixed)
        columns, rows = metadata['animation_grid']
        for attribute, filename in [('search_frames', 'chick-search-sheet.png'),
                                    ('think_frames', 'chick-think-sheet.png'),
                                    ('sleep_frames', 'chick-sleep-sheet.png')]:
            setattr(self, attribute, self._load_grid(path.with_name(filename), columns=columns, rows=rows, fixed_canvas=fixed))

    @staticmethod
    def animation_index(elapsed: float, interval: float, intro_interval: float = .14) -> int:
        """Play a four-frame entrance once, then loop the eight settled frames."""
        elapsed = max(0.0, elapsed)
        entrance = 4 * intro_interval
        if elapsed < entrance:
            return min(3, int(elapsed / intro_interval))
        return 4 + int((elapsed - entrance) / interval) % 8

    @staticmethod
    def _load_grid(path: Path, columns: int, rows: int, fixed_canvas: bool = False) -> list[QPixmap]:
        image = QImage(str(path))
        if image.isNull():
            raise RuntimeError(f"无法读取小鸡素材：{path}")
        if not image.hasAlphaChannel():
            raise RuntimeError("小鸡素材需要带透明通道的 PNG。")
        tiles = []
        for row in range(rows):
            for col in range(columns):
                x1, x2 = col * image.width() // columns, (col + 1) * image.width() // columns
                y1, y2 = row * image.height() // rows, (row + 1) * image.height() // rows
                tile = image.copy(x1, y1, x2 - x1, y2 - y1)
                bounds = QRegion(QPixmap.fromImage(tile).mask()).boundingRect()
                if bounds.isEmpty():
                    raise RuntimeError(f"素材中有空白动作格：{path.name}")
                tiles.append(QPixmap.fromImage(tile if fixed_canvas else tile.copy(bounds)))
        if fixed_canvas:
            return tiles
        ratio = min(352 / max(p.width() for p in tiles), 352 / max(p.height() for p in tiles))
        frames: list[QPixmap] = []
        for tile in tiles:
            tile = tile.scaled(
                round(tile.width() * ratio), round(tile.height() * ratio),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            frame = QPixmap(384, 384)
            frame.fill(Qt.GlobalColor.transparent)
            painter = QPainter(frame)
            painter.drawPixmap((384 - tile.width()) // 2, 372 - tile.height(), tile)
            painter.end()
            frames.append(frame)
        return frames


class ChatPanel(AttachmentComposer, QWidget):
    submitted = Signal(str)
    attachments_submitted = Signal(str, object)
    action_requested = Signal(str)
    campus_requested = Signal()
    workspace_requested = Signal(str)
    spelling_requested = Signal()
    conversion_requested = Signal()
    conversation_cleared = Signal()
    growth_requested = Signal(str)
    update_requested = Signal()
    quick_requested = Signal()
    quick_submitted = Signal(str, object, bool)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool)
        self.setWindowTitle("米花 · 聊天")
        self.resize(740, 600)
        self.setMinimumSize(300, 220)
        self.quick_mode = False
        self.lookup_enabled = False
        self._quick_draft = ''
        self.setStyleSheet("""
            QWidget { background: #fffaf3; color: #443c36; font-family: 'Microsoft YaHei'; font-size: 13px; }
            QLabel#heading { font-size: 24px; font-weight: bold; }
            QTextBrowser { background: #ffffff; border: 1px solid #eadfce; border-radius: 12px; padding: 10px; }
            QLineEdit, QPlainTextEdit { background: #fff; border: 1px solid #deccb6; border-radius: 9px; padding: 10px; }
            QPushButton { background: #f0e5d6; border: none; border-radius: 9px; padding: 9px 12px; }
            QPushButton:hover { background: #e6d6c2; }
            QPushButton#send { background: #d9e7dd; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.quick_bar = QWidget()
        quick_row = QHBoxLayout(self.quick_bar)
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.addStretch()
        self.quick_button = QPushButton('随手问 ↗')
        self.quick_button.setToolTip('打开迷你浮窗，在其他页面旁提问')
        self.quick_button.clicked.connect(self.quick_requested.emit)
        quick_row.addWidget(self.quick_button)
        self.save_conversation_button = QPushButton('保存全部对话')
        self.save_conversation_button.setToolTip('将截至目前的全部聊天文字和图片收藏到科研日志，预览后保存')
        self.save_conversation_button.clicked.connect(lambda: self.history.capture_conversation())
        quick_row.insertWidget(0, self.save_conversation_button)
        layout.addWidget(self.quick_bar)
        # Shared callbacks used by the main window's toolbox popup and pet menu.
        self.tool_actions = QWidget(self)
        tools_layout = QVBoxLayout(self.tool_actions)
        tools_layout.setContentsMargins(0,0,0,0)
        self.settings_button = QPushButton("模型设置")
        self.campus_button = QPushButton('网页快捷入口 · 带路小鸡')
        self.campus_button.clicked.connect(self.campus_requested.emit)
        self.conversion_button = QPushButton('文件转换')
        self.conversion_button.clicked.connect(self.conversion_requested.emit)
        for button in (self.campus_button,self.conversion_button,self.settings_button): tools_layout.addWidget(button)
        self.tool_actions.hide()
        self.history = ChatHistory()
        self.history.setOpenExternalLinks(False)
        self.history.setOpenLinks(False)
        self.history.anchorClicked.connect(self.open_source)
        self.history.conversation_cleared.connect(self.conversation_cleared.emit)
        self.history.conversation_cleared.connect(self.clear_actions)
        layout.addWidget(self.history, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: #f2eadf; color: #80644c; border-radius: 8px; padding: 7px;")
        self.status.hide()
        layout.addWidget(self.status)
        self._thinking_detail = ""
        self._thinking_tick = 0
        self.thinking_timer = QTimer(self)
        self.thinking_timer.setInterval(450)
        self.thinking_timer.timeout.connect(self._advance_thinking)
        self.action_box = QWidget()
        self.action_layout = QVBoxLayout(self.action_box)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_box.hide()
        layout.addWidget(self.action_box)
        entry = QHBoxLayout()
        self.input = ChatInput()
        self.input.setPlaceholderText("输入消息，或粘贴图片、拖入文件…\nEnter 发送，Shift+Enter 换行")
        self.input.returnPressed.connect(self.send)
        self.input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.input.customContextMenuRequested.connect(self.input_context_menu)
        self.setup_attachments(layout, entry)
        entry.addWidget(self.input, 1)
        send = QPushButton("发送")
        self.send_button = send
        send.setObjectName("send")
        send.clicked.connect(self.send)
        entry.addWidget(send)
        layout.addLayout(entry)

    def input_context_menu(self, position):
        menu = self.input.createStandardContextMenu()
        menu.addSeparator()
        clear = menu.addAction('清空对话记录')
        clear.setEnabled(not self.history.document().isEmpty())
        clear.triggered.connect(self.history.clear_conversation)
        save_all = menu.addAction('收藏截至目前的全部对话到科研日志')
        save_all.setEnabled(bool(self.history.messages))
        save_all.triggered.connect(self.history.capture_conversation)
        paste_image = menu.addAction('粘贴图片或文件')
        mime = QApplication.clipboard().mimeData()
        paste_image.setEnabled(self.send_button.isEnabled() and (mime.hasImage() or mime.hasUrls()))
        paste_image.triggered.connect(lambda:self.add_mime_attachments(QApplication.clipboard().mimeData()))
        menu.exec(self.input.mapToGlobal(position))
        menu.deleteLater()

    def add_message(self, speaker: str, message: str, *, markdown=False, attachments=()):
        color = "#567a62" if speaker == "你" else "#9c7152"
        cursor = QTextCursor(self.history.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        body_format = QTextBlockFormat()
        body_format.setBottomMargin(5)
        body_format.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        normal = QTextCharFormat()
        normal.setFont(self.history.font())
        if cursor.block().length() > 1:
            cursor.insertBlock(body_format, normal)
        cursor.setBlockFormat(body_format)
        if speaker == '你':
            self.history.add_question(message, cursor)
        message_start = QTextCursor(cursor)
        message_start.setKeepPositionOnInsert(True)
        speaker_format = QTextCharFormat(normal)
        speaker_format.setForeground(QColor(color))
        speaker_format.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(speaker, speaker_format)
        cursor.insertBlock(body_format, normal)
        if markdown:
            from westie_agent.chat_formatting import reply_document
            document = reply_document(message, self.history.font())
            cursor.insertFragment(QTextDocumentFragment(document))
        else:
            # insertText keeps user content and local reminders literal.
            cursor.insertText(message, normal)
        for index, attachment in enumerate(attachments):
            if attachment.is_image:
                image = QImage.fromData(attachment.data)
                # Resources stay in this document only; remote image loading stays blocked.
                url = QUrl(f'chat-attachment:{len(self.history.messages)}-{index}')
                self.history.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
                cursor.insertBlock(body_format, normal)
                image_format = QTextImageFormat()
                image_format.setName(url.toString())
                scale = min(1, 280/image.width(), 180/image.height())
                image_format.setWidth(image.width()*scale)
                image_format.setHeight(image.height()*scale)
                cursor.insertImage(image_format)
        self.history.remember_message(speaker,message,message_start,cursor,attachments=attachments)
        cursor.insertBlock(body_format, normal)
        bar = self.history.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, self.history, self.history.reveal_latest)

    def send(self):
        if not self.send_button.isEnabled():
            return
        text = self.input.text().strip()
        if not text and not self.attachments:
            return
        if len(text) > 20000:
            self.show_attachment_notice('消息最多 20000 字符，长文字可保存为文本附件。')
            return
        self.input.clear()
        if self.quick_mode:
            self._quick_draft = text
            attachments = tuple(self.attachments)
            self._attachment_question = text
            display = text + ('\n' if text and attachments else '') + '\n'.join('附件：'+a.name for a in attachments)
            self.add_message('你', display, attachments=attachments)
            self.quick_submitted.emit(text, attachments, self.lookup_enabled)
            return
        if self.attachments:
            attachments = tuple(self.attachments)
            self._attachment_question = text
            display = text + ('\n' if text else '') + '\n'.join('附件：'+a.name for a in attachments)
            self.add_message('你', display, attachments=attachments)
            self.attachments_submitted.emit(text, attachments)
        else:
            self.add_message("你", text)
            self.submitted.emit(text)

    def set_compact(self, compact):
        self.quick_mode = compact
        self.history.compact = compact
        self.quick_bar.setVisible(not compact)
        if compact:
            self.setMinimumSize(0, 0)
        else:
            self.setMinimumSize(300, 220)
        self.layout().setContentsMargins(0, 0 if compact else 8, 0, 0)
        self.layout().setSpacing(5 if compact else 6)
        self.attach_button.setText('＋' if compact else '附件')
        self.attach_button.setAccessibleName('添加附件')
        self.attach_button.setStyleSheet('padding: 6px 8px;' if compact else '')
        self.send_button.setStyleSheet('padding: 6px 8px;' if compact else '')
        for button in (self.attach_button, self.send_button):
            button.setMinimumHeight(ChatInput.COMPACT_HEIGHT if compact else 0)
            button.setMaximumHeight(ChatInput.COMPACT_HEIGHT if compact else 16777215)
        self.input.setStyleSheet('padding: 3px 8px;' if compact else '')
        self.input.setPlaceholderText('问个概念…  Enter 发送 · Esc 退出随手问' if compact else '输入消息，或粘贴图片、拖入文件…\nEnter 发送，Shift+Enter 换行')
        self.input.set_compact(compact)
        self.attachment_list.setFixedHeight(54 if compact else 78)

    def open_source(self, url):
        from westie_agent.web_lookup import safe_source_url
        if safe_source_url(url.toString()):
            QDesktopServices.openUrl(url)

    def quick_send_finished(self, success):
        if not success and self._quick_draft and not self.input.text():
            self.input.setText(self._quick_draft)
        self._quick_draft = ''

    def set_busy(self, busy: bool):
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.attach_button.setEnabled(not busy)
        if hasattr(self, 'quick_controls'):
            for control in self.quick_controls:
                control.setEnabled(not busy)
        self.remove_attachment_button.setEnabled(not busy)
        self.status.setVisible(busy)
        if busy:
            self._thinking_tick = 0
            self._thinking_detail = ""
            self._render_thinking()
            self.thinking_timer.start()
        else:
            self.thinking_timer.stop()
            self._thinking_detail = ""
            self.status.clear()

    def set_progress(self, text: str):
        self._thinking_detail = text
        if self.thinking_timer.isActive():
            self._render_thinking()

    def _advance_thinking(self):
        self._thinking_tick += 1
        self._render_thinking()

    def _render_thinking(self):
        text = "正在思考" + "." * (1 + self._thinking_tick % 3)
        if self._thinking_detail:
            text += "\n" + self._thinking_detail
        self.status.setText(text)

    def clear_actions(self):
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            item.widget().deleteLater()
        self.action_box.hide()

    def add_action(self, title, callback):
        button = QPushButton("查看方案：" + title[:36])
        button.clicked.connect(callback)
        self.action_layout.addWidget(button)
        self.action_box.show()


class PetWindow(QWidget):
    def __init__(self, sprites: SpriteBank, use_tray: bool = True):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setWindowTitle("米花 · 桌面小鸡")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.sprites = sprites
        self.agent_dispatch = None
        self.pet_size = DEFAULT_PET_SIZE
        self.agent_busy = False
        self.focus_active = False
        self.reading_unlocked = False
        self.equipped = {}
        self.state = "idle"
        self.state_started = time.monotonic()
        self.state_until = 0.0
        self.bubble = "叽！鼠标划过头顶，摸摸我吧"
        self.bubble_until = time.monotonic() + 8
        self.next_blink = time.monotonic() + random.uniform(2.5, 5.0)
        self.blink_until = 0.0
        self.pet_count = 0
        self._press_global: QPoint | None = None
        self._press_window = QPoint()
        self._dragging = False
        self._last_hover: QPointF | None = None
        self._stroke_distance = 0.0
        self._last_stroke = 0.0
        self._last_pet = 0.0
        self.paused = False
        self._pause_started: float | None = None
        self._frame = QPixmap()
        self.chat = ChatPanel()
        self.setWindowIcon(application_icon())
        self.workspace_dispatch = None
        self.chat.submitted.connect(self.handle_text)
        self.chat.action_requested.connect(self.perform)
        self.tray: QSystemTrayIcon | None = None
        self.set_pet_size(DEFAULT_PET_SIZE)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 12)
        if use_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(application_icon(), self)
            self.tray.setToolTip("米花 · 单击打开主界面，划过头顶抚摸")
            self.tray.setContextMenu(self.make_menu())
            self.tray.activated.connect(self.tray_activated)
            self.tray.messageClicked.connect(self.show_chat)
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.tick()

    @property
    def head_rect(self) -> QRectF:
        return QRectF(12 + self.pet_size * .18, 60 + self.pet_size * .05,
                      self.pet_size * .62, self.pet_size * .46)

    def set_pet_size(self, size: int):
        self.pet_size = size
        self.setFixedSize(size + 24, size + 76)
        if not self._frame.isNull():
            self.clamp_position()
            self.render_frame()

    def clamp_position(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.move(max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)))

    def say(self, text: str, duration: float = 4.0):
        self.bubble = text
        self.bubble_until = time.monotonic() + duration

    def set_state(self, state: str, duration: float = 0.0):
        if self.paused:
            self.toggle_pause()
        self.state = state
        self.state_started = time.monotonic()
        self.state_until = self.state_started + duration if duration else 0.0
        self.tick()

    def perform(self, action: str):
        if action == "pet":
            self.pet_count += 1
            self._last_pet = time.monotonic()
            self.set_state("pet", 2.6)
            self.say(random.choice(["舒服～再摸摸！", "叽！最喜欢你啦", "嘿嘿，翅膀停不下来啦"]), 2.8)
        elif action == "jump":
            self.set_state("jump", 1.8)
            self.say("接住我的开心！", 2.0)
        elif action == "sleep":
            self.set_state("sleep")
            self.say("呼……点击我，或者说「醒醒」", 4.0)
        elif action == "wake":
            self.set_state("idle")
            self.say("醒啦！我在呢。", 3.0)
        self.render_frame()
        self.update()

    def handle_text(self, text: str):
        local = text.strip().lower().rstrip("!！~～。 ")
        local_commands = {"醒醒", "起床", "醒来", "睡觉", "晚安", "摸摸", "乖", "可爱", "跳一下", "跳舞", "开心", "你好", "嗨", "米花", "帮助"}
        if self.agent_dispatch is not None and local not in local_commands:
            self.agent_dispatch(text)
            return
        if any(word in text for word in ["醒醒", "起床", "醒来"]):
            action, reply = "wake", "醒啦！今天也陪着你。"
        elif any(word in text for word in ["睡", "晚安"]):
            action, reply = "sleep", "好呀，陪你安静待一会儿。"
        elif any(word in text for word in ["摸", "乖", "可爱"]):
            action, reply = "pet", "叽～被你摸摸很开心！"
        elif any(word in text for word in ["跳", "开心", "跳舞"]):
            action, reply = "jump", "看我跳一下！"
        elif any(word.lower() in text.lower() for word in ["网络", "联网", "断网", "vpn", "蓝牙", "wifi"]):
            action, reply = "curious", "我现在是互动原型，还没接入网络诊断工具。下一阶段会加入这项本领。"
        elif any(word in text for word in ["你好", "嗨", "米花", "帮助"]):
            action, reply = "pet", "叽！试试说：摸摸、跳一下、睡觉、醒醒。"
        else:
            action, reply = "curious", "叽，收到啦！我现在会回应简单指令，试试「摸摸」或「跳一下」。"
        if action == "curious":
            self.set_state("curious", 3)
            self.say("歪头听你说～", 3)
        else:
            self.perform(action)
        self.chat.add_message("米花", reply)

    def set_agent_status(self, status: str):
        """Main-thread visual feedback for actual Agent work, not generated prose."""
        states = {
            "checking": ("search", "让我拿放大镜找一找…", 0),
            "thinking": ("curious", "正在准备，请稍等…", 0),
            "offline": ("curious", "发现网络异常，点击查看", 0),
            "success": ("pet", "连接恢复啦！", 3),
            "idle": ("idle", "我在这里陪你", 3),
        }
        if status not in states:
            raise ValueError(f"不支持的状态：{status}")
        self.agent_busy = status == "checking"
        state, text, duration = states[status]
        self.set_state(state, duration)
        self.say(text, duration or 3600)

    def show_chat(self):
        if self.state == "sleep":
            self.perform("wake")
        if self.workspace_dispatch is not None:
            opener=self.workspace_dispatch()
            if opener is not None:
                opener('home')
                return
        if not self.chat.isVisible():
            screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
            bounds = screen.availableGeometry()
            x = self.x() - self.chat.width() - 12
            if x < bounds.left():
                x = self.x() + self.width() + 12
            x = max(bounds.left(), min(x, bounds.right() - self.chat.width() + 1))
            y = max(bounds.top(), min(self.y(), bounds.bottom() - self.chat.height() + 1))
            self.chat.move(x, y)
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.input.setFocus()

    def toggle_pause(self):
        now = time.monotonic()
        if self.paused:
            paused_for = now - (self._pause_started or now)
            self.state_started += paused_for
            if self.state_until:
                self.state_until += paused_for
            self.next_blink += paused_for
            self.blink_until += paused_for
            self.bubble_until += paused_for
            self._pause_started = None
            self.paused = False
            self.timer.start()
        else:
            self.paused = True
            self._pause_started = now
            self.timer.stop()

    def make_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("打开主界面", self.show_chat)
        menu.addAction("和米花说话", lambda:self.chat.workspace_requested.emit('chat'))
        menu.addAction('随手问 · 迷你浮窗', self.chat.quick_requested.emit)
        for title, page in [('学习 · 番茄钟', 'study'), ('研究', 'research'), ('日常 · 待办清单', 'daily')]:
            menu.addAction(title, lambda checked=False, value=page: self.chat.workspace_requested.emit(value))
        menu.addAction('背单词 · 拼写练习', self.chat.spelling_requested.emit)
        menu.addAction('久坐提醒设置', lambda: self.sedentary_dispatch()())
        toolbox = menu.addMenu('工具箱')
        menu._toolbox = toolbox  # Keep PySide wrappers alive for the lifetime of the menu.
        toolbox.addAction('网页快捷入口 · 带路小鸡', self.chat.campus_requested.emit)
        toolbox.addAction('文件转换', self.chat.conversion_requested.emit)
        toolbox.addAction('模型设置', self.chat.settings_button.click)
        interaction = menu.addMenu('互动')
        menu._interaction = interaction
        interaction.addAction("摸摸", lambda: self.perform("pet"))
        interaction.addAction("跳一下", lambda: self.perform("jump"))
        interaction.addAction("睡觉 / 醒醒", lambda: self.perform("wake" if self.state == "sleep" else "sleep"))
        sizes = menu.addMenu("小鸡大小")
        menu._sizes = sizes
        for label, size in [("小 · 140", 140), ("中 · 190", 190), ("大 · 240", 240)]:
            sizes.addAction(label, lambda checked=False, value=size: self.set_pet_size(value))
        menu.addAction("暂停 / 继续动画", self.toggle_pause)
        menu.addSeparator()
        menu.addAction("软件更新", self.chat.update_requested.emit)
        menu.addAction("退出米花", self.shutdown)
        menu._actions = menu.actions()
        return menu

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.show_chat()

    def shutdown(self):
        self.timer.stop()
        self.chat.thinking_timer.stop()
        self.chat.close()
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.quit()

    def tick(self):
        if self.paused:
            return
        now = time.monotonic()
        if self.state_until and now >= self.state_until:
            self.state, self.state_until = ("search" if self.agent_busy else "idle"), 0
            self.state_started = now
        if now >= self.next_blink:
            self.blink_until = now + .16
            self.next_blink = now + random.uniform(2.5, 5)
        self.render_frame()
        self.update()

    def render_frame(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        elapsed = max(0, now - self.state_started)
        self._frame = QPixmap(self.size())
        self._frame.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self._frame)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.state == "search":
            index = self.sprites.animation_index(elapsed, .22)
            sprite = self.sprites.search_frames[index]
        elif self.state == "curious":
            index = self.sprites.animation_index(elapsed, .23)
            sprite = self.sprites.think_frames[index]
        elif self.state == "sleep" or self.focus_active:
            index = self.sprites.animation_index(elapsed, .52, intro_interval=.22)
            sprite = self.sprites.sleep_frames[index]
        else:
            index = (2 if int(elapsed*6)%2==0 else 3) if self.state in ("pet", "jump") else (1 if now < self.blink_until else 0)
            sprite = self.sprites.frames[index]
        animated_pose = self.state in ("sleep", "curious", "search") or self.focus_active
        breathing = 0 if animated_pose else math.sin(elapsed*2.2)*.7
        jump = abs(math.sin(elapsed*math.pi*2))*self.pet_size*.08 if self.state=="jump" else 0
        sway = math.sin(elapsed*12)*self.pet_size*.008 if self.state=="pet" else 0
        target = QRectF(12+sway, 60-jump-breathing, self.pet_size, self.pet_size)
        painter.drawPixmap(target, sprite, QRectF(0,0,384,384))
        if now < self.bubble_until and not self.focus_active:
            bubble = QRectF(6, 5, self.width() - 12, 43)
            painter.setPen(QPen(QColor("#dfd2c0"), 1))
            painter.setBrush(QColor("#fffaf2"))
            painter.drawRoundedRect(bubble, 14, 14)
            painter.setPen(QColor("#665749"))
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(bubble.adjusted(9, 3, -9, -3),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.bubble)
        if self.state == "pet":
            painter.setPen(QColor("#e995a4"))
            painter.setFont(QFont("Segoe UI Symbol", 18))
            painter.drawText(QPointF(self.width() * .77, 95 - (elapsed % 1) * 20), "♥")
        elif self.state == "sleep":
            painter.setPen(QColor("#8fa3a4"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.setOpacity(.65 + .25*math.sin(elapsed*1.5))
            painter.drawText(QPointF(12+self.pet_size*.18, 60+self.pet_size*.28-(elapsed%4)*2), "z z")
            painter.setOpacity(1)
        painter.end()
        # A shaped window prevents transparent areas from covering the desktop.
        if QApplication.platformName() != "offscreen":
            self.setMask(self._frame.mask())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._frame)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragging = False
            self._last_hover = None
            event.accept()

    def mouseMoveEvent(self, event):
        now = time.monotonic()
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                self.move(self._press_window + delta)
            return
        point = event.position()
        if self.head_rect.contains(point):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._last_hover is not None and now - self._last_stroke < .5:
                self._stroke_distance += (point - self._last_hover).manhattanLength()
            else:
                self._stroke_distance = 0
            self._last_hover = point
            self._last_stroke = now
            if self._stroke_distance >= self.pet_size * .32 and now - self._last_pet > .7:
                self._stroke_distance = 0
                self.perform("pet")
        else:
            self._last_hover = None
            self._stroke_distance = 0
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            dragged = self._dragging
            self._press_global = None
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if dragged:
                self.clamp_position()
            else:
                self.show_chat()

    def leaveEvent(self, event):
        self._last_hover = None
        self._stroke_distance = 0

    def contextMenuEvent(self, event):
        menu = self.make_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()


def launch_desktop(pet, quit_after=0):
    """Launch quietly with only the pet; the main window opens on user input."""
    pet.show()
    if quit_after:
        QTimer.singleShot(quit_after, pet.shutdown)


def main():
    from westie_agent import __version__
    parser = argparse.ArgumentParser(description=f"米花学习与生活助手 v{__version__}")
    parser.add_argument('--update-self-test', action='store_true', help='离屏检查自动更新入口与程序完整性，不访问 GitHub 或个人数据')
    parser.add_argument('--chick-self-test', action='store_true', help='离屏验证小鸡素材、图标和互动，不访问网络或个人数据')
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--quit-after", type=int, default=0, help="冒烟检查：指定毫秒后自动退出")
    parser.add_argument("--self-test", action="store_true", help="离屏检查打包完整性，不启动模型或执行网络操作")
    parser.add_argument("--model-self-test", action="store_true", help="离屏验证本地模型，不调用网络诊断工具")
    parser.add_argument("--diagnosis-self-test", action="store_true", help="离屏检查六个模拟场景的分层报告，不启动模型")
    parser.add_argument('--api-self-test',action='store_true',help='使用本机模拟 API 验证聊天和工具调用，不使用真实 Key 或付费接口')
    parser.add_argument('--offline-self-test',action='store_true',help='验证离线待办与词卡，不读取 Key、不调用模型或网络工具')
    parser.add_argument('--personal-self-test', action='store_true', help='在临时目录验证工作台、待办和番茄钟，不调用在线模型')
    parser.add_argument('--vocabulary-self-test', action='store_true', help='在临时目录验证统一拼写学习、复习与本地记录，不调用在线模型')
    parser.add_argument('--conversion-self-test', action='store_true', help='用临时样本验证真实文件转换，不读取个人文件或调用模型')
    parser.add_argument('--workspace-self-test', action='store_true', help='用临时文库验证问题导航、清空上下文和 Zotero 阅读列表')
    parser.add_argument('--reader-self-test', action='store_true', help='用合成文库和模拟模型验证 nature-reader，不读取真实文献或调用付费接口')
    parser.add_argument('--journal-self-test', action='store_true', help='用模拟日志、文库和 API 验证科研日志，不读取真实记录或调用付费接口')
    parser.add_argument('--unified-self-test', action='store_true', help='用临时数据验证统一主界面与全屏专注，不访问真实文库或付费接口')
    parser.add_argument('--attachments-self-test', action='store_true', help='用合成图片和模拟 API 验证聊天附件，不调用付费接口')
    parser.add_argument('--sedentary-self-test', action='store_true', help='用临时数据验证亮屏久坐提醒、设置和全屏退出')
    parser.add_argument('--dialog-chrome-self-test', action='store_true', help='用临时数据检查弹窗顶部、拼写高度与菜单圆角')
    parser.add_argument('--focus-style-self-test', action='store_true', help='用临时数据检查控件虚线框移除与键盘操作')
    parser.add_argument('--growth-self-test', action='store_true', help='兼容旧测试入口：验证单词拼写与成长入口移除，不调用网络')
    parser.add_argument('--iphone-sync-self-test', action='store_true', help='兼容旧检查入口：确认手机同步已移除，旧配置不会启动接收服务')
    parser.add_argument('--quick-ask-self-test', action='store_true', help='用临时数据检查迷你浮窗、复制解释与聊天切换，不调用付费接口')
    parser.add_argument('--web-shortcuts-self-test', action='store_true', help='用临时数据检查通用网页入口和旧配置迁移，不打开真实网页或调用付费接口')
    parser.add_argument('--reader-extract-input', default='', help=argparse.SUPPRESS)
    parser.add_argument('--reader-extract-output', default='', help=argparse.SUPPRESS)
    parser.add_argument("--self-test-output", default="")
    args = parser.parse_args()
    if args.reader_extract_input:
        from westie_agent.reader_extract import extract_pdf
        from westie_agent.reader_store import write_json
        output = Path(args.reader_extract_output)
        if not args.reader_extract_output or not output.is_dir():
            return 2
        try:
            extract_pdf(Path(args.reader_extract_input), output)
            return 0
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else 'PDF 提取未完成，文件可能已损坏或存在不支持的编码。'
            write_json(output/'extraction-result.json', {'ok':False,'error':message})
            return 2
    app = QApplication(sys.argv[:1])
    app.setApplicationName("米花")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    from westie_agent.window_chrome import install_dialog_chrome
    install_dialog_chrome()
    if args.update_self_test:
        import json
        from westie_agent.update_smoke import run_update_smoke
        from westie_agent.settings import application_dir
        report = run_update_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False), ROOT, application_dir())
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.chick_self_test:
        import json
        from westie_agent.chick_smoke import run_chick_smoke
        report = run_chick_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False), ROOT)
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.focus_style_self_test:
        import json
        from westie_agent.focus_style_smoke import run_focus_style_smoke
        report = run_focus_style_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.dialog_chrome_self_test:
        import json
        from westie_agent.dialog_chrome_smoke import run_dialog_chrome_smoke
        report = run_dialog_chrome_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.sedentary_self_test:
        import json
        from westie_agent.sedentary_smoke import run_sedentary_smoke
        report = run_sedentary_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.web_shortcuts_self_test:
        import json
        from westie_agent.web_shortcuts_smoke import run_web_shortcuts_smoke
        report = run_web_shortcuts_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys,'frozen',False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.quick_ask_self_test:
        import json
        from westie_agent.quick_ask_smoke import run_quick_ask_smoke
        report = run_quick_ask_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.iphone_sync_self_test:
        import json
        from westie_agent.iphone_sync_smoke import run_iphone_sync_smoke
        report = run_iphone_sync_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout: print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.growth_self_test:
        import json
        from westie_agent.growth_smoke import run_growth_smoke
        report=run_growth_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.attachments_self_test:
        import json
        from westie_agent.chat_attachments_smoke import run_attachments_smoke
        report = run_attachments_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.unified_self_test:
        import json
        from westie_agent.unified_smoke import run_unified_smoke
        report=run_unified_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.journal_self_test:
        import json
        from westie_agent.journal_smoke import run_journal_smoke
        report=run_journal_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.reader_self_test:
        import json
        from westie_agent.reader_smoke import run_reader_smoke
        report=run_reader_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False),ROOT/'assets')
        report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.workspace_self_test:
        import json
        from westie_agent.workspace_refresh_smoke import run_workspace_refresh_smoke
        report = run_workspace_refresh_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.conversion_self_test:
        import json
        from westie_agent.file_conversion_smoke import run_conversion_smoke
        base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT
        report = run_conversion_smoke(base, ROOT/'assets')
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.vocabulary_self_test:
        import json
        from westie_agent.vocabulary_smoke import run_vocabulary_smoke
        report = run_vocabulary_smoke(lambda: PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.personal_self_test:
        import json
        from westie_agent.personal_smoke import run_personal_smoke
        report = run_personal_smoke(lambda: PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False))
        report['frozen'] = bool(getattr(sys, 'frozen', False))
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.offline_self_test:
        import json
        from westie_agent.offline_smoke import run_offline_smoke
        report=run_offline_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
        report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.api_self_test:
        import json
        from westie_agent.api_smoke import run_protocol_smoke
        report=run_protocol_smoke();report['frozen']=bool(getattr(sys,'frozen',False))
        if args.self_test_output:Path(args.self_test_output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        elif sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    if args.diagnosis_self_test:
        import json
        from netpet_tools import ToolConfig, ToolService
        from westie_agent.bridge import ToolBridge
        from westie_agent.engine import LocalAgent
        from westie_agent.settings import Settings
        from westie_agent.reporting import report_html
        cases = [
            ("mock", "case_01_normal", "请进行分层网络检查"),
            ("mock", "case_02_portal", "检查校园认证页面跳转"),
            ("mock", "case_03_loss", "检查网关10.24.18.1十次丢包和 example.com 解析"),
            ("mock", "case_04_proxy_conflict", "检查 https://jwxt.campus.edu.test"),
            ("mock", "case_05_incomplete", "检查网络地址与PAC"),
            ("mock", "case_06_forbidden", "检查 https://paper-db.campus.edu.test/restricted")]
        results = []
        for mode, scenario, question in cases:
            settings = Settings(tools_mode=mode, **({"scenario_id": scenario} if scenario else {}))
            config = ToolConfig(mode=mode, allow_writes=False, **({"scenario_id": scenario} if scenario else {}))
            bridge = ToolBridge(settings, ToolService(config))
            reply = LocalAgent(settings, bridge).diagnose_without_model(question, lambda _: None)
            rendered = report_html(reply.diagnosis)
            if reply.diagnosis['schema_version'] != 'westie-diagnosis/1' or '<html' not in rendered:
                raise RuntimeError('分层报告生成失败')
            results.append({"scenario": scenario, "diagnosis": reply.diagnosis, "display_text": reply.text})
        report = {"frozen": bool(getattr(sys, "frozen", False)), "read_only": True,
                  "model_started": False, "results": results}
        if args.self_test_output:
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.self_test or args.model_self_test:
        import json
        from westie_agent.bridge import ToolBridge
        from westie_agent.engine import LocalAgent
        from westie_agent.settings import Settings
        sprites = SpriteBank(ROOT / "assets" / "chick-sheet.png")
        pet = PetWindow(sprites, use_tray=False)
        bridge = ToolBridge(Settings())
        import pydantic_ai
        import inspect
        report = {"ok": True, "frozen": bool(getattr(sys, "frozen", False)),
                  "sprite_frames": len(sprites.frames), "tools_imported": bridge.service is not None,
                  "tools_problem": bridge.problem, "removed_subtitle": not any(
                      label.text() == "互动原型 · 文字指令回应" for label in pet.chat.findChildren(QLabel))}
        if bridge.service is not None:
            probe = next(fn for fn in bridge.functions(lambda _: None) if fn.__name__ == "probe_connectivity")
            report["probe_default_route"] = inspect.signature(probe).parameters["route"].default
        report["default_pet_size"] = pet.pet_size
        pet.chat.add_message('米花', '**排版检查： **短段落。\n\n- 第一步\n- 第二步', markdown=True)
        plain = pet.chat.history.toPlainText()
        report['chat_formatting_ok'] = '**' not in plain and '排版检查：' in plain
        pet.chat.add_message('你', '**用户原文** <b>保持原样</b>')
        report['literal_user_text_ok'] = '**用户原文** <b>保持原样</b>' in pet.chat.history.toPlainText()
        report['auto_connect_removed_ok'] = not any(b.text()=='帮我联网' for b in pet.chat.findChildren(QPushButton))
        from westie_agent.campus import default_catalog, validate_catalog
        report['campus_entry_ok'] = bool(pet.chat.campus_button.text() and validate_catalog(default_catalog()) == {'schema_version':2,'entries':[]})
        report['conversion_entry_ok'] = pet.chat.conversion_button.text() == '文件转换'
        removed = {'网络守护', '网络检查', '导出排障报告', '排查原因'}
        report['network_features_removed_ok'] = not any(b.text() in removed for b in pet.chat.findChildren(QPushButton))
        from westie_agent.campus import CampusCatalog
        from westie_agent.campus_ui import CampusDialog
        from westie_agent.settings import application_dir
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix='bai-shortcuts-self-test-') as shortcuts_temp:
            campus_check = CampusDialog(CampusCatalog(Path(shortcuts_temp)))
        report['removed_status_areas_ok'] = campus_check.status.isHidden()
        from westie_agent.api_settings_ui import ApiSettingsDialog
        from westie_agent.credentials import SecretStore
        api_dialog=ApiSettingsDialog(Settings(),SecretStore(application_dir()))
        report['api_settings_entry_ok']=(pet.chat.settings_button.text()=='模型设置' and api_dialog.model.text()=='deepseek-flash')
        api_dialog.close()
        campus_check.close()
        report["search_frames"] = len(sprites.search_frames)
        pet.set_agent_status("checking")
        pet.chat.set_busy(True)
        report["search_feedback_ok"] = pet.state == "search" and "正在思考" in pet.chat.status.text()
        pet.agent_busy = False
        pet.chat.set_busy(False)
        pet.set_state("idle")
        mock_settings = Settings(tools_mode="mock", scenario_id="case_02_portal")
        mock_bridge = ToolBridge(mock_settings)
        if mock_bridge.service is not None:
            snapshot = next(fn for fn in mock_bridge.functions(lambda _: None)
                            if fn.__name__ == "get_network_snapshot")()
            report["packaged_mock_data_ok"] = snapshot.get("success") is True and snapshot.get("mode") == "mock"
            if not report["packaged_mock_data_ok"]:
                raise RuntimeError("打包后的工具场景数据读取失败。")
            from westie_agent.diagnostics import diagnostic_request,EvidenceCollector,build_diagnosis,diagnosis_text,diagnosis_details_text
            request = diagnostic_request('检查校园认证')
            completed = EvidenceCollector(mock_bridge,request).complete()
            diagnosis = build_diagnosis(request,mock_bridge.traces,'mock',missing=completed['missing'])
            plain = diagnosis_text(diagnosis)
            report['plain_guidance_ok'] = ('校园账号登录' in plain and 'HTTP' not in plain
                                           and 'HTTP 302' in diagnosis_details_text(diagnosis))
        if args.self_test_output:
            if args.model_self_test:
                from westie_agent.portable import PortableRuntime
                from westie_agent.settings import application_dir
                import threading
                runtime = PortableRuntime(application_dir())
                started = time.monotonic()
                class NoNetworkTools:
                    available_tools = {}
                child = None
                try:
                    runtime.ensure_ready("qwen3:4b", threading.Event())
                    child = runtime.process
                    use_mock = mock_bridge.service is not None
                    mock_settings.model_backend='local';mock_settings.model='qwen3:4b'
                    test_agent = LocalAgent(mock_settings, mock_bridge) if use_mock else LocalAgent(Settings(), ToolBridge(Settings(), NoNetworkTools()))
                    question = "请检查校园网为何连接后打不开网页，只做检查，不生成或执行任何修复方案。" if use_mock else "请用中文简短回复：本地模型已就绪。不要调用工具。"
                    answer = test_agent.reply(question,
                        runtime.url, lambda _: None, api_key=runtime.api_key)
                    report["model_response"] = answer.text
                    report["model_test_seconds"] = round(time.monotonic() - started, 2)
                    report["model_tool_calls"] = sum(t.get('origin', 'model') == 'model' for t in answer.traces)
                    report['host_completion_calls'] = sum(t.get('origin') == 'host_completion' for t in answer.traces)
                    report['model_error'] = (answer.diagnosis or {}).get('model_error')
                    report["model_observation_mode"] = "mock" if use_mock else "none"
                finally:
                    runtime.close()
                report["model_process_stopped"] = child is not None and child.poll() is not None
            Path(args.self_test_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        pet.shutdown()
        return 0
    from westie_agent.settings import application_dir
    from westie_agent.update_install import update_in_progress
    if update_in_progress(application_dir()):
        return 0
    data_dir = application_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(data_dir / "application.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "米花已经在运行", "请点击桌面小鸡或通知区域的小鸡图标。")
        return 0
    app.aboutToQuit.connect(instance_lock.unlock)
    try:
        sprites = SpriteBank(ROOT / "assets" / "chick-sheet.png")
        pet = PetWindow(sprites, use_tray=not args.no_tray)
        from westie_agent.controller import AgentController
        controller = AgentController(pet)
        from westie_agent.update_ui import UpdateController
        updater = UpdateController(pet, application_dir(), controller)
        app.aboutToQuit.connect(updater.close)
        app.aboutToQuit.connect(controller.close)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    launch_desktop(pet, args.quit_after)
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        if sys.stderr:
            traceback.print_exc(file=sys.stderr)
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
        try:
            logs = base / "data" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        if not any(flag in sys.argv for flag in ("--update-self-test", "--chick-self-test", "--focus-style-self-test", "--dialog-chrome-self-test", "--sedentary-self-test", "--web-shortcuts-self-test", "--quick-ask-self-test", "--self-test", "--model-self-test", "--diagnosis-self-test", "--api-self-test", "--offline-self-test", "--personal-self-test", "--vocabulary-self-test", "--conversion-self-test", "--workspace-self-test", "--reader-self-test", "--journal-self-test", "--unified-self-test", "--attachments-self-test", "--growth-self-test", "--iphone-sync-self-test", "--reader-extract-input")):
            from PySide6.QtWidgets import QMessageBox
            QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "米花暂时无法启动", "请查看 data/logs/startup-error.log，并联系开发者。")
        raise SystemExit(1)
