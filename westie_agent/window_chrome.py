"""In-content caption controls, with native Windows move, snap and resize."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import weakref

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialog, QHBoxLayout, QLabel,
                              QMenu, QProxyStyle, QPushButton, QStyle, QStyleFactory, QStyleOptionMenuItem, QWidget)


class QuietFocusStyle(QProxyStyle):
    """Keep native controls and keyboard focus, without the dotted focus frame."""
    def drawPrimitive(self, element, option, painter, widget=None):
        if element != QStyle.PrimitiveElement.PE_FrameFocusRect:
            super().drawPrimitive(element, option, painter, widget)


def install_focus_style():
    app = QApplication.instance()
    if app is not None and not hasattr(app, '_quiet_focus_style'):
        style = QuietFocusStyle(QStyleFactory.create(app.style().objectName()))
        app.setStyle(style)
        app._quiet_focus_style = style


class CaptionButton(QPushButton):
    def __init__(self, action, text, parent):
        super().__init__(parent)
        self.action = action
        self.setObjectName('captionClose' if action == 'close' else 'captionButton')
        self.setFixedSize(46, 36)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = '#ffffff' if self.action == 'close' and self.underMouse() else '#756e63'
        painter.setPen(QPen(QColor(color), 1.2))
        x, y = self.width() / 2, self.height() / 2
        if self.action == 'minimize':
            painter.drawLine(QPoint(round(x - 5), round(y)), QPoint(round(x + 5), round(y)))
        elif self.action == 'close':
            painter.drawLine(QPoint(round(x - 5), round(y - 5)), QPoint(round(x + 5), round(y + 5)))
            painter.drawLine(QPoint(round(x + 5), round(y - 5)), QPoint(round(x - 5), round(y + 5)))


class TitleBar(QWidget):
    def __init__(self, window, title='', *, minimize=True):
        super().__init__(window)
        self.setObjectName('integratedTitleBar')
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 14, 8, 6)
        row.setSpacing(0)
        self.heading = QLabel(title)
        self.heading.setObjectName('title')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.heading, 1)
        self.minimize = CaptionButton('minimize', '最小化', self)
        self.close_button = CaptionButton('close', '关闭', self)
        self.minimize.setVisible(minimize)
        self.minimize.clicked.connect(window.showMinimized)
        self.close_button.clicked.connect(window.close)
        for button in (self.minimize, self.close_button):
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet('''
            QWidget#integratedTitleBar { background: #fffaf3; }
            QWidget#integratedTitleBar QLabel#title {
                background: transparent; color: #443c36; font-family: 'Microsoft YaHei';
                font-size: 25px; font-weight: bold;
            }
            QPushButton#captionButton, QPushButton#captionClose {
                background: transparent; border: none; border-radius: 6px; padding: 0;
            }
            QPushButton#captionButton:hover { background: #eee8de; }
            QPushButton#captionButton:pressed { background: #e3dacd; }
            QPushButton#captionClose:hover { background: #c94f45; }
            QPushButton#captionClose:pressed { background: #ac3d35; }
            QPushButton#captionButton:focus, QPushButton#captionClose:focus { border: 1px solid #7e9a80; }
        ''')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


def hit_test(window, point):
    """Win32 HT codes from Qt logical coordinates, after physical DPI conversion."""
    if not window.isMaximized() and not window.isFullScreen():
        left, right = point.x() < 6, point.x() >= window.width() - 6
        top, bottom = point.y() < 6, point.y() >= window.height() - 6
        if top and left: return 13
        if top and right: return 14
        if bottom and left: return 16
        if bottom and right: return 17
        if left: return 10
        if right: return 11
        if top: return 12
        if bottom: return 15
    bar = getattr(window, 'title_bar', None)
    if bar and bar.rect().contains(bar.mapFrom(window, point)):
        if not isinstance(window.childAt(point), QAbstractButton):
            return 2  # HTCAPTION: native dragging; double click is suppressed below.
    caption_point = getattr(window, 'is_caption_drag_point', None)
    if caption_point and caption_point(point) and not isinstance(window.childAt(point), QAbstractButton):
        return 2
    return 1  # HTCLIENT: clicks continue to reach buttons and content.


class NativeWindowFrame:
    def __init__(self, *args, **kwargs):
        self._native_handle = None
        super().__init__(*args, **kwargs)
        install_dialog_chrome()

    def showEvent(self, event):
        super().showEvent(event)
        apply_native_frame(self)

    def nativeEvent(self, event_type, message):
        result = native_frame_event(self, message)
        return result if result is not None else super().nativeEvent(event_type, message)


def apply_native_frame(window):
    if sys.platform != 'win32' or QApplication.platformName() != 'windows' or window.isFullScreen():
        return
    handle = int(window.winId())
    if handle == getattr(window, '_native_handle', None):
        return
    window._native_handle = handle
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    # Keep resize, minimize and system menu; remove WS_MAXIMIZEBOX.
    style = (user.GetWindowLongW(handle, -16) | 0x00CC0000) & ~0x00030000
    if window.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint:
        style |= 0x00020000
    user.SetWindowLongW(handle, -16, style)
    user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0037)
    dwm = ctypes.WinDLL('dwmapi')
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    for attribute, value in ((20, 0), (33, 2), (34, 0x00D2DFE6)):
        setting = wintypes.DWORD(value)
        dwm.DwmSetWindowAttribute(handle, attribute, ctypes.byref(setting), ctypes.sizeof(setting))

def native_frame_event(window, message):
    if sys.platform == 'win32' and QApplication.platformName() == 'windows':
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x00A3:  # WM_NCLBUTTONDBLCLK: no caption/edge maximizing.
            return True, 0
        if msg.message == 0x0112 and (msg.wParam & 0xfff0) == 0xf030:
            return True, 0  # SC_MAXIMIZE, including system menu / keyboard requests.
        if msg.message == 0x0083:  # WM_NCCALCSIZE: client fills the window.
            return True, 0
        if msg.message == 0x0084 and not window.isFullScreen():  # Signed physical screen coordinates.
            user = ctypes.WinDLL('user32')
            user.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            point = wintypes.POINT(ctypes.c_short(msg.lParam & 0xffff).value,
                                   ctypes.c_short((msg.lParam >> 16) & 0xffff).value)
            if user.ScreenToClient(msg.hWnd, ctypes.byref(point)):
                scale = window.devicePixelRatioF()
                return True, hit_test(window, QPoint(int(point.x / scale), int(point.y / scale)))
    return None


class IntegratedWindow(NativeWindowFrame, QDialog):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint)


class DialogChrome(QObject, QAbstractNativeEventFilter):
    """Style every Qt dialog, including static message, input and file dialogs.

    Keep the original layout and dialog class so validation, modal results,
    default buttons and unsaved-edit handling continue to work as before.
    """
    def __init__(self, app):
        QObject.__init__(self, app)
        QAbstractNativeEventFilter.__init__(self)
        self.windows = {}
        app.installEventFilter(self)
        app.installNativeEventFilter(self)

    def eventFilter(self, dialog, event):
        if not isinstance(dialog, QDialog):
            return False
        kind = event.type()
        # QFileDialog may polish inside its C++ constructor, before PySide
        # finishes its final Python wrapper. Qt properties survive that step.
        if dialog.property('_integrated_dialog') and not hasattr(dialog, 'title_bar'):
            bar = dialog.findChild(TitleBar, 'integratedTitleBar', Qt.FindChildOption.FindDirectChildrenOnly)
            if bar is not None:
                dialog.title_bar = bar
                dialog._integrated_dialog = True
                dialog._content_heading = bool(dialog.property('_content_heading'))
        if kind == QEvent.Type.Polish:
            self.decorate(dialog)
        if getattr(dialog, '_integrated_dialog', False):
            if kind in (QEvent.Type.Resize, QEvent.Type.Show):
                bar = dialog.title_bar
                if kind == QEvent.Type.Show:
                    dialog.setMinimumWidth(max(dialog.minimumWidth(), bar.minimumSizeHint().width()))
                bar.setGeometry(0, 0, dialog.width(), bar.sizeHint().height())
                bar.raise_()
            if kind == QEvent.Type.Show:
                handle = int(dialog.winId())
                if handle not in self.windows:
                    self.windows[handle] = weakref.ref(dialog)
                    dialog.destroyed.connect(lambda *_, h=handle: self.windows.pop(h, None))
                apply_native_frame(dialog)
            if kind == QEvent.Type.WindowTitleChange and not dialog._content_heading:
                dialog.title_bar.heading.setText(dialog.windowTitle().removeprefix('米花 · '))
        return False

    def decorate(self, dialog):
        if (getattr(dialog, '_integrated_dialog', False) or hasattr(dialog, 'title_bar')
                or dialog.windowFlags() & Qt.WindowType.FramelessWindowHint):
            return
        layout = dialog.layout()
        if layout is None:
            return
        dialog._integrated_dialog = True
        dialog.setProperty('_integrated_dialog', True)
        flags = dialog.windowFlags()
        dialog.setWindowFlags((flags | Qt.WindowType.FramelessWindowHint)
                              & ~Qt.WindowType.WindowMaximizeButtonHint
                              & ~Qt.WindowType.WindowContextHelpButtonHint)
        title = dialog.windowTitle().removeprefix('米花 · ')
        first = layout.itemAt(0).widget() if layout.count() else None
        # Reuse short page headings; a paper's full title remains in its reader.
        dialog._content_heading = bool(isinstance(first, QLabel) and first.objectName() == 'title'
                                       and len(first.text()) <= 24)
        dialog.setProperty('_content_heading', dialog._content_heading)
        if dialog._content_heading:
            title = first.text()
            layout.removeWidget(first)
            first.hide()
            first.deleteLater()
        dialog.title_bar = TitleBar(dialog, title, minimize=bool(flags & Qt.WindowType.WindowMinimizeButtonHint))
        margins = layout.contentsMargins()
        dialog.title_bar.layout().setContentsMargins(margins.left(), 10, 6, 4)
        layout.setContentsMargins(margins.left(), 4, margins.right(), margins.bottom())
        height = dialog.title_bar.sizeHint().height()
        existing = dialog.contentsMargins()
        dialog.setContentsMargins(existing.left(), existing.top() + height, existing.right(), existing.bottom())
        dialog.title_bar.setGeometry(0, 0, dialog.width(), height)
        dialog.setMinimumWidth(max(dialog.minimumWidth(), dialog.title_bar.minimumSizeHint().width()))
        dialog.title_bar.show()

    def nativeEventFilter(self, event_type, message):
        if sys.platform != 'win32' or QApplication.platformName() != 'windows':
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        reference = self.windows.get(msg.hWnd)
        window = reference() if reference else None
        if window is not None:
            result = native_frame_event(window, message)
            if result is not None:
                return result
        return False, 0


def install_dialog_chrome():
    install_focus_style()
    app = QApplication.instance()
    if app is not None and not hasattr(app, '_dialog_chrome'):
        # Qt file pickers participate in the same title and palette treatment.
        app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
        app._dialog_chrome = DialogChrome(app)


class RoundedMenu(QMenu):
    """Paint the popup on transparent corners without a rectangular OS shadow."""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Windows' menu style also paints a square shadow outside paintEvent.
        self._panel_style = QuietFocusStyle(QStyleFactory.create('Fusion'))
        self._panel_style.setParent(self)
        self.setStyle(self._panel_style)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor('#fffdf8'))
        painter.setPen(QPen(QColor('#ded9cc'), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 10, 10)
        # QMenu's native panel painter adds a square lower-right frame/shadow.
        # Draw its standard action controls directly inside our rounded panel.
        for action in self.actions():
            if action.isVisible():
                option = QStyleOptionMenuItem()
                self.initStyleOption(option, action)
                option.rect = self.actionGeometry(action)
                self.style().drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, self)
