"""Offline learning economy. All balances and rewards commit in one transaction."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

FOCUS_COIN_SECONDS = 25 * 60
FOCUS_DAILY_SECONDS = 120 * 60
REVIEW_INTERVALS = (1, 3, 7, 14, 30)
ITEMS = {
    'scarf': ('格纹围巾', 'neck', 80, '#bc6861'),
    'bow': ('薄荷蝴蝶结', 'neck', 80, '#78a891'),
    'bell': ('铃铛项圈', 'neck', 80, '#d2ab57'),
    'beret': ('画家贝雷帽', 'head', 240, '#7296a4'),
    'scholar': ('学者帽', 'head', 240, '#545871'),
    'book': ('脚边小书', 'ground', 80, '#83a598'),
    'star': ('星空小夜灯', 'ground', 600, '#b6a0cf'),
    'graduate': ('毕业纪念帽', 'head', 600, '#79668c'),
    'badge': ('单词收藏家徽章', 'badge', 0, '#d2ab57'),
    'companion': ('七日陪伴围巾', 'neck', 0, '#e0a178'),
}



class GrowthStore:
    def __init__(self, base: Path, now=None, *, rewards_enabled=True):
        self.rewards_enabled = rewards_enabled
        self.now = now or datetime.now
        path = Path(base) / 'data' / 'growth.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0,1):
            self.db.close()
            raise ValueError('成长记录来自更新版本，请使用对应版本的米花打开。')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS profile (
                id INTEGER PRIMARY KEY CHECK(id=1), coins INTEGER NOT NULL DEFAULT 0 CHECK(coins>=0),
                xp INTEGER NOT NULL DEFAULT 0, focus_seconds REAL NOT NULL DEFAULT 0,
                coin_seconds REAL NOT NULL DEFAULT 0, welcomed INTEGER NOT NULL DEFAULT 0,
                new_goal INTEGER NOT NULL DEFAULT 5, review_goal INTEGER NOT NULL DEFAULT 5,
                focus_goal INTEGER NOT NULL DEFAULT 25);
            INSERT OR IGNORE INTO profile(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS words (
                word TEXT PRIMARY KEY, meaning TEXT NOT NULL, example TEXT NOT NULL DEFAULT '',
                learned TEXT, due TEXT, reviews INTEGER NOT NULL DEFAULT 0, last_success TEXT);
            CREATE TABLE IF NOT EXISTS daily (
                day TEXT PRIMARY KEY, new_words INTEGER NOT NULL DEFAULT 0,
                reviews INTEGER NOT NULL DEFAULT 0, focus REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, reason TEXT NOT NULL, amount INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS owned (item TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS equipped (slot TEXT PRIMARY KEY, item TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (key TEXT PRIMARY KEY, at TEXT NOT NULL, title TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS focus_receipts (event TEXT PRIMARY KEY);
        ''')
        with self.db:
            from .vocabulary_banks import PRESET_WORDS
            self.db.executemany('INSERT OR IGNORE INTO words(word,meaning,example) VALUES(?,?,?)',
                                PRESET_WORDS)
            self.db.execute('PRAGMA user_version=1')

    def day(self):
        return self.now().date().isoformat()

    def _today(self, day=None):
        day = day or self.day()
        self.db.execute('INSERT OR IGNORE INTO daily(day) VALUES(?)', (day,))
        return day

    def _credit(self, amount, reason, xp=None):
        self.db.execute('UPDATE profile SET coins=coins+?,xp=xp+? WHERE id=1',
                        (amount, amount if xp is None else xp))
        self.db.execute('INSERT INTO ledger(at,reason,amount) VALUES(?,?,?)',
                        (self.now().isoformat(timespec='seconds'), reason, amount))

    def _memory(self, key, title):
        return self.db.execute('INSERT OR IGNORE INTO memories(key,at,title) VALUES(?,?,?)',
                               (key, self.now().isoformat(timespec='seconds'), title)).rowcount

    def welcome(self):
        with self.db:
            if self.profile()['welcomed']:
                return False
            self.db.execute('UPDATE profile SET welcomed=1 WHERE id=1')
            self._credit(20, '初次见面礼', xp=0)
            self._memory('welcome', '今天起，和米花一起学习')
        return True

    def profile(self):
        return dict(self.db.execute('SELECT * FROM profile WHERE id=1').fetchone())

    def summary(self):
        p = self.profile()
        row = self.db.execute('SELECT * FROM daily WHERE day=?', (self.day(),)).fetchone()
        p['today'] = dict(row) if row else dict(new_words=0, reviews=0, focus=0)
        p['learned'] = self.db.execute('SELECT count(*) FROM words WHERE learned IS NOT NULL').fetchone()[0]
        p['mastered'] = self.db.execute('SELECT count(*) FROM words WHERE reviews>=3').fetchone()[0]
        p['due'] = self.db.execute('SELECT count(*) FROM words WHERE due<=?', (self.day(),)).fetchone()[0]
        p['days'] = self.db.execute('SELECT count(*) FROM daily WHERE new_words+reviews+focus>0').fetchone()[0]
        p['level'] = 1 + sum(p['xp'] >= n for n in (100, 300, 700, 1400, 2500, 4000))
        p['title'] = ('初见伙伴','学习搭子','默契朋友','陪读小助手','知识探险家','成长收藏家','终身学习搭档')[p['level']-1]
        return p

    def _milestones(self):
        s = self.summary()
        if s['learned']:
            self._memory('first_word', '第一次回忆成功：学习搭子出发啦')
        if s['focus_seconds'] >= 300:
            self._memory('first_focus', '第一次累计专注五分钟')
        if s['focus_seconds'] >= 18000:
            self._memory('reading', '累计专注 5 小时：解锁陪读动作')
        if s['mastered'] >= 100:
            self._memory('collector', '稳定掌握 100 个词：单词收藏家')
            self.db.execute("INSERT OR IGNORE INTO owned VALUES('badge')")
        if s['days'] >= 7:
            self._memory('seven_days', '七个学习日：米花的第一本学习日记')
            self.db.execute("INSERT OR IGNORE INTO owned VALUES('companion')")
        if s['mastered'] >= 100 and s['focus_seconds'] >= 36000:
            self._memory('graduation', '毕业纪念：100 个掌握词与 10 小时专注')

    @staticmethod
    def normalize_word(word):
        word = word.strip().lower().replace('’', "'")
        if not re.fullmatch(r"[a-z]+(?:[ '-][a-z]+)*", word) or len(word) > 80:
            raise ValueError('单词需为英文，可包含空格、连字符或撇号。')
        return word

    def add_words(self, rows):
        clean = []
        for row in rows:
            if len(row) < 2:
                raise ValueError('每行需要英文和中文释义。')
            word = self.normalize_word(row[0])
            meaning = row[1].strip()
            example = row[2].strip() if len(row) > 2 else ''
            if not meaning or len(meaning) > 300 or len(example) > 1000:
                raise ValueError('请填写释义（最多 300 字），例句最多 1000 字。')
            clean.append((word, meaning, example))
        with self.db:
            before = self.db.total_changes
            self.db.executemany('INSERT OR IGNORE INTO words(word,meaning,example) VALUES(?,?,?)', clean)
            return self.db.total_changes - before

    def _bank_filter(self, bank):
        if bank is None:
            return '', ()
        from .vocabulary_banks import BANK_BY_ID
        if bank not in BANK_BY_ID:
            raise ValueError('请选择有效的预置词库。')
        terms = tuple(row[0] for row in BANK_BY_ID[bank].words)
        return ' AND word IN (' + ','.join('?' for _ in terms) + ')', terms

    def word_stats(self, bank=None):
        clause, terms = self._bank_filter(bank)
        row = self.db.execute('''SELECT count(*) AS total,
            count(learned) AS learned, coalesce(sum(due<=?),0) AS due,
            coalesce(sum(reviews>=3),0) AS mastered FROM words WHERE 1=1''' + clause,
            (self.day(), *terms)).fetchone()
        return dict(row)

    def next_word(self, review=False, bank=None):
        clause, terms = self._bank_filter(bank)
        if review:
            row = self.db.execute('SELECT * FROM words WHERE due<=? AND last_success<>?' + clause + ' ORDER BY due,word LIMIT 1',
                                  (self.day(), self.day(), *terms)).fetchone()
        else:
            row = self.db.execute('SELECT * FROM words WHERE learned IS NULL' + clause + ' ORDER BY word LIMIT 1', terms).fetchone()
        return dict(row) if row else None

    def answer(self, word, answer):
        row = self.db.execute('SELECT * FROM words WHERE word=?', (word,)).fetchone()
        if row is None:
            raise ValueError('找不到这个单词。')
        if answer.strip().lower().replace('’', "'") != word:
            return False, 0
        today = self.day()
        with self.db:
            if row['last_success'] == today or (row['learned'] and row['due'] > today):
                return True, 0
            day = self._today()
            if not row['learned']:
                self.db.execute('UPDATE words SET learned=?,due=?,last_success=? WHERE word=?',
                                (today, (self.now().date()+timedelta(days=1)).isoformat(), today, word))
                self.db.execute('UPDATE daily SET new_words=new_words+1 WHERE day=?', (day,))
                amount, reason = 2, '新词回忆：' + word
            else:
                reviews = row['reviews'] + 1
                due = self.now().date() + timedelta(days=REVIEW_INTERVALS[min(reviews, 4)])
                self.db.execute('UPDATE words SET reviews=?,due=?,last_success=? WHERE word=?',
                                (reviews, due.isoformat(), today, word))
                self.db.execute('UPDATE daily SET reviews=reviews+1 WHERE day=?', (day,))
                amount, reason = 1, '到期复习：' + word
            if self.rewards_enabled:
                self._credit(amount, reason)
                self._milestones()
        return True, amount if self.rewards_enabled else 0

    def credit_focus(self, event, seconds, until=None):
        """Idempotently consume persisted active-time events from the shared timer."""
        if not 0 < seconds <= 10:
            return 0
        until = until or self.now()
        cursor = until - timedelta(seconds=seconds)
        remaining, coins, eligible = seconds, 0, 0.0
        with self.db:
            if not self.db.execute('INSERT OR IGNORE INTO focus_receipts VALUES(?)',(event,)).rowcount:
                return 0
            while remaining > 0.000001:
                midnight = datetime.combine(cursor.date()+timedelta(days=1), datetime.min.time(), tzinfo=cursor.tzinfo)
                part = min(remaining, (midnight-cursor).total_seconds())
                day = self._today(cursor.date().isoformat())
                used = self.db.execute('SELECT focus FROM daily WHERE day=?', (day,)).fetchone()[0]
                eligible += min(part, max(0, FOCUS_DAILY_SECONDS-used))
                self.db.execute('UPDATE daily SET focus=focus+? WHERE day=?', (part, day))
                cursor += timedelta(seconds=part)
                remaining -= part
            p = self.profile()
            total = p['coin_seconds'] + eligible
            coins = int((total+0.000001)//FOCUS_COIN_SECONDS)
            self.db.execute('UPDATE profile SET focus_seconds=focus_seconds+?,coin_seconds=? WHERE id=1',
                            (seconds, max(0, total-coins*FOCUS_COIN_SECONDS)))
            if coins:
                self._credit(coins, '累计有效专注 25 分钟', xp=0)
            # Growth continues beyond the daily currency cap (one XP per full minute).
            xp = int((p['focus_seconds']+seconds)//60)-int(p['focus_seconds']//60)
            self.db.execute('UPDATE profile SET xp=xp+? WHERE id=1', (xp,))
            self._milestones()
        return coins

    def purchase(self, item):
        if item not in ITEMS or ITEMS[item][2] <= 0:
            raise ValueError('这件装扮需要达成成就获得。')
        name, slot, price, _ = ITEMS[item]
        with self.db:
            if item in self.owned():
                raise ValueError('已经拥有这件装扮了。')
            if self.db.execute('UPDATE profile SET coins=coins-? WHERE id=1 AND coins>=?', (price, price)).rowcount != 1:
                raise ValueError('狗狗币还不够，再和米花学一会儿吧。')
            self.db.execute('INSERT INTO owned VALUES(?)', (item,))
            self.db.execute('INSERT INTO ledger(at,reason,amount) VALUES(?,?,?)',
                            (self.now().isoformat(timespec='seconds'), '购买：'+name, -price))
            self._memory('first_outfit', '第一件装扮：' + name)

    def owned(self):
        return {r[0] for r in self.db.execute('SELECT item FROM owned')}

    def equipped(self):
        return {r['slot']: r['item'] for r in self.db.execute('SELECT * FROM equipped') if r['item'] in ITEMS}

    def equip(self, item):
        if item not in self.owned() or item not in ITEMS:
            raise ValueError('请先获得这件装扮。')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO equipped VALUES(?,?)', (ITEMS[item][1], item))

    def unequip(self, slot=None):
        with self.db:
            if slot:
                self.db.execute('DELETE FROM equipped WHERE slot=?', (slot,))
            else:
                self.db.execute('DELETE FROM equipped')

    def goals(self, new, review, focus):
        if not (1 <= new <= 100 and 1 <= review <= 100 and 1 <= focus <= 240):
            raise ValueError('每日目标超出范围。')
        with self.db:
            self.db.execute('UPDATE profile SET new_goal=?,review_goal=?,focus_goal=? WHERE id=1', (new, review, focus))

    def history(self, table):
        if table not in ('ledger', 'memories'):
            raise ValueError('未知记录。')
        return [dict(r) for r in self.db.execute(f'SELECT * FROM {table} ORDER BY at DESC,rowid DESC LIMIT 200')]

    def close(self):
        self.db.close()
