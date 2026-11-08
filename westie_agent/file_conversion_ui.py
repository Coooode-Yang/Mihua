"""A small native file-conversion workbench, independent of model requests."""
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .file_conversion import ConversionCancelled, ConversionResult, FileeEngine, available_modes, validate_inputs
from .personal_ui import STYLE, label


class ConversionWorker(QThread):
    progress = Signal(int, object)
    completed = Signal(str, object)

    def __init__(self, engine, kind, paths, cancel, mode=None, output=None, parent=None):
        super().__init__(parent)
        self.engine, self.kind, self.paths = engine, kind, paths
        self.cancel, self.mode, self.output = cancel, mode, output

    def run(self):
        try:
            if self.kind == 'discover':
                data = self.engine.discover(self.paths, self.cancel)
            else:
                data = self.engine.convert(self.paths, self.mode, self.output, self.cancel, self.progress.emit)
            self.completed.emit(self.kind, data)
        except ConversionCancelled:
            self.completed.emit(self.kind, {'cancelled': True})
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, OSError)) else '这次转换未完成，请重新添加文件后重试。'
            self.completed.emit(self.kind, {'error': message})


class ConversionDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.engine = engine
        self.worker = None
        self.cancel_event = threading.Event()
        self.busy = False
        self.custom_output = False
        self.capabilities = {}
        self.preferred_mode = None
        self.discovery_pending = False
        self.setWindowTitle('米花 · 文件转换')
        self.resize(740, 650)
        self.setMinimumSize(600, 500)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE + '''
            QFrame#dropZone { background: #f1f3e9; border: 1px dashed #b5c5ad; border-radius: 14px; }
            QFrame#dropZone QLabel { background: transparent; }
            QTreeWidget { background: #fcfaf5; border: 1px solid #e6dfd2; border-radius: 12px; padding: 6px; outline: none; }
            QTreeWidget::item { padding: 10px 5px; border-bottom: 1px solid #efebe3; }
            QTreeWidget::item:selected { background: #e3ece3; color: #354f3d; }
            QHeaderView::section { background: #f3efe6; color: #82776c; border: none; padding: 8px 5px; }
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(10)
        outer.addWidget(label('文件转换', 'title'))
        outer.addWidget(label('在本机处理，原文件保留。', 'muted'))
        drop = QFrame()
        drop.setObjectName('dropZone')
        drop_layout = QHBoxLayout(drop)
        drop_layout.setContentsMargins(18, 16, 18, 16)
        drop_layout.addWidget(label('把文件拖到这里，也可以批量添加。'), 1)
        self.add_button = QPushButton('添加文件')
        self.add_button.setObjectName('primary')
        self.add_button.clicked.connect(self.choose_files)
        drop_layout.addWidget(self.add_button)
        outer.addWidget(drop)
        self.files = QTreeWidget()
        self.files.setHeaderLabels(['文件', '状态', '结果'])
        self.files.setRootIsDecorated(False)
        self.files.setUniformRowHeights(True)
        self.files.setAcceptDrops(False)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setColumnWidth(0, 260)
        self.files.setColumnWidth(1, 90)
        self.files.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.files.itemSelectionChanged.connect(self.update_actions)
        self.files.itemDoubleClicked.connect(lambda *_: self.open_result())
        outer.addWidget(self.files, 1)
        edit_row = QHBoxLayout()
        self.count_label = label('尚未添加文件', 'eyebrow')
        edit_row.addWidget(self.count_label, 1)
        self.up_button = QPushButton('上移')
        self.down_button = QPushButton('下移')
        self.remove_button = QPushButton('移除')
        self.clear_button = QPushButton('清空')
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_files)
        for button in (self.up_button, self.down_button, self.remove_button, self.clear_button):
            edit_row.addWidget(button)
        outer.addLayout(edit_row)
        mode_row = QHBoxLayout()
        mode_row.addWidget(label('转换为'))
        self.target = QComboBox()
        self.target.setMinimumContentsLength(15)
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.currentIndexChanged.connect(self.mode_changed)
        mode_row.addWidget(self.target, 1)
        outer.addLayout(mode_row)
        self.mode_hint = label('', 'muted')
        self.mode_hint.hide()
        outer.addWidget(self.mode_hint)
        output_row = QHBoxLayout()
        output_row.addWidget(label('保存到'))
        self.output = QLineEdit()
        self.output.setPlaceholderText('添加文件后，默认保存在原文件旁的“米花转换”文件夹')
        self.output.setAcceptDrops(False)
        self.output.textEdited.connect(self.output_edited)
        output_row.addWidget(self.output, 1)
        self.browse_button = QPushButton('选择文件夹')
        self.browse_button.clicked.connect(self.choose_output)
        output_row.addWidget(self.browse_button)
        outer.addLayout(output_row)
        self.detail = label('', 'muted')
        self.detail.hide()
        outer.addWidget(self.detail)
        self.status = label('', 'muted')
        self.status.hide()
        outer.addWidget(self.status)
        footer = QHBoxLayout()
        self.start_button = QPushButton('开始转换')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.start_conversion)
        self.cancel_button = QPushButton('取消')
        self.cancel_button.clicked.connect(self.cancel_event_set)
        self.open_button = QPushButton('打开结果')
        self.open_button.clicked.connect(self.open_result)
        self.folder_button = QPushButton('打开文件夹')
        self.folder_button.clicked.connect(self.open_folder)
        for button in (self.start_button, self.cancel_button, self.open_button, self.folder_button):
            footer.addWidget(button)
        footer.addStretch()
        outer.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.update_actions()
        self.mode_changed()
        if not self.engine.ready:
            self.set_status('转换组件未就绪，请使用完整的米花发行包。')

    def paths(self):
        return [Path(self.files.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)) for i in range(self.files.topLevelItemCount())]

    def set_status(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加需要转换的文件', '',
            '常用文件 (*.docx *.pptx *.xlsx *.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.txt *.md *.csv *.epub *.html *.doc *.ppt *.xls *.odt *.odp *.ods);;所有文件 (*)')
        if paths:
            self.add_files(paths)

    def add_files(self, values):
        if self.busy:
            return
        try:
            paths = validate_inputs([*self.paths(), *values])
        except (OSError, ValueError) as exc:
            self.set_status(str(exc))
            return
        self.files.clear()
        for path in paths:
            item = QTreeWidgetItem([path.name, '待识别', ''])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(0, str(path))
            self.files.addTopLevelItem(item)
        if not self.custom_output:
            self.output.setText(str(paths[0].parent / '米花转换'))
        self.count_label.setText(f'{len(paths)} 个文件')
        self.set_status('正在识别可用格式…')
        self.launch('discover')

    def launch(self, kind):
        if self.worker and self.worker.isRunning():
            return
        self.cancel_event = threading.Event()
        if kind == 'discover':
            self.discovery_pending = True
            self.preferred_mode = self.target.currentData() or self.preferred_mode
            self.target.clear()
        worker = ConversionWorker(self.engine, kind, self.paths(), self.cancel_event,
                                  self.target.currentData(), self.output.text().strip(), self)
        self.worker = worker
        worker.progress.connect(self.file_progress)
        worker.completed.connect(self.job_completed)
        worker.finished.connect(lambda: self.release_worker(worker))
        self.busy = True
        self.update_actions()
        worker.start()

    def release_worker(self, worker):
        if self.worker is worker:
            self.worker = None
        worker.deleteLater()

    def job_completed(self, kind, data):
        self.busy = False
        if isinstance(data, dict) and data.get('cancelled'):
            self.set_status('已取消。')
            if kind == 'convert':
                for index, path in enumerate(self.paths()):
                    self.file_progress(index, ConversionResult(str(path), 'cancelled'))
        elif isinstance(data, dict) and data.get('error'):
            self.set_status(data['error'])
            if kind == 'convert':
                for index, path in enumerate(self.paths()):
                    self.file_progress(index, ConversionResult(str(path), 'failed', error=data['error']))
        elif kind == 'discover':
            self.discovery_pending = False
            self.capabilities = data
            previous = self.preferred_mode
            self.target.blockSignals(True)
            self.target.clear()
            modes = available_modes(self.paths(), data)
            for mode in modes:
                self.target.addItem(mode.title, mode.key)
            selected = self.target.findData(previous)
            if selected >= 0:
                self.target.setCurrentIndex(selected)
            self.target.blockSignals(False)
            for index, path in enumerate(self.paths()):
                self.files.topLevelItem(index).setText(1, '待转换' if data[path.suffix.lower()].ready else '暂不支持')
            self.set_status('选择格式后即可开始；合并 PDF 时可调整列表顺序。' if modes else
                            '当前文件组合没有共同的可用格式，请分别转换。旧版 DOC/PPT 可先另存为 DOCX/PPTX。')
        else:
            done = sum(result.state == 'done' for result in data)
            failed = sum(result.state == 'failed' for result in data)
            cancelled = sum(result.state == 'cancelled' for result in data)
            self.set_status(f'已完成 {done} 个文件。' if not failed and not cancelled else
                            f'已完成 {done} 个，未完成 {failed} 个，已取消 {cancelled} 个。')
            if not self.files.selectedItems():
                for index, result in enumerate(data):
                    if result.state == 'done':
                        self.files.setCurrentItem(self.files.topLevelItem(index))
                        break
        self.mode_changed()

    def file_progress(self, index, result):
        item = self.files.topLevelItem(index)
        if item is None:
            return
        item.setText(1, {'running': '转换中…', 'done': '已完成', 'failed': '未完成', 'cancelled': '已取消'}[result.state])
        item.setForeground(1, QColor('#567a62' if result.state == 'done' else '#a76b52' if result.state == 'failed' else '#82776c'))
        item.setData(2, Qt.ItemDataRole.UserRole, result)
        item.setText(2, Path(result.outputs[0]).name if len(result.outputs) == 1 else f'{len(result.outputs)} 个结果' if result.outputs else '')
        item.setToolTip(2, '\n'.join(result.outputs) or result.error)
        self.update_actions()

    def start_conversion(self):
        if self.busy:
            return
        if not self.target.currentData():
            if self.discovery_pending:
                self.set_status('正在识别可用格式…')
                self.launch('discover')
            return
        self.set_status('正在转换，文件较大时请稍候…')
        self.launch('convert')

    def cancel_event_set(self):
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.set_status('正在取消，已完成的结果会保留。')

    def mode_changed(self):
        if hasattr(self, 'mode_hint'):
            note = 'PDF 转 Word 的版式可能变化；扫描件不会自动识别文字。' if (
                self.target.currentData() == 'docx' and any(path.suffix.lower() == '.pdf' for path in self.paths())) else ''
            self.mode_hint.setText(note)
            self.mode_hint.setVisible(bool(note))
        self.update_actions()

    def output_edited(self):
        self.custom_output = True
        self.update_actions()

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, '选择保存位置', self.output.text())
        if folder:
            self.custom_output = True
            self.output.setText(folder)
            self.update_actions()

    def remove_selected(self):
        if not self.busy:
            for item in self.files.selectedItems():
                self.files.takeTopLevelItem(self.files.indexOfTopLevelItem(item))
            self.refresh_after_edit()

    def clear_files(self):
        if not self.busy:
            self.files.clear()
            self.refresh_after_edit()

    def refresh_after_edit(self):
        self.count_label.setText(f'{len(self.paths())} 个文件' if self.paths() else '尚未添加文件')
        if self.paths():
            self.launch('discover')
        else:
            self.target.clear()
            self.discovery_pending = False
            self.set_status('')
            self.update_actions()

    def move_selected(self, direction):
        selected = self.files.selectedItems()
        if self.busy or len(selected) != 1:
            return
        index = self.files.indexOfTopLevelItem(selected[0])
        new_index = index + direction
        if 0 <= new_index < self.files.topLevelItemCount():
            item = self.files.takeTopLevelItem(index)
            self.files.insertTopLevelItem(new_index, item)
            self.files.setCurrentItem(item)
            self.update_actions()

    def selected_result(self):
        selected = self.files.selectedItems()
        return selected[0].data(2, Qt.ItemDataRole.UserRole) if selected else None

    def update_actions(self):
        selected = self.files.selectedItems()
        paths = self.paths()
        result = self.selected_result()
        for button in (self.add_button, self.browse_button):
            button.setEnabled(not self.busy)
        self.output.setEnabled(not self.busy)
        self.target.setEnabled(not self.busy and self.target.count() > 0)
        retry = self.discovery_pending and not self.target.currentData()
        self.start_button.setText('重新识别' if retry else '开始转换')
        self.start_button.setEnabled(not self.busy and bool(paths) and bool(self.target.currentData() or retry) and bool(self.output.text().strip()) and self.engine.ready)
        self.cancel_button.setEnabled(self.busy and not self.cancel_event.is_set())
        self.remove_button.setEnabled(not self.busy and bool(selected))
        self.clear_button.setEnabled(not self.busy and bool(paths))
        index = self.files.indexOfTopLevelItem(selected[0]) if len(selected) == 1 else -1
        self.up_button.setEnabled(not self.busy and index > 0)
        self.down_button.setEnabled(not self.busy and 0 <= index < len(paths)-1)
        self.open_button.setEnabled(bool(result and result.outputs))
        self.folder_button.setEnabled(bool(self.output.text()) and Path(self.output.text()).is_dir())
        text = (result.error or result.notice) if result else ''
        self.detail.setText(text)
        self.detail.setVisible(bool(text))

    def open_result(self):
        result = self.selected_result()
        if result and result.outputs:
            path = Path(result.outputs[0])
            if path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_folder(self):
        folder = Path(self.output.text())
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def dragEnterEvent(self, event):
        if not self.busy and event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.busy and all(url.isLocalFile() for url in event.mimeData().urls()):
            self.add_files([url.toLocalFile() for url in event.mimeData().urls()])
            event.acceptProposedAction()

    def closeEvent(self, event):
        if self.busy:
            self.cancel_event_set()
        super().closeEvent(event)

    def shutdown(self):
        self.cancel_event.set()
        if self.worker and self.worker.isRunning():
            self.worker.wait(15000)
        self.close()


class FileConversionController(QObject):
    def __init__(self, pet, base):
        super().__init__(pet)
        self.pet = pet
        self.engine = FileeEngine(base)
        self.window = None
        pet.chat.conversion_requested.connect(self.show)

    def show(self):
        if self.window is None:
            self.window = ConversionDialog(self.engine, self.pet.chat)
        if not self.window.isVisible():
            screen = QApplication.screenAt(self.pet.frameGeometry().center()) or QApplication.primaryScreen()
            bounds = screen.availableGeometry()
            self.window.resize(min(self.window.width(), bounds.width()-24), min(self.window.height(), bounds.height()-24))
            self.window.move(bounds.center()-self.window.rect().center())
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def close(self):
        if self.window:
            self.window.shutdown()
