from pathlib import Path

import pytest

from westie_agent.personal_store import PersonalStore
from westie_agent.sedentary import BreakTimer


@pytest.fixture
def timer(tmp_path):
    now = [0.0]
    timer = BreakTimer(PersonalStore(tmp_path), clock=lambda: now[0])
    timer.configure(True, 1, 1, 'fullscreen')
    return timer, now


def advance(timer, now, seconds, active=True):
    result = []
    for _ in range(seconds):
        now[0] += 1
        event = timer.poll(active)
        if event:
            result.append(event)
    return result


def test_screen_time_excludes_off_locked_sleep_and_wall_clock_gaps(timer):
    t, now = timer
    advance(t, now, 31)
    assert t.elapsed == 30
    advance(t, now, 600, active=False)
    assert t.elapsed == 30
    now[0] += 3600  # suspend / delayed event loop never grants a lump of time
    t.poll(True)
    assert t.elapsed == 30
    now[0] -= 5000
    t.poll(True)
    assert t.elapsed == 30
    assert advance(t, now, 30) == ['due']
    assert advance(t, now, 100) == []  # one reminder per interval


def test_snooze_rest_completion_and_disable(timer):
    t, now = timer
    assert advance(t, now, 61) == ['due']
    t.snooze()
    advance(t, now, 200, active=False)
    assert t.snooze_remaining == 300
    assert advance(t, now, 300) == []  # first active sample anchors resume
    assert advance(t, now, 1) == ['due']
    t.start_rest()
    assert t.elapsed == 0
    assert advance(t, now, 60, active=False) == ['rest_finished']
    assert t.phase == 'work'
    t.configure(False, 1, 1, 'fullscreen')
    assert advance(t, now, 1000) == []


def test_persistent_settings_and_progress_never_count_time_outside_app(timer):
    t, now = timer
    advance(t, now, 31)
    t.save()
    now[0] += 99999
    resumed = BreakTimer(t.store, clock=lambda: now[0])
    assert resumed.elapsed == 30 and resumed.settings['mode'] == 'fullscreen'
    resumed.poll(True)
    assert resumed.elapsed == 30
    assert advance(resumed, now, 30) == ['due']
    resumed.reset()
    assert resumed.elapsed == 0 and resumed.phase == 'work'


@pytest.mark.parametrize('args', [(True, 0, 3, 'popup'), (True, 45, 31, 'popup'),
                                  (True, 45, 3, 'lock'), (True, True, 3, 'popup')])
def test_invalid_settings_do_not_change_saved_preferences(timer, args):
    t, now = timer
    before = t.settings.copy()
    with pytest.raises(ValueError):
        t.configure(*args)
    assert t.settings == before
    assert BreakTimer(t.store).settings == before
