import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import ROOT, PetWindow, SpriteBank
from westie_agent import update_ui
from westie_agent.update_ui import UpdateController
from westie_agent.updates import Release


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False)
    agent = SimpleNamespace(busy=False)
    updater = UpdateController(pet, tmp_path, agent, start_automatic=False)
    yield updater, agent
    updater.close()
    pet.timer.stop()
    pet.chat.thinking_timer.stop()
    pet.chat.close()
    pet.close()


def test_each_startup_checks_even_after_recent_previous_session(controller, monkeypatch):
    updater, _ = controller
    updater.config = SimpleNamespace(enabled=True)
    updater.preferences['last_check'] = time.time()
    calls = []
    monkeypatch.setattr(updater, 'check', lambda **kwargs: calls.append(kwargs))
    updater.automatic_check()
    assert calls == []
    updater.automatic_check(startup=True)
    assert calls == [{'background': True}]
    updater.preferences['automatic'] = False
    updater.automatic_check(startup=True)
    assert len(calls) == 1


def test_background_new_version_downloads_without_interrupting_desktop(controller, monkeypatch):
    updater, _ = controller
    updater.config = SimpleNamespace(enabled=True)
    updater.background = True
    monkeypatch.setattr(update_ui.sys, 'frozen', True, raising=False)
    calls = []
    monkeypatch.setattr(updater, 'task', lambda kind, function: calls.append(kind))
    updater.finished('check', Release('0.5.29', '', '', 1, '新版说明', {}))
    assert calls == ['download']
    assert updater.dialog.notes.toPlainText() == '新版说明'
    assert not updater.dialog.isVisible()


def test_ready_notification_waits_for_focus_and_only_prompts_once(controller, monkeypatch):
    updater, agent = controller
    updater.show()
    updater.background = True
    updater.finished('download', updater.base / 'mock-staging')
    agent.personal = SimpleNamespace(focusing=True)
    calls = []
    monkeypatch.setattr(updater, 'show', lambda: calls.append('show'))
    monkeypatch.setattr(updater, 'install', lambda: calls.append('install'))
    updater.notify_ready()
    assert calls == [] and updater.pending_notification
    agent.personal.focusing = False
    updater.notify_ready()
    updater.notify_ready()
    assert calls == ['show', 'install']
    assert not updater.notify_timer.isActive()


def test_auto_download_can_be_disabled_and_persists(controller):
    updater, _ = controller
    updater.set_auto_download(False)
    assert json.loads(updater.preferences_path.read_text())['auto_download'] is False


def test_source_mode_never_downloads_or_replaces_code(controller, monkeypatch):
    updater, _ = controller
    updater.background = True
    monkeypatch.setattr(update_ui.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(updater, 'task', lambda *args: pytest.fail('Source checkout must not be replaced'))
    updater.finished('check', Release('9.0.0', '', '', 1, 'notes', {}))
    updater.install()
    assert '源码运行模式' in updater.dialog.status.text()


def test_background_network_error_stays_quiet_and_manual_retry_available(controller):
    updater, agent = controller
    updater.background = updater.running = True
    updater.failed('无法连接 GitHub')
    assert updater.dialog is None and not updater.running and not agent.busy


def test_install_waits_for_reader_worker(controller, monkeypatch):
    updater, agent = controller
    updater.show()
    updater.release = Release('0.5.29', '', '', 1, '', {})
    updater.work = updater.base / 'staged'
    agent.reader = SimpleNamespace(worker=SimpleNamespace(isRunning=lambda: True))
    monkeypatch.setattr(update_ui.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('Do not interrupt active reading'))
    updater.install()
    assert not updater.running and '解读' in updater.dialog.status.text()
