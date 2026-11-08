from PySide6.QtWidgets import QApplication

from app import ROOT, PetWindow, SpriteBank
from westie_agent.sync_removal_smoke import run_sync_removal_smoke


def test_legacy_enabled_sync_does_not_listen_or_remove_imported_tasks():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = run_sync_removal_smoke(lambda: PetWindow(SpriteBank(ROOT / 'assets/chick-sheet.png'), use_tray=False))
    assert report['iphone_sync_removed'] and report['no_receiver_started']
    assert report['imported_tasks_preserved'] and report['removed_modules_absent']
