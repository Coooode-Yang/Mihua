import pytest
from PySide6.QtWidgets import QApplication,QLabel,QPushButton

from app import ROOT,PetWindow,SpriteBank
from westie_agent.controller import AgentController
from westie_agent.personal import PersonalController
from westie_agent.growth import GrowthStore


@pytest.fixture
def spelling(tmp_path):
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=PersonalController(pet,tmp_path)
    controller.timer.stop()
    yield app,pet,controller
    controller.close();pet.timer.stop();pet.chat.close();pet.close()


def test_workbench_menu_and_chat_share_one_spelling_window(spelling):
    app,pet,controller=spelling
    controller.show_page('study')
    window=controller.window
    assert not hasattr(window,'vocabulary_button')
    assert not hasattr(window,'vocabulary_collection_button')
    assert not any(w.text()=='单词小纸条' for w in window.findChildren(QLabel))
    assert not any(w.text() in {'陪我记个词','再见见'} for w in window.findChildren(QPushButton))
    window.spelling_button.click()
    dialog=controller.growth.dialog
    assert dialog.isVisible() and not hasattr(dialog, 'tabs')
    dialog.next_card()
    original_word=dialog.card['word']
    for command in ('背单词','陪我记个词','抽个词','雅思抽背','单词拼写','拼写练习','回忆检查'):
        assert controller.handle_command(command)
        assert controller.growth.dialog is dialog and dialog.card['word']==original_word
    menu=pet.make_menu()
    assert all(a.text()!='陪我记个词' for a in menu.actions())
    next(a for a in menu.actions() if a.text()=='背单词 · 拼写练习').trigger()
    assert controller.growth.dialog is dialog
    assert not hasattr(controller,'vocabulary_window')
    assert not hasattr(controller,'vocabulary_store')


def test_review_aliases_use_spelling_review_mode(spelling):
    _,_,controller=spelling
    for command in ('单词复习','再见见'):
        assert controller.handle_command(command)
        assert controller.growth.dialog.word_mode.currentIndex()==1
    controller.handle_command('背单词')
    assert controller.growth.dialog.word_mode.currentIndex()==0


def test_existing_spelling_progress_wallet_and_wardrobe_survive(tmp_path):
    store=GrowthStore(tmp_path)
    store.welcome()
    for row in store.db.execute('SELECT word FROM words LIMIT 70').fetchall():store.answer(row['word'],row['word'])
    store.purchase('scarf');store.equip('scarf')
    before=store.profile()
    store.close()
    legacy=tmp_path/'data/vocabulary.json'
    legacy.write_bytes(b'preserved historical paper-card feedback')
    app=QApplication.instance() or QApplication([])
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=PersonalController(pet,tmp_path)
    controller.timer.stop()
    try:
        controller.show_spelling()
        assert controller.growth.store.profile()==before
        assert controller.growth.store.summary()['learned']==70
        assert controller.growth.store.owned()=={'scarf'}
        assert controller.growth.store.equipped()=={'neck':'scarf'}
        assert legacy.read_bytes()==b'preserved historical paper-card feedback'
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close()


def test_spelling_available_offline_while_model_busy(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    controller.personal.timer.stop()
    def forbidden(*args,**kwargs):pytest.fail('spelling must not use model or credentials')
    monkeypatch.setattr(controller,'work',forbidden)
    monkeypatch.setattr(controller.credentials,'load',forbidden)
    controller.busy=True
    try:
        controller.submit('背单词')
        dialog=controller.personal.growth.dialog
        dialog.next_card();word=dialog.card['word']
        dialog.recall_button.click();dialog.answer_input.setText(word);dialog.answer_button.click()
        assert controller.personal.growth.store.profile()['coins']==0
        assert not controller.personal.store.tasks() and not controller.personal.store.sessions()
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close()
