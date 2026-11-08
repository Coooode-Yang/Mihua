"""Isolated end-to-end break reminder verification, also used by frozen builds."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from .personal import PersonalController
from .sedentary import BreakTimer


def run_sedentary_smoke(pet_factory, preview_dir=None):
    report = dict(external_api_called=False, real_user_data_accessed=False)
    with TemporaryDirectory(prefix='bai-sedentary-smoke-') as folder:
        personal = None
        pet = pet_factory()
        try:
            personal = PersonalController(pet, Path(folder))
            personal.timer.stop()
            c = personal.sedentary
            c.pulse.stop()
            native = c.monitor
            now = [0.0]
            c.timer = BreakTimer(personal.store, clock=lambda: now[0])
            c.monitor = SimpleNamespace(active=True, problem='', shutdown=native.shutdown,
                                        deleteLater=native.deleteLater)
            personal.show_page('study')
            main = personal.window
            forbidden = {'米花成长', '米花衣柜', '成长记录'}
            assert not hasattr(main, 'growth_summary')
            assert not any(w.text() in forbidden for w in main.findChildren(QPushButton))
            assert not any(a.text() in forbidden for a in pet.make_menu().actions())
            QApplication.processEvents()
            assert main.sedentary_button.geometry().left() > main.spelling_button.geometry().left()
            main.sedentary_button.click()
            settings = c.dialog
            assert settings.isVisible() and not c.timer.settings['enabled']
            settings.enabled.setChecked(True)
            settings.interval.setValue(1)
            settings.rest.setValue(1)
            settings.save_button.click()
            assert c.timer.settings['enabled'] and c.timer.settings['interval_minutes'] == 1
            report['settings_and_removed_growth_entries_ok'] = True

            def advance(seconds, active=True):
                c.monitor.active = active
                for _ in range(seconds):
                    now[0] += 1
                    c.tick()

            advance(31)
            assert c.timer.elapsed == 30
            advance(500, active=False)
            assert c.timer.elapsed == 30 and not c.windows
            now[0] += 5000
            c.monitor.active = True
            c.tick()
            assert c.timer.elapsed == 30
            advance(30)
            assert len(c.windows) == 1 and c.timer.phase == 'waiting'
            popup = c.windows[0]
            assert not popup.fullscreen and popup.start_button.isVisible()
            advance(20)
            assert c.windows == [popup]
            QTest.keyClick(popup, Qt.Key.Key_Escape)
            assert not c.windows and c.timer.snooze_remaining == 300
            advance(301)
            c.windows[0].start_button.click()
            assert c.timer.phase == 'rest'
            advance(60, active=False)
            assert not c.windows and c.timer.phase == 'work'
            report['screen_on_pause_sleep_snooze_and_popup_rest_ok'] = True

            c.configure(True, 1, 1, 'fullscreen')
            advance(61)
            assert len(c.windows) == len(QApplication.screens())
            assert all(w.isFullScreen() and w.isVisible() for w in c.windows)
            advance(10)
            assert c.windows[0].countdown.text() == '00:50'
            advance(50)
            assert not c.windows and c.timer.phase == 'work'
            report['fullscreen_all_monitors_and_auto_close_ok'] = True

            c.timer.reset()
            c.show_reminder(preview=True, mode='fullscreen', minutes=2)
            assert c.windows[0].preview and c.timer.phase == 'work'
            advance(61)
            assert all(not w.preview for w in c.windows) and c.timer.phase == 'rest'
            c.configure(False, 1, 1, 'fullscreen')
            assert not c.windows
            advance(100)
            assert not c.windows
            report['preview_isolation_disable_and_no_duplicate_ok'] = True

            main.spelling_button.click()
            words = personal.growth.dialog
            words.next_card()
            term = words.card['word']
            words.begin_recall()
            words.answer_input.setText(term)
            words.answer_button.click()
            assert personal.growth.store.summary()['learned'] == 1
            assert personal.growth.store.profile()['coins'] == 0
            assert not hasattr(words, 'tabs') and not pet.equipped
            report['spelling_progress_without_economy_ok'] = True
            if preview_dir:
                output = Path(preview_dir)
                output.mkdir(parents=True, exist_ok=True)
                QApplication.processEvents()
                main.grab().save(str(output/'study.png'))
                c.show_settings()
                c.dialog.grab().save(str(output/'settings.png'))
                c.show_reminder(preview=True, mode='popup', minutes=3)
                c.windows[0].grab().save(str(output/'reminder.png'))
            report['ok'] = True
            return report
        finally:
            if personal:
                personal.close()
            pet.timer.stop()
            pet.chat.close()
            pet.close()
            QApplication.processEvents()
