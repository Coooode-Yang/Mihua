"""Local Markdown reading, with only generated local images allowed to load."""
import json
from dataclasses import replace
from pathlib import Path
import re

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QImage, QTextDocument, QTextCursor, QTextBlockFormat, QColor, QTextCharFormat
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from .personal_ui import STYLE


class ReaderBrowser(QTextBrowser):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.root=None
        self.journal_capture=None
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setStyleSheet('QTextBrowser { background:#fcfaf5; border:1px solid #e6dfd2; border-radius:12px; padding:16px; }')

    def loadResource(self,resource_type,url):
        if resource_type!=QTextDocument.ResourceType.ImageResource or self.root is None:
            return None
        if url.scheme() not in {'','file'}: return None
        value=url.toLocalFile() if url.isLocalFile() else url.path()
        path=(self.root/value).resolve()
        if not path.is_relative_to((self.root/'assets').resolve()) or not path.is_file(): return None
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}: return None
        image=QImage(str(path))
        if image.isNull(): return None
        # Keep the whole crop visible without horizontal scrolling.
        width=max(250,self.viewport().width()-50)
        if image.width()>width:
            image=image.scaledToWidth(width,Qt.TransformationMode.SmoothTransformation)
        return image

    def contextMenuEvent(self,event):
        menu=self.createStandardContextMenu()
        if self.journal_capture:
            menu.addSeparator();action=menu.addAction('收藏选中文字到科研日志')
            selected=self.textCursor().selectedText().replace('\u2029','\n')
            action.setEnabled(bool(selected.strip()))
            action.triggered.connect(lambda:self.journal_capture(selected))
        menu.exec(event.globalPos());menu.deleteLater()


class ReaderDialog(QDialog):
    def __init__(self,store,directory,paper,parent=None,*,journal=None):
        super().__init__(parent,Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.store,self.library_directory,self.paper=store,Path(directory),paper
        self.artifact=None;self.source_map={}
        self.setWindowTitle('米花 · 文献解读')
        self.resize(920,780);self.setMinimumSize(600,450);self.setStyleSheet(STYLE)
        layout=QVBoxLayout(self);layout.setContentsMargins(22,18,22,18)
        title=QLabel(paper.title);title.setObjectName('title');title.setWordWrap(True);title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)
        row=QHBoxLayout();row.addWidget(QLabel('解读版本'))
        self.versions=QComboBox();row.addWidget(self.versions,1)
        self.open_md=QPushButton('打开 MD');self.open_folder=QPushButton('打开文件夹')
        row.addWidget(self.open_md);row.addWidget(self.open_folder);layout.addLayout(row)
        self.status=QLabel();self.status.setObjectName('muted');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.browser=ReaderBrowser();layout.addWidget(self.browser,1)
        if journal:
            self.browser.journal_capture=lambda text:journal.capture_reader(self.library_directory,self.paper,self.artifact,text) if self.artifact else None
            self.collect=QPushButton('收藏选中文字到科研日志');self.collect.setEnabled(False)
            row.addWidget(self.collect)
            self.browser.copyAvailable.connect(self.collect.setEnabled)
            self.collect.clicked.connect(lambda:self.browser.journal_capture(self.browser.textCursor().selectedText().replace('\u2029','\n')))
        note=QLabel('解读由模型生成；页码和原图可回到 Zotero 核对。完整 MD 和配套图像保存在本机。')
        note.setObjectName('muted');note.setWordWrap(True);layout.addWidget(note)
        self.versions.currentIndexChanged.connect(self.load_version)
        self.open_md.clicked.connect(lambda:self.open_local(False))
        self.open_folder.clicked.connect(lambda:self.open_local(True))
        self.browser.anchorClicked.connect(self.follow_link)
        self.reload_versions()
        for button in self.findChildren(QPushButton): button.setAutoDefault(False)

    def reload_versions(self):
        self.versions.blockSignals(True);self.versions.clear()
        for artifact in self.store.versions(self.library_directory,self.paper):
            metadata=artifact.metadata
            state={'complete':'已生成','draft':'待核对','cancelled':'已取消的草稿','failed':'未完成的草稿'}.get(metadata.get('status'),'草稿')
            self.versions.addItem(metadata.get('created_at',artifact.directory.name)+' · '+state,artifact)
        self.versions.blockSignals(False)
        self.load_version()

    def load_version(self,*_):
        artifact=self.versions.currentData()
        self.artifact=artifact
        self.open_md.setEnabled(artifact is not None);self.open_folder.setEnabled(artifact is not None)
        if artifact is None:
            self.browser.clear();self.status.setText('这篇文献还没有解读文件。');return
        try:
            text=artifact.markdown.read_text(encoding='utf-8')
            self.source_map=json.loads((artifact.directory/'source_map.json').read_text(encoding='utf-8'))
            self.browser.root=artifact.directory.resolve()
            self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(artifact.directory.resolve())+'/'))
            preview=re.sub(r'<a\s+id="(?:[SCEFT]\d+(?:-\d+)?|page-\d+)"\s*></a>\s*','',text)
            self.browser.document().setMarkdown(preview,QTextDocument.MarkdownFeature.MarkdownNoHTML | QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
            block=self.browser.document().begin()
            while block.isValid():
                cursor=QTextCursor(block);format_=block.blockFormat()
                format_.setTopMargin(4);format_.setBottomMargin(8)
                format_.setLineHeight(100 if '\ufffc' in block.text() else 140,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                cursor.setBlockFormat(format_)
                if block.text().startswith('Source:'):
                    cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    muted=QTextCharFormat();muted.setForeground(QColor('#82776c'));cursor.mergeCharFormat(muted)
                block=block.next()
            metadata=artifact.metadata
            self.status.setText(f'{metadata.get("page_count",0)} 页 · 已翻译 {metadata.get("translated_blocks",0)}/{metadata.get("total_blocks",0)} 段 · '+
                                ('有未完成或待核对内容，请查看文末记录。' if metadata.get('status')!='complete' else '已生成逐段解读。'))
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        except (OSError,ValueError,KeyError,TypeError):
            self.browser.clear();self.status.setText('解读文件无法读取，请在文件夹中检查或重新生成。')

    def open_local(self,folder):
        if self.artifact:
            path=self.artifact.directory if folder else self.artifact.markdown
            if path.exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def follow_link(self,url):
        if url.fragment() and not url.scheme() and not url.path():
            anchor=url.fragment()
            if re.fullmatch(r'page-\d+',anchor):
                target=f'第 {int(anchor[5:])} 页 · 原文对照'
            else:
                block=next((b for b in self.source_map.get('blocks',[]) if b['id']==anchor),None)
                if not block:return
                target=f'Source: p.{block["page"]} {anchor}'
            cursor=self.browser.document().find(target)
            if not cursor.isNull():self.browser.setTextCursor(cursor);self.browser.ensureCursorVisible()
            return
        try:
            source=replace(self.paper,attachment_key=self.artifact.metadata.get('attachment_key',self.paper.attachment_key),
                           content_type=self.artifact.metadata.get('content_type','application/pdf')) if self.artifact else self.paper
            expected=source.open_url().split('?')[0]
        except ValueError:
            self.status.setText('解读来源标识无效，请重新生成。');return
        value=url.toString()
        if value==expected or re.fullmatch(re.escape(expected)+r'\?page=[1-9]\d{0,5}',value):
            QDesktopServices.openUrl(url)
