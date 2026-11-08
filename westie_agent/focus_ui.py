"""A minimal fullscreen view over the existing persistent focus timer."""
import math
import sqlite3

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget


class FocusScreen(QWidget):
    def __init__(self,controller):
        super().__init__(None,Qt.WindowType.Window)
        self.controller=controller;self.closing=False
        self.pet_was_visible=False
        self.setWindowTitle('米花 · 专注计时')
        self.setStyleSheet('''
            QWidget { background:#fffaf3; color:#4f705c; font-family:"Microsoft YaHei"; }
            QLabel#phase { font-size:20px; color:#82776c; }
            QPushButton { border:none; border-radius:9px; padding:12px 22px; background:#eee8de; color:#6c7166; font-size:14px; }
            QPushButton:hover { background:#e3e9dd; }
            QPushButton#pause { background:#567a62; color:white; }
            QPushButton:disabled { color:#aaa396; }
        ''')
        layout=QVBoxLayout(self);layout.setContentsMargins(32,24,32,32)
        top=QHBoxLayout();top.addStretch()
        self.exit_button=QPushButton('退出全屏  Esc');self.exit_button.clicked.connect(self.leave)
        top.addWidget(self.exit_button);layout.addLayout(top);layout.addStretch(2)
        self.phase=QLabel('专注中');self.phase.setObjectName('phase');self.phase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock=QLabel('25:00');self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.clock.setStyleSheet('font-family:Consolas;')
        layout.addWidget(self.phase);layout.addSpacing(18);layout.addWidget(self.clock);layout.addSpacing(32)
        row=QHBoxLayout();row.addStretch()
        self.pause_button=QPushButton('暂停');self.pause_button.setObjectName('pause');self.pause_button.clicked.connect(self.pause_or_resume)
        self.stop_button=QPushButton('结束计时');self.stop_button.clicked.connect(self.confirm_stop)
        row.addWidget(self.pause_button);row.addSpacing(10);row.addWidget(self.stop_button);row.addStretch();layout.addLayout(row)
        layout.addStretch(3)
        for button in (self.exit_button,self.pause_button,self.stop_button):button.setAutoDefault(False)
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.refresh)

    def open_fullscreen(self):
        screen=QApplication.screenAt(self.controller.pet.frameGeometry().center()) or QApplication.primaryScreen()
        if not self.isVisible():self.pet_was_visible=self.controller.pet.isVisible()
        self.controller.pet.hide()
        # Establish the target monitor before entering fullscreen.
        self.setGeometry(screen.availableGeometry());self.showFullScreen();self.raise_();self.activateWindow()
        self.refresh();self.timer.start();self.exit_button.setFocus()

    def refresh(self):
        try:
            state=self.controller.focus.snapshot()
            active=state['status'] in {'running','paused'}
            seconds=math.ceil(state['remaining_s'] if state['status']!='idle' else state['focus_minutes']*60)
            self.clock.setText(f'{seconds//60:02}:{seconds%60:02}')
            phase='专注' if state['phase']=='focus' else '休息'
            self.phase.setText({'running':phase+'中','paused':'已暂停','finished':phase+'已完成','idle':'计时已结束'}[state['status']])
            self.pause_button.setEnabled(active);self.stop_button.setEnabled(active)
            self.pause_button.setText('继续' if state['status']=='paused' else '暂停')
            self.exit_button.setText('退出全屏  Esc' if active else '返回主界面  Esc')
        except (OSError,ValueError,sqlite3.Error):self.phase.setText('暂时无法读取计时，请返回主界面重试。')

    def resizeEvent(self,event):
        super().resizeEvent(event)
        font=QFont('Consolas');font.setPixelSize(max(40,min(210,int(min(self.width()/4.9,self.height()/3.6)))))
        font.setWeight(QFont.Weight.DemiBold);self.clock.setFont(font)
        required=self.clock.fontMetrics().horizontalAdvance('180:00')
        available=max(1,self.width()-72)
        if required>available:
            font.setPixelSize(max(24,int(font.pixelSize()*available/required)))
            self.clock.setFont(font)

    def pause_or_resume(self):
        def action():
            if self.controller.focus.snapshot()['status']=='paused':self.controller.focus.resume()
            else:self.controller.focus.pause()
        self.controller.perform(action);self.refresh()

    def confirm_stop(self):
        if QMessageBox.question(self,'结束这段计时','提前结束会保留记录，但不会计入已完成番茄。继续结束吗？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self.controller.perform(self.controller.focus.stop);self.leave()

    def leave(self):
        self.timer.stop();self.hide()
        if not self.closing:
            if self.pet_was_visible:self.controller.pet.show()
            self.controller.show_page('study')

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.leave();event.accept()
        else:super().keyPressEvent(event)

    def closeEvent(self,event):
        self.leave();event.accept()

    def shutdown(self):
        self.closing=True;self.timer.stop();self.hide()
