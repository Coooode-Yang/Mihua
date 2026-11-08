"""Local personal data. No model, network, or connection to the course edition."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from .reminders import PRIORITIES, REMINDER_OPTIONS, reminder_time, validate_reminders

CATEGORIES = ('学习', '研究', '日常')


class PersonalStore:
    def __init__(self, base: Path):
        self.path = Path(base) / 'data' / 'personal.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as probe:
            initial_version = probe.execute('PRAGMA user_version').fetchone()[0]
        if initial_version == 1:
            backup_path = self.path.with_name('personal-before-reminders-v1.sqlite3')
            if not backup_path.exists():
                with closing(sqlite3.connect(self.path)) as original, closing(sqlite3.connect(backup_path)) as backup:
                    original.backup(backup)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 2:
                raise ValueError('个人数据来自更新版本，请使用相应版本的米花打开。')
            schema = '''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, note TEXT NOT NULL,
                    category TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 1,
                    due_at REAL, completed INTEGER NOT NULL DEFAULT 0,
                    notified_due REAL, deleted_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS tasks_due ON tasks(completed, deleted_at, due_at);
                CREATE TABLE IF NOT EXISTS focus_sessions (
                    id TEXT PRIMARY KEY, phase TEXT NOT NULL, task_id TEXT,
                    task_title TEXT NOT NULL, duration_s REAL NOT NULL,
                    started_at REAL NOT NULL, ended_at REAL,
                    status TEXT NOT NULL, elapsed_s REAL NOT NULL DEFAULT 0,
                    notified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS timer (
                    id INTEGER PRIMARY KEY CHECK(id=1), phase TEXT NOT NULL,
                    status TEXT NOT NULL, session_id TEXT, task_id TEXT,
                    duration_s REAL NOT NULL, remaining_s REAL NOT NULL,
                    ends_at REAL, focus_minutes INTEGER NOT NULL,
                    break_minutes INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO timer VALUES (1,'focus','idle',NULL,NULL,1500,1500,NULL,25,5);
                CREATE TABLE IF NOT EXISTS task_reminders (
                    task_id TEXT NOT NULL, option TEXT NOT NULL, trigger_at REAL NOT NULL,
                    due_at REAL NOT NULL, notified_at REAL, active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(task_id,option)
                );
                CREATE INDEX IF NOT EXISTS reminders_trigger ON task_reminders(active,notified_at,trigger_at);
            '''
            for statement in schema.split(';'):
                if statement.strip():
                    db.execute(statement)
            if version == 1:
                # The old "important" flag conveys no urgency; do not invent it.
                db.execute('UPDATE tasks SET priority=CASE WHEN priority=2 THEN 1 ELSE 0 END')
                db.execute('''INSERT OR IGNORE INTO task_reminders(task_id,option,trigger_at,due_at,notified_at)
                    SELECT id,'at_deadline',due_at,due_at,
                    CASE WHEN notified_due=due_at THEN notified_due ELSE NULL END
                    FROM tasks WHERE due_at IS NOT NULL''')
            db.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            # A write transaction also serializes reminder delivery / timer completion.
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def validate_task(title, note, category, priority, due_at):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ValueError('请填写待办名称，最多 200 字。')
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('备注最多 2000 字。')
        if category not in CATEGORIES or type(priority) is not int or priority not in PRIORITIES:
            raise ValueError('请选择有效的分类和优先级。')
        if due_at is not None and (type(due_at) not in (int, float) or not math.isfinite(due_at)
                                   or not 0 <= due_at <= 32503680000):
            raise ValueError('截止时间无效。')

    def save_task(self, title, note='', category='日常', priority=0, due_at=None, *, task_id=None, now=None, reminders=None):
        self.validate_task(title, note, category, priority, due_at)
        now = time.time() if now is None else now
        if reminders is not None:
            reminders = validate_reminders(reminders)
            if reminders and due_at is None:
                raise ValueError('请先设置截止时间，再选择提醒。')
        is_new = not task_id
        with self.connect() as db:
            if task_id:
                row = db.execute('SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL', (task_id,)).fetchone()
                if row is None:
                    raise ValueError('这条待办已删除，请刷新后重试。')
                notified = row['notified_due'] if row['due_at'] == due_at else None
                db.execute('''UPDATE tasks SET title=?,note=?,category=?,priority=?,due_at=?,notified_due=?,
                    updated_at=?,revision=revision+1 WHERE id=?''',
                    (title.strip(), note.strip(), category, priority, due_at, notified, now, task_id))
            else:
                task_id = uuid4().hex
                db.execute('''INSERT INTO tasks(id,title,note,category,priority,due_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)''', (task_id, title.strip(), note.strip(), category, priority, due_at, now, now))
            if reminders is None:
                reminders = [r[0] for r in db.execute('SELECT option FROM task_reminders WHERE task_id=? AND active=1', (task_id,))]
                if due_at is not None and (is_new or row['due_at'] is None):
                    reminders = ['at_deadline']
            self._sync_reminders(db, task_id, due_at, reminders, now)
        return task_id

    @staticmethod
    def _sync_reminders(db, task_id, due_at, choices, now):
        db.execute('UPDATE task_reminders SET active=0 WHERE task_id=?', (task_id,))
        if due_at is None:
            return
        for option in choices:
            trigger = reminder_time(due_at, option)
            previous = db.execute('SELECT * FROM task_reminders WHERE task_id=? AND option=?', (task_id, option)).fetchone()
            if previous and previous['due_at'] == due_at:
                notified = previous['notified_at']
            else:
                # A newly selected advance reminder that already passed is not
                # retroactively fired. Existing schedules still catch up on restart.
                notified = now if option != 'at_deadline' and trigger < now else None
            db.execute('''INSERT INTO task_reminders(task_id,option,trigger_at,due_at,notified_at,active)
                VALUES(?,?,?,?,?,1) ON CONFLICT(task_id,option) DO UPDATE SET
                trigger_at=excluded.trigger_at,due_at=excluded.due_at,notified_at=excluded.notified_at,active=1''',
                (task_id, option, trigger, due_at, notified))

    @staticmethod
    def _with_reminders(db, row):
        data = dict(row)
        selected = {r[0] for r in db.execute('SELECT option FROM task_reminders WHERE task_id=? AND active=1', (data['id'],))}
        data['reminders'] = [key for key in REMINDER_OPTIONS if key in selected]
        return data

    def tasks(self, filter_by='open'):
        condition = {'open': 'AND completed=0', 'done': 'AND completed=1', 'all': ''}.get(filter_by)
        if condition is None:
            raise ValueError('待办筛选无效。')
        with self.connect() as db:
            return [self._with_reminders(db, r) for r in db.execute(f'''SELECT * FROM tasks WHERE deleted_at IS NULL {condition}
                ORDER BY completed, due_at IS NULL, due_at, priority DESC, created_at''')]

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL', (task_id,)).fetchone()
            return self._with_reminders(db, row) if row else None

    def complete_task(self, task_id, completed, *, now=None):
        with self.connect() as db:
            cursor = db.execute('''UPDATE tasks SET completed=?,updated_at=?,revision=revision+1
                WHERE id=? AND deleted_at IS NULL''', (int(bool(completed)), time.time() if now is None else now, task_id))
            if not cursor.rowcount:
                raise ValueError('这条待办已删除。')

    def delete_task(self, task_id, *, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('''UPDATE tasks SET deleted_at=?,updated_at=?,revision=revision+1
                WHERE id=? AND deleted_at IS NULL''', (now, now, task_id))

    def due_reminders(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            rows = db.execute('''SELECT t.*,r.option,r.trigger_at FROM tasks t JOIN task_reminders r ON t.id=r.task_id
                WHERE t.deleted_at IS NULL AND t.completed=0 AND r.active=1 AND r.notified_at IS NULL
                AND r.trigger_at<=? ORDER BY r.trigger_at''', (now,)).fetchall()
            grouped = {}
            for row in rows:
                db.execute('UPDATE task_reminders SET notified_at=? WHERE task_id=? AND option=?', (now, row['id'], row['option']))
                if row['option'] == 'at_deadline':
                    db.execute('UPDATE tasks SET notified_due=due_at WHERE id=?', (row['id'],))
                if row['id'] not in grouped:
                    grouped[row['id']] = dict(row)
                    grouped[row['id']]['reminder_labels'] = []
                grouped[row['id']]['reminder_labels'].append(REMINDER_OPTIONS[row['option']])
            return list(grouped.values())

    def timer(self):
        with self.connect() as db:
            return dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())

    def focus_reminders(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM focus_sessions WHERE status='completed' AND notified=0 ORDER BY ended_at").fetchall()
            for row in rows:
                db.execute('UPDATE focus_sessions SET notified=1 WHERE id=?', (row['id'],))
            return [dict(row) for row in rows]

    def sessions(self, limit=30):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT * FROM focus_sessions WHERE phase='focus'
                ORDER BY started_at DESC,rowid DESC LIMIT ?''', (limit,))]

    def today_stats(self, now=None):
        local = datetime.fromtimestamp(time.time() if now is None else now).astimezone()
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        with self.connect() as db:
            row = db.execute('''SELECT COUNT(*) AS count,COALESCE(SUM(elapsed_s),0) AS seconds
                FROM focus_sessions WHERE phase='focus' AND status='completed'
                AND ended_at>=? AND ended_at<?''', (start.timestamp(), end.timestamp())).fetchone()
            return dict(row)


class FocusTimer:
    """Wall-clock deadlines survive suspend/restart. No automatic next round."""
    def __init__(self, store: PersonalStore, clock=time.time):
        self.store = store
        self.clock = clock

    def snapshot(self):
        state = self.store.timer()
        if state['status'] == 'running':
            state['remaining_s'] = max(0, min(state['duration_s'], state['ends_at'] - self.clock()))
        return state

    def configure(self, focus_minutes, break_minutes):
        if (type(focus_minutes) is not int or not 1 <= focus_minutes <= 180
                or type(break_minutes) is not int or not 1 <= break_minutes <= 60):
            raise ValueError('专注时长为 1–180 分钟，休息为 1–60 分钟。')
        with self.store.connect() as db:
            db.execute('UPDATE timer SET focus_minutes=?,break_minutes=? WHERE id=1', (focus_minutes, break_minutes))

    def start(self, phase='focus', task_id=None):
        if phase not in ('focus', 'break'):
            raise ValueError('计时类型无效。')
        now = self.clock()
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status'] in ('running', 'paused'):
                raise ValueError('请先结束当前计时。')
            task = None
            if task_id and phase == 'focus':
                task = db.execute('SELECT * FROM tasks WHERE id=? AND completed=0 AND deleted_at IS NULL', (task_id,)).fetchone()
                if task is None:
                    raise ValueError('关联待办已完成或已删除，请重新选择。')
            task_id = task['id'] if task else None
            duration = state['focus_minutes' if phase == 'focus' else 'break_minutes'] * 60
            session_id = uuid4().hex
            db.execute('''INSERT INTO focus_sessions(id,phase,task_id,task_title,duration_s,started_at,status)
                VALUES(?,?,?,?,?,?,'running')''', (session_id, phase, task_id, task['title'] if task else '', duration, now))
            db.execute('''UPDATE timer SET phase=?,status='running',session_id=?,task_id=?,duration_s=?,
                remaining_s=?,ends_at=? WHERE id=1''', (phase, session_id, task_id, duration, duration, now + duration))

    def poll(self):
        now = self.clock()
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status'] != 'running' or now < state['ends_at']:
                return None
            db.execute("UPDATE timer SET status='finished',remaining_s=0,ends_at=NULL WHERE id=1")
            db.execute("UPDATE focus_sessions SET status='completed',elapsed_s=?,ended_at=? WHERE id=?",
                       (state['duration_s'], state['ends_at'], state['session_id']))
            return state  # Only the transaction that completes it receives the event.

    def pause(self):
        if self.poll():
            return False
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status'] != 'running':
                raise ValueError('当前没有正在进行的计时。')
            remaining = max(0, min(state['duration_s'], state['ends_at'] - self.clock()))
            db.execute("UPDATE timer SET status='paused',remaining_s=?,ends_at=NULL WHERE id=1", (remaining,))
            db.execute("UPDATE focus_sessions SET status='paused' WHERE id=?", (state['session_id'],))
            return True

    def resume(self):
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status'] != 'paused':
                raise ValueError('当前没有暂停的计时。')
            db.execute("UPDATE timer SET status='running',ends_at=? WHERE id=1", (self.clock() + state['remaining_s'],))
            db.execute("UPDATE focus_sessions SET status='running' WHERE id=?", (state['session_id'],))

    def stop(self):
        if self.poll():
            return
        now = self.clock()
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status'] in ('running', 'paused'):
                remaining = (state['ends_at'] - now) if state['status'] == 'running' else state['remaining_s']
                elapsed = max(0, min(state['duration_s'], state['duration_s'] - remaining))
                db.execute("UPDATE focus_sessions SET status='cancelled',elapsed_s=?,ended_at=? WHERE id=?",
                           (elapsed, now, state['session_id']))
            db.execute("UPDATE timer SET status='idle',session_id=NULL,task_id=NULL,ends_at=NULL,remaining_s=duration_s WHERE id=1")
