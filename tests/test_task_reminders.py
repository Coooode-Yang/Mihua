from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
import time

import pytest
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from app import ROOT, PetWindow, SpriteBank
from westie_agent.personal import PersonalController
from westie_agent.personal_store import PersonalStore
from westie_agent.reminders import PRIORITIES, reminder_time


def test_four_priorities_persist(tmp_path):
    store = PersonalStore(tmp_path)
    for value in PRIORITIES:
        rid = store.save_task(str(value), priority=value)
        assert PersonalStore(tmp_path).task(rid)['priority'] == value


@pytest.mark.parametrize('choices', [
    ['at_deadline', 'before_1m', 'before_10m', 'before_15m'],
    ['before_1m', 'before_1m'], ['unknown'], ['at_deadline', []],
])
def test_invalid_reminder_selection_leaves_no_task(tmp_path, choices):
    store = PersonalStore(tmp_path)
    with pytest.raises(ValueError):
        store.save_task('not saved', due_at=10000, reminders=choices, now=100)
    assert not store.tasks()


def test_advance_reminders_fire_at_three_times_once_across_restart(tmp_path):
    store = PersonalStore(tmp_path)
    rid = store.save_task('提交论文', due_at=10000, now=100,
                         reminders=['before_1h', 'before_10m', 'at_deadline'])
    assert not store.due_reminders(6399)
    assert store.due_reminders(6400)[0]['reminder_labels'] == ['提前1小时']
    assert not PersonalStore(tmp_path).due_reminders(6401)
    store.save_task('改个标题', due_at=10000, task_id=rid, now=7000)
    assert not store.due_reminders(9399)
    assert store.due_reminders(9400)[0]['reminder_labels'] == ['提前10分钟']
    assert store.due_reminders(10000)[0]['reminder_labels'] == ['截止时']
    assert not store.due_reminders(20000)


def test_restart_merges_missed_times_and_parallel_pollers_cannot_duplicate(tmp_path):
    store = PersonalStore(tmp_path)
    store.save_task('合并提醒', due_at=10000, now=100,
                    reminders=['before_1h', 'before_10m', 'at_deadline'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.due_reminders(20000), range(2)))
    assert sum(len(rows) for rows in results) == 1
    event = next(rows[0] for rows in results if rows)
    assert len(event['reminder_labels']) == 3
    assert not PersonalStore(tmp_path).due_reminders(20001)


def test_change_deadline_rearms_but_metadata_and_reselect_do_not(tmp_path):
    store = PersonalStore(tmp_path)
    rid = store.save_task('提醒', due_at=10000, reminders=['before_10m'], now=100)
    assert len(store.due_reminders(9400)) == 1
    store.save_task('提醒', due_at=10000, task_id=rid, reminders=[], now=9450)
    store.save_task('改备注', due_at=10000, task_id=rid, reminders=['before_10m'], now=9460)
    assert not store.due_reminders(9461)
    store.save_task('改日期', due_at=20000, task_id=rid, reminders=['before_10m'], now=9500)
    assert not store.due_reminders(19399)
    assert len(store.due_reminders(19400)) == 1


def test_no_reminders_and_done_deleted_tasks_never_notify(tmp_path):
    store = PersonalStore(tmp_path)
    rid = store.save_task('不提醒', due_at=10000, reminders=[], now=100)
    store.save_task('仍不提醒', due_at=10000, task_id=rid, now=200)
    done = store.save_task('已完成', due_at=10000, now=100)
    deleted = store.save_task('已删除', due_at=10000, now=100)
    store.complete_task(done, True)
    store.delete_task(deleted)
    assert not store.due_reminders(20000)


def test_past_advance_reminders_are_skipped_but_scheduled_missed_reminders_catch_up(tmp_path):
    store = PersonalStore(tmp_path)
    store.save_task('太晚选择', due_at=10000, reminders=['before_1h'], now=7000)
    rid = store.save_task('按时设置', due_at=10000, reminders=['before_10m'], now=7000)
    assert [row['id'] for row in store.due_reminders(20000)] == [rid]


def test_same_day_seven_uses_local_date_and_max_three_includes_deadline(tmp_path):
    due = datetime(2026, 11, 2, 18, 0).timestamp()
    assert datetime.fromtimestamp(reminder_time(due, 'same_day_0700')) == datetime(2026, 11, 2, 7)
    store = PersonalStore(tmp_path)
    store.save_task('早上提醒', due_at=due, reminders=['same_day_0700'], now=due-86400)
    assert not store.due_reminders(datetime(2026, 11, 2, 6, 59).timestamp())
    assert len(store.due_reminders(datetime(2026, 11, 2, 7).timestamp())) == 1


def test_v1_migration_backs_up_and_preserves_previous_delivery(tmp_path):
    store = PersonalStore(tmp_path)
    important = store.save_task('旧重要', priority=2, due_at=500, now=100)
    normal = store.save_task('旧普通', priority=1, due_at=600, now=100)
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TABLE task_reminders')
        db.execute('UPDATE tasks SET notified_due=due_at WHERE id=?', (important,))
        db.execute('PRAGMA user_version=1')
    migrated = PersonalStore(tmp_path)
    assert migrated.task(important)['priority'] == 1
    assert migrated.task(normal)['priority'] == 0
    assert migrated.task(important)['reminders'] == ['at_deadline']
    assert [r['id'] for r in migrated.due_reminders(1000)] == [normal]
    with sqlite3.connect(store.path.with_name('personal-before-reminders-v1.sqlite3')) as backup:
        assert backup.execute('PRAGMA user_version').fetchone()[0] == 1
        assert backup.execute('SELECT priority FROM tasks WHERE id=?', (important,)).fetchone()[0] == 2
    assert PersonalStore(tmp_path).task(important)['priority'] == 1


def test_reminder_picker_limit_cancel_and_save_and_pet_action(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    now = [time.time()]
    controller = PersonalController(pet, tmp_path, clock=lambda: now[0])
    controller.timer.stop()
    try:
        controller.show_page('daily')
        window = controller.window
        assert not window.reminder_button.isEnabled()
        window.has_due.click()
        window.reminder_button.click()
        picker = window.reminder_dialog
        picker.checks['before_10m'].click()
        picker.checks['before_15m'].click()
        assert len(picker.selected()) == 3 and not picker.checks['before_30m'].isEnabled()
        picker.reject()
        assert window.reminder_choices == ['at_deadline']
        window.reminder_button.click()
        picker = window.reminder_dialog
        picker.checks['before_1m'].click()
        picker.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
        assert window.reminder_choices == ['at_deadline', 'before_1m']
        window.task_title.setText('带提醒的事项')
        due = QDateTime.fromSecsSinceEpoch(int(now[0])+600)
        window.due_date.setDate(due.date())
        window.due_time.setTime(due.time())
        window.priority.setCurrentIndex(window.priority.findData(3))
        window.save_button.click()
        task = controller.store.tasks()[0]
        assert task['reminders'] == ['at_deadline', 'before_1m'] and task['priority'] == 3
        window.task_list.setCurrentRow(0)
        window.edit_button.click()
        assert window.reminder_choices == task['reminders']
        controller.focus.start()
        now[0] = task['due_at']-60
        controller.tick()
        assert pet.state == 'jump' and controller.focusing
        assert '带提醒的事项' in pet.chat.history.toPlainText()
        assert '带提醒的事项' in pet.bubble
        controller.tick()
        assert pet.chat.history.toPlainText().count('米花 · 待办提醒') == 1
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close();app.processEvents()
