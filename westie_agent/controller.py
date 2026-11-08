from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from .bridge import ToolBridge
from .engine import AgentReply, LocalAgent
from .portable import PortableRuntime
from .runtime import Cancelled, SetupRequired
from .settings import Settings, application_dir
from .campus import CampusCatalog, validated_url
from .campus_ui import CampusDialog
from .credentials import SecretStore
from .api_settings_ui import ApiSettingsDialog
from .api_connection import api_error_message,test_connection


class Events(QObject):
    progress = Signal(str)
    finished = Signal(str, object, int)
    failed = Signal(str, int)


class AgentController(QObject):
    def __init__(self, pet, base: Path | None = None):
        super().__init__(pet)
        self.pet = pet
        self.base = base or application_dir()
        self.settings = Settings.load(self.base)
        self.runtime = PortableRuntime(self.base)
        self.credentials = SecretStore(self.base)
        self.bridge = ToolBridge(self.settings)
        self.agent = LocalAgent(self.settings, self.bridge, allow_network=False)
        self.cancel = threading.Event()
        self.busy = False
        self.conversation_generation = 0
        self.dialog = None
        self.campus_dialog = None
        from .personal import PersonalController
        self.personal = PersonalController(pet, self.base)
        from .reader_controller import PaperReaderController
        self.reader = PaperReaderController(self)
        self.personal.reader = self.reader
        from .journal_controller import JournalController
        self.journal = JournalController(self)
        self.personal.journal = self.journal
        from .file_conversion_ui import FileConversionController
        self.conversion = FileConversionController(pet, self.base)
        self.catalog = CampusCatalog(self.base)
        self.events = Events(self)
        self.events.progress.connect(self.progress)
        self.events.finished.connect(self.receive_finished)
        self.events.failed.connect(self.receive_failed)
        pet.chat.conversation_cleared.connect(self.clear_conversation)
        pet.chat.attachments_submitted.connect(self.submit_attachments)
        pet.agent_dispatch = self.submit
        pet.chat.settings_button.clicked.connect(self.show_settings)
        pet.chat.campus_requested.connect(self.show_campus)
        # Embed chat before any model worker can deliver a reply. The main
        # window owns its widgets even while the desktop pet is the only view.
        from .personal_ui import Workbench
        self.personal.window = Workbench(self.personal)
        from .quick_ask import QuickAskController
        self.quick_ask = QuickAskController(self)
        self.personal.quick_ask = self.quick_ask
        for problem in (self.catalog.problem,):
            if problem:pet.chat.add_message('米花',problem)

    def work(self, kind, function):
        if self.busy:
            return False
        self.busy = True
        generation = self.conversation_generation if kind == 'chat' else -1
        self.pet.chat.set_busy(True)
        self.pet.set_agent_status("thinking" if kind == "setup" else "checking")
        if self.dialog:
            self.dialog.set_busy(True)
        def run():
            try:
                result = function()
                if not self.cancel.is_set():
                    self.events.finished.emit(kind, result, generation)
            except Cancelled:
                pass
            except Exception as exc:
                if not self.cancel.is_set():
                    message = str(exc) if isinstance(exc, (SetupRequired, ValueError)) else "本次处理未完成，请重试；可在“模型设置”检查 API 连接。"
                    self.events.failed.emit(message, generation)
        threading.Thread(target=run, name="westie-" + kind, daemon=True).start()
        return True

    def submit(self, text: str):
        from .paper_reader import is_reader_command
        if is_reader_command(text):
            self.reader.start(text)
            return
        if text.strip().rstrip('。！!') in {'取消解读','停止解读','取消文献解读'}:
            self.reader.cancel_job()
            return
        if text.strip().rstrip('。！!') in {'文件转换', '转换文件', '打开文件转换'}:
            self.conversion.show()
            return
        if self.personal.handle_command(text):
            return
        if text.strip() in {'网络检查','帮我做一次基础网络检查','网络守护','守护设置','暂停网络守护',
                            '开启网络守护','关闭网络守护','启动网络守护','导出排障报告','查看检查依据'}:
            self.pet.chat.add_message('米花', '个人版已移除网络检查、守护和排障报告。')
            return
        if text.strip() in {'校园入口','带路狗','带路小鸡','打开校园入口','网页入口','网页快捷入口','打开网页入口','打开网页快捷入口'}:
            self.show_campus(); return
        if text.strip() in {'模型设置','API设置','API 设置'}:
            self.show_settings(); return
        entry = self.catalog.match(text)
        if entry:
            self.open_campus_entry(entry['id']); return
        self.submit_chat(text)

    def submit_attachments(self, text, attachments):
        # A question about an attachment never executes a local command, even
        # when its wording happens to match one (e.g. "添加待办：...").
        self.submit_chat(text, tuple(attachments))

    def submit_chat(self, text, attachments=(), *, quick=False, web=False):
        if self.busy:
            self.pet.chat.add_message('米花', '我还在处理上一条，请稍等。')
            return
        self.pet.chat.clear_actions()
        agent = self.agent
        def task():
            options = {'attachments': attachments} if attachments else {}
            if quick:
                options['quick'] = True
            evidence = None
            if web:
                from .web_lookup import lookup
                self.events.progress.emit('正在检索网页摘要…')
                evidence = lookup(text, use_system_proxy=self.settings.api_use_system_proxy)
                options['web_evidence'] = evidence
            if self.settings.model_backend == 'api':
                key = self.credentials.load(self.settings.api_base_url)
                if not key:
                    if evidence:
                        return AgentReply('已检索到以下摘要；配置模型 Key 后可以进一步解释。\n\n'+
                                          '\n\n'.join(s.title+'\n'+s.snippet for s in evidence.sources)+evidence.footer(),
                                          [], [], 'conversation', False)
                    raise SetupRequired('还没配置 API Key。请打开“模型设置”填写；待办、计时和词卡仍可使用。')
                self.events.progress.emit('正在联系模型服务…')
                try:
                    return agent.reply(text, self.settings.api_base_url, self.events.progress.emit, api_key=key, **options)
                except SetupRequired:
                    if not evidence:
                        raise
                    return AgentReply('已获得检索摘要，但模型整理未完成。可以先打开以下来源核对。\n\n'+
                                      '\n\n'.join(s.title+'\n'+s.snippet for s in evidence.sources)+evidence.footer(),
                                      [], [], 'conversation', False)
            self.runtime.ensure_ready(self.settings.model, self.cancel, self.settings.context_size)
            return agent.reply(text, self.runtime.url, self.events.progress.emit, api_key=self.runtime.api_key, **options)
        self.work('chat', task)

    def clear_conversation(self):
        self.conversation_generation += 1
        self.pet.chat.attachment_send_finished(True)
        self.pet.chat.quick_send_finished(True)
        # A response already in flight belongs to the old context. It cannot
        # repopulate the cleared transcript or the next model conversation.
        self.agent = LocalAgent(self.settings, self.bridge, allow_network=False)

    def receive_finished(self, kind, result, generation):
        if generation >= 0 and generation != self.conversation_generation:
            self.reset_busy()
            return
        self.finished(kind, result)

    def receive_failed(self, message, generation):
        if generation >= 0 and generation != self.conversation_generation:
            self.reset_busy()
            return
        self.failed(message)

    def progress(self, text):
        self.pet.chat.set_progress(text)
        if self.busy and text.startswith("检查"):
            self.pet.say(text, 3600)
        if self.dialog:
            self.dialog.status.setText(text)

    def reset_busy(self):
        self.busy = False
        self.pet.chat.set_busy(False)
        self.pet.agent_busy = False
        self.pet.set_state("idle")
        if self.dialog:
            self.dialog.set_busy(False)

    def finished(self, kind, result):
        self.reset_busy()
        if kind == 'chat':
            self.pet.chat.quick_send_finished(True)
            self.pet.chat.attachment_send_finished(True)
            self.pet.chat.add_message('米花', result.text, markdown=True)
            self.pet.say('我整理好啦，看看对话框吧', 4)
        elif kind == 'api_test' and self.dialog:
            self.dialog.status.setText(result['message'] + ' 测试不保存设置，请点击“保存”。')

    def show_campus(self):
        if self.campus_dialog:
            self.campus_dialog.show(); self.campus_dialog.raise_(); self.campus_dialog.activateWindow(); return
        dialog = CampusDialog(self.catalog, self.pet.chat)
        dialog.open_requested.connect(self.open_campus_entry)
        dialog.finished.connect(lambda _: setattr(self, 'campus_dialog', None))
        self.campus_dialog = dialog
        dialog.show()
        dialog.raise_();dialog.activateWindow()

    def open_campus_entry(self, rid):
        entry = self.catalog.entry(rid)
        if not entry:
            return
        url = validated_url(entry['url'])
        opened = QDesktopServices.openUrl(QUrl(url))
        message = '已请求默认浏览器打开“'+entry['name']+'”。' if opened else '未能调用默认浏览器，请复制网址打开：'+url
        self.pet.chat.add_message('米花 · 带路小鸡', message)
        self.pet.say('带你去 '+entry['name'], 5)
        if self.campus_dialog:
            self.campus_dialog.show_status(message)

    def failed(self, message):
        self.reset_busy()
        self.pet.chat.quick_send_finished(False)
        self.pet.chat.attachment_send_finished(False)
        self.pet.chat.add_message("米花", message)
        self.pet.say("这次没完成，看看提示吧", 4)
        if self.dialog:
            self.dialog.status.setText(message)


    def show_settings(self):
        if self.dialog:
            self.dialog.show()
            self.dialog.raise_()
            return
        dialog=ApiSettingsDialog(self.settings,self.credentials,self.pet.chat)
        dialog.save_requested.connect(self.save_api_settings)
        dialog.test_requested.connect(lambda options,key:self.work('api_test',lambda:test_connection(options,key)))
        dialog.finished.connect(lambda _: setattr(self, "dialog", None))
        self.dialog = dialog
        dialog.set_busy(self.busy)
        dialog.show()

    def save_api_settings(self,options,key,remember):
        if self.busy:return
        try:
            self.credentials.save(options.api_base_url,key,remember)
            options.save(self.base)
        except (OSError,ValueError,SetupRequired) as exc:
            if self.dialog:self.dialog.status.setText('设置未能保存：'+str(exc))
            return
        self.runtime.close()
        self.settings=options
        self.bridge=ToolBridge(options);self.agent=LocalAgent(options,self.bridge,allow_network=False)
        self.pet.chat.clear_actions()
        if self.dialog:
            self.dialog.original=options;self.dialog.key.clear()
            self.dialog.status.setText('已保存。现在可以关闭窗口和米花对话，无需下载本地模型。')

    def model_ready(self):
        if self.settings.model_backend=='local':return self.runtime.ready_on_disk(self.settings.model)
        try:return bool(self.credentials.load(self.settings.api_base_url))
        except SetupRequired:return False


    def close(self):
        self.cancel.set()
        self.quick_ask.close()
        self.journal.close()
        self.reader.close()
        self.conversion.close()
        self.personal.close()
        self.pet.chat.set_busy(False)
        self.runtime.close()
