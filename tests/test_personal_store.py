import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from westie_agent.personal_store import FocusTimer, PersonalStore


@pytest.fixture
def setup(tmp_path):
    now = [100000.0]
    store = PersonalStore(tmp_path)
    return store, FocusTimer(store, lambda: now[0]), now


def test_tasks_survive_restart_and_keep_fields(tmp_path):
    store = PersonalStore(tmp_path)
    task_id = store.save_task('  读民法论文  ', '第二节', '研究', 2, 100100, now=100000)
    reopened = PersonalStore(tmp_path)
    task = reopened.task(task_id)
    assert (task['title'], task['note'], task['category'], task['priority'], task['due_at']) == ('读民法论文', '第二节', '研究', 2, 100100)
    reopened.save_task('读第三节', category='学习', task_id=task_id, now=100010)
    assert reopened.task(task_id)['revision'] == 2
    reopened.complete_task(task_id, True)
    assert not reopened.tasks() and len(reopened.tasks('done')) == 1
    reopened.complete_task(task_id, False)
    assert len(reopened.tasks()) == 1
    reopened.delete_task(task_id)
    assert reopened.task(task_id) is None and not reopened.tasks('all')


@pytest.mark.parametrize('title,note,category,priority,due', [
    (' ', '', '日常', 1, None), ('x' * 201, '', '学习', 1, None),
    ('读论文', 'x' * 2001, '研究', 1, None), ('读论文', '', '其他', 1, None),
    ('读论文', '', '研究', 9, None), ('读论文', '', '研究', 1, float('nan')),
    ('读论文', '', '研究', 1, float('inf')), ('读论文', '', '研究', 1, -1),
])
def test_invalid_tasks_leave_no_partial_rows(setup, title, note, category, priority, due):
    store, _, _ = setup
    with pytest.raises(ValueError):
        store.save_task(title, note, category, priority, due)
    assert store.tasks('all') == []


def test_reminders_once_per_deadline_even_after_restart(tmp_path):
    store = PersonalStore(tmp_path)
    task = store.save_task('交作业', due_at=100)
    completed = store.save_task('已完成的事项', due_at=80)
    deleted = store.save_task('已删除的事项', due_at=80)
    store.complete_task(completed, True)
    store.delete_task(deleted)
    assert not store.due_reminders(99)
    assert [row['id'] for row in store.due_reminders(100)] == [task]
    assert not PersonalStore(tmp_path).due_reminders(1000)
    store.save_task('改个标题', due_at=100, task_id=task)
    assert not store.due_reminders(1000)
    store.save_task('推迟到新时间', due_at=1200, task_id=task)
    assert not store.due_reminders(1199)
    assert len(store.due_reminders(1200)) == 1


def test_concurrent_pollers_cannot_deliver_same_reminder_twice(setup):
    store, _, _ = setup
    store.save_task('一起到期', due_at=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.due_reminders(200), range(2)))
    assert sum(len(rows) for rows in results) == 1


def test_pause_restart_resume_preserves_remaining_and_counts_one_completion(setup, tmp_path):
    store, focus, now = setup
    focus.configure(2, 1)
    task = store.save_task('论文第二节', category='研究')
    focus.start(task_id=task)
    now[0] += 30
    focus.pause()
    assert focus.snapshot()['remaining_s'] == 90
    now[0] += 10000
    reopened = FocusTimer(PersonalStore(tmp_path), lambda: now[0])
    assert reopened.snapshot()['status'] == 'paused' and reopened.snapshot()['remaining_s'] == 90
    reopened.resume()
    now[0] += 89
    assert reopened.poll() is None
    now[0] += 1
    assert reopened.poll()['phase'] == 'focus'
    assert reopened.poll() is None
    assert reopened.snapshot()['status'] == 'finished'
    sessions = store.sessions()
    assert len(sessions) == 1 and sessions[0]['elapsed_s'] == 120
    assert sessions[0]['task_title'] == '论文第二节'
    assert len(store.focus_reminders()) == 1 and not store.focus_reminders()


def test_running_timer_survives_suspend_and_restarts_without_extra_rounds(setup, tmp_path):
    store, focus, now = setup
    focus.configure(1, 1)
    focus.start()
    end = focus.snapshot()['ends_at']
    now[0] += 86400
    focus = FocusTimer(PersonalStore(tmp_path), lambda: now[0])
    assert focus.snapshot()['remaining_s'] == 0
    focus.poll()
    assert focus.snapshot()['status'] == 'finished'
    assert len(store.sessions()) == 1 and store.sessions()[0]['ended_at'] == end
    assert store.sessions()[0]['elapsed_s'] == 60


def test_stop_keeps_partial_history_and_deleting_task_keeps_snapshot(setup):
    store, focus, now = setup
    task = store.save_task('保留文章名称')
    focus.start(task_id=task)
    store.delete_task(task)
    now[0] += 30
    focus.stop()
    assert focus.snapshot()['status'] == 'idle'
    session = store.sessions()[0]
    assert session['status'] == 'cancelled' and session['elapsed_s'] == 30
    assert session['task_title'] == '保留文章名称'
    assert not store.focus_reminders() and store.today_stats(now[0])['count'] == 0


def test_break_does_not_count_as_focus(setup):
    store, focus, now = setup
    focus.configure(1, 1)
    focus.start('break')
    now[0] += 60
    assert focus.poll()['phase'] == 'break'
    assert store.sessions() == [] and store.today_stats(now[0])['count'] == 0
    assert store.focus_reminders()[0]['phase'] == 'break'


def test_duplicate_start_and_invalid_link_are_rejected_atomically(setup):
    store, focus, _ = setup
    with pytest.raises(ValueError):
        focus.start(task_id='missing')
    assert store.sessions() == []
    focus.start()
    session_id = focus.snapshot()['session_id']
    with pytest.raises(ValueError):
        focus.start('break')
    assert focus.snapshot()['session_id'] == session_id and len(store.sessions()) == 1


def test_completion_at_pause_boundary_is_still_available_to_notifier(setup):
    store, focus, now = setup
    focus.configure(1, 1)
    focus.start()
    now[0] += 60
    assert focus.pause() is False
    assert len(store.focus_reminders()) == 1
    assert focus.snapshot()['status'] == 'finished'


def test_two_timer_instances_complete_once(setup):
    store, focus, now = setup
    focus.configure(1, 1)
    focus.start()
    now[0] += 60
    second = FocusTimer(store, lambda: now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        events = list(pool.map(lambda timer: timer.poll(), [focus, second]))
    assert sum(e is not None for e in events) == 1
    assert len(store.sessions()) == 1


def test_future_schema_is_not_overwritten(tmp_path):
    store = PersonalStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError, match='更新版本'):
        PersonalStore(tmp_path)
