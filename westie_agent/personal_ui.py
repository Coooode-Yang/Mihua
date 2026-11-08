"""Study / research / daily workbench; all v0.5 features work without an API."""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import time

from PySide6.QtCore import QDateTime, QPoint, QSize, QTime, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QStyle, QStyleOptionTab, QStylePainter,
    QTabBar, QTabWidget, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
)

from .personal_store import CATEGORIES
from .reminders import PRIORITIES, REMINDER_OPTIONS
from .window_chrome import IntegratedWindow, RoundedMenu, TitleBar

STYLE = '''
QWidget { background: #fffaf3; color: #443c36; font-family: "Microsoft YaHei"; font-size: 13px; }
QLabel#eyebrow { color: #6a7967; font-size: 12px; }
QLabel#title { font-size: 25px; font-weight: bold; }
QLabel#clock { color: #4f705c; font-family: "Consolas"; font-size: 56px; font-weight: bold; }
QLabel#muted { color: #82776c; }
QFrame#card { background: #f4f1e8; border: 1px solid #e6dfd2; border-radius: 14px; }
QFrame#card QLabel { background: transparent; }
QFrame#durationCard, QFrame#deadlineRow { background: #f4f1e8; border: 1px solid #e8e1d5; border-radius: 12px; }
QFrame#durationCard QLabel, QFrame#deadlineRow QLabel { background: transparent; }
QPushButton { background: #eee4d6; border: none; border-radius: 8px; padding: 9px 14px; }
QPushButton:hover { background: #e5d6c3; }
QPushButton:disabled { color: #a69f94; background: #f0ece6; }
QPushButton#primary { color: #fff; background: #567a62; }
QPushButton#primary:hover { background: #456951; }
QPushButton#deadlineToggle:checked { background: #567a62; color: #fff; }
QPushButton#toolboxToggle { background: #eee8de; padding: 9px 16px; }
QPushButton#toolboxToggle:hover, QPushButton#toolboxToggle:checked { background: #e1eadd; color: #456951; }
QMenu#toolboxMenu { background: transparent; border: none; padding: 6px; }
QMenu#toolboxMenu::item { background: transparent; padding: 11px 16px; border-radius: 6px; }
QMenu#toolboxMenu::item:selected { background: #e1eadd; color: #354f3d; }
QPushButton#durationPreset, QPushButton#dateShortcut { background: #eae5db; color: #777164; border: 1px solid transparent; border-radius: 7px; padding: 7px 9px; }
QPushButton#durationPreset:hover, QPushButton#dateShortcut:hover { background: #e3e9dd; color: #456951; }
QPushButton#durationPreset:checked { background: #e1eadd; border: 1px solid #b9cbb7; color: #456951; }
QPushButton#durationPreset:disabled { background: #efebe3; color: #b0a89b; border-color: transparent; }
QLineEdit, QTextEdit, QDateEdit, QTimeEdit, QSpinBox, QComboBox {
    background: #f6f2eb; border: 1px solid #e4ddcf; border-radius: 9px; padding: 8px 11px;
    selection-background-color: #dfe8db; selection-color: #354f3d;
}
QLineEdit:hover, QTextEdit:hover, QSpinBox:hover, QComboBox:hover, QDateEdit:hover, QTimeEdit:hover { border-color: #c6cdbb; }
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus { border-color: #7e9a80; background: #fffdf8; }
QDateEdit:disabled, QTimeEdit:disabled { background: #eeebe4; color: #a49b8d; border-color: transparent; }
QSpinBox#minuteInput { background: #fffdf8; border-color: #e1dacd; padding: 6px; font-family: "Segoe UI"; font-size: 18px; font-weight: 600; }
QSpinBox#minuteInput:disabled { color: #a49b8d; background: #eeebe4; }
QComboBox { padding-right: 34px; min-height: 20px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 30px; border: none; background: transparent; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background: #fffdf8; border: 1px solid #ded9cc; padding: 6px; selection-background-color: #e1eadd; selection-color: #354f3d; outline: none; }
QDateEdit { padding-right: 35px; min-height: 20px; }
QDateEdit::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 32px; border: none; background: transparent; }
QDateEdit::down-arrow { image: url("@CALENDAR@"); width: 18px; height: 18px; }
QTimeEdit { min-height: 20px; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #e7eddf; }
QCalendarWidget QToolButton { color: #456951; background: transparent; border: none; border-radius: 6px; padding: 6px; }
QCalendarWidget QAbstractItemView { background: #fffdf8; selection-background-color: #567a62; selection-color: white; border: none; outline: none; }
QListWidget { background: #fcfaf5; border: 1px solid #e6dfd2; border-radius: 12px; padding: 8px; outline: none; }
QListWidget::item { padding: 10px; border-bottom: 1px solid #efebe3; }
QListWidget::item:selected { background: #e3ece3; color: #354f3d; border-radius: 7px; }
QTabWidget::pane { border: none; }
QScrollArea { border: none; }
QScrollBar:vertical { width: 8px; margin: 0; background: transparent; }
QScrollBar::handle:vertical { background: #d7d7c9; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QTabBar::tab { background: #eee8de; border-radius: 8px; padding: 11px 26px; margin: 4px 6px 12px 0; }
QTabBar::tab:selected { color: #fff; background: #567a62; }
QCheckBox#reminderChoice { spacing: 12px; padding: 9px 5px; }
QCheckBox#reminderChoice::indicator { width: 20px; height: 20px; border: 1px solid #c5c9b9; border-radius: 10px; background: #fffdf8; }
QCheckBox#reminderChoice::indicator:checked { border-color: #567a62; background: #567a62; image: url("@CHECK@"); }
QCheckBox#reminderChoice:disabled { color: #aaa396; }
'''
_UI_ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'ui'
STYLE = STYLE.replace('@CHEVRON@', (_UI_ASSETS / 'chevron-down.svg').as_posix()).replace(
    '@CALENDAR@', (_UI_ASSETS / 'calendar.svg').as_posix()).replace('@CHECK@', (_UI_ASSETS / 'check.svg').as_posix())


def label(text='', name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


class EmptyList(QListWidget):
    """A calm empty state without a selectable placeholder or separator line."""
    def __init__(self, empty_text, parent=None):
        super().__init__(parent)
        self.empty_text = empty_text

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count():
            painter = QPainter(self.viewport())
            painter.setPen(QColor('#9a9284'))
            painter.drawText(self.viewport().rect().adjusted(18, 8, -18, -8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)


class ReminderPicker(QDialog):
    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.setWindowTitle('设置提醒')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(370, 530)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(label('选择提醒时间', 'title'))
        self.count_label = label('', 'muted')
        layout.addWidget(self.count_label)
        page = QWidget()
        options = QVBoxLayout(page)
        options.setContentsMargins(0, 0, 0, 0)
        self.checks = {}
        for key, title in REMINDER_OPTIONS.items():
            check = QCheckBox(title)
            check.setObjectName('reminderChoice')
            check.setChecked(key in choices)
            check.toggled.connect(self.refresh_limit)
            self.checks[key] = check
            options.addWidget(check)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        layout.addWidget(scroll, 1)
        layout.addWidget(label('最多选 3 项；取消全部选择即可不提醒。已过去的提前提醒不会补发。', 'muted'))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Ok).setText('确定')
        actions.button(QDialogButtonBox.StandardButton.Ok).setObjectName('primary')
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)
        self.refresh_limit()

    def selected(self):
        return [key for key, check in self.checks.items() if check.isChecked()]

    def refresh_limit(self):
        selected = len(self.selected())
        self.count_label.setText(f'已选 {selected} / 3 项 · 相对事项截止时间')
        for check in self.checks.values():
            check.setEnabled(check.isChecked() or selected < 3)


class WorkbenchTabBar(QTabBar):
    """Keep keyboard navigation while using the selected fill as the focus cue."""
    toolbox_button = None

    def sizeHint(self):
        size = super().sizeHint()
        if self.toolbox_button:
            size += QSize(self.toolbox_button.width() + 6, 0)
        return size

    def minimumSizeHint(self):
        return self.sizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toolbox_button and self.count():
            last = self.tabRect(self.count() - 1)
            self.toolbox_button.move(last.right() + 1, last.top() + 4)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        current = self.currentIndex()
        indices = [i for i in range(self.count()) if i != current]
        if current >= 0:
            indices.append(current)
        for index in indices:
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            option.state &= ~QStyle.StateFlag.State_HasFocus
            painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)


class Workbench(IntegratedWindow):
    def __init__(self, controller):
        # The main window owns the embedded chat, independent of the desktop pet.
        super().__init__()
        self.controller = controller
        self.setWindowIcon(controller.pet.windowIcon())
        self.store = controller.store
        self.focus = controller.focus
        self.editing_id = None
        self.reminder_choices = ['at_deadline']
        self.reminder_dialog = None
        from . import __version__
        self.setWindowTitle(f'米花 · 学习与生活 v{__version__}')
        self.setMinimumSize(600, 500)
        self.resize(self.minimumWidth(), 780)
        self.setStyleSheet(STYLE)
        frame = QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.title_bar.heading.hide()
        self.title_bar.layout().setContentsMargins(0, 4, 0, 12)
        content = QWidget()
        frame.addWidget(content, 1)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 12, 24, 20)
        outer.setSpacing(9)
        self.tabs = QTabWidget()
        self.tabs.setTabBar(WorkbenchTabBar())
        self.tabs.setCornerWidget(self.title_bar, Qt.Corner.TopRightCorner)
        outer.addWidget(self.tabs, 1)
        self.build_study()
        self.build_research()
        self.build_daily()
        self.build_chat()
        self.build_toolbox()
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
            control.setDefault(False)
        self.status = label('', 'muted')
        self.status.hide()
        outer.addWidget(self.status)
        self.tabs.currentChanged.connect(self.page_changed)
        self.refresh()

    def page_changed(self, index):
        quick = getattr(self.controller, 'quick_ask', None)
        if index == 3 and quick:
            quick.attach_to_main()
        self.status.clear()
        self.status.hide()
        if index == 1:
            self.research.activate()
        QTimer.singleShot(0, self.fit_current_page)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.fit_current_page)

    def is_caption_drag_point(self, point):
        """Use the navigation row's empty space for native window dragging."""
        bar = self.tabs.tabBar()
        local = bar.mapFrom(self, point)
        if not 0 <= local.y() < bar.height():
            return False
        return bar.tabAt(local) < 0

    def fit_current_page(self):
        if hasattr(self, 'tabs'):
            page = self.tabs.currentWidget()
            if isinstance(page, QScrollArea) and page.widget():
                page.widget().layout().setGeometry(page.widget().rect())

    def show_page(self, page):
        if page != 'toolbox':
            self.toolbox_menu.hide()
        quick = getattr(self.controller, 'quick_ask', None)
        if quick and (page == 'chat' or (page == 'home' and self.tabs.currentIndex() == 3)):
            quick.attach_to_main()
        index={'study': 0, 'research': 1, 'daily': 2, 'chat': 3}.get(page)
        if index is not None:self.tabs.setCurrentIndex(index)
        self.refresh()
        screen = QApplication.screenAt(self.controller.pet.frameGeometry().center()) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.resize(min(self.width(), bounds.width() - 24), min(self.height(), bounds.height() - 24))
        if not self.isVisible():
            self.move(bounds.center() - self.rect().center())
        self.show()
        self.raise_()
        self.activateWindow()
        if self.tabs.currentIndex() == 1:
            self.research.activate()
        elif self.tabs.currentIndex() == 3:
            self.controller.pet.chat.input.setFocus()
        if page == 'toolbox':
            self.show_toolbox()

    def build_chat(self):
        self.chat_page = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_page)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.addWidget(self.controller.pet.chat)
        self.tabs.addTab(self.chat_page, '聊天')

    def build_toolbox(self):
        bar = self.tabs.tabBar()
        bar.setExpanding(False)
        self.toolbox_button = QPushButton('工具箱  ▾', bar)
        self.toolbox_button.setObjectName('toolboxToggle')
        self.toolbox_button.setAccessibleName('工具箱')
        self.toolbox_button.setCheckable(True)
        self.toolbox_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toolbox_button.setFixedSize(99, 39)
        bar.toolbox_button = self.toolbox_button
        bar.updateGeometry()
        self.toolbox_menu = RoundedMenu(self)
        self.toolbox_menu.setObjectName('toolboxMenu')
        self.toolbox_menu.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.toolbox_menu.setMinimumWidth(224)
        chat = self.controller.pet.chat
        for button in (chat.campus_button, chat.conversion_button, chat.settings_button):
            self.toolbox_menu.addAction(button.text(), button.click)
        self.toolbox_menu.aboutToShow.connect(lambda: self.toolbox_button.setChecked(True))
        self.toolbox_menu.aboutToHide.connect(lambda: self.toolbox_button.setChecked(False))
        self.toolbox_button.clicked.connect(self.show_toolbox)

    def show_toolbox(self):
        if self.toolbox_menu.isVisible():
            self.toolbox_menu.hide()
            return
        self.toolbox_menu.popup(self.toolbox_button.mapToGlobal(QPoint(0, self.toolbox_button.height() + 4)))

    @staticmethod
    def scroll_page(page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def build_study(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName('card')
        clock_layout = QVBoxLayout(card)
        clock_layout.setContentsMargins(22, 16, 22, 18)
        self.phase_label = label('', 'eyebrow')
        phase_row=QHBoxLayout();phase_row.addWidget(self.phase_label,1)
        self.fullscreen_button=QPushButton('全屏计时');self.fullscreen_button.clicked.connect(self.controller.show_focus_screen)
        phase_row.addWidget(self.fullscreen_button);clock_layout.addLayout(phase_row)
        self.clock_label = label('25:00', 'clock')
        clock_layout.addWidget(self.clock_label, alignment=Qt.AlignmentFlag.AlignCenter)
        controls = QHBoxLayout()
        self.start_button = QPushButton('开始专注')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(lambda: self.controller.perform(self.start_focus))
        self.pause_button = QPushButton('暂停')
        self.pause_button.clicked.connect(lambda: self.controller.perform(self.pause_or_resume))
        self.stop_button = QPushButton('结束计时')
        self.stop_button.clicked.connect(self.confirm_stop)
        self.break_button = QPushButton('休息一下')
        self.break_button.clicked.connect(lambda: self.controller.perform(self.start_break))
        for button in (self.start_button, self.pause_button, self.stop_button, self.break_button):
            controls.addWidget(button)
        clock_layout.addLayout(controls)
        layout.addWidget(card)
        settings = QHBoxLayout()
        settings.setSpacing(12)
        self.focus_minutes = QSpinBox()
        self.focus_minutes.setRange(1, 180)
        self.break_minutes = QSpinBox()
        self.break_minutes.setRange(1, 60)
        for field, title in ((self.focus_minutes, '专注时长'), (self.break_minutes, '休息时长')):
            field.setObjectName('minuteInput')
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.setKeyboardTracking(False)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
            field.setFixedWidth(58)
            field.setAccessibleName(title + '（分钟）')
            field.setToolTip('直接输入分钟数，或选择右侧常用时长')
        state = self.focus.snapshot()
        self.focus_minutes.setValue(state['focus_minutes'])
        self.break_minutes.setValue(state['break_minutes'])
        self.duration_presets = {}
        for title, field, presets in (('专注时长', self.focus_minutes, (25, 45, 60)),
                                       ('休息时长', self.break_minutes, (5, 10, 15))):
            group = QFrame()
            group.setObjectName('durationCard')
            body = QVBoxLayout(group)
            body.setContentsMargins(12, 10, 12, 12)
            body.setSpacing(7)
            body.addWidget(label(title, 'eyebrow'))
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(field)
            row.addWidget(label('分钟', 'muted'))
            row.addStretch()
            buttons = []
            for minutes in presets:
                preset = QPushButton(str(minutes))
                preset.setObjectName('durationPreset')
                preset.setCheckable(True)
                preset.setCursor(Qt.CursorShape.PointingHandCursor)
                preset.setAccessibleName(f'{title} {minutes} 分钟')
                preset.clicked.connect(lambda checked=False, target=field, value=minutes: self.choose_duration(target, value))
                row.addWidget(preset)
                buttons.append((minutes, preset))
            self.duration_presets[field] = buttons
            body.addLayout(row)
            settings.addWidget(group, 1)
        layout.addLayout(settings)
        self.focus_minutes.valueChanged.connect(self.save_durations)
        self.break_minutes.valueChanged.connect(self.save_durations)
        self.stats_label = label('', 'eyebrow')
        layout.addWidget(self.stats_label)
        layout.addWidget(label('最近的专注'))
        self.sessions = EmptyList('暂无专注记录')
        self.sessions.setMinimumHeight(90)
        layout.addWidget(self.sessions, 1)
        study_actions = QHBoxLayout()
        self.spelling_button = QPushButton('单词拼写')
        self.spelling_button.clicked.connect(self.controller.show_spelling)
        self.sedentary_button = QPushButton('久坐提醒设置')
        self.sedentary_button.clicked.connect(self.controller.show_sedentary_settings)
        study_actions.addWidget(self.spelling_button)
        study_actions.addWidget(self.sedentary_button)
        layout.addLayout(study_actions)
        self.tabs.addTab(self.scroll_page(page), '学习')

    def build_research(self):
        from .zotero_ui import ResearchPage
        self.research = ResearchPage(self.controller.zotero_library,reader=self.controller.reader,journal=self.controller.journal)
        self.tabs.addTab(self.scroll_page(self.research), '研究')

    def build_daily(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.task_count = label('', 'eyebrow')
        top.addWidget(self.task_count, 1)
        self.filter = QComboBox()
        self.filter.setMinimumWidth(116)
        for text, key in [('未完成', 'open'), ('已完成', 'done'), ('全部', 'all')]:
            self.filter.addItem(text, key)
        self.filter.currentIndexChanged.connect(lambda: self.controller.perform(self.refresh_tasks))
        top.addWidget(self.filter)
        layout.addLayout(top)
        self.task_list = EmptyList('暂无待办')
        self.task_list.setMinimumHeight(110)
        self.task_list.currentItemChanged.connect(self.selection_changed)
        self.task_list.itemDoubleClicked.connect(lambda _: self.edit_task())
        layout.addWidget(self.task_list, 1)
        actions = QHBoxLayout()
        self.complete_button = QPushButton('标记完成')
        self.complete_button.clicked.connect(lambda: self.controller.perform(self.toggle_task))
        self.edit_button = QPushButton('编辑')
        self.edit_button.clicked.connect(lambda: self.controller.perform(self.edit_task))
        self.delete_button = QPushButton('删除')
        self.delete_button.clicked.connect(self.confirm_delete)
        for button in (self.complete_button, self.edit_button, self.delete_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.editor_heading = label('新增待办')
        layout.addWidget(self.editor_heading)
        self.task_title = QLineEdit()
        self.task_title.setMaxLength(200)
        self.task_title.setPlaceholderText('例如：读完民法论文第二节')
        self.task_title.returnPressed.connect(lambda: self.controller.perform(self.save_task))
        layout.addWidget(self.task_title)
        self.task_note = QTextEdit()
        self.task_note.setAcceptRichText(False)
        self.task_note.setPlaceholderText('备注（可选，最多 2000 字）')
        self.task_note.setFixedHeight(62)
        layout.addWidget(self.task_note)
        attributes = QHBoxLayout()
        attributes.addWidget(label('分类'))
        self.category = QComboBox()
        self.category.setMinimumWidth(124)
        self.category.addItems([category for category in CATEGORIES if category != '研究'])
        self.category.setCurrentText('日常')
        attributes.addWidget(self.category)
        attributes.addWidget(label('优先级'))
        self.priority = QComboBox()
        self.priority.setMinimumWidth(196)
        for value, text in PRIORITIES.items():
            self.priority.addItem(text, value)
        self.priority.setCurrentIndex(self.priority.findData(0))
        attributes.addWidget(self.priority)
        attributes.addStretch()
        layout.addLayout(attributes)
        deadline_panel = QFrame()
        deadline_panel.setObjectName('deadlineRow')
        deadline_body = QVBoxLayout(deadline_panel)
        deadline_body.setContentsMargins(10, 10, 10, 10)
        deadline = QHBoxLayout()
        deadline_body.addLayout(deadline)
        deadline.setSpacing(8)
        self.has_due = QPushButton('设置截止时间')
        self.has_due.setObjectName('deadlineToggle')
        self.has_due.setCheckable(True)
        initial_due = QDateTime.currentDateTime().addSecs(3600)
        self.due_date = QDateEdit(initial_due.date())
        self.due_date.setCalendarPopup(True)
        self.due_date.calendarWidget().setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.due_date.calendarWidget().setGridVisible(False)
        self.due_date.setDisplayFormat('yyyy-MM-dd')
        self.due_date.setAccessibleName('截止日期')
        self.due_date.setFixedWidth(176)
        self.due_time = QTimeEdit(QTime(initial_due.time().hour(), initial_due.time().minute()))
        self.due_time.setDisplayFormat('HH:mm')
        self.due_time.setAccessibleName('截止时间')
        self.due_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.due_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.due_time.setFixedWidth(86)
        self.due_time.setToolTip('点击小时或分钟，直接输入数字')
        self.due_date.setEnabled(False)
        self.due_time.setEnabled(False)
        self.has_due.toggled.connect(self.set_deadline_enabled)
        deadline.addWidget(self.has_due)
        deadline.addWidget(self.due_date)
        deadline.addWidget(self.due_time)
        deadline.addStretch()
        self.reminder_button = QPushButton('设置提醒')
        self.reminder_button.clicked.connect(self.show_reminder_picker)
        self.reminder_button.setEnabled(False)
        deadline.addWidget(self.reminder_button)
        reminder_row = QHBoxLayout()
        self.reminder_summary = label('启用截止时间后可设置提醒', 'muted')
        reminder_row.addWidget(self.reminder_summary, 1)
        self.date_shortcuts = []
        for text, offset in (('今天', 0), ('明天', 1)):
            shortcut = QPushButton(text)
            shortcut.setObjectName('dateShortcut')
            shortcut.clicked.connect(lambda checked=False, days=offset: self.choose_deadline_day(days))
            reminder_row.addWidget(shortcut)
            self.date_shortcuts.append(shortcut)
        deadline_body.addLayout(reminder_row)
        layout.addWidget(deadline_panel)
        save_row = QHBoxLayout()
        self.save_button = QPushButton('添加待办')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(lambda: self.controller.perform(self.save_task))
        self.cancel_edit = QPushButton('取消编辑')
        self.cancel_edit.clicked.connect(self.reset_editor)
        self.cancel_edit.hide()
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.cancel_edit)
        save_row.addStretch()
        layout.addLayout(save_row)
        self.tabs.addTab(self.scroll_page(page), '日常')

    def refresh(self):
        self.status.setVisible(bool(self.status.text()))
        state = self.focus.snapshot()
        for widget, value in ((self.focus_minutes, state['focus_minutes']), (self.break_minutes, state['break_minutes'])):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.refresh_duration_presets()
        self.refresh_tasks()
        self.refresh_records()
        self.refresh_clock()

    def refresh_tasks(self):
        selected = self.selected_task_id()
        self.task_list.clear()
        rows = self.store.tasks(self.filter.currentData())
        now = time.time()
        for task in rows:
            parts = [task['category'], PRIORITIES[task['priority']]]
            if task['completed']:
                parts.append('已完成')
            if task['due_at'] is not None:
                due = datetime.fromtimestamp(task['due_at']).strftime('%m-%d %H:%M')
                parts.append(('已到期 ' if not task['completed'] and task['due_at'] <= now else '截止 ') + due)
            title = ('✓ ' if task['completed'] else '○ ') + task['title']
            item = QListWidgetItem(title + '\n' + ' · '.join(parts))
            item.setData(Qt.ItemDataRole.UserRole, task['id'])
            item.setToolTip(task['note'])
            if task['reminders']:
                item.setToolTip(task['note'] + '\n提醒：' + '、'.join(REMINDER_OPTIONS[key] for key in task['reminders']))
            self.task_list.addItem(item)
            if task['id'] == selected:
                self.task_list.setCurrentItem(item)
        self.task_list.setToolTip('双击一条待办可编辑')
        opened = self.store.tasks('open')
        self.task_count.setText(f'{len(opened)} 件待办' if opened else '待办清单')
        self.selection_changed()
        self.task_list.empty_text = '暂无已完成的待办' if self.filter.currentData() == 'done' else '暂无待办'
        self.task_list.viewport().update()

    def refresh_records(self):
        stats = self.store.today_stats()
        self.stats_label.setText(f"今日已完成 {stats['count']} 个番茄 · {round(stats['seconds'] / 60)} 分钟专注")
        self.sessions.clear()
        for session in self.store.sessions():
            status = {'running': '进行中', 'paused': '已暂停', 'completed': '已完成', 'cancelled': '提前结束'}[session['status']]
            stamp = datetime.fromtimestamp(session['started_at']).strftime('%m-%d %H:%M')
            minutes = round((session['elapsed_s'] if session['status'] == 'cancelled' else session['duration_s']) / 60, 1)
            self.sessions.addItem(f"{stamp}  ·  {session['task_title'] or '自由专注'}\n{status}  ·  {minutes:g} 分钟")

    def refresh_clock(self):
        state = self.focus.snapshot()
        seconds = math.ceil(state['remaining_s'] if state['status'] != 'idle' else state['focus_minutes'] * 60)
        self.clock_label.setText(f'{seconds // 60:02}:{seconds % 60:02}')
        active = state['status'] in ('running', 'paused')
        self.fullscreen_button.setEnabled(active)
        phase = '专注' if state['phase'] == 'focus' else '休息'
        self.phase_label.setText({'idle': '', 'running': f'{phase}中', 'paused': f'{phase}已暂停', 'finished': f'{phase}已完成'}[state['status']])
        self.phase_label.setVisible(bool(self.phase_label.text()))
        self.start_button.setEnabled(not active)
        self.break_button.setEnabled(not active)
        self.pause_button.setEnabled(active)
        self.pause_button.setText('继续' if state['status'] == 'paused' else '暂停')
        self.stop_button.setEnabled(active)
        for widget in (self.focus_minutes, self.break_minutes):
            widget.setEnabled(not active)
        for buttons in self.duration_presets.values():
            for _, preset in buttons:
                preset.setEnabled(not active)

    def refresh_duration_presets(self):
        for field, buttons in self.duration_presets.items():
            for value, preset in buttons:
                preset.setChecked(field.value() == value)

    def choose_duration(self, field, value):
        if field.isEnabled():
            field.setValue(value)
            self.refresh_duration_presets()

    def choose_deadline_day(self, offset):
        self.has_due.setChecked(True)
        self.due_date.setDate(QDateTime.currentDateTime().date().addDays(offset))

    def save_durations(self):
        self.controller.perform(lambda: self.focus.configure(self.focus_minutes.value(), self.break_minutes.value()))

    def start_focus(self):
        self.focus.configure(self.focus_minutes.value(), self.break_minutes.value())
        self.focus.start('focus')
        self.controller.show_focus_screen()
        self.controller.pet.say('我会安静陪你，专注结束再提醒。', 4)

    def start_break(self):
        self.focus.configure(self.focus_minutes.value(), self.break_minutes.value())
        self.focus.start('break')

    def pause_or_resume(self):
        if self.focus.snapshot()['status'] == 'paused':
            self.focus.resume()
        else:
            self.focus.pause()

    def confirm_stop(self):
        if QMessageBox.question(self, '结束这段计时', '提前结束会保留记录，但不会计入已完成番茄。继续结束吗？',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.controller.perform(self.focus.stop)

    def selected_task_id(self):
        item = self.task_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selection_changed(self, *_):
        task = self.store.task(self.selected_task_id()) if self.selected_task_id() else None
        for button in (self.complete_button, self.edit_button, self.delete_button):
            button.setEnabled(task is not None)
        self.complete_button.setText('恢复待办' if task and task['completed'] else '标记完成')

    def save_task(self):
        if self.has_due.isChecked():
            # Commit text still being edited before reading the Qt value.
            self.due_date.interpretText()
            self.due_time.interpretText()
            due_at = QDateTime(self.due_date.date(), self.due_time.time()).toSecsSinceEpoch()
        else:
            due_at = None
        self.store.save_task(self.task_title.text(), self.task_note.toPlainText(), self.category.currentText(),
                             self.priority.currentData(), due_at,
                             task_id=self.editing_id, reminders=self.reminder_choices if due_at is not None else [])
        self.reset_editor()
        self.status.setText('已保存')
        self.status.show()

    def reset_editor(self):
        self.editing_id = None
        self.editor_heading.setText('新增待办')
        self.task_title.clear()
        self.task_note.clear()
        self.has_due.setChecked(False)
        self.reminder_choices = ['at_deadline']
        self.refresh_reminder_summary()
        initial_due = QDateTime.currentDateTime().addSecs(3600)
        self.due_date.setDate(initial_due.date())
        self.due_time.setTime(QTime(initial_due.time().hour(), initial_due.time().minute()))
        self.category.setCurrentText('日常')
        legacy = self.category.findText('研究')
        if legacy >= 0:
            self.category.removeItem(legacy)
        self.priority.setCurrentIndex(self.priority.findData(0))
        self.save_button.setText('添加待办')
        self.cancel_edit.hide()

    def set_deadline_enabled(self, enabled):
        self.due_date.setEnabled(enabled)
        self.due_time.setEnabled(enabled)
        self.has_due.setText('取消截止时间' if enabled else '设置截止时间')
        self.reminder_button.setEnabled(enabled)
        self.refresh_reminder_summary()
        if enabled:
            self.due_date.setFocus()

    def refresh_reminder_summary(self):
        enabled = self.has_due.isChecked()
        self.reminder_button.setText(f'提醒 · {len(self.reminder_choices)}/3' if enabled else '设置提醒')
        self.reminder_summary.setText(('提醒：' + '、'.join(REMINDER_OPTIONS[key] for key in self.reminder_choices)
                                       if self.reminder_choices else '不提醒') if enabled else '启用截止时间后可设置提醒')

    def show_reminder_picker(self):
        if not self.has_due.isChecked():
            return
        if self.reminder_dialog:
            self.reminder_dialog.raise_()
            return
        dialog = ReminderPicker(self.reminder_choices, self)
        def apply():
            self.reminder_choices = dialog.selected()
            self.refresh_reminder_summary()
        dialog.accepted.connect(apply)
        dialog.finished.connect(lambda _: setattr(self, 'reminder_dialog', None))
        self.reminder_dialog = dialog
        dialog.open()

    def edit_task(self):
        task = self.store.task(self.selected_task_id())
        if not task:
            return
        self.editing_id = task['id']
        self.editor_heading.setText('编辑待办')
        self.task_title.setText(task['title'])
        self.task_note.setPlainText(task['note'])
        if self.category.findText(task['category']) < 0:
            self.category.addItem(task['category'])
        self.category.setCurrentText(task['category'])
        self.priority.setCurrentIndex(self.priority.findData(task['priority']))
        self.reminder_choices = task['reminders'][:]
        self.has_due.setChecked(task['due_at'] is not None)
        self.refresh_reminder_summary()
        if task['due_at'] is not None:
            due = QDateTime.fromSecsSinceEpoch(int(task['due_at']))
            self.due_date.setDate(due.date())
            self.due_time.setTime(due.time())
        self.save_button.setText('保存修改')
        self.cancel_edit.show()
        self.task_title.setFocus()

    def toggle_task(self):
        task = self.store.task(self.selected_task_id())
        if task:
            self.store.complete_task(task['id'], not task['completed'])

    def confirm_delete(self):
        task_id = self.selected_task_id()
        if not task_id:
            return
        if QMessageBox.question(self, '删除待办', '删除这条待办？关联的专注记录会保留。',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            def remove():
                self.store.delete_task(task_id)
                if self.editing_id == task_id:
                    self.reset_editor()
            self.controller.perform(remove)
