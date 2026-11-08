"""Attachment staging UI; failed sends leave the draft available for retry."""
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QListWidget, QListWidgetItem, QPushButton, QHBoxLayout, QWidget

from .chat_attachments import (FILE_FILTER, MAX_FILES, MAX_TOTAL_BYTES,
                               image_attachment, load_attachment)


class AttachmentComposer:
    def setup_attachments(self, layout, entry):
        self.attachments = []
        self._attachment_question = ''
        self.attachment_box = QWidget()
        row = QHBoxLayout(self.attachment_box)
        row.setContentsMargins(0, 0, 0, 0)
        self.attachment_list = QListWidget()
        self.attachment_list.setFlow(QListWidget.Flow.LeftToRight)
        self.attachment_list.setIconSize(QSize(64, 48))
        self.attachment_list.setFixedHeight(78)
        self.attachment_list.setAccessibleName('待发送附件，选中后点击移除')
        self.attachment_list.setStyleSheet('QListWidget { background: #fff; border: 1px solid #eadfce; border-radius: 8px; }')
        row.addWidget(self.attachment_list, 1)
        self.remove_attachment_button = QPushButton('移除')
        self.remove_attachment_button.clicked.connect(self.remove_attachment)
        row.addWidget(self.remove_attachment_button)
        layout.addWidget(self.attachment_box)
        self.attachment_box.hide()
        self.attachment_notice = QLabel()
        self.attachment_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.attachment_notice.setWordWrap(True)
        self.attachment_notice.setStyleSheet('color: #80644c; font-size: 11px;')
        self.attachment_notice.hide()
        layout.addWidget(self.attachment_notice)
        self.attach_button = QPushButton('附件')
        self.attach_button.setToolTip('添加图片、PDF、Word 或文本；也可拖入或 Ctrl+V 粘贴图片')
        self.attach_button.clicked.connect(self.choose_attachments)
        entry.addWidget(self.attach_button)
        self.input.attachment_pasted.connect(self.add_mime_attachments)
        self.setAcceptDrops(True)
        self.history.viewport().setAcceptDrops(True)
        self.history.viewport().installEventFilter(self)

    def show_attachment_notice(self, message):
        self.attachment_notice.setText(message)
        self.attachment_notice.setVisible(bool(message))

    def choose_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加聊天附件', '', FILE_FILTER)
        self.add_attachment_paths(paths)

    def add_attachment_paths(self, paths):
        if not self.send_button.isEnabled():
            return
        errors = []
        for path in paths:
            try:
                if len(self.attachments) >= MAX_FILES:
                    raise ValueError('每次最多添加 5 个附件。')
                self.stage_attachment(load_attachment(path))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            self.show_attachment_notice('\n'.join(dict.fromkeys(errors)))

    def stage_attachment(self, attachment):
        if len(self.attachments) >= MAX_FILES:
            raise ValueError('每次最多添加 5 个附件。')
        if sum(len(a.data) for a in self.attachments) + len(attachment.data) > MAX_TOTAL_BYTES:
            raise ValueError('本次附件合计不能超过 20 MB。')
        self.attachments.append(attachment)
        self.refresh_attachments()

    def refresh_attachments(self):
        self.attachment_list.clear()
        for attachment in self.attachments:
            item = QListWidgetItem(attachment.name)
            item.setToolTip(f'{attachment.name}\n{len(attachment.data)/1024:.0f} KB')
            if attachment.is_image:
                image = QImage.fromData(attachment.data)
                item.setIcon(QIcon(QPixmap.fromImage(image).scaled(64, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                                                 Qt.TransformationMode.SmoothTransformation)))
            self.attachment_list.addItem(item)
        if self.attachments:
            self.attachment_list.setCurrentRow(len(self.attachments)-1)
        self.attachment_box.setVisible(bool(self.attachments))
        self.show_attachment_notice('发送后交给当前模型；文档共最多 4 万字符，PDF 最多前 30 页文字层。每次最多 5 个附件。' if self.attachments else '')

    def remove_attachment(self):
        index = self.attachment_list.currentRow()
        if self.send_button.isEnabled() and 0 <= index < len(self.attachments):
            self.attachments.pop(index)
            self.refresh_attachments()

    def add_mime_attachments(self, mime):
        if not self.send_button.isEnabled():
            return
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                self.add_attachment_paths(paths)
            elif mime.hasText():
                self.input.insertPlainText(mime.text())
            else:
                self.show_attachment_notice('请拖入本机文件；网页链接可直接粘贴为文字。')
        elif mime.hasImage():
            try:
                value = mime.imageData()
                if isinstance(value, QPixmap):
                    value = value.toImage()
                self.stage_attachment(image_attachment(value, f'粘贴的图片-{len(self.attachments)+1}.png'))
            except (ValueError, TypeError) as exc:
                self.show_attachment_notice(str(exc) if isinstance(exc, ValueError) else '剪贴板图片无法读取，请先保存成 PNG 后添加。')

    def dragEnterEvent(self, event):
        if self.send_button.isEnabled() and (event.mimeData().hasImage() or event.mimeData().hasUrls()):
            event.acceptProposedAction()

    def eventFilter(self, watched, event):
        if watched is self.history.viewport() and event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            if self.send_button.isEnabled() and (event.mimeData().hasImage() or event.mimeData().hasUrls()):
                if event.type() == QEvent.Type.Drop:
                    self.add_mime_attachments(event.mimeData())
                event.acceptProposedAction()
                return True
        return super().eventFilter(watched, event)

    def dropEvent(self, event):
        self.add_mime_attachments(event.mimeData())
        event.acceptProposedAction()

    def attachment_send_finished(self, success):
        if success:
            self.attachments.clear()
            self.refresh_attachments()
        elif self.attachments and not self.input.text():
            self.input.setText(self._attachment_question)
        self._attachment_question = ''
