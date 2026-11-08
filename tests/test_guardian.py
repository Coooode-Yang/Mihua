import json
import threading
import time
from dataclasses import replace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from westie_agent.guardian import GuardianSettings,GuardianDebouncer,GuardianSampler,NetworkGuardian,classify


def snapshot(connected=True):
    return {'success':True,'mode':'real','data':{'selected_interface':{'status':'connected' if connected else 'disconnected'}}}


def probe(success=True):
    return {'mode':'real','data':{'attempts':[{'probed_url':u,'expected_signature_matched':success,'outcome':'response' if success else 'timeout'} for u in ['http://one.test/204','http://two.test/204']]}}


def observation(state,sample_id):return {'state':state,'detail':'test fact','sample_id':sample_id}


def test_debounce_no_transient_alert_and_no_repeated_alerts_for_stable_failure():
    policy=GuardianDebouncer(GuardianSettings())
    assert policy.observe(observation('disconnected',1),0) is None
    assert policy.observe(observation('healthy',2),30) is None
    assert policy.observe(observation('disconnected',3),60) is None
    assert policy.observe(observation('disconnected',4),90)['kind']=='problem'
    assert policy.observe(observation('disconnected',5),1000) is None
    assert policy.observe(observation('healthy',6),1030) is None
    assert policy.observe(observation('healthy',7),1060)['kind']=='recovery'


def test_cached_http_failure_does_not_count_twice_and_cooldown_spans_flapping():
    policy=GuardianDebouncer(GuardianSettings())
    assert policy.observe(observation('limited',('http',1)),0) is None
    assert policy.observe(observation('limited',('http',1)),30) is None
    assert policy.observe(observation('limited',('http',2)),120)['kind']=='problem'
    policy.observe(observation('healthy',3),150)
    assert policy.observe(observation('healthy',4),180)['kind']=='recovery'
    policy.observe(observation('disconnected',5),210)
    assert policy.observe(observation('disconnected',6),240) is None
    assert policy.observe(observation('disconnected',7),750)['kind']=='problem'
    assert policy.observe(observation('disconnected',8),1500) is None


def test_unknown_breaks_consecutive_failure_evidence():
    policy=GuardianDebouncer(GuardianSettings())
    policy.observe(observation('disconnected',1),0)
    policy.observe(observation('unknown',2),30)
    assert policy.observe(observation('disconnected',3),60) is None
    assert policy.observe(observation('disconnected',4),90)['kind']=='problem'


def test_error_or_partial_single_target_does_not_imply_machine_offline():
    assert classify({'mode':'real','success':False,'error':{'code':'DENIED'}})['state']=='unknown'
    assert classify(snapshot(),{'mode':'real','error':{'code':'PERMISSION_DENIED'}})['state']=='unknown'
    one=probe(False);one['data']['attempts']=one['data']['attempts'][:1]
    assert classify(snapshot(),one)['state']=='unknown'
    assert classify(snapshot(),probe(False))['state']=='limited'
    assert classify(snapshot(),probe())['state']=='healthy'
    mock=snapshot();mock['mode']='mock'
    assert classify(mock,probe())['state']=='unknown'
    assert classify({'success':True,'mode':'real','data':{}})['state']=='unknown'
    unknown=snapshot();unknown['data']['selected_interface']['status']='unknown'
    assert classify(unknown)['state']=='unknown'


def test_other_connected_interface_prevents_false_wifi_disconnect_alert():
    snap=snapshot(False)
    snap['data']['candidate_interfaces']=[{'status':'connected','name':'Ethernet'}]
    assert classify(snap)['state']=='link_up'
    assert classify(snapshot(),{'mode':'real','data':{'attempts':[{'portal_state':'suspected'}]}})['state']=='portal'


def test_sampler_dedicated_bridge_caches_probe_and_disconnect_invalidates_cache(monkeypatch):
    calls=[]
    connected=[True]
    clock=[0]
    monkeypatch.setattr('westie_agent.guardian.time.monotonic',lambda:clock[0])
    class Bridge:
        def functions(self,callback):
            def get_network_snapshot(include_recent_events=False):
                calls.append('snapshot');assert not include_recent_events;return snapshot(connected[0])
            def probe_connectivity(target_url=None,route='auto',timeout_s=3):
                calls.append('probe');assert target_url is None and route=='auto';return probe()
            return [get_network_snapshot,probe_connectivity]
    sampler=GuardianSampler(GuardianSettings(),lambda:Bridge())
    first=sampler.sample();clock[0]=30;second=sampler.sample()
    assert first['sample_id']==second['sample_id'] and calls.count('probe')==1
    connected[0]=False;clock[0]=60
    assert sampler.sample()['state']=='disconnected'
    connected[0]=True;clock[0]=90
    assert sampler.sample()['state']=='healthy' and calls.count('probe')==2
    assert first['read_only'] and not first['model_started']


def test_local_only_guardian_never_calls_external_probe():
    class Bridge:
        def functions(self,callback):
            def get_network_snapshot(**kwargs):return snapshot()
            return [get_network_snapshot]
    assert GuardianSampler(GuardianSettings(internet_checks=False),lambda:Bridge()).sample()['state']=='link_up'


def test_settings_persist_pause_and_invalid_config_never_enables_guardian(tmp_path):
    options=GuardianSettings(enabled=False,interval_s=45)
    options.save(tmp_path)
    assert GuardianSettings.load(tmp_path)==(options,'')
    (tmp_path/'data/guardian.json').write_text('{broken',encoding='utf-8')
    settings,warning=GuardianSettings.load(tmp_path)
    assert not settings.enabled and warning
    with pytest.raises(ValueError):GuardianSettings.validated({'interval_s':True})


def test_background_check_does_not_block_and_pause_discards_late_result(tmp_path):
    app=QApplication.instance() or QApplication([])
    entered,release=threading.Event(),threading.Event()
    class Sampler:
        def __init__(self,s):pass
        def sample(self,force=False):
            entered.set();assert release.wait(3);return observation('disconnected',1)
    guardian=NetworkGuardian(tmp_path,lambda:True,sampler_factory=Sampler)
    received=[];guardian.updated.connect(lambda *args:received.append(args))
    try:
        assert guardian.poll() and entered.wait(1)
        assert not guardian.poll()  # one worker at a time
        guardian.configure(replace(guardian.settings,enabled=False))
        release.set()
        for _ in range(100):
            QTest.qWait(10)
            if not guardian.inflight:break
        assert not received and not guardian.timer.isActive() and not guardian.poll()
    finally:release.set();guardian.close()


def test_foreground_or_mock_gate_prevents_all_background_work(tmp_path):
    app=QApplication.instance() or QApplication([])
    class Forbidden:
        def __init__(self,s):pass
        def sample(self,force=False):pytest.fail('busy/mock gate must not call tools')
    guardian=NetworkGuardian(tmp_path,lambda:False,sampler_factory=Forbidden)
    try:assert not guardian.poll(force=True)
    finally:guardian.close()
