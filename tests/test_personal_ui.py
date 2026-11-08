import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import threading

import pytest
from PySide6.QtCore import QDateTime, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QTableView

from app import ROOT, PetWindow, SpriteBank
from westie_agent.controller import AgentController
from westie_agent.personal import PersonalController
from westie_agent.personal_store import PersonalStore


@pytest.fixture
def personal(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False)
    now = [100000.0]
    controller = PersonalController(pet, tmp_path, clock=lambda: now[0])
    controller.timer.stop()
    yield app, pet, controller, now
    controller.close()
    pet.timer.stop()
    pet.chat.close()
    pet.close()


def test_navigation_offline_task_edit_complete_and_reopen(personal):
    app, pet, controller, _ = personal
    pet.chat.workspace_requested.emit('daily')
    app.processEvents()
    window = controller.window
    assert window.tabs.currentIndex() == 2 and window.isVisible()
    window.task_title.setText('民法阅读')
    window.category.setCurrentText('研究')
    window.save_button.click()
    assert controller.store.tasks()[0]['title'] == '民法阅读'
    window.task_list.setCurrentRow(0)
    window.edit_button.click()
    window.task_title.setText('民法阅读第二节')
    window.save_button.click()
    window.task_list.setCurrentRow(0)
    window.complete_button.click()
    assert not controller.store.tasks() and len(controller.store.tasks('done')) == 1
    window.filter.setCurrentIndex(1)
    window.task_list.setCurrentRow(0)
    window.complete_button.click()
    assert controller.store.tasks()[0]['title'] == '民法阅读第二节'
    window.close()
    pet.chat.workspace_requested.emit('study')
    assert controller.window is window and window.tabs.currentIndex() == 0 and window.isVisible()


def test_deadline_can_be_enabled_picked_typed_saved_reopened_and_removed(personal):
    app, _, controller, _ = personal
    controller.show_page('daily')
    window = controller.window
    assert not window.due_date.isEnabled() and not window.due_time.isEnabled()
    QTest.mouseClick(window.has_due, Qt.MouseButton.LeftButton)
    assert window.has_due.isChecked() and window.has_due.text() == '取消截止时间'
    assert window.due_date.isEnabled() and window.due_time.isEnabled()
    QTest.mouseClick(window.due_date, Qt.MouseButton.LeftButton,
                     pos=QPoint(window.due_date.width() - 10, window.due_date.height() // 2))
    app.processEvents()
    calendar = window.due_date.calendarWidget()
    assert calendar.isVisible()
    selected = calendar.selectedDate()
    calendar_view = calendar.findChild(QTableView, 'qt_calendar_calendarview')
    calendar_view.setFocus()
    QTest.keyClick(calendar_view, Qt.Key.Key_Right)
    QTest.keyClick(calendar_view, Qt.Key.Key_Return)
    app.processEvents()
    assert window.due_date.date() == selected.addDays(1)
    window.due_time.setFocus()
    window.due_time.setSelectedSection(window.due_time.Section.HourSection)
    QTest.keyClicks(window.due_time, '18')
    window.due_time.setSelectedSection(window.due_time.Section.MinuteSection)
    QTest.keyClicks(window.due_time, '45')
    window.task_title.setText('提交论文提纲')
    expected = QDateTime(window.due_date.date(), window.due_time.time()).toSecsSinceEpoch()
    QTest.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    task = controller.store.tasks()[0]
    assert task['due_at'] == expected
    assert QDateTime.fromSecsSinceEpoch(int(task['due_at'])).toString('HH:mm') == '18:45'
    assert PersonalStore(controller.store.path.parents[1]).task(task['id'])['due_at'] == expected
    window.task_list.setCurrentRow(0)
    QTest.mouseClick(window.edit_button, Qt.MouseButton.LeftButton)
    assert window.has_due.isChecked() and window.due_time.time().toString('HH:mm') == '18:45'
    QTest.mouseClick(window.has_due, Qt.MouseButton.LeftButton)
    assert not window.has_due.isChecked() and not window.due_time.isEnabled()
    QTest.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert controller.store.task(task['id'])['due_at'] is None


def test_timer_controls_pause_resume_and_refresh_after_command(personal):
    app, pet, controller, now = personal
    controller.show_page('study')
    window = controller.window
    window.focus_minutes.setValue(1)
    window.start_button.click()
    assert controller.focus.snapshot()['status'] == 'running'
    now[0] += 15
    window.pause_button.click()
    assert controller.focus.snapshot()['remaining_s'] == 45
    assert window.pause_button.text() == '继续'
    now[0] += 100
    window.pause_button.click()
    now[0] += 45
    controller.tick()
    assert controller.focus.snapshot()['status'] == 'finished'
    assert '00:00' == window.clock_label.text()
    assert len(controller.store.sessions()) == 1
    assert '专注啦' in pet.chat.history.toPlainText()
    assert controller.handle_command('开始10分钟专注')
    assert window.focus_minutes.value() == 10


def test_numeric_duration_typing_presets_and_restart(personal):
    app, _, controller, _ = personal
    controller.show_page('study')
    app.processEvents()
    window = controller.window
    assert window.focus_minutes.suffix() == '' and window.break_minutes.suffix() == ''
    assert window.focus_minutes.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    field = window.focus_minutes
    field.setFocus()
    field.lineEdit().selectAll()
    QTest.keyClicks(field, '37')
    # Enter commits a custom duration; it must not accidentally start the timer.
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert controller.focus.snapshot()['focus_minutes'] == 37
    assert controller.focus.snapshot()['status'] == 'idle'
    assert not any(b.isChecked() for _, b in window.duration_presets[field])
    preset = next(b for n, b in window.duration_presets[field] if n == 45)
    QTest.mouseClick(preset, Qt.MouseButton.LeftButton)
    assert field.value() == 45 and preset.isChecked()
    QTest.mouseClick(preset, Qt.MouseButton.LeftButton)
    assert preset.isChecked()
    rest = window.break_minutes
    rest.setFocus()
    app.processEvents()
    rest.lineEdit().selectAll()
    QTest.keyClicks(rest, '8')
    QTest.keyClick(rest, Qt.Key.Key_Tab)
    app.processEvents()
    assert controller.focus.snapshot()['break_minutes'] == 8
    from westie_agent.personal_store import FocusTimer
    restarted = FocusTimer(PersonalStore(controller.store.path.parents[1]))
    assert restarted.snapshot()['focus_minutes'] == 45 and restarted.snapshot()['break_minutes'] == 8
    window.start_button.click()
    assert all(not b.isEnabled() for values in window.duration_presets.values() for _, b in values)
    assert not window.focus_minutes.isEnabled()


def test_deadline_day_shortcuts_keep_typed_time_and_persist(personal):
    _, _, controller, _ = personal
    controller.show_page('daily')
    window = controller.window
    window.date_shortcuts[1].click()
    assert window.has_due.isChecked() and window.due_date.date() == QDateTime.currentDateTime().date().addDays(1)
    assert window.due_time.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    window.due_time.setSelectedSection(window.due_time.Section.HourSection)
    QTest.keyClicks(window.due_time, '18')
    window.due_time.setSelectedSection(window.due_time.Section.MinuteSection)
    QTest.keyClicks(window.due_time, '45')
    window.task_title.setText('检查截止时间快捷选择')
    window.save_button.click()
    task = controller.store.tasks()[0]
    saved = QDateTime.fromSecsSinceEpoch(int(task['due_at']))
    assert saved.date() == QDateTime.currentDateTime().date().addDays(1)
    assert saved.toString('HH:mm') == '18:45'


def test_task_reminder_is_punctual_during_focus_and_is_not_repeated(personal):
    _, pet, controller, now = personal
    controller.store.save_task('喝水', due_at=now[0])
    controller.focus.configure(1, 1)
    controller.focus.start()
    controller.tick()
    assert pet.chat.history.toPlainText().count('这些事情到时间') == 1
    assert pet.state == 'jump' and controller.focusing
    now[0] += 60
    controller.tick()
    assert pet.chat.history.toPlainText().count('这些事情到时间') == 1
    now[0] += 20
    controller.tick()
    assert pet.chat.history.toPlainText().count('这些事情到时间') == 1


def test_commands_are_explicit_and_user_text_is_plain(personal):
    _, pet, controller, _ = personal
    assert controller.handle_command('添加待办：<b>读论文</b>')
    assert controller.store.tasks()[0]['title'] == '<b>读论文</b>'
    assert '<b>读论文</b>' in pet.chat.history.toPlainText()
    assert not controller.handle_command('为什么需要开始25分钟专注？')
    assert not controller.handle_command('帮我把这些事情都完成')
    assert controller.handle_command('开始999分钟专注')
    assert controller.focus.snapshot()['status'] == 'idle'


def test_research_entry_has_reading_shelf_without_task_settings(personal):
    _, _, controller, _ = personal
    controller.show_page('research')
    window = controller.window
    assert window.tabs.currentIndex() == 1
    assert not hasattr(window, 'research_task') and window.research.continue_button.text() == '继续阅读'
    assert not hasattr(window, 'task_combo') and not hasattr(window, 'task_focus_button')
    assert window.status.isHidden()


def test_study_layout_fits_after_switching_from_research(personal):
    _, _, controller, _ = personal
    controller.show_page('research')
    window = controller.window
    window.show_page('study')
    QTest.qWait(40)
    page = window.tabs.widget(0)
    assert page.widget().layout().geometry().width() == page.widget().width()
    assert page.horizontalScrollBar().maximum() == 0


def test_background_model_does_not_block_todo_or_timer(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    entered, release = threading.Event(), threading.Event()
    def worker():
        entered.set()
        assert release.wait(3)
        return None
    try:
        assert controller.work('test', worker)
        assert entered.wait(1)
        controller.submit('添加待办：等模型时也能记事')
        controller.submit('开始1分钟专注')
        assert controller.busy and controller.personal.store.tasks()
        assert controller.personal.focus.snapshot()['status'] == 'running'
        assert controller.personal.window.tabs.isEnabled()
        pet.chat.workspace_requested.emit('daily')
        assert controller.personal.window.tabs.currentIndex() == 2
        release.set()
        for _ in range(50):
            QTest.qWait(10)
            if not controller.busy:
                break
        assert not controller.busy
    finally:
        release.set()
        controller.close()
        pet.timer.stop()
        pet.chat.close()
        pet.close()
