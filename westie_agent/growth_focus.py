"""Active-time rewards over the existing focus timer, with a durable outbox."""
from datetime import datetime
import os
import time
from uuid import uuid4

from .personal_store import FocusTimer


def desktop_locked():
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.restype = wintypes.BOOL
    desktop = user32.OpenInputDesktop(0, False, 1)
    if not desktop:
        return True
    user32.GetUserObjectInformationW.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
    try:
        ok = user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed))
        return not ok or name.value.lower() != 'default'
    finally:
        user32.CloseDesktop(desktop)


class RewardFocusTimer(FocusTimer):
    """One timer for fullscreen/workbench; unattended intervals never earn coins."""
    def __init__(self, store, clock=time.time, *, monotonic=time.monotonic, locked=desktop_locked, track_rewards=True):
        self.track_rewards = track_rewards
        super().__init__(store, clock)
        self.monotonic = monotonic
        self.is_locked = locked
        self.locked = self.is_locked()
        self.last_sample = monotonic()
        self.rewards = None
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS focus_reward_outbox (
                id TEXT PRIMARY KEY, seconds REAL NOT NULL, at TEXT NOT NULL)''')
            existed = db.execute("SELECT 1 FROM sqlite_master WHERE name='focus_active_checkpoint'").fetchone()
            db.execute('CREATE TABLE IF NOT EXISTS focus_active_checkpoint (id INTEGER PRIMARY KEY)')
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status']=='running':
                # The first upgrade has only a deadline. Later launches use the
                # actual checkpoint, never subtracting time spent outside the app.
                remaining = state['remaining_s'] if existed else max(0,min(state['remaining_s'],state['ends_at']-clock()))
                db.execute("UPDATE timer SET status='paused',remaining_s=?,ends_at=NULL WHERE id=1",(remaining,))
                db.execute("UPDATE focus_sessions SET status='paused' WHERE id=?",(state['session_id'],))

    def _sample(self):
        mono = self.monotonic()
        seconds = mono-self.last_sample
        self.last_sample = mono
        locked = self.is_locked()
        excluded = locked or self.locked or seconds > 10 or seconds < 0
        self.locked = locked
        if seconds == 0:
            return
        now = self.clock()
        with self.store.connect() as db:
            state = dict(db.execute('SELECT * FROM timer WHERE id=1').fetchone())
            if state['status']!='running':
                return
            if excluded:
                # Retain the last persisted remaining time throughout lock/sleep.
                remaining = state['remaining_s']
                db.execute('UPDATE timer SET ends_at=? WHERE id=1',(now+remaining,))
            else:
                remaining=max(0,min(state['duration_s'],state['ends_at']-now))
                consumed=max(0,state['remaining_s']-remaining)
                # Include small checkpoint/DB overhead so a completed 25-minute
                # round really credits 1,500 seconds. Large wall-clock jumps
                # still cannot mint time that the monotonic sample did not see.
                observed=consumed if abs(consumed-seconds)<=0.25 else seconds
                active=min(observed,state['remaining_s'],10)
                if self.track_rewards and state['phase']=='focus' and active>0:
                    db.execute('INSERT INTO focus_reward_outbox VALUES(?,?,?)',
                               (uuid4().hex,active,datetime.fromtimestamp(now).isoformat(timespec='microseconds')))
            db.execute('UPDATE timer SET remaining_s=? WHERE id=1',(remaining,))
            db.execute('UPDATE focus_sessions SET elapsed_s=? WHERE id=?',
                       (state['duration_s']-remaining,state['session_id']))

    def drain_rewards(self):
        if self.rewards is None:
            return
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM focus_reward_outbox ORDER BY at,rowid')]
        for event in rows:
            # Commit reward + receipt first. If a crash prevents deleting the
            # source event, the receipt makes replay safe on next launch.
            self.rewards.credit_focus(event['id'],event['seconds'],datetime.fromisoformat(event['at']))
            with self.store.connect() as db:
                db.execute('DELETE FROM focus_reward_outbox WHERE id=?',(event['id'],))

    def poll(self):
        self._sample()
        result=super().poll()
        self.drain_rewards()
        return result

    def start(self, phase='focus', task_id=None):
        super().start(phase,task_id)
        self.last_sample=self.monotonic()
        self.locked=self.is_locked()

    def resume(self):
        super().resume()
        self.last_sample=self.monotonic()
        self.locked=self.is_locked()

    def checkpoint_close(self):
        self.poll()
        if self.snapshot()['status']=='running':
            self.pause()
        self.drain_rewards()
