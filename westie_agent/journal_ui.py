"""Daily research notebook and a small shelf in the research page."""
from datetime import date
import hashlib
from pathlib import Path
import sqlite3
import re
import uuid

from PySide6.QtCore import QBuffer, QDate, QIODevice, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QListView, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from .journal_store import KINDS, image_limit
from .personal_ui import STYLE, label
from .reader_ui import ReaderBrowser


def fit_window(window, width=1000, height=780):
    screen=window.screen() or QApplication.primaryScreen();bounds=screen.availableGeometry()
    window.resize(min(width,bounds.width()-32),min(height,bounds.height()-40))
    window.move(bounds.center()-window.rect().center())


def show_markdown(browser,text):
    browser.document().setMarkdown(text,QTextDocument.MarkdownFeature.MarkdownNoHTML|QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
    block=browser.document().begin()
    while block.isValid():
        cursor=QTextCursor(block);format_=block.blockFormat()
        format_.setTopMargin(6);format_.setBottomMargin(10)
        format_.setLineHeight(100 if '\ufffc' in block.text() else 145,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(format_);block=block.next()
    browser.moveCursor(QTextCursor.MoveOperation.Start)


class JournalShelf(QFrame):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.controller=controller;self.setObjectName('card')
        layout=QVBoxLayout(self);layout.setContentsMargins(15,12,15,12)
        row=QHBoxLayout();row.addWidget(label('科研日志','title'),1)
        self.all_button=QPushButton('查看全部');self.all_button.clicked.connect(lambda:controller.show(all_dates=True))
        row.addWidget(self.all_button);layout.addLayout(row)
        self.summary=label('','eyebrow');layout.addWidget(self.summary)
        self.recent=label('');self.recent.setMaximumHeight(62);layout.addWidget(self.recent)
        row=QHBoxLayout()
        self.add=QPushButton('＋ 随手记');self.add.setObjectName('primary');self.add.clicked.connect(lambda:controller.edit())
        self.today=QPushButton('打开今日日志');self.today.clicked.connect(lambda:controller.show())
        row.addWidget(self.add);row.addWidget(self.today);row.addStretch();layout.addLayout(row)
        self.refresh()

    def refresh(self):
        try:
            day=date.today().isoformat();entries=self.controller.store.entries(day)
            counts=[f'{name} {sum(r["kind"]==kind for r in entries)} 条' for kind,name in KINDS.items()
                    if any(r['kind']==kind for r in entries)]
            self.summary.setText(day+' · '+('  /  '.join(counts) if counts else '随时留下一点想法'))
            self.recent.setText('\n'.join(f'{KINDS[r["kind"]]} · {r["title"][:55]}' for r in entries[-2:]) or
                                '阅读中的启发、讨论里的建议，或今天遇到的新知识。')
        except (OSError,ValueError,sqlite3.Error):
            self.summary.setText('日志暂时无法读取');self.recent.setText('请检查数据目录权限后重试。')


class JournalTextEdit(QTextEdit):
    image_pasted = Signal(object)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            self.image_pasted.emit(source)
        else:
            super().insertFromMimeData(source)


class EntryEditor(QDialog):
    def __init__(self,controller,entry=None,seed=None,parent=None):
        super().__init__(parent,Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller=controller;self.entry=entry;self.seed=seed or {};self.saved=None
        data=dict(entry or {},**self.seed);self.sources=list(data.get('sources',[]));self.attachments=list(data.get('attachments',[]));self.pending=list(data.get('_pending',[]))
        self.draft_id=data.get('_draft_id',uuid.uuid4().hex)
        self.setStyleSheet(STYLE);self.setWindowTitle('米花 · '+('编辑科研日志' if entry else '随手记'))
        fit_window(self,760,760)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18)
        row=QHBoxLayout();self.day=QDateEdit();self.day.setCalendarPopup(True);self.day.setDisplayFormat('yyyy-MM-dd')
        self.day.setDate(QDate.fromString(data.get('day',date.today().isoformat()),'yyyy-MM-dd'))
        self.kind=QComboBox()
        for key,name in KINDS.items(): self.kind.addItem(name,key)
        self.kind.setCurrentIndex(max(0,self.kind.findData(data.get('kind','discovery'))))
        row.addWidget(self.day);row.addWidget(self.kind);row.addStretch();layout.addLayout(row)
        self.title=QLineEdit(data.get('title',''));self.title.setMaxLength(200);self.title.setPlaceholderText('标题（可选）')
        layout.addWidget(self.title)
        self.body=JournalTextEdit();self.body.setAcceptRichText(False);self.body.setPlainText(data.get('body',''))
        self.body.image_pasted.connect(self.paste_image)
        self.body.setPlaceholderText('留下一点今天的想法，也可以直接粘贴笔记或图片（Ctrl+V）。支持 Markdown。');layout.addWidget(self.body,1)
        row=QHBoxLayout();self.template=QPushButton('插入组会模板');self.template.clicked.connect(self.insert_template)
        self.add_image=QPushButton('添加图片');self.add_image.clicked.connect(self.choose_images)
        self.add_image.setToolTip('选择图片，或在正文中按 Ctrl+V 粘贴截图')
        row.addWidget(self.template);row.addWidget(self.add_image);row.addStretch();layout.addLayout(row)
        self.images=label('','muted');layout.addWidget(self.images)
        self.image_previews=QListWidget()
        self.image_previews.setFlow(QListWidget.Flow.LeftToRight)
        self.image_previews.setIconSize(QSize(96,72))
        self.image_previews.setFixedHeight(110)
        self.image_previews.setAccessibleName('待保存的日志图片预览')
        layout.addWidget(self.image_previews)
        self.clear_images=QPushButton('移除本条全部图片');self.clear_images.clicked.connect(self.remove_images);layout.addWidget(self.clear_images)
        self.reflection=QTextEdit();self.reflection.setAcceptRichText(False);self.reflection.setMaximumHeight(85)
        self.reflection.setPlaceholderText('为什么值得留下 / 我的想法（可选）');self.reflection.setPlainText(data.get('reflection',''));layout.addWidget(self.reflection)
        row=QHBoxLayout();self.tags=QLineEdit('，'.join(data.get('tags',[])));self.tags.setPlaceholderText('标签（可选，用逗号分隔）')
        self.url=QLineEdit(data.get('_url',''));self.url.setPlaceholderText('补充来源链接（可选）');row.addWidget(self.tags);row.addWidget(self.url);layout.addLayout(row)
        self.status=label('','muted');self.status.hide();layout.addWidget(self.status)
        row=QHBoxLayout();row.addStretch();cancel=QPushButton('取消');cancel.clicked.connect(self.close)
        self.save_button=QPushButton('保存记录');self.save_button.setObjectName('primary');self.save_button.clicked.connect(self.save)
        if entry:
            self.save_copy=QPushButton('另存一条');self.save_copy.clicked.connect(lambda:self.save(as_copy=True));row.addWidget(self.save_copy)
        row.addWidget(cancel);row.addWidget(self.save_button);layout.addLayout(row)
        for button in self.findChildren(QPushButton): button.setAutoDefault(False)
        self.refresh_details()
        self.draft_timer=QTimer(self);self.draft_timer.setInterval(10000);self.draft_timer.timeout.connect(self.stash_draft);self.draft_timer.start()

    def stash_draft(self):
        if self.saved or (not self.body.toPlainText().strip() and not self.title.text().strip() and not self.pending):return
        if self.entry and not self.modified(self.entry):return
        try:
            data={'day':self.day.date().toString('yyyy-MM-dd'),'kind':self.kind.currentData(),'title':self.title.text(),
                  'body':self.body.toPlainText(),'reflection':self.reflection.toPlainText(),
                  'tags':re.split(r'[,，;；\n]+',self.tags.text()),'sources':self.sources,
                  'attachments':self.attachments,'_pending':self.pending,'_url':self.url.text(),
                  '_editing_id':self.entry['id'] if self.entry else None}
            self.controller.store.save_draft(self.draft_id,data)
        except (OSError,ValueError):self.set_status('草稿暂时无法保存，请检查目录权限。')

    def set_status(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def refresh_details(self):
        count=len(self.attachments)+len(self.pending)
        self.images.setText(f'已添加 {count} / {image_limit(self.sources)} 张图片')
        self.image_previews.clear()
        paths=[]
        for relative in self.attachments:
            try:paths.append(self.controller.store.asset(relative))
            except ValueError:continue
        paths.extend(Path(p) for p in self.pending)
        for index,path in enumerate(paths):
            reader=QImageReader(str(path))
            size=reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(96,72,Qt.AspectRatioMode.KeepAspectRatio))
            image=reader.read()
            item=QListWidgetItem(QIcon(QPixmap.fromImage(image)),f'图片 {index+1}')
            item.setToolTip(path.name)
            self.image_previews.addItem(item)
        self.image_previews.setVisible(count>0)
        self.clear_images.setVisible(count>0)

    def insert_template(self):
        self.kind.setCurrentIndex(self.kind.findData('meeting'))
        self.body.insertPlainText(('\n\n' if self.body.toPlainText().strip() else '')+'讨论主题：\n\n主要意见：\n\n我的理解：\n\n后续考虑：\n')

    def choose_images(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加日志图片','','图片 (*.png *.jpg *.jpeg *.webp)')
        candidates=list(dict.fromkeys(self.pending+paths))
        if len(candidates)+len(self.attachments)>image_limit(self.sources):self.set_status(f'每条记录最多添加 {image_limit(self.sources)} 张图片。');return
        self.pending=candidates;self.refresh_details()

    def paste_image(self, source):
        path=None
        try:
            if len(self.attachments)+len(self.pending)>=image_limit(self.sources):
                raise ValueError(f'每条记录最多添加 {image_limit(self.sources)} 张图片。')
            image=source.imageData()
            if isinstance(image,QPixmap):image=image.toImage()
            if not isinstance(image,QImage) or image.isNull():
                raise ValueError('剪贴板图片无法读取，请重新复制或使用“添加图片”。')
            if image.width()*image.height()>40_000_000:
                raise ValueError('图片尺寸过大，请先缩小后添加。')
            buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer,'PNG'):
                raise ValueError('剪贴板图片无法读取，请重新复制或使用“添加图片”。')
            if buffer.size()>15*1024*1024:
                raise ValueError('每张图片请控制在 15 MB 以内。')
            # Keep a local PNG alongside the draft so recovery survives restarts.
            path=self.controller.store.root/'drafts'/f'{self.draft_id}-{uuid.uuid4().hex}.png'
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:stream.write(bytes(buffer.data()))
            self.pending.append(str(path));self.refresh_details()
            self.set_status('已粘贴图片，点击“保存记录”后保存到本机。')
        except (OSError,ValueError) as exc:
            if path is not None:
                try:path.unlink(missing_ok=True)
                except OSError:pass
            self.set_status(str(exc) if isinstance(exc,ValueError) else '图片暂时无法保存，请检查磁盘空间与目录权限。')

    def remove_images(self):
        references=set(self.attachments)
        for pending in self.pending:
            path=Path(pending)
            if path.is_file():
                references.add('assets/'+hashlib.sha256(path.read_bytes()).hexdigest()+path.suffix.lower())
        try:self.controller.store.clear_draft_images(self.draft_id)
        except OSError:
            self.set_status('图片暂时无法移除，请检查目录权限。');return
        text=self.body.toPlainText()
        text=re.sub(r'!\[[^\]\n]*\]\(<?(assets/[0-9a-f]{64}\.(?:png|jpg|jpeg|webp))>?\)',
                    lambda match:'' if match[1] in references else match[0],text)
        self.body.setPlainText(text)
        self.attachments=[];self.pending=[];self.refresh_details()

    def save(self,as_copy=False):
        import re
        import sqlite3
        try:
            sources=list(self.sources)
            if self.url.text().strip(): sources.append({'type':'link','title':'补充来源','url':self.url.text().strip()})
            # Validate the text first, before copying optional images.
            if not self.body.toPlainText().strip(): raise ValueError('写一点内容再保存吧。')
            attachments=self.attachments+[self.controller.store.import_image(p) for p in self.pending]
            self.saved=self.controller.store.save(day=self.day.date().toString('yyyy-MM-dd'),kind=self.kind.currentData(),
                title=self.title.text(),body=self.body.toPlainText(),reflection=self.reflection.toPlainText(),
                tags=re.split(r'[,，;；\n]+',self.tags.text()),sources=sources,attachments=attachments,
                identifier=self.entry['id'] if self.entry and not as_copy else None,
                expected_updated_at=self.entry['updated_at'] if self.entry and not as_copy else None)
            try:self.controller.store.remove_draft(self.draft_id)
            except OSError:pass
            self.controller.changed.emit();self.accept()
        except (OSError,ValueError,sqlite3.Error) as exc:
            self.set_status(str(exc) if isinstance(exc,ValueError) else '未能保存，请检查图片格式、磁盘空间与目录权限。')

    def modified(self,original):
        return (self.body.toPlainText()!=original.get('body','') or self.title.text()!=original.get('title','') or
               self.reflection.toPlainText()!=original.get('reflection','') or bool(self.pending) or
               self.tags.text()!='，'.join(original.get('tags',[])) or bool(self.url.text().strip()) or
               self.attachments!=original.get('attachments',[]) or
               self.day.date().toString('yyyy-MM-dd')!=original.get('day',date.today().isoformat()) or
               self.kind.currentData()!=original.get('kind','discovery'))
    def reject(self):
        dirty=self.modified(self.entry or self.seed)
        if dirty and QMessageBox.question(self,'放弃本次编辑？','这些修改还没有保存。确定放弃？',
                QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Discard:return
        try:self.controller.store.remove_draft(self.draft_id)
        except OSError:pass
        super().reject()


class SnapshotDialog(QDialog):
    def __init__(self,controller,snapshot,parent=None):
        super().__init__(parent,Qt.WindowType.Window);self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('米花 · 整理时的原始记录');self.setStyleSheet(STYLE);fit_window(self,800,700)
        layout=QVBoxLayout(self);layout.addWidget(label('原始记录快照','title'))
        layout.addWidget(label('保留 AI 整理时的文字，之后编辑或移走原记录也不会改变此快照。','muted'))
        browser=ReaderBrowser();browser.root=controller.store.root
        # Snapshot source actions must not resolve against a subsequently edited entry.
        record=dict(snapshot,sources=[{k:v for k,v in s.items() if k in {'title','url'}} for s in snapshot.get('sources',[])])
        show_markdown(browser,controller.store.markdown(record['day'],entries=[record],preview=True))
        browser.anchorClicked.connect(controller.follow_link);layout.addWidget(browser,1)


class JournalDialog(QDialog):
    def __init__(self,controller,parent=None):
        super().__init__(parent,Qt.WindowType.Window);self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.controller=controller;self.setWindowTitle('米花 · 科研日志');self.setStyleSheet(STYLE);fit_window(self)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18)
        layout.addWidget(label('科研日志','title'))
        layout.addWidget(label('把阅读、讨论和新发现，留在它们发生的那一天。','muted'))
        row=QHBoxLayout();self.day=QDateEdit(QDate.currentDate());self.day.setCalendarPopup(True);self.day.setDisplayFormat('yyyy-MM-dd')
        self.today=QPushButton('今天');self.today.clicked.connect(self.go_today)
        self.all_dates=QCheckBox('全部日期');self.search=QLineEdit();self.search.setPlaceholderText('搜索标题、正文、想法或标签')
        self.kind=QComboBox();self.kind.addItem('全部类型','')
        for key,name in KINDS.items():self.kind.addItem(name,key)
        row.addWidget(self.day);row.addWidget(self.today);row.addWidget(self.all_dates);row.addWidget(self.kind);row.addWidget(self.search,1);layout.addLayout(row)
        row=QHBoxLayout();self.add=QPushButton('＋ 随手记');self.add.setObjectName('primary')
        self.edit_button=QPushButton('编辑');self.remove=QPushButton('移到回收站');self.trash=QCheckBox('回收站')
        self.export=QPushButton('导出当天 MD')
        for w in (self.add,self.edit_button,self.remove,self.trash,self.export):row.addWidget(w)
        row.addStretch();layout.addLayout(row)
        self.recover=QPushButton('恢复未保存草稿');self.recover.clicked.connect(controller.recover_draft);layout.addWidget(self.recover)
        split=QSplitter();self.list=QListWidget();self.list.setWordWrap(True);self.list.setMinimumWidth(190)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser=ReaderBrowser();self.browser.root=controller.store.root
        split.addWidget(self.list);split.addWidget(self.browser);split.setSizes([280,650]);layout.addWidget(split,1)
        row=QHBoxLayout();self.tidy=QPushButton('整理这条记录');self.day_review=QPushButton('生成当天回顾');self.week_review=QPushButton('回顾本周')
        self.cancel_ai=QPushButton('取消整理')
        for w in (self.tidy,self.day_review,self.week_review,self.cancel_ai):row.addWidget(w)
        row.addStretch();layout.addLayout(row)
        self.status=label('','eyebrow');self.status.hide();row.addWidget(self.status,1)
        self.add.clicked.connect(lambda:controller.edit(seed={'day':self.selected_day()}))
        self.edit_button.clicked.connect(self.edit_selected);self.remove.clicked.connect(self.trash_selected)
        self.export.clicked.connect(self.export_day);self.list.currentItemChanged.connect(self.selection_changed)
        self.browser.anchorClicked.connect(controller.follow_link)
        self.tidy.clicked.connect(lambda:controller.assist('tidy',self.selected_day(),self.selected_entry()))
        self.day_review.clicked.connect(lambda:controller.assist('day',self.selected_day()))
        self.week_review.clicked.connect(lambda:controller.assist('week',self.selected_day()))
        self.cancel_ai.clicked.connect(controller.cancel_job)
        self.search_timer=QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh);self.search.textChanged.connect(lambda:self.search_timer.start())
        for signal in (self.day.dateChanged,self.all_dates.toggled,self.kind.currentIndexChanged,self.trash.toggled):signal.connect(self.refresh)
        for button in self.findChildren(QPushButton):button.setAutoDefault(False)
        self.refresh()

    def selected_day(self):return self.day.date().toString('yyyy-MM-dd')
    def selected_entry(self):
        item=self.list.currentItem();return item.data(Qt.ItemDataRole.UserRole) if item else None

    def go_today(self):
        self.all_dates.setChecked(False);self.day.setDate(QDate.currentDate());self.refresh()

    def refresh(self,*_):
        import sqlite3
        previous=self.selected_entry();self.list.clear()
        try:
            rows=self.controller.store.entries(None if self.all_dates.isChecked() else self.selected_day(),
                query=self.search.text(),kind=self.kind.currentData(),deleted=self.trash.isChecked())
            drafts=self.controller.store.drafts();self.recover.setVisible(bool(drafts));self.recover.setText(f'恢复未保存草稿（{len(drafts)}）')
            # The calendar highlights days with records; no empty-day obligations.
            calendar=self.day.calendarWidget();calendar.setDateTextFormat(QDate(),QTextCharFormat())
            highlight=QTextCharFormat();highlight.setForeground(QColor('#456951'));highlight.setFontWeight(700)
            for day in {r['day'] for r in self.controller.store.entries()}:
                calendar.setDateTextFormat(QDate.fromString(day,'yyyy-MM-dd'),highlight)
            for entry in rows:
                item=QListWidgetItem(entry['day']+' · '+KINDS[entry['kind']]+'\n'+entry['title'])
                item.setData(Qt.ItemDataRole.UserRole,entry);self.list.addItem(item)
                if previous and previous['id']==entry['id']:self.list.setCurrentItem(item)
            if rows and self.list.currentRow()<0:self.list.setCurrentRow(0)
            if not rows:
                self.browser.clear()
                normal=QTextCharFormat();normal.setFont(self.browser.font());self.browser.setCurrentCharFormat(normal)
                self.browser.setPlainText('暂无记录')
            self.selection_changed();self.update_ai()
        except (OSError,ValueError,sqlite3.Error):self.status.setText('日志暂时无法读取，请检查数据目录后重试。')

    def selection_changed(self,*_):
        entry=self.selected_entry();deleted=self.trash.isChecked()
        self.edit_button.setEnabled(bool(entry) and not deleted);self.remove.setEnabled(bool(entry))
        self.remove.setText('恢复记录' if deleted else '移到回收站')
        if entry:
            show_markdown(self.browser,self.controller.store.markdown(entry['day'],preview=True,entries=[entry]))
        self.update_ai()

    def update_ai(self):
        busy=self.controller.worker is not None;deleted=self.trash.isChecked()
        self.tidy.setEnabled(not busy and bool(self.selected_entry()) and not deleted)
        self.day_review.setEnabled(not busy and not deleted);self.week_review.setEnabled(not busy and not deleted)
        self.cancel_ai.setVisible(busy);self.cancel_ai.setEnabled(busy and not self.controller.worker.cancel.is_set() if busy else False)
        self.status.setText(self.controller.message)
        self.status.setVisible(bool(self.controller.message))
        self.export.setEnabled(not self.all_dates.isChecked() and not deleted)
        self.day.setEnabled(not self.all_dates.isChecked())

    def edit_selected(self):
        entry=self.selected_entry()
        if entry:self.controller.edit(entry=entry)

    def trash_selected(self):
        entry=self.selected_entry()
        if entry:self.controller.attempt(lambda:self.controller.store.trash(entry['id'],not self.trash.isChecked()))

    def export_day(self):
        path,_=QFileDialog.getSaveFileName(self,'导出科研日志',self.selected_day()+'.md','Markdown (*.md)',
                                         options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            def action():
                self.controller.store.export(self.selected_day(),path)
                self.controller.message='已导出：'+path
            self.controller.attempt(action)
