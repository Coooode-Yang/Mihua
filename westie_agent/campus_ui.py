"""Campus browser navigation and editing without a programming environment."""
from __future__ import annotations

import copy
import uuid

from PySide6.QtCore import Qt,QTimer,Signal
from PySide6.QtWidgets import (QComboBox,QDialog,QDialogButtonBox,QFormLayout,QHBoxLayout,
    QLabel,QLineEdit,QListWidget,QListWidgetItem,QMessageBox,QPlainTextEdit,QPushButton,QVBoxLayout)

from .campus import validate_catalog


class EntryEditor(QDialog):
    def __init__(self,entry=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle('编辑网页入口' if entry else '新增网页入口')
        self.resize(510,350)
        self.entry=copy.deepcopy(entry) if entry else dict(id=uuid.uuid4().hex,name='',url='',note='',aliases=[])
        layout=QVBoxLayout(self)
        form=QFormLayout()
        self.name=QLineEdit(self.entry['name']);self.name.setMaxLength(60)
        self.url=QLineEdit(self.entry['url']);self.url.setMaxLength(2048)
        self.url.setPlaceholderText('https://example.com/')
        self.aliases=QLineEdit('，'.join(self.entry['aliases']))
        self.aliases.setPlaceholderText('例如：邮箱，文献，工作台；最多 10 个')
        self.note=QPlainTextEdit(self.entry['note']);self.note.setMaximumHeight(100)
        for label,widget in [('名称',self.name),('网址',self.url),('对话关键词',self.aliases),('说明',self.note)]:form.addRow(label,widget)
        layout.addLayout(form)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.hide()
        layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.validate_and_accept);buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        self.entry.update(name=self.name.text(),url=self.url.text(),
                          aliases=[a.strip() for a in self.aliases.text().replace('，',',').split(',') if a.strip()],note=self.note.toPlainText())
        try:
            self.entry=validate_catalog({'schema_version':2,'entries':[self.entry]})['entries'][0]
        except ValueError as exc:
            self.status.setText(str(exc));self.status.show();return
        self.accept()


class CampusDialog(QDialog):
    open_requested=Signal(str)
    changed=Signal()

    def __init__(self,catalog,parent=None):
        super().__init__(parent)
        self.catalog=catalog
        self.setWindowTitle('米花 · 带路小鸡')
        self.resize(650,600)
        layout=QVBoxLayout(self)
        self.entries=QListWidget();self.entries.currentRowChanged.connect(self.describe)
        self.entries.itemDoubleClicked.connect(lambda _:self.open_selected())
        layout.addWidget(self.entries,1)
        self.details=QLabel();self.details.setWordWrap(True);self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        actions=QHBoxLayout()
        self.open_button=QPushButton('打开网站');self.open_button.clicked.connect(self.open_selected)
        actions.addWidget(self.open_button);layout.addLayout(actions)
        edit=QHBoxLayout()
        for title,callback in [('新增入口',lambda:self.edit_entry(False)),('编辑所选',lambda:self.edit_entry(True)),
                               ('删除所选',self.delete_selected)]:
            button=QPushButton(title);button.clicked.connect(callback);edit.addWidget(button)
            if title=='编辑所选':self.edit_button=button
            if title=='删除所选':self.delete_button=button
        layout.addLayout(edit)
        self.status=QLabel('')
        self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide();layout.addWidget(self.status)
        self.status_timer=QTimer(self);self.status_timer.setSingleShot(True);self.status_timer.timeout.connect(self.status.hide)
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(self.close)
        close.button(QDialogButtonBox.StandardButton.Close).setText('关闭');layout.addWidget(close)
        self.refresh()
        if catalog.problem:self.show_status(catalog.problem)

    def show_status(self,text):
        self.status.setText(text);self.status.setVisible(bool(text))
        if text:self.status_timer.start(6000)

    def selected(self):
        item=self.entries.currentItem()
        return self.catalog.entry(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self,rid=None):
        self.entries.clear()
        for entry in self.catalog.data['entries']:
            item=QListWidgetItem(entry['name']);item.setData(Qt.ItemDataRole.UserRole,entry['id']);self.entries.addItem(item)
            if entry['id']==rid:self.entries.setCurrentItem(item)
        if self.entries.currentRow()<0 and self.entries.count():self.entries.setCurrentRow(0)
        self.describe()

    def describe(self,*args):
        entry=self.selected()
        self.open_button.setEnabled(bool(entry))
        self.edit_button.setEnabled(bool(entry));self.delete_button.setEnabled(bool(entry))
        self.details.setText(('\n'.join(filter(None,[entry['url'],entry['note']]))) if entry else '尚无网页。点击“新增入口”添加网址。')

    def save_data(self,data,rid=None):
        try:self.catalog.save(data)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'未能保存入口',str(exc));return False
        self.refresh(rid);self.show_status('已保存');self.changed.emit();return True

    def edit_entry(self,existing):
        entry=self.selected() if existing else None
        if existing and not entry:return
        editor=EntryEditor(entry,self)
        if editor.exec()!=QDialog.DialogCode.Accepted:return
        data=copy.deepcopy(self.catalog.data)
        if entry:data['entries']=[editor.entry if e['id']==entry['id'] else e for e in data['entries']]
        else:data['entries'].append(editor.entry)
        self.save_data(data,editor.entry['id'])

    def delete_selected(self):
        entry=self.selected()
        if not entry:return
        if QMessageBox.question(self,'删除入口','删除“'+entry['name']+'”？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        data=copy.deepcopy(self.catalog.data);data['entries']=[e for e in data['entries'] if e['id']!=entry['id']]
        self.save_data(data)

    def open_selected(self):
        entry=self.selected()
        if entry:self.open_requested.emit(entry['id'])
