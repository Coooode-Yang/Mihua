"""API configuration without developer tools or keys in the chat history."""
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFormLayout,QHBoxLayout,
                              QLabel,QLineEdit,QPushButton,QVBoxLayout)

from .credentials import validate_api_key
from .settings import DEEPSEEK_MODEL,DEEPSEEK_URL


class ApiSettingsDialog(QDialog):
    save_requested=Signal(object,str,bool)
    test_requested=Signal(object,str)

    def __init__(self,settings,store,parent=None):
        super().__init__(parent)
        self.original=settings;self.store=store
        self.setWindowTitle('米花 · 模型 API 设置');self.resize(540,470)
        layout=QVBoxLayout(self)
        form=QFormLayout()
        self.provider=QComboBox();self.provider.addItem('DeepSeek 官方','deepseek');self.provider.addItem('自定义 OpenAI 兼容接口','compatible')
        self.provider.setCurrentIndex(self.provider.findData(settings.api_provider))
        self.url=QLineEdit(settings.api_base_url)
        self.model=QLineEdit(settings.model if settings.model_backend=='api' else DEEPSEEK_MODEL)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setMaxLength(4096)
        self.key.setPlaceholderText('填写 API Key；已保存时留空可继续使用')
        self.show_key=QCheckBox('显示本次输入的 Key')
        self.show_key.toggled.connect(lambda checked:self.key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        self.remember=QCheckBox('记住 Key（仅当前 Windows 用户可解密）');self.remember.setChecked(True)
        self.proxy=QCheckBox('API 请求使用系统环境代理');self.proxy.setChecked(settings.api_use_system_proxy)
        self.mode=QComboBox();self.mode.addItem('真实检查本机网络','real');self.mode.addItem('课程演示（模拟数据）','mock')
        self.mode.setCurrentIndex(self.mode.findData(settings.tools_mode))
        for label,widget in [('服务商',self.provider),('API 地址',self.url),('模型名称',self.model),('API Key',self.key),('网络检查',self.mode)]:form.addRow(label,widget)
        layout.addLayout(form)
        for widget in (self.show_key,self.remember,self.proxy):layout.addWidget(widget)
        self.status=QLabel('先测试连接，成功后点击“保存”。测试会发起两次小额 API 请求，不读取你的网络信息。')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QHBoxLayout()
        self.test_button=QPushButton('测试连接与工具调用');self.test_button.clicked.connect(self.request_test)
        self.clear_button=QPushButton('清除已保存的 Key');self.clear_button.clicked.connect(self.clear_key)
        actions.addWidget(self.test_button);actions.addWidget(self.clear_button);layout.addLayout(actions)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        self.buttons.accepted.connect(self.request_save);self.buttons.rejected.connect(self.close);layout.addWidget(self.buttons)
        self.provider.currentIndexChanged.connect(self.switch_provider)
        self.url.setReadOnly(self.provider.currentData()=='deepseek')

    def switch_provider(self):
        if self.provider.currentData()=='deepseek':self.url.setText(DEEPSEEK_URL);self.model.setText(DEEPSEEK_MODEL)
        self.url.setReadOnly(self.provider.currentData()=='deepseek')
        self.key.clear()

    def values(self):
        options=replace(self.original,model_backend='api',api_provider=self.provider.currentData(),
                        api_base_url=self.url.text().strip(),model=self.model.text().strip(),
                        tools_mode=self.mode.currentData(),api_use_system_proxy=self.proxy.isChecked()).validate()
        key=self.key.text().strip() or self.store.load(options.api_base_url)
        return options,validate_api_key(key)

    def request_test(self):
        try:options,key=self.values()
        except Exception as exc:self.status.setText(str(exc));return
        self.test_requested.emit(options,key)

    def request_save(self):
        try:options,key=self.values()
        except Exception as exc:self.status.setText(str(exc));return
        self.save_requested.emit(options,key,self.remember.isChecked())

    def clear_key(self):
        try:self.store.clear()
        except OSError:self.status.setText('Key 没有清除成功，请检查文件夹是否可写。');return
        self.key.clear();self.status.setText('已清除本次会话和本机保存的 Key，其他设置不变。')

    def set_busy(self,busy):
        for widget in (self.provider,self.url,self.model,self.key,self.show_key,self.remember,self.proxy,self.mode,self.test_button,self.clear_button,
                       self.buttons.button(QDialogButtonBox.StandardButton.Save)):
            widget.setEnabled(not busy)
