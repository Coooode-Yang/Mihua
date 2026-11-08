"""Bounded readonly background checks, debounced transitions, no model/writes."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict,dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject,QTimer,Signal

from .bridge import ToolBridge
from .settings import Settings

STATE_LABELS={'starting':'等待首次检查','link_up':'本机已连接，外网待确认','healthy':'受测联网端点可达',
              'disconnected':'未观察到已连接的网络','portal':'可能需要校园认证',
              'limited':'外网检查持续异常','unknown':'当前观测不足'}
ABNORMAL={'disconnected','portal','limited'}


@dataclass
class GuardianSettings:
    enabled:bool=True
    interval_s:int=30
    internet_checks:bool=True
    internet_interval_s:int=120
    failures_required:int=2
    cooldown_s:int=600

    @classmethod
    def load(cls,base):
        path=Path(base)/'data/guardian.json'
        if not path.exists():return cls(),''
        try:
            return cls.validated(json.loads(path.read_text(encoding='utf-8'))),''
        except (OSError,ValueError,TypeError):
            # Do not enable unsolicited monitoring after a malformed setting.
            return cls(enabled=False),'守护配置读取失败，已暂停；可在网络守护窗口重新保存。'

    @classmethod
    def validated(cls,data):
        if not isinstance(data,dict):raise ValueError('守护设置格式无效。')
        result=cls(**{k:v for k,v in data.items() if k in cls.__dataclass_fields__})
        if type(result.enabled) is not bool or type(result.internet_checks) is not bool:
            raise ValueError('守护开关格式无效。')
        for value,low,high in [(result.interval_s,15,300),(result.internet_interval_s,60,1800),
                               (result.failures_required,2,5),(result.cooldown_s,60,3600)]:
            if type(value) is not int or not low<=value<=high:raise ValueError('守护间隔设置超出范围。')
        return result

    def save(self,base):
        validated=self.validated(asdict(self))
        path=Path(base)/'data/guardian.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(asdict(validated),ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)


def classify(snapshot,probe=None):
    """Return bounded observations. Tool policy errors never mean offline."""
    if not snapshot or snapshot.get('mode')!='real' or snapshot.get('error') or not snapshot.get('success'):
        return {'state':'unknown','detail':'未取得有效的本机连接快照，不能据此判定断网。'}
    data=snapshot.get('data') or {}
    interfaces=[data.get('selected_interface') or {},*(data.get('candidate_interfaces') or [])]
    if not any(i.get('status')=='connected' for i in interfaces):
        known=[i for i in interfaces if i]
        if any(i.get('status') not in {'disconnected','down','disabled','not_present'} for i in known) or (not known and 'candidate_interfaces' not in data):
            return {'state':'unknown','detail':'接口状态或接口列表尚不完整，不能把未知状态当成断网。'}
        return {'state':'disconnected','detail':'本轮未观察到已连接接口；打开 Wi-Fi 与完成校园认证是不同步骤。'}
    if probe is None:
        return {'state':'link_up','detail':'系统有已连接接口；当前尚未取得新的外网端点观测。'}
    if probe.get('mode')!='real' or probe.get('error'):
        return {'state':'unknown','detail':'网页检查未完成或被本地工具限制；不能据此声称网站封禁或网络已断。'}
    attempts=(probe.get('data') or {}).get('attempts') or []
    if any(a.get('expected_signature_matched') is True for a in attempts):
        return {'state':'healthy','detail':'受测端点返回预期响应；只代表这些端点与本次路径，不保证所有网站正常。'}
    if any(a.get('portal_state') in {'confirmed','suspected'} for a in attempts):
        return {'state':'portal','detail':'联网端点出现认证跳转或内容变化；需进一步核对认证地址，不自动打开未知跳转。'}
    # Two independent fallback endpoints must have been attempted before calling
    # the external check abnormal. A partial/budget-limited check stays unknown.
    targets={a.get('probed_url') for a in attempts if a.get('probed_url')}
    if len(targets)>=2:
        return {'state':'limited','detail':'两个受测联网端点均未获得预期响应；可能涉及链路、认证、DNS 或代理，尚未确定根因。'}
    return {'state':'unknown','detail':'受测端点证据不足，未把单个检查失败当成整机断网。'}


class GuardianDebouncer:
    def __init__(self,settings:GuardianSettings):
        self.settings=settings
        self.pending=None
        self.count=0
        self.stable=None
        self.last_alert=float('-inf')
        self.last_sample_id=None
        self.unnotified_problem=False

    def observe(self,observation,now):
        state=observation['state']
        # A cached HTTP failure must not count as two independent failures.
        sample_id=observation.get('sample_id')
        if sample_id is not None and sample_id==self.last_sample_id:return None
        self.last_sample_id=sample_id
        if state not in ABNORMAL|{'healthy','link_up'}:
            self.pending=None
            self.count=0
            return None
        if state==self.pending:self.count+=1
        else:self.pending,self.count=state,1
        if self.count<self.settings.failures_required:return None
        old=self.stable
        if state==self.stable:
            if not (state in ABNORMAL and self.unnotified_problem):return None
        else:
            self.stable=state
            self.unnotified_problem=state in ABNORMAL
        if state in ABNORMAL:
            if now-self.last_alert<self.settings.cooldown_s:return None
            self.last_alert=now
            self.unnotified_problem=False
            return {'kind':'problem','state':state,'message':STATE_LABELS[state]+'。'+observation['detail']}
        if old in ABNORMAL:
            return {'kind':'recovery','state':state,
                    'message':('受测端点已连续恢复预期响应。' if state=='healthy' else '本机接口已连续显示连接；外网是否恢复仍待检查。')}
        return None


class GuardianSampler:
    def __init__(self,settings,bridge_factory=None):
        self.settings=settings
        self.bridge_factory=bridge_factory or self._bridge
        self.cached_probe=None
        self.probe_at=float('-inf')
        self.sequence=0
        self.probe_sequence=0

    @staticmethod
    def _bridge():
        from netpet_tools import ToolConfig,ToolService
        return ToolBridge(Settings(tools_mode='real'),ToolService(ToolConfig(mode='real',allow_writes=False)))

    def sample(self,force=False):
        now=time.monotonic()
        bridge=self.bridge_factory()
        functions={f.__name__:f for f in bridge.functions(lambda _:None)}
        snapshot=functions['get_network_snapshot'](include_recent_events=False)
        local=classify(snapshot)
        if local['state']=='disconnected':
            self.cached_probe=None
            self.probe_at=float('-inf')
        elif local['state']=='link_up' and self.settings.internet_checks:
            if force or now-self.probe_at>=self.settings.internet_interval_s:
                self.cached_probe=functions['probe_connectivity'](target_url=None,route='auto',timeout_s=3)
                self.probe_at=now
                self.probe_sequence+=1
        observation=classify(snapshot,self.cached_probe if self.settings.internet_checks else None)
        self.sequence+=1
        observation.update(checked_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                           sample_id=('http',self.probe_sequence) if observation['state'] in {'healthy','limited','portal'} else ('local',self.sequence),
                           probe_age_s=round(now-self.probe_at,1) if self.cached_probe else None,
                           mode='real',read_only=True,model_started=False)
        if observation['probe_age_s'] is not None and observation['probe_age_s']>1:
            observation['detail']+=' 外网观测距今约 '+str(round(observation['probe_age_s']))+' 秒；本机连接为本轮读取。'
        return observation


class NetworkGuardian(QObject):
    updated=Signal(dict,object)
    _sample_ready=Signal(int,dict)

    def __init__(self,base,can_poll,parent=None,sampler_factory=GuardianSampler):
        super().__init__(parent)
        self.base=base
        self.settings,self.problem=GuardianSettings.load(base)
        self.can_poll=can_poll
        self.sampler_factory=sampler_factory
        self.sampler=sampler_factory(self.settings)
        self.policy=GuardianDebouncer(self.settings)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.poll)
        self._sample_ready.connect(self._finish)
        self.generation=0
        self.inflight=False
        self.closed=False
        self.last_observation=None
        self.history=[]

    def start(self):
        if self.settings.enabled and not self.closed:
            self.timer.start(self.settings.interval_s*1000)
            QTimer.singleShot(8000,self.poll)

    def configure(self,settings,persist=True):
        clean=GuardianSettings.validated(asdict(settings))
        if persist:clean.save(self.base)
        self.timer.stop()
        self.settings=clean
        self.generation+=1
        self.sampler=self.sampler_factory(clean)
        self.policy=GuardianDebouncer(clean)
        self.last_observation=None
        self.problem=''
        self.start()

    def poll(self,force=False):
        if self.closed or self.inflight or not self.settings.enabled or not self.can_poll():return False
        self.inflight=True
        generation,sampler=self.generation,self.sampler
        def worker():
            try:
                observation=sampler.sample(force=force)
            except Exception as exc:
                observation={'state':'unknown','detail':'本轮守护检查未完成（'+type(exc).__name__+'），未据此判定断网。',
                             'checked_at':datetime.now().astimezone().isoformat(timespec='seconds')}
            if not self.closed:
                try:self._sample_ready.emit(generation,observation)
                except RuntimeError:pass  # window may have been destroyed during exit
        threading.Thread(target=worker,name='westie-guardian',daemon=True).start()
        return True

    def _finish(self,generation,observation):
        self.inflight=False
        if self.closed or generation!=self.generation or not self.settings.enabled:return
        # If the foreground changed mode while this sample was running, discard.
        if not self.can_poll():return
        self.last_observation=observation
        event=self.policy.observe(observation,time.monotonic())
        self.history.append(observation)
        self.history=self.history[-50:]
        self.updated.emit(observation,event)

    def close(self):
        self.closed=True
        self.timer.stop()
        self.generation+=1
