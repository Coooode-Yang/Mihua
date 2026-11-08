from PySide6.QtWidgets import QApplication
from app import ROOT, SpriteBank, PetWindow
from westie_agent.chick_smoke import run_chick_smoke


def test_chick_assets_and_interaction_contract():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    report = run_chick_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False), ROOT)
    assert report['ok'] and not report['external_api_called']


def test_animation_entrance_runs_once_then_loops_settled_pose():
    index = SpriteBank.animation_index
    assert [index(t,.2) for t in (0,.15,.29,.43)] == [0,1,2,3]
    assert index(.56,.2)==4
    assert index(.56+7*.2+.01,.2)==11
    assert index(.56+8*.2+.01,.2)==4


def test_sleep_and_thinking_keep_a_shared_anchor_and_animate():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    sprites = SpriteBank(ROOT/'assets/chick-sheet.png')
    for frames in (sprites.sleep_frames,sprites.think_frames,sprites.search_frames):
        assert len(frames)==12 and all((f.width(),f.height())==(384,384) for f in frames)
        assert frames[4].toImage()!=frames[6].toImage()
    from app import PetWindow
    pet=PetWindow(sprites,use_tray=False)
    pet.timer.stop()
    try:
        pet.set_state('curious')
        pet.render_frame(pet.state_started+.57)
        first=pet._frame.toImage()
        pet.render_frame(pet.state_started+1.1)
        assert first!=pet._frame.toImage()
        assert pet.pet_size==140
        assert [a.text() for a in pet.make_menu()._sizes.actions()]==['小 · 140','中 · 190','大 · 240']
    finally:
        pet.timer.stop(); pet.chat.close(); pet.close()
