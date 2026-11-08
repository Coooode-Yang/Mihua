"""Exercises the packaged personal workflow in a temporary, isolated database."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QAbstractSpinBox, QApplication

from .personal import PersonalController
from .personal_store import FocusTimer, PersonalStore


def run_personal_smoke(pet_factory):
    report = {'version': '0.5.4', 'external_api_called': False}
    with TemporaryDirectory(prefix='bai-personal-smoke-') as directory:
        base = Path(directory)
        pet = pet_factory()
        now = [1800000000.0]
        from .zotero import ZoteroLibrary
        controller = PersonalController(pet, base, clock=lambda: now[0],
                                        zotero_library=ZoteroLibrary(base, home=base/'home', roaming=base/'roaming'))
        controller.timer.stop()
        try:
            for name, index in [('study', 0), ('research', 1), ('daily', 2)]:
                pet.chat.workspace_requested.emit(name)
                QApplication.processEvents()
                assert controller.window.tabs.currentIndex() == index
            report['three_entries_ok'] = True
            window = controller.window
            assert window.phase_label.isHidden()
            assert window.focus_minutes.suffix() == '' and window.break_minutes.suffix() == ''
            assert window.focus_minutes.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
            assert window.due_time.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
            preset = next(button for value, button in window.duration_presets[window.focus_minutes] if value == 45)
            preset.click()
            assert controller.focus.snapshot()['focus_minutes'] == 45 and preset.isChecked()
            window.focus_minutes.setValue(25)
            report['numeric_duration_presets_ok'] = True
            assets = Path(__file__).resolve().parents[1] / 'assets/ui'
            assert (assets / 'chevron-down.svg').is_file() and (assets / 'calendar.svg').is_file()
            report['styled_control_assets_ok'] = True
            report['deadline_without_spin_buttons_ok'] = True
            window.task_title.setText('测试：阅读法学论文第二节')
            window.category.setCurrentText('日常')
            window.save_button.click()
            task = controller.store.tasks()[0]
            assert PersonalStore(base).task(task['id'])['category'] == '日常'
            window.show_page('study')
            window.start_button.click()
            assert controller.focus.snapshot()['task_id'] is None
            assert not hasattr(window, 'task_combo') and not hasattr(window, 'task_focus_button')
            report['task_persistence_and_unlinked_focus_ok'] = True
            now[0] += 30
            window.pause_button.click()
            remaining = controller.focus.snapshot()['remaining_s']
            now[0] += 500
            restarted = FocusTimer(PersonalStore(base), lambda: now[0])
            assert restarted.snapshot()['remaining_s'] == remaining
            window.pause_button.click()
            now[0] += remaining
            controller.tick()
            assert restarted.snapshot()['status'] == 'finished'
            assert len(controller.store.sessions()) == 1
            assert controller.store.sessions()[0]['status'] == 'completed'
            report['pause_restart_and_completion_ok'] = True
            count = pet.chat.history.toPlainText().count('分钟专注啦')
            controller.tick()
            assert count == 1 and pet.chat.history.toPlainText().count('分钟专注啦') == 1
            report['completion_reminder_once_ok'] = True
            controller.store.complete_task(task['id'], True)
            assert not controller.store.tasks()
            controller.store.complete_task(task['id'], False)
            controller.store.save_task(task['title'], due_at=now[0], task_id=task['id'])
            now[0] += 11
            controller.tick()
            assert pet.chat.history.toPlainText().count('这些事情到时间') == 1
            assert not controller.store.due_reminders(now[0])
            report['task_reminder_once_ok'] = True
            window.close()
            controller.show_page('study')
            assert controller.window is window and window.isVisible()
            report['close_reopen_ok'] = True
            window.show_page('daily')
            window.task_title.setText('测试：设置论文截止时间')
            window.has_due.click()
            deadline = QDateTime.fromSecsSinceEpoch(int(now[0]) + 7200)
            window.due_date.setDate(deadline.date())
            window.due_time.setTime(deadline.time())
            expected = QDateTime(window.due_date.date(), window.due_time.time()).toSecsSinceEpoch()
            window.save_button.click()
            saved = next(task for task in controller.store.tasks() if task['title'] == '测试：设置论文截止时间')
            assert PersonalStore(base).task(saved['id'])['due_at'] == expected
            report['deadline_picker_persistence_ok'] = True
            assert window.priority.count() == 4
            report['four_priorities_ok'] = True
            window.task_title.setText('测试：三次提醒与宠物动作')
            window.has_due.setChecked(True)
            window.due_date.setDate(deadline.date())
            window.due_time.setTime(deadline.time())
            window.reminder_button.click()
            picker = window.reminder_dialog
            picker.checks['before_1m'].click()
            picker.checks['before_10m'].click()
            assert len(picker.selected()) == 3 and not picker.checks['before_15m'].isEnabled()
            picker.accept()
            window.priority.setCurrentIndex(window.priority.findData(3))
            window.save_button.click()
            reminder_task = next(task for task in controller.store.tasks() if task['title'] == '测试：三次提醒与宠物动作')
            assert len(PersonalStore(base).task(reminder_task['id'])['reminders']) == 3
            assert reminder_task['priority'] == 3
            report['three_reminders_persist_ok'] = True
            controller.focus.configure(180, 5)
            controller.focus.start()
            now[0] = reminder_task['due_at'] - 600
            controller.tick()
            assert controller.focusing and pet.state == 'jump'
            assert '三次提醒' in pet.bubble
            report['reminder_pet_action_ok'] = True
            assert not list(base.rglob('api-key*'))
            report['no_api_key_required'] = True
            report['ok'] = True
            return report
        finally:
            controller.close()
            pet.timer.stop()
            pet.chat.close()
            pet.close()
