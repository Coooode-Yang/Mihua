"""On-demand local speech. Never sends study text to a remote service."""
from PySide6.QtCore import QObject, QLocale, Signal
from PySide6.QtTextToSpeech import QTextToSpeech


class WordSpeech(QObject):
    status_changed = Signal(str)
    speaking_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = None
        self.english_voice = False

    def _initialize(self):
        if self.engine is not None:
            return
        engines = [e for e in QTextToSpeech.availableEngines() if e != 'mock']
        for name in engines:
            candidate = QTextToSpeech(name, self)
            locales = [locale for locale in candidate.availableLocales()
                       if locale.language() == QLocale.Language.English]
            if locales:
                candidate.setLocale(next((l for l in locales if l.name() == 'en_GB'), locales[0]))
                voices = candidate.availableVoices()
                english = [v for v in voices if v.locale().language() == QLocale.Language.English]
                if english:
                    candidate.setVoice(english[0])
                    if self.engine is not None:
                        self.engine.deleteLater()
                    self.engine = candidate
                    self.english_voice = True
                    break
            if self.engine is None and candidate.state() != QTextToSpeech.State.Error:
                self.engine = candidate
            else:
                candidate.deleteLater()
        if self.engine is None:
            raise RuntimeError('系统语音暂不可用，请在 Windows 设置中添加英语语音包。')
        self.engine.setRate(-0.15)
        self.engine.stateChanged.connect(self._state_changed)
        self.engine.errorOccurred.connect(lambda *_: self.status_changed.emit('播放失败，请检查声音输出或系统语音设置。'))

    def _state_changed(self, state):
        self.speaking_changed.emit(state == QTextToSpeech.State.Speaking)
        if state == QTextToSpeech.State.Ready:
            self.status_changed.emit(self.voice_note())

    def voice_note(self):
        return ('英语系统语音 · 可重复播放' if self.english_voice else
                '当前使用系统默认音色；添加英语语音包可改善发音。')

    def say(self, text):
        if not text or not text.strip():
            return
        try:
            self._initialize()
            self.engine.stop()
            self.status_changed.emit(self.voice_note())
            self.engine.say(text.strip())
        except (RuntimeError, OSError) as exc:
            self.status_changed.emit(str(exc))

    def stop(self):
        if self.engine is not None:
            self.engine.stop()
        self.speaking_changed.emit(False)
