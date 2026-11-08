from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QLabel, QMessageBox, QVBoxLayout

from westie_agent.window_chrome import install_dialog_chrome


def test_dialog_heading_is_integrated_once_and_close_rejects():
    app = QApplication.instance() or QApplication([])
    install_dialog_chrome()
    dialog = QDialog()
    dialog.setWindowTitle('米花 · 测试窗口')
    dialog.resize(400, 200)
    layout = QVBoxLayout(dialog)
    heading = QLabel('页面标题'); heading.setObjectName('title')
    layout.addWidget(heading); layout.addWidget(QLabel('正文'))
    dialog.show(); app.processEvents()
    assert dialog.title_bar.heading.text() == '页面标题'
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    margins = dialog.contentsMargins()
    dialog.hide(); dialog.show(); app.processEvents()
    assert margins == dialog.contentsMargins()
    assert dialog.title_bar.geometry().bottom() < dialog.contentsRect().top()
    dialog.title_bar.close_button.click()
    assert not dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected


def test_static_message_results_and_cpp_file_picker_survive_decoration(tmp_path):
    app = QApplication.instance() or QApplication([])
    install_dialog_chrome()
    def confirm():
        box = app.activeModalWidget()
        assert isinstance(box, QMessageBox) and box.title_bar.isVisible()
        box.button(QMessageBox.StandardButton.Yes).click()
    QTimer.singleShot(0, confirm)
    assert QMessageBox.question(None, '确认', '继续？') == QMessageBox.StandardButton.Yes
    files = QFileDialog(None, '添加文件', str(tmp_path))
    files.show(); app.processEvents()
    assert files.title_bar.isVisible()
    assert files.title_bar.geometry().bottom() < files.contentsRect().top()
    files.title_bar.close_button.click()
    assert not files.isVisible()
