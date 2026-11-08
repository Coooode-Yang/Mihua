"""Observe Windows display power, suspend, and lock state through a hidden HWND.

https://learn.microsoft.com/en-us/windows/win32/power/power-setting-guids
https://learn.microsoft.com/en-us/windows/win32/power/registering-for-power-events
"""
import ctypes
from ctypes import wintypes
import os
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from .growth_focus import desktop_locked

DISPLAY_GUID = uuid.UUID('6fe69556-704a-47a0-8f24-c28d936fda47').bytes_le


class ScreenActivity(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.display_on = None
        self.suspended = False
        self.notification = None
        self.problem = ''
        if os.name != 'nt' or QApplication.platformName() != 'windows':
            self.problem = '屏幕状态监测仅支持 Windows 桌面环境。'
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        self.user.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        self.user.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
        self.user.UnregisterPowerSettingNotification.restype = wintypes.BOOL
        guid = ctypes.create_string_buffer(DISPLAY_GUID)
        self.notification = self.user.RegisterPowerSettingNotification(int(self.winId()), guid, 0)
        if not self.notification:
            self.problem = '暂时无法读取屏幕状态，久坐计时已暂停。请重启米花后再试。'

    @property
    def active(self):
        return self.display_on is True and not self.suspended and not desktop_locked()

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x218:  # WM_POWERBROADCAST
            if msg.wParam == 0x8013 and msg.lParam:  # PBT_POWERSETTINGCHANGE
                address = msg.lParam
                if ctypes.string_at(address, 16) == DISPLAY_GUID:
                    length = wintypes.DWORD.from_address(address + 16).value
                    if length == 4:
                        value = wintypes.DWORD.from_address(address + 20).value
                        self.display_on = value in (1, 2)  # On or dimmed, never off.
            elif msg.wParam == 4:  # PBT_APMSUSPEND
                self.suspended = True
            elif msg.wParam in (7, 18):  # Resume suspend / automatic
                self.suspended = False
            return True, 1
        return super().nativeEvent(event_type, message)

    def shutdown(self):
        if self.notification:
            self.user.UnregisterPowerSettingNotification(self.notification)
            self.notification = None
        self.close()
