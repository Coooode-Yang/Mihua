"""Verify the single spelling vocabulary flow in isolated local data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from .personal import PersonalController
from .growth import GrowthStore
from .vocabulary_words import WORDS


def run_vocabulary_smoke(pet_factory, preview_dir=None):
    report={'external_api_called':False,'word_count':len(WORDS),'vocabulary_mode':'spelling'}
    preview_dir=Path(preview_dir) if preview_dir else None
    if preview_dir:preview_dir.mkdir(parents=True,exist_ok=True)
    def capture(name,widget):
        if preview_dir:
            QApplication.processEvents()
            assert widget.grab().save(str(preview_dir/(name+'.png')))
    with TemporaryDirectory(prefix='bai-spelling-smoke-') as folder:
        base=Path(folder)
        pet=pet_factory()
        personal=PersonalController(pet,base)
        personal.timer.stop()
        try:
            personal.show_page('study')
            window=personal.window
            assert not hasattr(window,'vocabulary_button')
            assert not hasattr(window,'vocabulary_collection_button')
            assert not any(w.text()=='单词小纸条' for w in window.findChildren(QLabel))
            assert not any(w.text() in {'陪我记个词','再见见'} for w in window.findChildren(QPushButton))
            window.spelling_button.click()
            dialog=personal.growth.dialog
            assert dialog and not hasattr(dialog, 'tabs')
            assert not hasattr(personal,'vocabulary_window')
            capture('study',window)
            dialog.next_card()
            word=dialog.card['word']
            dialog.recall_button.click()
            assert dialog.word_title.text()!=word and not dialog.word_example.text()
            dialog.answer_input.setText(word)
            dialog.answer_button.click()
            assert personal.growth.store.summary()['learned']==1
            assert personal.growth.store.profile()['coins']==0
            dialog.submit_answer()
            assert personal.growth.store.profile()['coins']==0
            capture('spelling',dialog)
            for command in ('背单词','陪我记个词','单词拼写','回忆检查'):
                assert personal.handle_command(command)
                assert personal.growth.dialog is dialog and not hasattr(dialog, 'tabs')
            menu=pet.make_menu()
            next(a for a in menu.actions() if a.text()=='背单词 · 拼写练习').trigger()
            assert personal.growth.dialog is dialog
            report['single_spelling_entry_ok']=True
            personal.handle_command('单词复习')
            assert dialog.word_mode.currentIndex()==1
            report['review_mode_ok']=True
            restored=GrowthStore(base)
            try:
                assert restored.summary()['learned']==1 and restored.profile()['coins']==0
            finally:restored.close()
            assert not (base/'data/vocabulary.json').exists()
            report['spelling_progress_without_rewards_ok']=True
            report['legacy_paper_cards_removed_ok']=True
            capture('review',dialog)
            report['ok']=True
            return report
        finally:
            personal.close();pet.timer.stop();pet.chat.close();pet.close()
            QApplication.processEvents()
