from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from westie_agent.window_chrome import IntegratedWindow, TitleBar, hit_test


def test_caption_controls_and_hit_targets():
    app = QApplication.instance() or QApplication([])
    window = IntegratedWindow()
    window.resize(820, 600)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    window.title_bar = TitleBar(window)
    layout.addWidget(window.title_bar)
    layout.addWidget(QWidget(), 1)
    window.show()
    app.processEvents()
    bar = window.title_bar
    assert hit_test(window, QPoint(2, 2)) == 13
    assert hit_test(window, QPoint(window.width() - 2, window.height() - 2)) == 17
    assert hit_test(window, bar.mapTo(window, QPoint(300, 24))) == 2
    assert hit_test(window, bar.close_button.mapTo(window, bar.close_button.rect().center())) == 1
    assert not hasattr(bar, 'maximize')
    assert not window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(300, 24))
    assert not window.isMaximized()
    QTest.mouseClick(bar.minimize, Qt.MouseButton.LeftButton)
    assert window.isMinimized()
    window.showNormal()
    QTest.mouseClick(bar.close_button, Qt.MouseButton.LeftButton)
    assert not window.isVisible()
    window.deleteLater()
    app.processEvents()
