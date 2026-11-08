"""Independent reader jobs; chat, reminders and PDF extraction stay responsive."""
from dataclasses import replace
from pathlib import Path
import re
import threading

from PySide6.QtCore import QObject, QThread, Signal

from .paper_reader import ReaderCancelled, ReaderGenerator, ReaderModel
from .reader_store import ReaderStore
from .runtime import SetupRequired


def requested_title(command):
    tail=re.split(r'(?:阅读|解读|精读)',command,maxsplit=1)[-1].strip(' ：:。！!《》“”"\'')
    return '' if tail in {'','这篇','这篇文献','这篇论文','当前文献','当前论文','选中的文献','选中的论文'} else tail


class ReaderWorker(QThread):
    progress=Signal(str)
    selected=Signal(object,object)
    completed=Signal(object)

    def __init__(self,owner,library,settings,paper,directory,command):
        super().__init__(owner)
        self.owner,self.library,self.settings=owner,library,settings
        self.paper,self.directory,self.command=paper,directory,command
        self.cancel=threading.Event()

    def run(self):
        try:
            if self.settings.model_backend!='api':
                raise SetupRequired('文献解读目前使用 API 模型，请先在“模型设置”选择并配置在线模型。')
            generator=ReaderGenerator(self.owner.agent.base)
            guidance=generator.guidance()
            model=self.owner.create_model(self.settings,guidance)
            query=requested_title(self.command)
            if self.paper is None or query:
                snapshot=self.library.recent(self.cancel)
                papers=snapshot.papers
                if query:
                    normalized=re.sub(r'\s+','',query).casefold()
                    papers=[p for p in papers if normalized in re.sub(r'\s+','',p.title).casefold()]
                    if len(papers)!=1:
                        raise ValueError('没有找到唯一匹配的文献。请先在研究页选中目标文献，再说“使用 nature-reader 阅读这篇文献”。')
                if not papers: raise ValueError('还没有可用的最近阅读记录，请先在研究页选择 PDF 文献。')
                self.paper,self.directory=papers[0],snapshot.directory
            if self.cancel.is_set():raise ReaderCancelled()
            pdf=self.library.resolve_pdf(self.paper,self.directory)
            self.selected.emit(self.paper,self.directory)
            outcome=generator.run(pdf,self.paper,self.directory,model,self.cancel,self.progress.emit)
            self.completed.emit(outcome)
        except ReaderCancelled:
            self.completed.emit({'cancelled':True})
        except (OSError,ValueError,SetupRequired) as exc:
            self.completed.emit({'error':str(exc) if not isinstance(exc,OSError) else '无法读取或保存解读文件，请检查目录权限和磁盘空间。'})
        except Exception:
            self.completed.emit({'error':'这次解读未完成，请检查 PDF 或模型设置后重试。'})


class PaperReaderController(QObject):
    def __init__(self,agent):
        super().__init__(agent)
        self.agent=agent
        self.store=ReaderStore(agent.base)
        self.page=None;self.worker=None;self.paper=None;self.directory=None
        self.message='';self.viewers=[]
        self.model_factory=None

    def create_model(self,settings,guidance):
        if self.model_factory:return self.model_factory(settings,guidance)
        key=self.agent.credentials.load(settings.api_base_url)
        if not key:raise SetupRequired('还没配置 API Key，请先在“工具箱 → 模型设置”中填写。')
        return ReaderModel(settings,key,guidance)

    def attach(self,page):
        self.page=page
        page.generate_reader.clicked.connect(lambda:self.start())
        page.view_reader.clicked.connect(self.view_selected)
        page.cancel_reader.clicked.connect(self.cancel_job)
        self.update_page()

    def start(self,command='使用 nature-reader 阅读这篇文献'):
        if self.worker is not None:
            self.agent.pet.chat.add_message('米花','已有一篇文献正在解读，可在研究页查看进度或取消。');return
        page=self.page
        paper=page.selected_paper() if page else None
        directory=page.snapshot_directory if page else None
        self.paper,self.directory=None,None
        self.message='正在准备 nature-reader…'
        worker=ReaderWorker(self,self.agent.personal.zotero_library,replace(self.agent.settings),paper,directory,command)
        self.worker=worker
        worker.progress.connect(self.progress)
        worker.selected.connect(self.selected)
        worker.completed.connect(self.completed)
        worker.finished.connect(lambda:self.release(worker))
        self.update_page();worker.start()
        self.agent.personal.show_page('research')

    def selected(self,paper,directory):
        self.paper,self.directory=paper,Path(directory)
        self.agent.pet.chat.add_message('米花','开始按 nature-reader 解读《'+paper.title+'》。完成后可在研究页对应文献中查看 MD。')
        self.update_page()

    def progress(self,message):
        self.message=message;self.update_page()

    def completed(self,result):
        if isinstance(result,dict):
            self.message='已取消解读。' if result.get('cancelled') else result.get('error','解读未完成。')
        elif result.artifact:
            self.message=('已取消，已保留解读草稿。' if result.cancelled else '解读未完成，已保留草稿：'+result.error if result.error else
                          '解读已生成，可在对应文献中查看。'+('部分内容需核对。' if result.artifact.metadata['status']=='draft' else ''))
            self.message+='\n《'+self.paper.title+'》'
        else:self.message='本次未生成解读文件。'
        self.agent.pet.chat.add_message('米花',self.message)
        self.update_page()

    def release(self,worker):
        if self.worker is worker:self.worker=None
        worker.deleteLater();self.update_page()

    def update_page(self):
        if self.page:
            self.page.reader_status.setText(self.message)
            self.page.reader_status.setVisible(bool(self.message))
            self.page.cancel_reader.setVisible(self.worker is not None)
            self.page.cancel_reader.setEnabled(self.worker is not None and not self.worker.cancel.is_set())
            self.page.refresh_reader_badges()

    def cancel_job(self):
        if self.worker:
            self.worker.cancel.set();self.message='正在取消，已完成的逐段译文会保存为草稿。';self.update_page()

    def view_selected(self):
        if not self.page or not self.page.selected_paper():return
        paper=self.page.selected_paper();directory=self.page.snapshot_directory
        if not self.store.latest(directory,paper):return
        from .reader_ui import ReaderDialog
        dialog=ReaderDialog(self.store,directory,paper,self.agent.pet.chat,journal=self.agent.journal)
        self.viewers.append(dialog)
        dialog.finished.connect(lambda _:self.viewers.remove(dialog) if dialog in self.viewers else None)
        dialog.show();dialog.raise_()

    def close(self):
        if self.worker:
            self.worker.cancel.set();self.worker.wait(15000)
        for viewer in list(self.viewers):viewer.close()
