"""Non-blocking update UI, independent of model/API setup."""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout

from .update_install import launch_helper
from .updates import GitHubUpdater, UpdateConfig, UpdateError, installed_version
from . import __version__ as VERSION


class UpdateEvents(QObject):
    done = Signal(str, object)
    failed = Signal(str)
    progress = Signal(int)


class UpdateDialog(QDialog):
    def __init__(self, updater):
        super().__init__(updater.pet.chat)
        self.setWindowTitle("米花 · 软件更新")
        self.resize(470, 330)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("当前版本：" + updater.current))
        self.status = QLabel("点击检查更新，查看是否有新版本。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        layout.addWidget(self.notes)
        self.automatic = QCheckBox("启动时自动检查更新（运行期间每 6 小时检查一次）")
        self.automatic.setChecked(updater.preferences.get("automatic", True))
        self.automatic.toggled.connect(updater.set_automatic)
        layout.addWidget(self.automatic)
        self.auto_download = QCheckBox("自动下载新版本，安装前询问我")
        self.auto_download.setChecked(updater.preferences.get("auto_download", True))
        self.auto_download.toggled.connect(updater.set_auto_download)
        layout.addWidget(self.auto_download)
        row = QHBoxLayout()
        self.check = QPushButton("检查更新")
        self.check.clicked.connect(updater.check)
        self.install = QPushButton("下载更新")
        self.install.setEnabled(False)
        self.install.clicked.connect(updater.install)
        self.cancel = QPushButton("取消下载")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(updater.cancel.set)
        row.addWidget(self.check)
        row.addWidget(self.install)
        row.addWidget(self.cancel)
        layout.addLayout(row)


class UpdateController(QObject):
    def __init__(self, pet, base: Path, agent, *, start_automatic=True):
        super().__init__(pet)
        self.pet, self.base, self.agent = pet, base.resolve(), agent
        self.problem = ""
        try:
            self.current = installed_version(self.base)
        except UpdateError as exc:
            self.current = VERSION
            self.problem = str(exc)
        try:
            self.config = UpdateConfig.load(self.base)
        except UpdateError as exc:
            self.config = UpdateConfig()
            self.problem = str(exc)
        if self.problem:
            self.config = UpdateConfig()
        self.preferences_path = self.base / "data" / "update-preferences.json"
        try:
            preferences = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            self.preferences = {"automatic": bool(preferences.get("automatic", True)),
                                "auto_download": bool(preferences.get("auto_download", True)),
                                "last_check": float(preferences.get("last_check", 0))}
        except (OSError, ValueError, TypeError, AttributeError):
            self.preferences = {"automatic": True, "auto_download": True, "last_check": 0}
        self.dialog = None
        self.release = self.work = None
        self.running = self.stopped = self.background = False
        self.cancel = threading.Event()
        self.events = UpdateEvents(self)
        self.events.done.connect(self.finished)
        self.events.failed.connect(self.failed)
        self.events.progress.connect(self.progress)
        pet.chat.update_requested.connect(self.show)
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self.automatic_check)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(lambda: self.automatic_check(startup=True))
        self.notify_timer = QTimer(self)
        self.notify_timer.setInterval(5000)
        self.notify_timer.timeout.connect(self.notify_ready)
        self.pending_notification = False
        if start_automatic:
            self.timer.start()
            self.startup_timer.start(8000)

    def set_auto_download(self, enabled):
        self.preferences['auto_download'] = enabled
        try:
            self.save_preferences()
        except OSError:
            if self.dialog:
                self.dialog.status.setText("自动下载偏好暂未保存，本次会话仍然生效。")

    def busy(self):
        if self.agent.busy or getattr(getattr(self.agent, 'personal', None), 'focusing', False):
            return True
        for component in ('reader', 'journal'):
            worker = getattr(getattr(self.agent, component, None), 'worker', None)
            if worker is not None and worker.isRunning():
                return True
        return bool(getattr(getattr(getattr(self.agent, 'conversion', None), 'window', None), 'busy', False))

    def notify_ready(self):
        if self.stopped or not self.pending_notification or self.busy():
            return
        self.pending_notification = False
        self.notify_timer.stop()
        self.show()
        self.install()

    def save_preferences(self):
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.preferences), encoding="utf-8")
        temporary.replace(self.preferences_path)

    def set_automatic(self, enabled):
        self.preferences["automatic"] = enabled
        try:
            self.save_preferences()
        except OSError:
            self.dialog.status.setText("自动检查偏好暂未保存，本次会话仍然生效。")

    def show(self):
        if self.dialog is None:
            self.dialog = UpdateDialog(self)
            if self.problem:
                self.dialog.status.setText(self.problem)
            elif not self.config.enabled:
                self.dialog.status.setText("此版本尚未开通在线更新，请联系发布者获取新版。")
        self.dialog.check.setEnabled(not self.running and self.config.enabled)
        self.dialog.install.setEnabled(not self.running and self.release is not None)
        self.dialog.automatic.setEnabled(self.config.enabled)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def automatic_check(self, *, startup=False):
        if (not self.stopped and not self.running and self.config.enabled
                and self.preferences.get("automatic", True)
                and (startup or time.time() - self.preferences.get("last_check", 0) >= 6 * 3600)):
            self.check(background=True)

    def task(self, kind, function):
        if self.running or self.stopped:
            return
        self.running = True
        self.cancel.clear()
        if self.dialog:
            self.dialog.check.setEnabled(False)
            self.dialog.install.setEnabled(False)
            self.dialog.cancel.setEnabled(kind == "download")

        def run():
            try:
                result = function()
                if not self.stopped:
                    self.events.done.emit(kind, result)
                elif kind == 'download':
                    shutil.rmtree(result, ignore_errors=True)
            except Exception as exc:
                if not self.stopped:
                    self.events.failed.emit(str(exc) if isinstance(exc, (UpdateError, OSError)) else "更新未完成，请稍后重试。")
        threading.Thread(target=run, name="mihua-update-" + kind, daemon=True).start()

    def check(self, checked=False, *, background=False):
        if self.running or self.stopped:
            return
        self.background = background
        if self.work is not None:
            if not background:
                self.show()
                self.dialog.status.setText("更新已下载，请点击“退出并安装”。")
            return
        if not background:
            self.show()
            self.dialog.status.setText("正在连接 GitHub 检查新版本…")
        # Cache attempts as well as successes to avoid repeated network errors.
        self.preferences["last_check"] = time.time()
        try:
            self.save_preferences()
        except OSError:
            pass
        self.task("check", lambda: GitHubUpdater(self.config, self.current, self.cancel).check())

    def install(self):
        if self.running or self.stopped or not self.release:
            return
        if not getattr(sys, "frozen", False) or sys.platform != "win32":
            self.dialog.status.setText("源码运行模式仅支持检查版本，请使用打包后的 Windows 程序安装更新。")
            return
        if self.work is None:
            self.dialog.status.setText("正在下载并验证更新，现有程序仍可使用…")
            self.task("download", lambda: GitHubUpdater(self.config, self.current, self.cancel).download(
                self.release, self.base, self.events.progress.emit))
            return
        if self.busy():
            self.dialog.status.setText("请等待当前聊天、解读或文件转换完成，并暂停专注计时后再安装更新。")
            return
        answer = QMessageBox.question(self.dialog, "退出并安装更新", "米花将退出并更新到 " + self.release.version + "，随后自动启动。\n你的聊天、待办、学习记录、模型设置和 API Key 将保留。\n现在安装吗？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        # Prevent new chat/diagnosis operations while preparing process handoff.
        self.agent.busy = True
        self._launching = True
        self._disabled_windows = [window for window in QApplication.topLevelWidgets() if window.isEnabled()]
        for window in self._disabled_windows:
            window.setEnabled(False)
        self.pet.chat.set_busy(True)
        self.dialog.status.setText("正在准备更新，准备完成后自动重启…")
        self.task("launch", lambda: launch_helper(self.base, self.work))

    def progress(self, percent):
        if not self.stopped and self.dialog:
            self.dialog.status.setText(f"正在下载并验证更新：{percent}%")

    def finished(self, kind, result):
        if self.stopped:
            if kind == 'download':
                shutil.rmtree(result, ignore_errors=True)
            return
        self.running = False
        if kind == "launch":
            self.pet.shutdown()
            return
        if kind == "check":
            if self.work:
                shutil.rmtree(self.work, ignore_errors=True)
                self.work = None
            self.release = result
            if (result is not None and self.background and self.preferences.get('auto_download', True)
                    and getattr(sys, 'frozen', False) and sys.platform == 'win32'):
                if self.dialog is None:
                    self.dialog = UpdateDialog(self)
                self.dialog.notes.setPlainText(result.notes or "发布者未提供更新说明。")
                self.install()
                return
            if self.background and result is None and (self.dialog is None or not self.dialog.isVisible()):
                return
            self.show()
            if result is None:
                self.dialog.status.setText("当前已经是最新版本。")
                self.dialog.notes.clear()
            else:
                self.dialog.status.setText("发现新版本：" + result.version)
                self.dialog.notes.setPlainText(result.notes or "发布者未提供更新说明。")
            self.dialog.install.setText("下载更新")
            self.dialog.install.setEnabled(result is not None)
        elif kind == "download":
            self.work = result
            self.dialog.status.setText("更新已下载并通过验证，方便时可退出安装。")
            self.dialog.install.setText("退出并安装")
            self.dialog.install.setEnabled(True)
            if self.background:
                self.pending_notification = True
                self.notify_timer.start()
        self.dialog.check.setEnabled(self.config.enabled)
        self.dialog.cancel.setEnabled(False)

    def failed(self, message):
        if self.stopped:
            return
        self.running = False
        # Only the launch handoff sets agent.busy; never interrupt a real chat.
        if getattr(self, "_launching", False):
            self.agent.reset_busy()
            self._launching = False
            for window in self._disabled_windows:
                window.setEnabled(True)
        if self.background and (self.dialog is None or not self.dialog.isVisible()):
            return
        self.show()
        self.dialog.status.setText(message)
        self.dialog.check.setEnabled(self.config.enabled)
        self.dialog.install.setEnabled(self.release is not None)
        self.dialog.cancel.setEnabled(False)

    def close(self):
        self.stopped = True
        self.cancel.set()
        self.timer.stop()
        self.startup_timer.stop()
        self.notify_timer.stop()
        if self.dialog:
            self.dialog.close()
        if self.work and not getattr(self, '_launching', False):
            shutil.rmtree(self.work, ignore_errors=True)
