from PySide6.QtWidgets import QApplication

from app import ROOT, PetWindow, SpriteBank
from westie_agent.sedentary_smoke import run_sedentary_smoke


def test_reminder_settings_spelling_and_fullscreen_end_to_end():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    result = run_sedentary_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
    assert result['ok']
