from PySide6.QtWidgets import QApplication

from app import ROOT, PetWindow, SpriteBank
from westie_agent.focus_style_smoke import run_focus_style_smoke


def test_focus_frames_are_suppressed_without_losing_keyboard_activation():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = run_focus_style_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
    assert report['checked_controls'] >= 100

