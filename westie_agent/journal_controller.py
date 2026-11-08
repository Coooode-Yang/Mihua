"""Connect explicit captures, daily notes and independent AI preview jobs."""
import copy
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
import re
import sqlite3
import threading
import uuid
import hashlib

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from .journal_ai import JournalModel, snapshot_payload
from .journal_store import JournalStore, safe_url, valid_day
from .paper_reader import ReaderCancelled
from .runtime import SetupRequired


class JournalWorker(QThread):
    completed=Signal(object)
    def __init__(self,owner,model,payload,entries,day):
        super().__init__(owner);self.model=model;self.payload=payload;self.entries=copy.deepcopy(entries);self.day=day
        self.cancel=threading.Event()

    def run(self):
        try:self.completed.emit({'result':self.model.request(self.payload,self.cancel)})
        except ReaderCancelled:self.completed.emit({'cancelled':True})
        except (ValueError,SetupRequired) as exc:self.completed.emit({'error':str(exc)})
        except Exception:self.completed.emit({'error':'这次整理未完成，请检查模型设置后重试。原始记录仍已保存。'})


class JournalController(QObject):
    changed=Signal()
    def __init__(self,agent):
        super().__init__(agent);self.agent=agent;self.store=JournalStore(agent.base)
        self.window=None;self.editors=[];self.shelves=[];self.worker=None;self.message='';self.model_factory=None;self.closing=False
        self.changed.connect(self.refresh)
        agent.pet.chat.history.capture_requested.connect(self.capture_chat)

    def refresh(self):
        for shelf in self.shelves:shelf.refresh()
        if self.window:self.window.refresh()

    def attach(self,page):
        from .journal_ui import JournalShelf
        from PySide6.QtWidgets import QPushButton
        shelf=JournalShelf(self,page);page.layout().addWidget(shelf);self.shelves.append(shelf)
        page.journal_shelf=shelf
        page.note_button=QPushButton('记笔记')
        page.action_row.insertWidget(1,page.note_button)
        page.note_button.clicked.connect(lambda:self.capture_paper(page))
        page.selection_changed()

    def attempt(self,action):
        try:
            result=action();self.changed.emit();return result
        except (ValueError,OSError,sqlite3.Error,SetupRequired) as exc:
            self.message=str(exc) if isinstance(exc,(ValueError,SetupRequired)) else '操作未完成，请检查磁盘空间、文件和目录权限。'
            if self.window:self.window.update_ai()
            self.agent.pet.chat.add_message('米花',self.message)

    def show(self,*,all_dates=False,day=None):
        from .journal_ui import JournalDialog
        from PySide6.QtCore import QDate
        def action():
            if self.window is None:
                self.window=JournalDialog(self,self.agent.pet.chat)
                self.window.finished.connect(lambda _:setattr(self,'window',None))
            self.window.all_dates.setChecked(all_dates)
            self.window.day.setDate(QDate.fromString(day or date.today().isoformat(),'yyyy-MM-dd'))
            self.window.refresh();self.window.show();self.window.raise_();self.window.activateWindow()
        self.attempt(action)

    def edit(self,*,entry=None,seed=None):
        from .journal_ui import EntryEditor
        def action():
            editor=EntryEditor(self,entry,seed,self.agent.pet.chat);self.editors.append(editor)
            editor.finished.connect(lambda _:self.editors.remove(editor) if editor in self.editors else None)
            editor.show();editor.raise_();return editor
        return self.attempt(action)

    def paper_source(self,paper,directory,artifact=None):
        source={'type':'paper','title':paper.title,'url':paper.open_url(),'paper':asdict(paper),'directory':str(directory)}
        if artifact:
            source['reader']=artifact.markdown.resolve().relative_to(self.store.base).as_posix()
        return source

    def recover_draft(self):
        from PySide6.QtWidgets import QInputDialog
        def action():
            drafts=self.store.drafts()
            open_ids={getattr(e,'draft_id',None) for e in self.editors}
            drafts=[d for d in drafts if d['_draft_id'] not in open_ids]
            if not drafts:raise ValueError('可恢复的草稿已在编辑窗口中打开。')
            names=[f'{i+1}. {d["day"]} · {d.get("title") or d["body"][:35]}' for i,d in enumerate(drafts)]
            name,ok=QInputDialog.getItem(self.window,'恢复草稿','选择要继续编辑的草稿',names,0,False)
            if not ok:return
            draft=drafts[names.index(name)];entry=self.store.get(draft.get('_editing_id'))
            if entry and entry['deleted']:entry=None
            return self.edit(entry=entry,seed=draft)
        return self.attempt(action)

    def capture_paper(self,page):
        paper=page.selected_paper();directory=page.snapshot_directory
        if paper is None or directory is None:return
        def action():
            artifact=self.agent.reader.store.latest(directory,paper)
            return self.edit(seed={'kind':'reading','title':paper.title,'sources':[self.paper_source(paper,directory,artifact)]})
        return self.attempt(action)

    def capture_reader(self,directory,paper,artifact,text):
        def action():
            source=self.paper_source(paper,directory,artifact)
            return self.edit(seed={'kind':'reading','title':paper.title,
                'body':'> '+text.strip().replace('\n','\n> '),'sources':[source]})
        return self.attempt(action)

    def capture_chat(self,data):
        def action():
            whole = data.get('scope') == 'conversation'
            messages = data.get('messages', ()) if whole else ()
            body = data.get('body', '').strip()
            if not body and not messages:
                return
            question = data.get('question', '').strip()
            draft_id = uuid.uuid4().hex
            pending, staged = [], {}
            def images_markdown(images):
                lines = []
                for image in images:
                    if not image.is_image:
                        continue
                    digest = hashlib.sha256(image.data).hexdigest()
                    if digest not in staged:
                        path, relative = self.store.stage_chat_image(draft_id, image.data)
                        pending.append(str(path));staged[digest] = relative
                    lines.append('![对话配图]('+staged[digest]+')')
                return '\n\n'.join(lines)
            try:
                source = {'type':'chat', 'title':'与米花的对话（'+date.today().isoformat()+'）'}
                if whole:
                    parts = []
                    for message in messages:
                        parts.append('### '+message['speaker']+'\n\n'+message['text'])
                        images = images_markdown(message.get('images', ()))
                        if images:
                            parts.append(images)
                    body = '\n\n'.join(parts)
                    question = next((m['text'] for m in messages if m['speaker']=='你'), '')
                    source.update(scope='conversation', message_count=len(messages), image_count=len(staged))
                    title = '完整对话 · '+(question.splitlines()[0] if question else date.today().isoformat())
                else:
                    images = images_markdown(data.get('images', ()))
                    if data.get('include_question') and question:
                        body = '问题：'+question+('\n\n'+images if images else '')+'\n\n米花的回答：\n\n'+body
                    elif images:
                        body += '\n\n'+images
                    title = question or body.splitlines()[0]
                editor = self.edit(seed={'kind':'discovery', 'title':title[:120], 'body':body,
                    'sources':[source], '_draft_id':draft_id, '_pending':pending})
                if editor is None:
                    self.store.remove_draft(draft_id)
                return editor
            except (OSError, ValueError):
                self.store.remove_draft(draft_id)
                raise
        return self.attempt(action)

    def create_model(self,settings):
        if self.model_factory:return self.model_factory(settings)
        if settings.model_backend!='api':raise SetupRequired('日志整理目前使用 API 模型，请先在“模型设置”配置在线模型。')
        key=self.agent.credentials.load(settings.api_base_url)
        if not key:raise SetupRequired('还没配置 API Key，请先打开“工具箱 → 模型设置”。普通记录可直接使用。')
        return JournalModel(settings,key)

    def assist(self,mode,day,entry=None):
        if self.worker is not None:return
        def action():
            target_day=valid_day(day)
            if mode not in {'tidy','day','week'}:raise ValueError('不支持的整理方式。')
            start=(date.fromisoformat(target_day)-timedelta(days=date.fromisoformat(target_day).weekday())).isoformat() if mode=='week' else target_day
            if mode=='tidy':
                current=self.store.get(entry['id']) if entry else None
                if not current or current['deleted']:raise ValueError('请选择一条已保存的记录。')
                entries=[current];target_day=current['day'];start=target_day
            else:entries=[e for e in self.store.entries(start=start,end=target_day) if e['kind']!='review']
            payload=snapshot_payload(entries,mode,start,target_day)
            model=self.create_model(replace(self.agent.settings))
            worker=JournalWorker(self,model,payload,entries,target_day);self.worker=worker
            self.message=f'正在整理 {len(entries)} 条记录 · {start} 至 {target_day}…'
            worker.completed.connect(lambda result:self.completed(worker,result))
            worker.finished.connect(lambda:self.release(worker));worker.start()
        self.attempt(action)

    def completed(self,worker,outcome):
        if self.closing:return
        if outcome.get('cancelled') or worker.cancel.is_set():self.message='已取消整理，原始记录已保留。'
        elif outcome.get('error'):self.message=outcome['error']
        else:
            result=outcome['result'];sources=[]
            for index,entry in enumerate(worker.entries):
                snapshot=dict(entry,sources=[{k:v for k,v in s.items() if k!='snapshot'} for s in entry['sources']])
                sources.append({'type':'entry','entry_id':entry['id'],'day':entry['day'],'title':f'R{index+1:03} · '+entry['title'],
                                'snapshot':snapshot})
            body=result['body']+'\n\n---\nAI '+('整理' if worker.payload['mode']=='tidy' else '回顾')+' · '+worker.payload['start']+' 至 '+worker.day
            self.edit(seed={'day':worker.day,'kind':'review','title':result['title'],'body':body,'sources':sources,
                            'tags':['AI 整理' if worker.payload['mode']=='tidy' else 'AI 回顾']})
            self.message=''
        if self.window:self.window.update_ai()

    def release(self,worker):
        if self.worker is worker:self.worker=None
        worker.deleteLater()
        if self.window:self.window.update_ai()

    def cancel_job(self):
        if self.worker:
            self.worker.cancel.set();self.message='正在取消整理…'
            if self.window:self.window.update_ai()

    def follow_link(self,url):
        value=url.toString()
        match=re.fullmatch(r'bai-reader:/([0-9a-f]{32})/(\d+)',value)
        note=re.fullmatch(r'bai-note:/([0-9a-f]{32})/(\d+)',value)
        if match:
            def action():
                row=self.store.get(match[1]);index=int(match[2])
                if not row or index>=len(row['sources']):raise ValueError('来源记录不存在。')
                source=row['sources'][index];path=self.store.reader_path(source.get('reader'))
                from .zotero import RecentPaper
                from .reader_ui import ReaderDialog
                paper=RecentPaper(**source['paper'])
                viewer=ReaderDialog(self.agent.reader.store,source['directory'],paper,self.agent.pet.chat,journal=self)
                found=next((i for i in range(viewer.versions.count()) if viewer.versions.itemData(i).markdown.resolve()==path),None)
                if found is None:viewer.close();raise ValueError('收藏时的解读版本已不可用。')
                viewer.versions.setCurrentIndex(found)
                self.agent.reader.viewers.append(viewer)
                viewer.finished.connect(lambda _:self.agent.reader.viewers.remove(viewer) if viewer in self.agent.reader.viewers else None)
                viewer.show();viewer.raise_()
            self.attempt(action)
        elif note:
            def action():
                row=self.store.get(note[1]);index=int(note[2])
                if not row or index>=len(row['sources']):raise ValueError('来源记录不存在。')
                source=row['sources'][index];snapshot=source.get('snapshot')
                if not isinstance(snapshot,dict):raise ValueError('来源快照不可用。')
                from .journal_ui import SnapshotDialog
                viewer=SnapshotDialog(self,snapshot,self.agent.pet.chat);self.editors.append(viewer)
                viewer.finished.connect(lambda _:self.editors.remove(viewer) if viewer in self.editors else None)
                viewer.show();viewer.raise_()
            self.attempt(action)
        elif safe_url(value):QDesktopServices.openUrl(QUrl(value))

    def close(self):
        self.closing=True
        if self.worker:self.worker.cancel.set();self.worker.wait(15000)
        for editor in list(self.editors):
            if hasattr(editor,'stash_draft'):editor.stash_draft()
            editor.deleteLater()
        self.editors.clear()
        if self.window:self.window.close()
