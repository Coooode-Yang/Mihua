"""Recent-reading shelf backed by read-only local Zotero metadata."""
from datetime import datetime
import threading

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget


class ReadingWorker(QThread):
    completed = Signal(object)
    def __init__(self, library, parent):
        super().__init__(parent)
        self.library = library
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.library.recent(self.cancel)
        except (OSError, ValueError) as exc:
            result = str(exc)
        except Exception:
            result = '这次未能读取文库，请稍后刷新。'
        if not self.cancel.is_set():
            self.completed.emit(result)


class ResearchPage(QWidget):
    def __init__(self, library, parent=None, *, reader=None, journal=None):
        super().__init__(parent)
        self.library = library
        self.reader = reader
        self.journal = journal
        self.snapshot_directory = None
        self.worker = None
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        title = QLabel('最近阅读'); title.setObjectName('title')
        layout.addWidget(title)
        self.summary = QLabel('把上次读到的地方接着读。'); self.summary.setObjectName('muted')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.refresh_button = QPushButton('刷新')
        self.choose_button = QPushButton('选择文库')
        self.auto_button = QPushButton('自动查找')
        row.addWidget(self.refresh_button); row.addWidget(self.choose_button); row.addWidget(self.auto_button); row.addStretch()
        layout.addLayout(row)
        self.path_label = QLabel(); self.path_label.setObjectName('muted'); self.path_label.setWordWrap(True)
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.path_label)
        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        self.list.setSpacing(3)
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet('QListWidget::item { padding: 15px 10px; }')
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.list, 1)
        self.status = QLabel('进入研究页后自动读取本机 Zotero。')
        self.status.setWordWrap(True); self.status.setObjectName('muted'); self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.reader_status = QLabel()
        self.reader_status.setWordWrap(True);self.reader_status.setObjectName('eyebrow')
        self.reader_status.setTextFormat(Qt.TextFormat.PlainText);self.reader_status.hide()
        layout.addWidget(self.reader_status)
        footer = QHBoxLayout()
        self.action_row = footer
        self.continue_button = QPushButton('继续阅读'); self.continue_button.setObjectName('primary')
        self.continue_button.setEnabled(False); self.continue_button.clicked.connect(self.open_selected)
        self.generate_reader = QPushButton('生成解读')
        self.generate_reader.setToolTip('使用 nature-reader 逐段解读选中的 PDF，并保存 MD 文件')
        self.view_reader = QPushButton('查看解读')
        self.cancel_reader = QPushButton('取消解读');self.cancel_reader.hide()
        for button in (self.continue_button,self.generate_reader,self.view_reader,self.cancel_reader):footer.addWidget(button)
        footer.addStretch()
        layout.addLayout(footer)
        self.refresh_button.clicked.connect(self.refresh)
        self.choose_button.clicked.connect(self.choose_directory)
        self.auto_button.clicked.connect(self.auto_directory)
        self.timer = QTimer(self); self.timer.setInterval(30000)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start()
        if self.reader:self.reader.attach(self)
        if self.journal:self.journal.attach(self)
        self.selection_changed()

    def activate(self):
        if self.journal:self.journal_shelf.refresh()
        self.refresh()

    def refresh(self):
        if self.worker is not None:
            return
        self.loaded = True
        self.status.setText('正在读取最近阅读…')
        for button in (self.refresh_button, self.choose_button, self.auto_button): button.setEnabled(False)
        worker = ReadingWorker(self.library, self)
        self.worker = worker
        worker.completed.connect(self.show_result)
        worker.finished.connect(lambda: self.release(worker))
        worker.start()

    def release(self, worker):
        if self.worker is worker: self.worker = None
        for button in (self.refresh_button, self.choose_button, self.auto_button): button.setEnabled(True)
        worker.deleteLater()

    def show_result(self, result):
        previous = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.clear()
        if isinstance(result, str):
            self.snapshot_directory=None
            self.summary.setText('连接本机 Zotero 文库后，显示最近阅读的文献。')
            self.path_label.clear()
            self.status.setText(result)
            self.selection_changed()
            return
        self.path_label.setText(str(result.directory))
        self.snapshot_directory=result.directory
        self.summary.setText(f'最近 7 天 {result.week_count} 篇 · 最近 30 天 {result.month_count} 篇')
        for paper in result.papers:
            stamp = datetime.fromtimestamp(paper.last_read).strftime('%m-%d %H:%M')
            detail = f'{paper.evidence}  {stamp}' + (f' · 第 {paper.page} 页' if paper.page else '')
            item = QListWidgetItem(paper.title+'\n'+detail)
            item.setData(Qt.ItemDataRole.UserRole, paper)
            item.setData(int(Qt.ItemDataRole.UserRole)+1,paper.title+'\n'+detail)
            item.setToolTip(paper.title+'\n'+detail)
            self.list.addItem(item)
            if previous and (previous.library_id, previous.item_key) == (paper.library_id, paper.item_key):
                self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentRow() < 0: self.list.setCurrentRow(0)
        self.status.setText(('按最近阅读排序，最多显示 100 篇。' + ('部分记录依据阅读位置文件的更新时间。' if result.approximate_count else ''))
                            if result.papers else '还没有可用的阅读记录。在 Zotero 中打开文献后，回来刷新。')
        self.refresh_reader_badges()

    def selection_changed(self, *_):
        self.continue_button.setEnabled(self.list.currentItem() is not None)
        if hasattr(self,'note_button'):self.note_button.setEnabled(self.list.currentItem() is not None)
        selected=self.selected_paper()
        self.generate_reader.setEnabled(bool(self.reader and selected and selected.content_type=='application/pdf' and self.reader.worker is None))
        artifact=self.reader.store.latest(self.snapshot_directory,self.selected_paper()) if (
            self.reader and self.snapshot_directory is not None and self.selected_paper() is not None) else None
        self.view_reader.setEnabled(artifact is not None)

    def selected_paper(self):
        item=self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_reader_badges(self):
        if self.reader and self.snapshot_directory is not None:
            for index in range(self.list.count()):
                item=self.list.item(index);paper=item.data(Qt.ItemDataRole.UserRole)
                original=item.data(int(Qt.ItemDataRole.UserRole)+1) or item.text()
                artifact=self.reader.store.latest(self.snapshot_directory,paper)
                suffix=(' · 已有解读' if artifact.metadata.get('status')=='complete' else ' · 已有解读草稿') if artifact else ''
                item.setText(original+suffix)
        self.selection_changed()

    def open_selected(self):
        item = self.list.currentItem()
        if item is None: return
        paper = item.data(Qt.ItemDataRole.UserRole)
        try:
            opened = QDesktopServices.openUrl(QUrl(paper.open_url()))
            self.status.setText('已请求 Zotero 打开所选文献。' if opened else '未能打开 Zotero，请确认已安装并启动 Zotero。')
        except ValueError as exc:
            self.status.setText(str(exc))

    def choose_directory(self):
        folder = QFileDialog.getExistingDirectory(self, '选择包含 zotero.sqlite 的文库文件夹')
        if folder:
            try:
                self.library.select_directory(folder)
                self.refresh()
            except (OSError, ValueError) as exc: self.status.setText(str(exc))

    def auto_directory(self):
        try:
            self.library.reset_directory(); self.refresh()
        except OSError: self.status.setText('无法恢复自动查找，请检查米花数据目录权限。')

    def shutdown(self):
        self.timer.stop()
        if self.worker:
            self.worker.cancel.set()
            self.worker.wait(5000)
