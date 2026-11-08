from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import ipaddress
import re

DEEPSEEK_URL='https://api.deepseek.com'
DEEPSEEK_MODEL='deepseek-flash'


def normalize_api_url(value):
    try:
        if not isinstance(value,str) or len(value)>2048 or re.search(r'[\s\\\x00-\x1f]',value):raise ValueError()
        p=urlsplit(value.strip().rstrip('/'))
        port=p.port
        if p.scheme not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:raise ValueError()
        local=p.hostname=='localhost'
        try:local=local or ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:pass
        if p.scheme=='http' and not local:raise ValueError()
        path=p.path.removesuffix('/chat/completions').rstrip('/')
        return urlunsplit((p.scheme,p.netloc,path,'',''))
    except (ValueError,TypeError):
        raise ValueError('API 地址需使用 HTTPS，且不能包含账号、密码或查询参数；本机接口可以使用 HTTP。') from None


def application_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    model: str = DEEPSEEK_MODEL
    model_backend: str = 'api'
    api_provider: str = 'deepseek'
    api_base_url: str = DEEPSEEK_URL
    api_use_system_proxy: bool = False
    tools_mode: str = "real"
    scenario_id: str = "case_02_portal"
    context_size: int = 8192
    response_tokens: int = 700
    keep_alive: str = "2m"
    request_timeout_s: int = 180

    def validate(self):
        if self.model_backend not in {'api','local'} or self.tools_mode not in {'real','mock'}:
            raise ValueError('不支持的模型或工具模式')
        if not isinstance(self.model,str) or not self.model.strip() or len(self.model)>160 or re.search(r'[\s\x00-\x1f]',self.model):
            raise ValueError('请填写服务商提供的模型名称。')
        if self.model_backend=='local' and self.model not in {'qwen3:4b','qwen3:1.7b'}:raise ValueError('不支持的本地模型')
        if self.api_provider not in {'deepseek','compatible'} or type(self.api_use_system_proxy) is not bool:raise ValueError('API 设置无效')
        self.api_base_url=normalize_api_url(self.api_base_url)
        if self.api_provider=='deepseek' and self.api_base_url not in {DEEPSEEK_URL,DEEPSEEK_URL+'/v1'}:
            raise ValueError('DeepSeek 预设使用官方 API 地址；其他服务请选“自定义兼容接口”。')
        if type(self.context_size) is not int or not 2048<=self.context_size<=16384 or type(self.response_tokens) is not int or not 64<=self.response_tokens<=1500:
            raise ValueError('模型配置超出范围')
        if type(self.request_timeout_s) is not int or not 10<=self.request_timeout_s<=300:raise ValueError('模型超时配置超出范围')
        return self

    @classmethod
    def load(cls, base: Path) -> "Settings":
        path = base / "data" / "settings.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data,dict):raise ValueError('设置格式无效')
            if 'model_backend' not in data and data.get('model') in {'qwen3:4b','qwen3:1.7b'}:
                data['model_backend']='local'  # explicit legacy development support
            result = cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})
            return result.validate()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("设置文件损坏，请在 data/settings.json 中恢复有效配置。") from exc

    def save(self, base: Path):
        self.validate()
        target = base / "data" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
