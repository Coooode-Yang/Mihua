"""Break settings and dismissible, optionally full-screen rest windows."""
import sqlite3

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from .growth_ui import STYLE, label, button
from .screen_activity import ScreenActivity
from .sedentary import BreakTimer


def duration(seconds):
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class BreakSettings(QDialog):
    def __init__(self, controller):
        super().__init__(controller.personal.window or controller.personal.pet.chat)
        self.c = controller
        self.setWindowTitle('米花 · 久坐提醒设置')
        self.setWindowIcon(controller.personal.pet.windowIcon())
        self.setStyleSheet(STYLE)
        self.resize(500, 430)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        root.addWidget(label('让米花提醒你歇一歇', 'title'))
        self.enabled = QCheckBox('开启久坐提醒')
        self.enabled.setChecked(bool(controller.timer.settings['enabled']))
        root.addWidget(self.enabled)
        form = QFormLayout()
        form.setSpacing(14)
        self.interval = QSpinBox()
        self.interval.setRange(1, 240)
        self.interval.setSuffix(' 分钟')
        self.interval.setValue(controller.timer.settings['interval_minutes'])
        self.rest = QSpinBox()
        self.rest.setRange(1, 30)
        self.rest.setSuffix(' 分钟')
        self.rest.setValue(controller.timer.settings['rest_minutes'])
        for field in (self.interval, self.rest):
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.setKeyboardTracking(False)
        self.mode = QComboBox()
        self.mode.addItem('米花弹窗提醒', 'popup')
        self.mode.addItem('全屏休息 · 暂时打断操作', 'fullscreen')
        self.mode.setCurrentIndex(self.mode.findData(controller.timer.settings['mode']))
        form.addRow('累计亮屏多久后提醒', self.interval)
        form.addRow('每次休息多久', self.rest)
        form.addRow('提醒方式', self.mode)
        root.addLayout(form)
        root.addWidget(label('只在米花运行且屏幕点亮、未锁屏时累计；息屏、锁屏和休眠期间暂停。看视频或阅读时也会计时。\n全屏休息会覆盖各个屏幕，倒计时结束自动关闭；可按 Esc 或点击“5 分钟后提醒”暂时返回。', 'note'))
        self.status = label('', 'note')
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.reset_button = button('重新累计亮屏时间', self.reset)
        self.preview_button = button('预览提醒', self.preview)
        self.save_button = button('保存设置', self.save_settings, True)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self.refresh_status()
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)

    def refresh_status(self):
        t = self.c.timer
        if self.c.monitor.problem:
            message = self.c.monitor.problem
        elif not t.settings['enabled']:
            message = '提醒已关闭。开启后，米花会按你的节奏提醒。'
        elif t.phase == 'rest':
            message = '正在休息 · 剩余 ' + duration(t.remaining)
        else:
            state = '正在累计' if self.c.monitor.active else '暂停累计'
            message = f'{state} · 已累计亮屏 {int(t.elapsed // 60)} 分钟 · 距下次提醒 {duration(t.remaining)}'
        self.status.setText(message)

    def save_settings(self):
        try:
            self.c.configure(self.enabled.isChecked(), self.interval.value(), self.rest.value(), self.mode.currentData())
            self.status.setText('已保存。' + ('米花会按亮屏时间提醒你休息。' if self.enabled.isChecked() else '久坐提醒已关闭。'))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.status.setText('保存失败：' + str(exc))

    def reset(self):
        try:
            self.c.timer.reset()
            self.c.close_windows()
            self.refresh_status()
        except (OSError, sqlite3.Error):
            self.status.setText('暂时无法保存，请检查磁盘空间和目录权限。')

    def preview(self):
        self.c.show_reminder(preview=True, mode=self.mode.currentData(), minutes=self.rest.value())


class RestWindow(QDialog):
    def __init__(self, controller, *, fullscreen, preview=False, minutes=3):
        # Parentless so minimizing the workbench does not hide the reminder.
        flags = Qt.WindowType.WindowStaysOnTopHint
        if fullscreen:
            flags |= Qt.WindowType.FramelessWindowHint
        super().__init__(None, flags)
        self.c = controller
        self.preview = preview
        self.fullscreen = fullscreen
        self.closing = False
        self.setWindowTitle('米花 · 起来活动一下吧')
        self.setWindowIcon(controller.personal.pet.windowIcon())
        self.setStyleSheet(STYLE)
        self.resize(510, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addStretch()
        pet = QLabel()
        pet.setPixmap(controller.personal.pet.sprites.frames[0].scaled(
            160, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        root.addWidget(pet, alignment=Qt.AlignmentFlag.AlignCenter)
        self.heading = label('叽！起来活动一下吧', 'title')
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.heading)
        self.note = label('伸伸懒腰，喝口水，让眼睛看看远处。', 'note')
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.note)
        self.countdown = label(duration(minutes * 60), 'timer')
        self.countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.countdown)
        actions = QHBoxLayout()
        actions.addStretch()
        self.start_button = button('开始休息', controller.start_rest, True)
        self.start_button.setVisible(not fullscreen and not preview)
        actions.addWidget(self.start_button)
        self.snooze_button = button('关闭预览' if preview else '5 分钟后提醒', self.dismiss)
        actions.addWidget(self.snooze_button)
        actions.addStretch()
        root.addLayout(actions)
        hint = label('预览 · 不会改变当前计时' if preview else
                     'Esc 暂时返回 · 休息结束后自动关闭' if fullscreen else
                     '点击“开始休息”进入倒计时 · Esc 稍后提醒', 'note')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)
        root.addStretch()
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
        if preview:
            # Never leave an unattended preview covering the desktop.
            QTimer.singleShot(15000, self.dismiss)

    def refresh(self):
        if self.c.timer.phase == 'rest' and not self.preview:
            self.heading.setText('米花陪你休息一会儿')
            self.countdown.setText(duration(self.c.timer.remaining))
            self.start_button.hide()

    def dismiss(self):
        if self.preview:
            self.c.close_windows(preview_only=True)
        else:
            self.c.timer.snooze()
            self.c.close_windows()

    def reject(self):
        self.dismiss()

    def closeEvent(self, event):
        if not self.closing:
            event.ignore()
            self.dismiss()
        else:
            event.accept()


class SedentaryController(QObject):
    def __init__(self, personal):
        super().__init__(personal)
        self.personal = personal
        self.timer = BreakTimer(personal.store)
        self.monitor = ScreenActivity()
        self.dialog = None
        self.windows = []
        self.error_reported = False
        self.pulse = QTimer(self)
        self.pulse.setInterval(1000)
        self.pulse.timeout.connect(self.tick)
        self.pulse.start()

    def show_settings(self):
        if self.dialog is None:
            self.dialog = BreakSettings(self)
            self.dialog.finished.connect(lambda _: setattr(self, 'dialog', None))
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def configure(self, enabled, interval, rest, mode):
        self.timer.configure(enabled, interval, rest, mode)
        if not enabled:
            self.close_windows()

    def tick(self):
        try:
            event = self.timer.poll(self.monitor.active)
            if event == 'due':
                self.show_reminder()
                self.personal.pet.say('叽！起来活动一下，休息一会儿吧。', 8)
            elif event == 'rest_finished':
                self.close_windows()
            if not self.monitor.active:
                for window in self.windows:
                    window.hide()
            else:
                for window in self.windows:
                    if not window.isVisible():
                        window.show()
            for window in self.windows:
                window.refresh()
            if self.dialog and self.dialog.isVisible():
                self.dialog.refresh_status()
            self.error_reported = False
        except (ValueError, OSError, sqlite3.Error):
            if not self.error_reported:
                self.personal.pet.chat.add_message('米花 · 久坐提醒', '提醒数据暂时无法保存，请检查磁盘空间与目录权限。')
                self.error_reported = True

    def start_rest(self):
        try:
            self.timer.start_rest()
            for window in self.windows:
                window.refresh()
        except (OSError, sqlite3.Error):
            self.personal.pet.say('休息计时暂时无法保存，请稍后重试。', 8)

    def show_reminder(self, *, preview=False, mode=None, minutes=None):
        # Previews must never replace or dismiss an actual scheduled reminder.
        if any(not w.preview for w in self.windows):
            return
        self.close_windows(preview_only=True)
        fullscreen = (mode or self.timer.settings['mode']) == 'fullscreen'
        minutes = minutes or self.timer.settings['rest_minutes']
        if fullscreen and not preview:
            self.timer.start_rest()
        screens = QApplication.screens() if fullscreen else [None]
        for screen in screens:
            window = RestWindow(self, fullscreen=fullscreen, preview=preview, minutes=minutes)
            self.windows.append(window)
            if screen:
                window.setGeometry(screen.geometry())
                window.show()
                window.windowHandle().setScreen(screen)
                window.showFullScreen()
            else:
                window.show()
            window.refresh()
            window.raise_()
        self.windows[0].activateWindow()
        self.windows[0].snooze_button.setFocus()

    def close_windows(self, *, preview_only=False):
        for window in self.windows[:]:
            if preview_only and not window.preview:
                continue
            self.windows.remove(window)
            window.closing = True
            window.close()
            window.deleteLater()

    def close(self):
        self.pulse.stop()
        self.close_windows()
        if self.dialog:
            self.dialog.close()
        self.monitor.shutdown()
        self.monitor.deleteLater()
        self.timer.save()
