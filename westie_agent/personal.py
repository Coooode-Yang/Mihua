"""Main-thread personal feature coordinator. UI controls never wait for the LLM."""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import time
import weakref

from PySide6.QtCore import QObject, QTimer

from .personal_store import FocusTimer, PersonalStore


class PersonalController(QObject):
    def __init__(self, pet, base: Path, *, clock=time.time, zotero_library=None):
        super().__init__(pet)
        self.pet = pet
        self.store = PersonalStore(base)
        from .growth_focus import RewardFocusTimer
        self.focus = RewardFocusTimer(self.store, clock, track_rewards=False)
        self.clock = clock
        from .zotero import ZoteroLibrary
        self.zotero_library = zotero_library or ZoteroLibrary(base)
        self.window = None
        self.reader = None
        self.journal = None
        self.focus_screen = None
        from .growth_ui import GrowthController
        self.growth = GrowthController(self, base)
        from .sedentary_ui import SedentaryController
        self.sedentary = SedentaryController(self)
        self.last_refresh = 0
        self.last_task_check = 0
        self.error_reported = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        pet.chat.workspace_requested.connect(self.show_page)
        pet.workspace_dispatch = weakref.WeakMethod(self.show_page)
        pet.sedentary_dispatch = weakref.WeakMethod(self.show_sedentary_settings)
        pet.chat.spelling_requested.connect(self.show_spelling)
        self.timer.start()

    @property
    def focusing(self):
        state = self.focus.snapshot()
        return state['status'] == 'running' and state['phase'] == 'focus'

    def show_spelling(self, *, review=False):
        self.growth.show('words')
        self.growth.dialog.word_mode.setCurrentIndex(1 if review else 0)

    def show_sedentary_settings(self):
        self.sedentary.show_settings()

    def show_page(self, page='study'):
        def show():
            if self.window is None:
                from .personal_ui import Workbench
                self.window = Workbench(self)
            self.window.research.library = self.zotero_library
            self.window.show_page(page)
        self.perform(show)

    def show_focus_screen(self):
        from .focus_ui import FocusScreen
        def show():
            if self.focus_screen is None:self.focus_screen=FocusScreen(self)
            self.focus_screen.open_fullscreen()
        self.perform(show)

    def perform(self, action):
        try:
            self.focus.poll()
            result = action()
            self.growth.sync()
            if self.window:
                self.window.refresh()
            return result
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = str(exc) if isinstance(exc, ValueError) else '个人数据暂时无法保存，请检查磁盘空间与目录权限后重试。'
            if self.window:
                self.window.status.setText(message)
                self.window.status.show()
            self.pet.chat.add_message('米花', message)
            return None

    def notify(self, title, message, *, task=False):
        # Also retain it in chat when Windows notifications are muted or unavailable.
        self.pet.chat.add_message(title, message)
        if task:
            self.pet.perform('jump')
        self.pet.say(message[:48], 8)
        if self.pet.tray:
            self.pet.tray.showMessage(title, message, self.pet.tray.MessageIcon.Information, 8000)

    def tick(self):
        try:
            self.focus.poll()
            self.growth.tick()
            for session in self.store.focus_reminders():
                if session['phase'] == 'focus':
                    message = f"完成 {round(session['duration_s'] / 60)} 分钟专注啦，休息一下吧。"
                else:
                    message = '休息结束啦，准备好后再开始下一段专注。'
                self.notify('米花 · 番茄钟', message)
                if self.window:
                    self.window.refresh_records()
            now = self.clock()
            # User-selected reminders remain punctual during a focus round;
            # the pet animation does not alter the independent focus timer.
            if now - self.last_task_check >= 1 or now < self.last_task_check:
                tasks = self.store.due_reminders(now)
                self.last_task_check = now
                if tasks:
                    names = '、'.join(task['title'] for task in tasks[:3])
                    suffix = f' 等 {len(tasks)} 件待办' if len(tasks) > 3 else ''
                    labels = tasks[0]['reminder_labels']
                    detail = '（' + labels[0] + '）' if len(tasks) == 1 and len(labels) == 1 else ''
                    self.notify('米花 · 待办提醒', '这些事情到时间啦：' + names + suffix + detail, task=True)
                    self.pet.say('提醒：' + tasks[0]['title'][:14] + (' 等事项' if len(tasks) > 1 else ''), 8)
            if self.window and self.window.isVisible():
                self.window.refresh_clock()
                if now - self.last_refresh >= 30 or now < self.last_refresh:
                    self.window.refresh_records()
                    self.window.refresh_tasks()
                    self.last_refresh = now
            self.error_reported = False
        except (ValueError, OSError, sqlite3.Error):
            if not self.error_reported:
                self.pet.chat.add_message('米花', '个人数据暂时无法读取，计时和提醒可能延迟。请检查磁盘空间与目录权限。')
                self.error_reported = True

    def handle_command(self, text):
        command = text.strip().rstrip('。！!')
        if command in {'久坐提醒', '久坐提醒设置', '休息提醒'}:
            self.show_sedentary_settings()
            return True
        if self.journal and command in {'科研日志','打开科研日志','今日日志','打开今日日志','随手记','记科研日志'}:
            if command in {'随手记','记科研日志'}: self.journal.edit()
            else: self.journal.show()
            return True
        if command in {'陪我记个词', '背单词', '抽个词', '雅思抽背', '再见见', '单词拼写', '拼写练习', '回忆检查', '单词复习'}:
            self.show_spelling(review=command in {'再见见','单词复习'})
            return True
        pages = {'学习': 'study', '番茄钟': 'study', '打开番茄钟': 'study',
                 '研究': 'research', '最近阅读': 'research', 'Zotero': 'research',
                 '聊天': 'chat', '打开聊天': 'chat', '工具箱': 'toolbox', '打开工具箱': 'toolbox',
                 '日常': 'daily', '待办': 'daily', '待办清单': 'daily', '打开待办': 'daily'}
        if command in pages:
            self.show_page(pages[command])
            return True
        match = re.fullmatch(r'(?:新增|添加)待办[：:]\s*(.+)', command, re.DOTALL)
        if match:
            def add():
                self.store.save_task(match[1])
                self.pet.chat.add_message('米花', '记好啦：' + match[1].strip())
            self.perform(add)
            return True
        match = re.fullmatch(r'开始\s*(?:(\d{1,3})\s*分钟\s*)?专注', command)
        if match:
            def start():
                if match[1]:
                    self.focus.configure(int(match[1]), self.focus.snapshot()['break_minutes'])
                self.focus.start()
                self.show_page('study')
                self.show_focus_screen()
                self.pet.chat.add_message('米花', '专注开始啦，我会在结束时提醒你。')
            self.perform(start)
            return True
        actions = {'暂停专注': self.focus.pause, '暂停计时': self.focus.pause,
                   '继续专注': self.focus.resume, '继续计时': self.focus.resume,
                   '结束专注': self.focus.stop, '结束计时': self.focus.stop,
                   '开始休息': lambda: self.focus.start('break')}
        if command in actions:
            self.perform(actions[command])
            self.show_page('study')
            if command in {'继续专注','继续计时'}:self.show_focus_screen()
            return True
        return False

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self.timer.stop()
        self.sedentary.close()
        if hasattr(self.focus, 'checkpoint_close'):
            self.focus.checkpoint_close()
        self.growth.close()
        if self.focus_screen:
            self.focus_screen.shutdown();self.focus_screen.deleteLater();self.focus_screen=None
        # Save and pause active time; restart never counts time outside the app.
        if self.window:
            self.window.research.shutdown()
            self.window.close()
            self.window.deleteLater()
            self.window=None
