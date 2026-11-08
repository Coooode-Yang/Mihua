from datetime import datetime, timedelta

import pytest

from westie_agent.growth import GrowthStore
from westie_agent.growth_focus import RewardFocusTimer
from westie_agent.personal_store import PersonalStore


@pytest.fixture
def growth(tmp_path):
    now=[datetime(2026,11,2,10)]
    store=GrowthStore(tmp_path,now=lambda:now[0])
    yield store,now
    store.close()


def feed(store, now, seconds, prefix='time'):
    n=0
    while seconds>0:
        part=min(10,seconds)
        now[0]+=timedelta(seconds=part)
        store.credit_focus(f'{prefix}-{n}',part,now[0])
        n+=1
        seconds-=part


def test_25_minutes_one_coin_no_completion_bonus_and_partial_survives_restart(growth,tmp_path):
    store,now=growth
    feed(store,now,1490)
    assert store.profile()['coins']==0
    reopened=GrowthStore(tmp_path,now=lambda:now[0])
    try:
        assert reopened.profile()['coin_seconds']==1490
        assert reopened.credit_focus('last-ten',10,now[0])==1
        assert reopened.profile()['coins']==1
        assert reopened.profile()['coin_seconds']==0
        assert reopened.credit_focus('last-ten',10,now[0])==0
        assert reopened.profile()['focus_seconds']==1500
    finally:
        reopened.close()


def test_daily_cap_retains_remainder_and_later_time_still_grows(growth):
    store,now=growth
    feed(store,now,7800)
    p=store.profile()
    assert p['coins']==4 and p['coin_seconds']==1200
    assert p['focus_seconds']==7800 and p['xp']>=130
    now[0]+=timedelta(days=1)
    feed(store,now,300,'next-day')
    assert store.profile()['coins']==5 and store.profile()['coin_seconds']==0


def test_midnight_splits_cap_between_two_days(growth):
    store,now=growth
    with store.db:
        store.db.execute("INSERT INTO daily(day,focus) VALUES('2026-11-02',7200)")
    now[0]=datetime(2026,11,3,0,0,5)
    store.credit_focus('midnight',10,now[0])
    assert store.profile()['coin_seconds']==5
    assert store.summary()['today']['focus']==5


def test_word_checks_rewards_and_cross_day_review_mastery(growth):
    store,now=growth
    word=store.next_word()['word']
    assert store.answer(word,'wrong')==(False,0)
    assert store.summary()['learned']==0
    assert store.answer(word,word.upper())==(True,2)
    assert store.answer(word,word)==(True,0)
    assert store.next_word(review=True) is None
    for interval in (1,3,7):
        now[0]+=timedelta(days=interval)
        assert store.next_word(review=True)['word']==word
        assert store.answer(word,word)==(True,1)
        assert store.answer(word,word)==(True,0)
    assert store.summary()['mastered']==1
    assert store.profile()['coins']==5


def test_word_import_is_atomic_and_existing_progress_not_overwritten(growth):
    store,_=growth
    assert store.add_words([('companion','伙伴','My companion is here.')])==1
    store.answer('companion','companion')
    assert store.add_words([('companion','不同释义')])==0
    with pytest.raises(ValueError):
        store.add_words([('testword','测试词'),('123','无效')])
    assert store.db.execute("SELECT 1 FROM words WHERE word='testword'").fetchone() is None
    assert store.summary()['learned']==1


def test_preset_banks_filter_learning_and_review_and_preserve_progress(growth, tmp_path):
    from westie_agent.vocabulary_banks import BANKS, PRESET_WORDS
    store, now = growth
    assert len(BANKS) == 6 and len(PRESET_WORDS) == 162
    for bank in BANKS:
        assert store.word_stats(bank.key)['total'] == len(bank.words)
        assert store.next_word(bank=bank.key)['word'] in {row[0] for row in bank.words}
    term = store.next_word(bank='ielts_listening')['word']
    store.answer(term, term)
    assert store.word_stats('ielts_listening')['learned'] == 1
    assert store.word_stats('academic')['learned'] == 0
    now[0] += timedelta(days=1)
    assert store.next_word(review=True, bank='ielts_listening')['word'] == term
    assert store.next_word(review=True, bank='academic') is None
    before = dict(store.db.execute('SELECT * FROM words WHERE word=?', (term,)).fetchone())
    reopened = GrowthStore(tmp_path, now=lambda: now[0])
    try:
        assert dict(reopened.db.execute('SELECT * FROM words WHERE word=?', (term,)).fetchone()) == before
        assert reopened.profile()['coins'] == 2
    finally:
        reopened.close()
    with pytest.raises(ValueError):
        store.next_word(bank='missing')


def test_purchase_is_atomic_no_duplicates_and_equipment_persists(growth,tmp_path):
    store,now=growth
    with pytest.raises(ValueError):
        store.purchase('scarf')
    assert not store.owned() and store.profile()['coins']==0
    assert store.welcome() and not store.welcome()
    for row in store.db.execute('SELECT word FROM words LIMIT 70').fetchall():
        store.answer(row['word'],row['word'])
    xp=store.profile()['xp']
    store.purchase('scarf')
    store.purchase('bow')
    store.equip('scarf')
    store.equip('bow')
    assert store.equipped()=={'neck':'bow'}
    assert store.profile()['xp']==xp and store.profile()['coins']==0
    with pytest.raises(ValueError):store.purchase('bow')
    with pytest.raises(ValueError):store.equip('graduate')
    with pytest.raises(ValueError):store.purchase('badge')
    reopened=GrowthStore(tmp_path,now=lambda:now[0])
    try:
        assert reopened.equipped()=={'neck':'bow'} and len(reopened.owned())==2
        reopened.unequip()
        assert not reopened.equipped()
    finally:reopened.close()


def test_seven_learning_days_need_not_be_consecutive(growth):
    store,now=growth
    for i in range(7):
        store.answer(store.next_word()['word'],store.next_word()['word'])
        now[0]+=timedelta(days=2)
    assert store.summary()['days']==7
    assert 'companion' in store.owned()
    assert any(m['key']=='seven_days' for m in store.history('memories'))


def timer_setup(tmp_path):
    wall=[datetime(2026,11,2,12).timestamp()]
    mono=[0.0]
    locked=[False]
    store=PersonalStore(tmp_path)
    focus=RewardFocusTimer(store,lambda:wall[0],monotonic=lambda:mono[0],locked=lambda:locked[0])
    rewards=GrowthStore(tmp_path,now=lambda:datetime.fromtimestamp(wall[0]))
    focus.rewards=rewards
    def advance(seconds):
        wall[0]+=seconds
        mono[0]+=seconds
        focus.poll()
    return focus,rewards,wall,mono,locked,advance


def test_shared_timer_excludes_pause_lock_sleep_restart_and_break(tmp_path):
    focus,rewards,wall,mono,locked,advance=timer_setup(tmp_path)
    try:
        focus.configure(25,1)
        focus.start()
        advance(10)
        assert rewards.profile()['focus_seconds']==10
        focus.pause()
        advance(100)
        focus.resume()
        locked[0]=True
        advance(10)
        locked[0]=False
        advance(10)  # Conservative sample around unlocking is also excluded.
        assert rewards.profile()['focus_seconds']==10
        advance(3600)  # Sleep/long scheduling gap.
        assert focus.snapshot()['remaining_s']==1490
        advance(10)
        assert rewards.profile()['focus_seconds']==20
        focus.checkpoint_close()
        remaining=focus.snapshot()['remaining_s']
        wall[0]+=10000
        reopened=RewardFocusTimer(focus.store,lambda:wall[0],monotonic=lambda:mono[0],locked=lambda:False)
        reopened.rewards=rewards
        assert reopened.snapshot()['status']=='paused'
        assert reopened.snapshot()['remaining_s']==remaining
        focus.stop()
        focus.start('break')
        for _ in range(6):advance(10)
        assert focus.snapshot()['status']=='finished'
        assert rewards.profile()['focus_seconds']==20
    finally:rewards.close()


def test_completion_emits_only_one_coin_and_outbox_replay_safe(tmp_path):
    focus,rewards,_,_,_,advance=timer_setup(tmp_path)
    try:
        focus.start()
        for _ in range(150):advance(10)
        assert focus.snapshot()['status']=='finished'
        focus.poll()
        assert rewards.profile()['coins']==1
        assert rewards.profile()['focus_seconds']==1500
        assert rewards.profile()['xp']==25
        with focus.store.connect() as db:
            db.execute("INSERT INTO focus_reward_outbox VALUES('recover',10,'2026-11-02T12:25:10')")
        focus.drain_rewards()
        with focus.store.connect() as db:
            db.execute("INSERT INTO focus_reward_outbox VALUES('recover',10,'2026-11-02T12:25:10')")
        focus.drain_rewards()
        assert rewards.profile()['focus_seconds']==1510
    finally:rewards.close()


def test_mastery_achievement_and_reading_milestone_are_permanent(growth):
    store,now=growth
    extra=[('custom'+chr(97+i),'自定义词') for i in range(10)]
    store.add_words(extra)
    words=[r['word'] for r in store.db.execute('SELECT word FROM words LIMIT 100').fetchall()]
    assert len(words)==100
    for word in words:store.answer(word,word)
    for interval in (1,3,7):
        now[0]+=timedelta(days=interval)
        for word in words:store.answer(word,word)
    assert store.summary()['mastered']==100 and 'badge' in store.owned()
    feed(store,now,18000,'reading')
    assert any(m['key']=='reading' for m in store.history('memories'))
    now[0]+=timedelta(days=90)
    assert store.summary()['mastered']==100 and 'badge' in store.owned()


def test_unknown_growth_database_version_is_not_rewritten(tmp_path):
    import sqlite3
    path=tmp_path/'data/growth.sqlite3'
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=999')
        db.execute('CREATE TABLE keep_me(value TEXT)')
        db.execute("INSERT INTO keep_me VALUES('original')")
    with pytest.raises(ValueError):GrowthStore(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==999
        assert db.execute('SELECT * FROM keep_me').fetchone()[0]=='original'


def test_completed_round_includes_small_checkpoint_overhead(tmp_path):
    focus,rewards,wall,_,_,advance=timer_setup(tmp_path)
    try:
        focus.start()
        wall[0]+=0.02
        advance(1)
        for _ in range(149):advance(10)
        advance(9)
        assert focus.snapshot()['status']=='finished'
        assert rewards.profile()['focus_seconds']==pytest.approx(1500)
        assert rewards.profile()['coins']==1
    finally:rewards.close()
