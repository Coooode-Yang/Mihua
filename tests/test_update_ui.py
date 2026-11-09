import threading
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import ROOT, PetWindow, SpriteBank
from westie_agent import update_ui
from westie_agent.update_ui import UpdateController
from westie_agent.updates import UpdateError, Release


@pytest.fixture
def desktop(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False)
    agent = SimpleNamespace(busy=False, reset_calls=0)
    def reset():
        agent.busy = False
        agent.reset_calls += 1
        pet.chat.set_busy(False)
    agent.reset_busy = reset
    updater = UpdateController(pet, tmp_path, agent, start_automatic=False)
    yield app, pet, agent, updater
    updater.close()
    pet.timer.stop()
    pet.chat.close()
    pet.close()


def wait_done(updater):
    for _ in range(150):
        QTest.qWait(10)
        if not updater.running:
            return
    pytest.fail('Update worker did not finish')


def test_menu_opens_update_dialog_without_model_or_network(desktop, monkeypatch):
    app, pet, agent, updater = desktop
    monkeypatch.setattr(update_ui.GitHubUpdater, 'check', lambda _: pytest.fail('No configured repository'))
    action = next(action for action in pet.make_menu().actions() if action.text() == '软件更新')
    action.trigger()
    app.processEvents()
    assert updater.dialog.isVisible()
    assert updater.dialog.notes.isHidden()
    assert '尚未开通' in updater.dialog.status.text()
    assert not updater.dialog.check.isEnabled()
    updater.automatic_check()
    assert not updater.running and not agent.busy


def test_check_worker_keeps_ui_responsive(desktop, monkeypatch):
    _, pet, agent, updater = desktop
    entered, release = threading.Event(), threading.Event()
    def check(_):
        entered.set()
        assert release.wait(3)
        return None
    monkeypatch.setattr(update_ui.GitHubUpdater, 'check', check)
    try:
        updater.check()
        assert entered.wait(1) and updater.running
        pet.perform('pet')
        QTest.qWait(30)
        assert pet.pet_count == 1 and not agent.busy
        release.set()
        wait_done(updater)
        assert '最新版本' in updater.dialog.status.text()
    finally:
        release.set()


def test_update_preference_persists(desktop):
    _, _, _, updater = desktop
    updater.show()
    updater.set_automatic(False)
    import json
    assert json.loads(updater.preferences_path.read_text())['automatic'] is False


def test_failed_handoff_resets_agent_busy(desktop, monkeypatch):
    _, _, agent, updater = desktop
    updater.show()
    updater.release = Release('0.5.1', '', '', 1, '', {})
    updater.work = updater.base.parent / '.MihuaAgent-update-test'
    monkeypatch.setattr(update_ui.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    def fail(*a):
        raise UpdateError('helper launch failed')
    monkeypatch.setattr(update_ui, 'launch_helper', fail)
    updater.install()
    assert agent.busy
    wait_done(updater)
    assert not agent.busy and agent.reset_calls == 1
    assert 'helper launch failed' in updater.dialog.status.text()


def test_install_waits_for_active_diagnosis(desktop, monkeypatch):
    _, _, agent, updater = desktop
    updater.show()
    updater.release = Release('0.5.1', '', '', 1, '', {})
    updater.work = updater.base.parent / '.MihuaAgent-update-test'
    monkeypatch.setattr(update_ui.sys, 'frozen', True, raising=False)
    agent.busy = True
    updater.install()
    assert not updater.running and '等待' in updater.dialog.status.text()


def test_opening_dialog_during_background_check_restores_controls(desktop):
    _, _, _, updater = desktop
    updater.config = SimpleNamespace(enabled=True)
    updater.background = updater.running = True
    updater.show()
    assert not updater.dialog.check.isEnabled()
    updater.finished('check', None)
    assert updater.dialog.check.isEnabled()
    assert '最新版本' in updater.dialog.status.text()


def test_corrupt_update_metadata_does_not_stop_application(desktop, monkeypatch):
    _, pet, agent, updater = desktop
    def broken(_):
        raise UpdateError('无法确定当前版本')
    monkeypatch.setattr(update_ui, 'installed_version', broken)
    other = UpdateController(pet, updater.base, agent)
    try:
        other.show()
        assert not other.config.enabled
        assert '无法确定' in other.dialog.status.text()
        assert not agent.busy
    finally:
        other.close()


def test_background_failure_records_only_safe_network_category(desktop, monkeypatch):
    import json
    _, _, _, updater = desktop
    def fail(_):
        raise update_ui.UpdateConnectionError('timeout')
    monkeypatch.setattr(update_ui.GitHubUpdater, 'check', fail)
    updater.check(background=True)
    wait_done(updater)
    assert updater.dialog is None
    record = json.loads((updater.base/'data/logs/update-connection.json').read_text(encoding='utf-8'))
    assert set(record) == {'time','stage','reason','http_status'}
    assert record['reason'] == 'timeout' and record['stage'] == 'check'
