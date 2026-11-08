"""Single-purpose word spelling window; legacy module name retained for compatibility."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDialog, QFrame, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from .growth import GrowthStore
from .vocabulary_banks import BANKS, BANK_BY_ID
from .word_speech import WordSpeech


STYLE = '''
QDialog, QWidget { background: #fffaf3; color: #443c36; font-family: 'Microsoft YaHei'; font-size: 13px; }
QLabel#title { font-size: 25px; font-weight: bold; color: #4b6b58; }
QLabel#word { font-size: 30px; font-weight: bold; }
QLabel#timer { font-size: 46px; font-weight: bold; color: #4b6b58; }
QLabel#note { color: #82756a; }
QPushButton { background: #e9e0d2; border: none; border-radius: 8px; padding: 9px 13px; min-height: 18px; }
QPushButton:hover { background: #deceb8; }
QPushButton:pressed { background: #cbb99e; }
QPushButton:focus { border: 1px solid #779b83; }
QPushButton#primary { background: #d9e8dc; color: #365b43; }
QPushButton#primary:hover { background: #bdd7c4; color: #244a32; }
QPushButton#primary:pressed { background: #a5c6ae; }
QPushButton:disabled, QPushButton#primary:disabled { color: #aaa296; background: #f0ece5; }
QFrame#wordCard { background: #ffffff; border: 1px solid #e4d7c6; border-radius: 12px; }
QFrame#wordCard QLabel { background: transparent; border: none; }
QLineEdit, QSpinBox, QComboBox, QListWidget, QTextBrowser { background: white; border: 1px solid #e4d7c6; border-radius: 7px; padding: 7px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #eee6da; padding: 10px 15px; margin: 2px; border-radius: 6px; }
QTabBar::tab:selected { background: #d9e8dc; color: #365b43; }
QTabBar::tab:hover { background: #d5e1d4; color: #365b43; }
QTabBar::tab:selected:hover { background: #bdd7c4; }
QProgressBar { border: none; background: #eae2d6; border-radius: 5px; text-align: center; min-height: 13px; }
QProgressBar::chunk { background: #91b49b; border-radius: 5px; }
'''


def label(text='', name=None):
    result = QLabel(text)
    result.setWordWrap(True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        result.setObjectName(name)
    return result


def button(text, callback, primary=False):
    result = QPushButton(text)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        result.setObjectName('primary')
    result.clicked.connect(callback)
    return result


class GrowthDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.personal.window or controller.pet.chat)
        self.c = controller
        self.store = controller.store
        self.setWindowTitle('米花 · 单词拼写')
        self.setWindowIcon(controller.pet.windowIcon())
        self.setStyleSheet(STYLE)
        self.resize(640, 530)
        self.setMinimumSize(600, 510)
        self.card = None
        self.card_phase = 'idle'
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 12)
        root.addWidget(label('每天认识几个新词', 'title'))
        self.words = QVBoxLayout()
        root.addLayout(self.words, 1)
        self.build_words()
        self.notice = label('', 'note')
        root.addWidget(self.notice)
        self.refresh()
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)

    def run(self, callback, message=''):
        try:
            result = callback()
            self.notice.setText(message)
            self.refresh()
            return result
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.notice.setText(str(exc))
            return None

    def refresh(self):
        self.refresh_word_stats()

    def build_words(self):
        self.words.setSpacing(10)
        self.speech = WordSpeech(self)
        bank_row = QHBoxLayout()
        bank_row.addWidget(label('学习词库'))
        self.word_bank = QComboBox()
        for bank in BANKS:
            self.word_bank.addItem(f'{bank.title} · {len(bank.words)} 词', bank.key)
        bank_row.addWidget(self.word_bank, 1)
        self.words.addLayout(bank_row)
        self.bank_note = label('', 'note')
        self.words.addWidget(self.bank_note)
        row = QHBoxLayout()
        self.word_mode = QComboBox()
        self.word_mode.addItems(['认识新词','复习到期单词'])
        self.word_mode.currentIndexChanged.connect(self.reset_card)
        self.word_mode.setMaximumWidth(170)
        row.addWidget(self.word_mode)
        row.addStretch()
        self.next_word_button = button('开始 / 下一词', self.next_card, True)
        row.addWidget(self.next_word_button)
        self.words.addLayout(row)
        self.word_stats = label()
        self.words.addWidget(self.word_stats)
        card = QFrame()
        card.setObjectName('wordCard')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18,14,18,14)
        card_layout.setSpacing(8)
        self.word_title = label('准备好认识一个新朋友了吗？', 'word')
        card_layout.addWidget(self.word_title)
        self.word_meaning = label('先认识单词，再遮住答案回忆。')
        card_layout.addWidget(self.word_meaning)
        self.word_example = label('', 'note')
        card_layout.addWidget(self.word_example)
        audio = QHBoxLayout()
        self.speak_word_button = button('单词发音', lambda: self.speak_card(False))
        self.speak_example_button = button('朗读例句', lambda: self.speak_card(True))
        for control in (self.speak_word_button, self.speak_example_button):
            control.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.stop_speech_button = button('停止', self.speech.stop)
        self.stop_speech_button.setEnabled(False)
        audio.addWidget(self.speak_word_button)
        audio.addWidget(self.speak_example_button)
        audio.addWidget(self.stop_speech_button)
        audio.addStretch()
        card_layout.addLayout(audio)
        self.speech_note = label('系统语音朗读 · 点击播放', 'note')
        card_layout.addWidget(self.speech_note)
        self.speech.status_changed.connect(self.speech_note.setText)
        self.speech.speaking_changed.connect(self.stop_speech_button.setEnabled)
        self.words.addWidget(card)
        self.recall_button = button('记住了，遮住答案',self.begin_recall,True)
        self.answer_input = QLineEdit()
        self.answer_input.setPlaceholderText('根据中文释义，输入英文单词')
        self.answer_input.returnPressed.connect(self.submit_answer)
        self.words.addWidget(self.answer_input)
        self.answer_button = button('检查我的回忆',self.submit_answer,True)
        actions = QHBoxLayout()
        actions.addWidget(self.recall_button)
        actions.addWidget(self.answer_button)
        actions.addStretch()
        self.words.addLayout(actions)
        self.word_feedback = label()
        self.words.addWidget(self.word_feedback)
        self.words.addStretch()
        self.word_bank.currentIndexChanged.connect(self.change_bank)
        self.bank_note.setText(BANK_BY_ID[self.word_bank.currentData()].description + ' 精选练习词库。')
        self.reset_card()

    def tab_changed(self, index):
        self.speech.stop()
        self.refresh()

    def hideEvent(self, event):
        self.speech.stop()
        super().hideEvent(event)

    def change_bank(self, *_):
        self.reset_card()
        self.bank_note.setText(BANK_BY_ID[self.word_bank.currentData()].description + ' 精选练习词库。')
        self.refresh_word_stats()

    def refresh_word_stats(self):
        stats = self.store.word_stats(self.word_bank.currentData())
        self.word_stats.setText(f'当前词库：已学 {stats["learned"]}/{stats["total"]} 词 · 待复习 {stats["due"]} 词 · 掌握 {stats["mastered"]} 词')

    def update_speech_buttons(self):
        visible = bool(self.card) and self.card_phase in ('learn', 'done', 'retry')
        self.speak_word_button.setEnabled(visible)
        self.speak_example_button.setEnabled(visible and bool(self.card['example']))

    def speak_card(self, example=False):
        if self.card and self.card_phase in ('learn', 'done', 'retry'):
            self.speech.say(self.card['example'] if example else self.card['word'])

    def reset_card(self, *_):
        self.speech.stop()
        self.card = None
        self.card_phase = 'idle'
        self.word_title.setText('每天认识几个新词')
        self.word_meaning.setText('选好词库，点击“开始 / 下一词”。')
        self.word_example.setText('')
        self.word_feedback.setText('')
        self.answer_input.clear()
        self.answer_input.setEnabled(False)
        self.answer_button.setEnabled(False)
        self.recall_button.setEnabled(False)
        self.recall_button.setText('记住了，遮住答案')
        self.speech_note.setText('系统语音朗读 · 点击播放')
        self.update_speech_buttons()

    def next_card(self):
        self.reset_card()
        self.card = self.store.next_word(self.word_mode.currentIndex()==1, bank=self.word_bank.currentData())
        if not self.card:
            self.word_title.setText('这一轮完成啦')
            self.word_meaning.setText('当前词库暂无到期单词，可切换词库或认识新词。' if self.word_mode.currentIndex() else '当前词库的新词学完了，换一个词库继续吧。')
            return
        if self.word_mode.currentIndex()==1:
            self.begin_recall()
        else:
            self.card_phase = 'learn'
            self.word_title.setText(self.card['word'])
            self.word_meaning.setText(self.card['meaning'])
            self.word_example.setText(self.card['example'])
            self.recall_button.setEnabled(True)
            self.update_speech_buttons()

    def begin_recall(self):
        if not self.card:
            return
        self.speech.stop()
        self.card_phase = 'recall'
        self.update_speech_buttons()
        self.word_title.setText('它的英文是什么？')
        self.word_meaning.setText(self.card['meaning'])
        self.word_example.setText('')
        self.word_feedback.setText('')
        self.answer_input.clear()
        self.answer_input.setEnabled(True)
        self.answer_button.setEnabled(True)
        self.recall_button.setEnabled(False)
        self.recall_button.setText('记住了，遮住答案')
        self.answer_input.setFocus()

    def submit_answer(self):
        if not self.card or self.card_phase != 'recall':
            return
        if not self.answer_input.text().strip():
            self.word_feedback.setText('先试着写下你的答案。')
            return
        result = self.run(lambda: self.store.answer(self.card['word'],self.answer_input.text()))
        if result is None:
            return
        correct, coins = result
        self.answer_button.setEnabled(False)
        self.answer_input.setEnabled(False)
        if correct:
            self.card_phase = 'done'
            self.word_title.setText(self.card['word'])
            self.word_example.setText(self.card['example'])
            self.word_feedback.setText('答对啦！已记录学习进度并安排下次复习。')
        else:
            self.card_phase = 'retry'
            self.word_title.setText(self.card['word'])
            self.word_example.setText(self.card['example'])
            self.word_feedback.setText('再认识一次就好。准备好后遮住答案重试。')
            self.recall_button.setText('再试一次 · 遮住答案')
            self.recall_button.setEnabled(True)
        self.update_speech_buttons()



class GrowthController:
    """Spelling only. Old database and class name preserve existing word progress."""
    def __init__(self, personal, base):
        self.personal = personal
        self.pet = personal.pet
        self.store = GrowthStore(Path(base), rewards_enabled=False)
        self.dialog = None
        self.closed = False
        self.pet.chat.growth_requested.connect(self.show)
        self.sync()

    def show(self, page='words'):
        if page != 'words':
            return
        if self.dialog is None:
            self.dialog = GrowthDialog(self)
        self.dialog.refresh()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def practice(self, term):
        self.show('words')
        row = self.store.db.execute('SELECT * FROM words WHERE word=?', (term,)).fetchone()
        if row:
            self.dialog.reset_card()
            self.dialog.card = dict(row)
            self.dialog.begin_recall()

    def sync(self):
        self.pet.equipped = {}
        self.pet.reading_unlocked = False
        self.pet.focus_active = self.personal.focusing
        self.pet.render_frame()
        self.pet.update()
        if self.dialog:
            self.dialog.refresh()

    def tick(self):
        self.pet.focus_active = self.personal.focusing

    def close(self):
        if self.closed:
            return
        if self.dialog:
            self.dialog.close()
        self.store.close()
        self.closed = True
