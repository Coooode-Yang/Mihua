"""Local screen-on break timer. No input hooks, network, or system settings writes."""
from __future__ import annotations

import math
import time


class BreakTimer:
    def __init__(self, store, *, clock=time.monotonic):
        self.store = store
        self.clock = clock
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS sedentary_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                interval_minutes INTEGER NOT NULL, rest_minutes INTEGER NOT NULL,
                mode TEXT NOT NULL, elapsed REAL NOT NULL)''')
            db.execute("INSERT OR IGNORE INTO sedentary_settings VALUES(1,0,45,3,'popup',0)")
            self.settings = dict(db.execute('SELECT * FROM sedentary_settings WHERE id=1').fetchone())
        self.elapsed = self.settings.pop('elapsed')
        self.settings.pop('id')
        self.last = clock()
        self.last_active = False
        self.phase = 'work'
        self.snooze_remaining = 0
        self.rest_until = None
        self.dirty = 0

    def save(self):
        with self.store.connect() as db:
            db.execute('''UPDATE sedentary_settings SET enabled=?,interval_minutes=?,
                rest_minutes=?,mode=?,elapsed=? WHERE id=1''',
                (self.settings['enabled'], self.settings['interval_minutes'],
                 self.settings['rest_minutes'], self.settings['mode'], self.elapsed))
        self.dirty = 0

    def configure(self, enabled, interval_minutes, rest_minutes, mode):
        if (type(enabled) is not bool or type(interval_minutes) is not int or
                type(rest_minutes) is not int or not 1 <= interval_minutes <= 240 or
                not 1 <= rest_minutes <= 30 or mode not in ('popup', 'fullscreen')):
            raise ValueError('请选择有效的提醒设置：间隔 1–240 分钟，休息 1–30 分钟。')
        previous = self.settings.copy()
        self.settings = dict(enabled=enabled, interval_minutes=interval_minutes,
                             rest_minutes=rest_minutes, mode=mode)
        try:
            self.save()
        except Exception:
            self.settings = previous
            raise
        self.last = self.clock()
        self.last_active = False
        if not enabled:
            self.phase = 'work'
            self.snooze_remaining = 0
            self.rest_until = None

    def poll(self, active):
        now = self.clock()
        delta = now - self.last
        self.last = now
        eligible = active and self.last_active and 0 <= delta <= 10
        self.last_active = active
        if not self.settings['enabled']:
            return None
        if self.phase == 'rest':
            if now >= self.rest_until:
                self.phase = 'work'
                self.rest_until = None
                self.last_active = False
                return 'rest_finished'
            return None
        if self.phase == 'waiting' or not eligible:
            return None
        if self.snooze_remaining > 0:
            self.snooze_remaining = max(0, self.snooze_remaining - delta)
            due = self.snooze_remaining == 0
        else:
            self.elapsed += delta
            due = self.elapsed >= self.settings['interval_minutes'] * 60
        self.dirty += delta
        if due:
            self.save()
            self.phase = 'waiting'
            return 'due'
        if self.dirty >= 30:
            self.save()
        return None

    def start_rest(self):
        self.elapsed = 0
        self.save()
        self.phase = 'rest'
        self.snooze_remaining = 0
        self.rest_until = self.clock() + self.settings['rest_minutes'] * 60

    def snooze(self):
        self.phase = 'work'
        self.rest_until = None
        self.snooze_remaining = 5 * 60
        self.last = self.clock()
        self.last_active = False

    def reset(self):
        previous = self.elapsed
        self.elapsed = 0
        try:
            self.save()
        except Exception:
            self.elapsed = previous
            raise
        self.phase = 'work'
        self.rest_until = None
        self.snooze_remaining = 0
        self.last = self.clock()
        self.last_active = False

    @property
    def remaining(self):
        if self.phase == 'rest':
            return max(0, math.ceil(self.rest_until - self.clock()))
        if self.snooze_remaining:
            return math.ceil(self.snooze_remaining)
        return max(0, math.ceil(self.settings['interval_minutes'] * 60 - self.elapsed))
