"""API secrets protected for the current Windows user; never in settings.json."""
from __future__ import annotations

import base64
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path

from .runtime import SetupRequired
from .settings import normalize_api_url,DEEPSEEK_URL


class Blob(C.Structure):
    _fields_=[('size',W.DWORD),('data',C.POINTER(C.c_ubyte))]


def _crypt(data:bytes,decrypt=False):
    if os.name!='nt':raise SetupRequired('当前系统不支持记住 Key；可以取消“记住”后仅本次使用。')
    buffer=(C.c_ubyte*len(data)).from_buffer_copy(data)
    source=Blob(len(data),buffer);target=Blob()
    crypto=C.WinDLL('crypt32',use_last_error=True)
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[C.c_void_p];kernel.LocalFree.restype=C.c_void_p
    if decrypt:
        fn=crypto.CryptUnprotectData
        fn.argtypes=[C.POINTER(Blob),C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
    else:
        fn=crypto.CryptProtectData
        fn.argtypes=[C.POINTER(Blob),W.LPCWSTR,C.c_void_p,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
    fn.restype=W.BOOL
    try:
        if not fn(C.byref(source),None,None,None,None,1,C.byref(target)):
            raise SetupRequired('无法读取或保存 API Key，请重新填写；也可以取消“记住”后仅本次使用。')
        return C.string_at(target.data,target.size)
    finally:
        if target.data:kernel.LocalFree(C.cast(target.data,C.c_void_p))


def validate_api_key(value):
    if not isinstance(value,str) or not value.strip() or len(value)>4096 or any(c.isspace() for c in value.strip()):
        raise ValueError('请填写完整的 API Key，不要包含空格或换行。')
    return value.strip()


class SecretStore:
    def __init__(self,base:Path):
        self.path=base/'data/api-key.dpapi.json'
        self._session={}

    def load(self,endpoint):
        endpoint=normalize_api_url(endpoint)
        if endpoint in self._session:return self._session[endpoint]
        if not self.path.exists():return self._environment_key(endpoint)
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('endpoint')!=endpoint:return self._environment_key(endpoint)
            return validate_api_key(_crypt(base64.b64decode(data['ciphertext'],validate=True),decrypt=True).decode('utf-8'))
        except (OSError,ValueError,KeyError,TypeError,UnicodeError):
            raise SetupRequired('已保存的 API Key 无法读取，请在“模型设置”里重新填写。') from None

    @staticmethod
    def _environment_key(endpoint):
        # Never forward the official provider's environment key to an arbitrary
        # user-edited endpoint. Session and endpoint-bound saved keys win.
        if endpoint not in {DEEPSEEK_URL,DEEPSEEK_URL+'/v1'}:return ''
        value=os.environ.get('DEEPSEEK_API_KEY','')
        if not value:return ''
        try:return validate_api_key(value)
        except ValueError:raise SetupRequired('DEEPSEEK_API_KEY 环境变量格式无效，请在本机重新配置。') from None

    def save(self,endpoint,key,remember=True):
        endpoint=normalize_api_url(endpoint);key=validate_api_key(key)
        if remember:
            payload={'version':1,'endpoint':endpoint,'ciphertext':base64.b64encode(_crypt(key.encode('utf-8'))).decode('ascii')}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8');temporary.replace(self.path)
        else:
            self.path.unlink(missing_ok=True)
        self._session={endpoint:key}

    def clear(self):
        self.path.unlink(missing_ok=True);self._session.clear()
