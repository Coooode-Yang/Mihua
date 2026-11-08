import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import ROOT,PetWindow,SpriteBank
from westie_agent.personal import PersonalController


@pytest.fixture
def companion(tmp_path):
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    personal=PersonalController(pet,tmp_path)
    personal.timer.stop()
    yield app,pet,personal
    personal.close()
    pet.timer.stop()
    pet.chat.close()
    pet.close()


def test_spelling_keeps_progress_without_coins_and_return_key(companion):
    app, pet, personal = companion
    personal.show_page('study')
    personal.window.spelling_button.click()
    dialog = personal.growth.dialog
    assert dialog.isVisible() and not hasattr(dialog, 'tabs')
    dialog.next_card()
    word = dialog.card['word']
    dialog.recall_button.click()
    assert word not in dialog.word_title.text() and dialog.word_example.text() == ''
    dialog.answer_input.setText(word)
    QTest.keyClick(dialog.answer_input, Qt.Key.Key_Return)
    assert personal.growth.store.profile()['coins'] == 0
    assert personal.growth.store.summary()['learned'] == 1
    assert dialog.card_phase == 'done' and not dialog.answer_button.isEnabled()
    assert '币' not in dialog.word_feedback.text()
    dialog.submit_answer()
    assert personal.growth.store.summary()['learned'] == 1


def test_growth_wardrobe_journal_and_summary_removed(companion):
    from PySide6.QtWidgets import QLabel, QPushButton
    app, pet, personal = companion
    personal.show_page('study')
    forbidden = {'米花成长', '米花衣柜', '成长记录'}
    assert not any(w.text() in forbidden for w in personal.window.findChildren(QPushButton))
    assert not hasattr(personal.window, 'growth_summary')
    assert not any('狗狗币' in w.text() for w in personal.window.findChildren(QLabel))
    assert not any(a.text() in forbidden for a in pet.make_menu().actions())
    for command in forbidden:
        assert not personal.handle_command(command)
    pet.chat.growth_requested.emit('shop')
    assert personal.growth.dialog is None
    personal.show_spelling()
    assert not hasattr(personal.growth.dialog, 'shop')
    assert not personal.focus.track_rewards and personal.focus.rewards is None


def test_bank_switching_and_speech_respect_hidden_answers(companion, monkeypatch):
    app, pet, personal = companion
    personal.show_spelling()
    dialog = personal.growth.dialog
    spoken, stopped = [], []
    monkeypatch.setattr(dialog.speech, 'say', spoken.append)
    monkeypatch.setattr(dialog.speech, 'stop', lambda: stopped.append(True))
    assert not hasattr(dialog, 'custom_word')
    assert dialog.word_bank.count() == 6
    dialog.word_bank.setCurrentIndex(dialog.word_bank.findData('academic'))
    dialog.next_card()
    card = dialog.card.copy()
    dialog.speak_word_button.click()
    dialog.speak_example_button.click()
    assert spoken == [card['word'], card['example']]
    dialog.begin_recall()
    assert stopped and not dialog.speak_word_button.isEnabled()
    assert not dialog.speak_example_button.isEnabled()
    dialog.speak_card()
    assert len(spoken) == 2
    dialog.answer_input.setText(card['word'])
    dialog.submit_answer()
    assert dialog.speak_word_button.isEnabled()
    dialog.word_bank.setCurrentIndex(dialog.word_bank.findData('ielts_core'))
    assert dialog.card is None and not dialog.speak_word_button.isEnabled()
    assert '已学 0/30' in dialog.word_stats.text()
    dialog.word_bank.setCurrentIndex(dialog.word_bank.findData('academic'))
    assert '已学 1/30' in dialog.word_stats.text()
    dialog.next_card()
    count = len(stopped)
    dialog.hide()
    assert len(stopped) > count


def test_compact_layout_keeps_audio_buttons_and_controls_readable(companion):
    app, pet, personal = companion
    personal.show_spelling()
    dialog = personal.growth.dialog
    dialog.next_card()
    dialog.resize(600, 600)
    app.processEvents()
    for control in (dialog.speak_word_button, dialog.speak_example_button,
                    dialog.recall_button, dialog.answer_button, dialog.next_word_button):
        assert control.height() >= 32
    assert dialog.width() == 600 and dialog.height() == 600
