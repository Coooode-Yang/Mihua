"""Evidence coverage and layered diagnostics; Tools still return observations.

The LLM selects tools first. The host fills required missing observations, and
renders bounded findings from current-turn evidence instead of trusting a draft
that can contradict PAC, IPv6 or HTTP observations. Origins stay auditable.
"""
from __future__ import annotations

import ipaddress
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .probe_compat import normalize_web_target


@dataclass
class DiagnosticRequest:
    question: str
    target_url: str | None = None
    dns_domain: str | None = None
    investigate_loss: bool = False
    ping_count: int = 4

    @property
    def measure_link(self):
        return self.investigate_loss or bool(re.search(r'分层|全面|完整检查|基础网络检查',self.question))


def diagnostic_request(text: str) -> DiagnosticRequest | None:
    # Structured logs are evidence, not additional targets/instructions. Only
    # extract targets from the natural-language portion before a data block.
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith(("{", "[", "```")):
            break
        lines.append(line)
    question = "\n".join(lines).strip() or text[:500]
    # Legal research often contains "代理" or "网络" without a device fault.
    if (re.search(r'法学|法条|民法|刑法|行政法|侵权|判例|表见代理|无权代理|代理权|网络犯罪', question)
            and not re.search(r'帮我检查|帮我诊断|网络检查|打不开|无法访问|断网|掉线|连不上|上不了网|丢包|DNS|Wi-?Fi|VPN|https?://', question, re.I)):
        return None
    if not re.search(r"网络|联网|校园网|教务|网站|网页|浏览器|域名|解析|断网|掉线|丢包|延迟|网速|卡顿|视频断续|认证|代理|wifi|wi-fi|wlan|dns|vpn|https?://|\b403\b|\b502\b", question, re.I):
        return None
    # General educational questions remain ordinary chat, not an unsolicited
    # live diagnosis. Explicit checking/problem reports take the diagnosis path.
    if re.search(r"解释.{0,8}(流程|概念|原理)|什么是|介绍一下|学习.{0,5}知识", question) and not re.search(r"帮我检查|帮我诊断|打不开|无法访问", question):
        return None
    targets = re.findall(r"https?://[^\s<>\"'`，。；！）]+", question, re.I)
    bare = re.search(r"(?<![\w@/])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?::\d{1,5})?(?:/[^\s<>\"'`，。；！）]*)?", question, re.I)
    target = targets[0].rstrip(".,;!)]}") if targets else None
    if not target and bare and re.search(r"网站|网页|访问|打不开|站点", question):
        target = normalize_web_target(bare.group().rstrip(".,;!)]}"))
    try:
        domain = urlsplit(target).hostname if target else (bare.group().split('/')[0].split(':')[0] if bare else None)
    except ValueError:
        domain = None  # original Tools validation supplies the input error
    try:
        if domain:
            ipaddress.ip_address(domain)
            domain = None  # literal targets do not need DNS
    except ValueError:
        pass
    loss = bool(re.search(r"丢包|延迟|卡顿|视频断续|卡得|网络慢|网速慢|rtt|ping", question, re.I))
    return DiagnosticRequest(question, target, domain, loss, 10 if loss else 4)


def _domain(value):
    try:
        return (urlsplit(value if '://' in value else '//'+value).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''


class EvidenceCollector:
    def __init__(self, bridge, request: DiagnosticRequest, on_event=lambda _: None):
        self.bridge, self.request, self.on_event = bridge, request, on_event
        self.functions = {fn.__name__: fn for fn in bridge.functions(on_event)}
        self.missing = []

    def _find(self, name, predicate=lambda t: True):
        return next((t for t in reversed(self.bridge.traces) if t['tool']==name and predicate(t)
                     and (t['result'].get('error') or {}).get('code')!='INVALID_INPUT'), None)

    def _ensure(self, name, predicate=lambda t: True, **arguments):
        found = self._find(name, predicate)
        if found:
            return found['result']
        function = self.functions.get(name)
        if function is None:
            self.missing.append(f"{name}：当前版本未提供该项检查")
            return {}
        self.on_event("检查分层观测，补齐缺少的测量…")
        start = len(self.bridge.traces)
        result = function(**arguments)
        for trace in self.bridge.traces[start:]:
            trace['origin'] = 'host_completion'
        return result

    def complete(self):
        began = time.monotonic()
        snapshot = self._ensure('get_network_snapshot')
        proxy = self._ensure('get_proxy_state')
        iface = (snapshot.get('data') or {}).get('selected_interface') or {}
        proxy_data = proxy.get('data') or {}
        from .offline import disconnected_snapshot
        if disconnected_snapshot(snapshot):
            self.missing.append('电脑当前未连接网络，联网查询和网页测试暂不执行；连接后可重新检查。')
            return {'seconds':round(time.monotonic()-began,3),'missing':self.missing}
        config = getattr(self.bridge.service, 'config', None)
        endpoint = None
        if config and config.probe.probe_endpoints:
            endpoint = config.probe.probe_endpoints[0].url
        domain = self.request.dns_domain or (_domain(endpoint) if endpoint else None)
        if domain:
            compare = self.request.investigate_loss
            self._ensure('query_dns', lambda t: _domain((t.get('arguments') or {}).get('domain',''))==domain.lower()
                         and (t.get('arguments') or {}).get('record_type','A')=='A'
                         and (not compare or (t.get('arguments') or {}).get('compare_public') is True),
                         domain=domain, compare_public=compare)
            if iface.get('has_usable_ipv6') or self.request.target_url:
                self._ensure('query_dns', lambda t: _domain((t.get('arguments') or {}).get('domain',''))==domain.lower()
                             and (t.get('arguments') or {}).get('record_type')=='AAAA',
                             domain=domain, record_type='AAAA', compare_public=False)
        if self.request.target_url:
            target = self.request.target_url
            self._ensure('check_target_routing_rule', lambda t: _domain((t.get('arguments') or {}).get('url_or_domain',''))==_domain(target),
                         url_or_domain=target)
            for route in ('direct','configured_proxy'):
                if route=='configured_proxy' and not proxy_data.get('configured_proxy_url'):
                    # The bridge may discover an already-declared loopback
                    # proxy in real mode. PAC-only configurations are not one.
                    hkcu = proxy_data.get('hkcu_internet_settings') or {}
                    if hkcu.get('auto_config_url') or not hkcu.get('proxy_server'):
                        self.missing.append('固定代理路径：未配置可用固定代理；PAC 不作为静态 HTTP 代理执行')
                        continue
                self._ensure('probe_connectivity', lambda t,r=route: (t.get('arguments') or {}).get('target_url')==target
                             and (t.get('arguments') or {}).get('route','auto')==r,
                             target_url=target, route=route, timeout_s=4)
            # A direct fake-IP guard is not a failed HTTP attempt; also request
            # auto compatibility so the existing local proxy can be observed.
            direct = self._find('probe_connectivity', lambda t: (t.get('arguments') or {}).get('target_url')==target
                                and (t.get('arguments') or {}).get('route')=='direct')
            if direct and ((direct['result'].get('host_note') or {}).get('fake_ip_suspected')):
                self._ensure('probe_connectivity', lambda t: (t.get('arguments') or {}).get('target_url')==target
                             and (t.get('arguments') or {}).get('route','auto')=='auto',
                             target_url=target, route='auto', timeout_s=4)
        else:
            self._ensure('probe_connectivity', lambda t: (t.get('arguments') or {}).get('target_url') is None,
                         target_url=None, route='auto', timeout_s=4)
        if self.request.measure_link:
            gateways = iface.get('default_gateways_ipv4') or []
            explicit = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", self.request.question)
            host = explicit.group() if explicit else (gateways[0] if gateways else None)
            if host:
                self._ensure('measure_latency_and_loss', lambda t: (t.get('arguments') or {}).get('host')==host
                             and (t.get('arguments') or {}).get('count',4)==self.request.ping_count,
                             host=host, count=self.request.ping_count)
            else:
                self.missing.append('链路质量：未取得可测量的 IPv4 网关或明确目标')
        return {'seconds': round(time.monotonic()-began, 3), 'missing': self.missing}


def build_diagnosis(request: DiagnosticRequest, traces: list[dict], mode: str, *, missing=(), model_error=None):
    for n, trace in enumerate(traces,1):
        trace.setdefault('evidence_id', f'E{n}')
        trace.setdefault('origin', 'model')
    def latest(name):
        return next((t for t in reversed(traces) if t['tool']==name and not (t['result'].get('error'))), None)
    snapshot = latest('get_network_snapshot')
    proxy = latest('get_proxy_state')
    iface = ((snapshot or {}).get('result',{}).get('data') or {}).get('selected_interface') or {}
    pdata = ((proxy or {}).get('result',{}).get('data') or {})
    hkcu = pdata.get('hkcu_internet_settings') or {}
    layers, causes, unknown = [], [], list(missing)
    def layer(name, status, facts, refs=()):
        layers.append({'name':name,'status':status,'facts':facts,'evidence_ids':[t['evidence_id'] for t in refs if t]})
    def cause(name, support, against, next_step):
        causes.append({'name':name,'support':support,'against':against,'next_step':next_step,'certainty':'possible'})
    if not iface:
        layer('本机连接', 'unknown', ['未获得可用连接快照；不能判定网络正常。'], [snapshot])
        unknown.append('本机接口与地址状态未确认')
    else:
        state = iface.get('status','unknown')
        facts = [f"接口 {iface.get('name','未知')}："+{'connected':'系统显示已连接','disconnected':'未连接','down':'接口未启用或不可用'}.get(state,'状态未知')]
        wlan = iface.get('wlan') or {}
        signal = wlan.get('signal_quality_pct')
        if signal is not None:
            facts.append(f'无线信号质量 {signal}%；强信号不能排除干扰、丢包或上游故障。')
        if wlan.get('permission_issue') or wlan.get('state')=='location_restricted':
            facts.append('Windows 位置权限限制了无线详情读取；SSID、RSSI 等未知，不据此断定网络被封禁。')
        if signal==100:
            facts.append('100% 是信号质量的边界值，不能反推出精确 RSSI。')
        layer('本机连接','ok' if state=='connected' else 'warning', facts,[snapshot])
        addresses = [x.get('ip') for x in iface.get('ipv4_addresses',[]) if x.get('ip')]
        v6 = [x.get('ip') for x in iface.get('ipv6_addresses',[]) if x.get('ip')]
        facts = ['IPv4：'+('、'.join(addresses) or '未取得'), 'IPv6：'+('、'.join(v6) or '未取得'),
                 'IPv4 网关：'+('、'.join(iface.get('default_gateways_ipv4') or []) or '未取得')]
        if iface.get('has_apipa_ipv4'):
            facts.append('IPv4 存在 APIPA 地址，IPv4 地址分配值得排查；不能单凭它断定所有网络不可用。')
        if iface.get('has_usable_ipv6'):
            facts.append('同时存在可用 IPv6 地址；IPv6 的目标访问仍需单独验证。')
        lease = iface.get('dhcp_lease_valid')
        facts.append('DHCP 租约有效性：'+{True:'观测为有效',False:'观测为无效',None:'未知，未据缺失时间编造有效性'}.get(lease,'未知'))
        layer('地址与路由','warning' if iface.get('has_apipa_ipv4') else 'ok',facts,[snapshot])

    dns = [t for t in traces if t['tool']=='query_dns' and not t['result'].get('error')
           and (not request.dns_domain or _domain((t.get('arguments') or {}).get('domain',''))==request.dns_domain.lower())]
    dns_facts=[]
    for trace in dns:
        data = trace['result'].get('data') or {}
        for query in data.get('queries') or ([data['primary_result']] if data.get('primary_result') else []):
            label='系统 DNS' if query.get('resolver_label')=='system' else '公共 DNS '+','.join(query.get('nameservers') or [])
            dns_facts.append(f"{data.get('domain','目标')} {data.get('record_type','A')}：{label} {query.get('status','未知')}"
                             +(f"，记录 {', '.join(query['records'])}" if query.get('records') else ''))
    layer('域名解析','ok' if dns_facts and all('NOERROR' in x for x in dns_facts) else 'warning' if dns_facts else 'unknown',
          list(dict.fromkeys(dns_facts)) or ['未取得可用 DNS 观测，暂不能判定解析正常。'],dns)
    if any('TIMEOUT' in x for x in dns_facts):
        cause('DNS 查询路径异常', '存在 DNS 查询超时；若其他解析器成功，只能说明这些查询结果有差异。',
              '超时也可能受链路、过滤、VPN/TUN 影响，不能直接断言校园 DNS 服务器故障。','核对具体域名及系统 DNS，结合链路测量复检。')

    http = []
    for trace in traces:
        if trace['tool']!='probe_connectivity':continue
        args=trace.get('arguments') or {}
        if request.target_url and args.get('target_url')!=request.target_url:continue
        if not request.target_url and args.get('target_url') is not None:continue
        data=trace['result'].get('data') or {}
        for attempt in data.get('attempts') or []:
            http.append((trace,data,attempt))
    http_facts=[]
    for trace,data,attempt in http:
        route=data.get('route','unknown')
        name='固定代理' if route=='configured_proxy' else '未显式配置 HTTP 代理的路径' if route=='direct' else '未知路径'
        status=attempt.get('http_status')
        url=attempt.get('probed_url') or request.target_url or '联网检查端点'
        http_facts.append(f'{url}：'+(f'HTTP {status}' if status is not None else f"未取得网页响应（{attempt.get('outcome','未知')}）")+f'，路径：{name}。')
        if attempt.get('candidate_login_url'):
            http_facts.append(f"认证跳转：{attempt['candidate_login_url']}，可信名单核对：{'是' if attempt.get('login_url_trusted') else '未确认'}。")
    errors=[t for t in traces if t['tool']=='probe_connectivity' and t['result'].get('error')
            and (not request.target_url or (t.get('arguments') or {}).get('target_url')==request.target_url)]
    for trace in errors:
        note=trace['result'].get('host_note') or {}
        if note.get('http_request_sent') is False:
            http_facts.append('有一次请求被小鸡的范围/参数检查停止，未发送网页请求；不是网站拒绝或 Windows 权限改变的证据。')
        else:
            http_facts.append('有网页检查未完成：'+str((trace['result'].get('error') or {}).get('code','未知错误'))+'。')
    status_values=[a.get('http_status') for _,_,a in http]
    healthy_http=any(isinstance(s,int) and 200<=s<300 for s in status_values)
    portal=any(a.get('portal_state')=='confirmed' and a.get('login_url_trusted') for _,_,a in http)
    layer('网站响应','warning' if portal or any(s is None or s>=400 for s in status_values) else 'ok' if healthy_http else 'unknown',
          list(dict.fromkeys(http_facts)) or ['尚未获得所需目标的 HTTP 响应，不能判断网页可用。'],[t for t,_,_ in http]+errors)
    if request.target_url:
        stages=[]
        for trace,data,attempt in http:
            route=data.get('route','unknown')
            endpoint='本地代理连接' if route=='configured_proxy' else '本次目标连接'
            tcp=attempt.get('tcp_connect_time_ms')
            tls=attempt.get('tls_handshake_time_ms')
            response=attempt.get('http_status') is not None
            stages.append(f"{route} TCP："+(f'{endpoint}已建立，记录 {tcp} ms' if tcp is not None else '由 HTTP 响应可知传输路径已建立' if response else '未取得建立成功的证据'))
            scheme=urlsplit(request.target_url).scheme.lower()
            detail=str(attempt.get('error_detail') or '')
            if scheme=='https':
                stages.append(f'{route} TLS：'+(f'握手完成，记录 {tls} ms' if tls is not None else '收到 HTTPS 响应，握手已完成（由响应推断，未独立测量证书详情）' if response else 'TLS/证书验证失败；未关闭证书校验' if re.search(r'SSL|TLS|certificate',detail,re.I) else '未知，未取得握手成功的证据'))
            else:
                stages.append(f'{route} TLS：该目标为 HTTP，不适用。')
        # HTTP rejection is an application-layer result, not a transport
        # failure: a 403 can still demonstrate a completed HTTPS handshake.
        transport_status = ('ok' if http and all(a.get('http_status') is not None for _,_,a in http)
                            else 'warning' if any(a.get('outcome')=='tls_error' or re.search(r'SSL|TLS|certificate',str(a.get('error_detail') or ''),re.I) for _,_,a in http)
                            else 'unknown')
        layer('TCP / TLS',transport_status,list(dict.fromkeys(stages)) or ['请求尚未取得传输阶段证据；未作独立端口扫描或证书详情读取。'],[t for t,_,_ in http])

    proxy_facts=[]
    if proxy:
        enabled=hkcu.get('proxy_enable')
        proxy_facts.append('Windows 当前用户手动代理：'+{True:'开启',False:'关闭',None:'未知'}.get(enabled,'未知')+'；它不能代表所有程序或 TUN 流量。')
        pac=hkcu.get('auto_config_url')
        proxy_facts.append('PAC 自动配置地址：'+str(pac)+'。尚未执行 PAC，不能推断浏览器实际所走代理。' if pac else '未读到 PAC 自动配置地址。')
        client=pdata.get('configured_client') or {}
        proxy_facts.append(f"代理客户端模式：{client.get('mode','unknown')}；TUN：{client.get('tun_state','unknown')}。")
    rules=[t for t in traces if t['tool']=='check_target_routing_rule' and not t['result'].get('error')
           and request.target_url and _domain((t.get('arguments') or {}).get('url_or_domain',''))==_domain(request.target_url)]
    policy=((rules[-1]['result'].get('data') or {}).get('expected_policy')) if rules else None
    if policy:
        proxy_facts.append('已配置的目标规则：'+{'direct':'直连','proxy':'代理','unknown':'未匹配明确规则'}.get(policy,str(policy))+'。规则不是实际流量路径证明。')
    layer('代理与规则','warning' if hkcu.get('auto_config_url') else 'ok' if proxy else 'unknown',proxy_facts or ['未取得代理配置。'],[proxy]+rules)

    direct=[a.get('http_status') for _,d,a in http if d.get('route')=='direct']
    proxied=[a.get('http_status') for _,d,a in http if d.get('route')=='configured_proxy']
    if portal:
        cause('校园认证尚待完成','收到 HTTP 302 等重定向，并指向已核对的可信认证页面。',
              '无线关联成功与信号良好不能替代账号认证；尚未验证登录已完成。','核对认证地址后由用户完成登录，再复检原目标。')
    if 403 in direct and 403 in proxied:
        cause('网站或共同访问路径的访问策略','同一目标在直连与固定代理路径均返回 HTTP 403，已经收到网页响应。',
              '两路径同样拒绝削弱了“仅因固定代理故障”的解释；不能保证关闭代理就能恢复。','核对该网站的账号、登录与授权策略；不据此修改系统权限。')
    elif any(s in {200,204} for s in direct) and any(s and s>=400 for s in proxied):
        cause('目标在代理路径上访问异常',f'同一目标直连返回 {direct}，固定代理路径返回 {proxied}；已配置规则为 {policy or "尚未确认"}。',
              '路径差异支持优先排查代理相关配置，但不证明整个代理服务或所有网站都故障。','依据已确认的域名规则选择访问方式；如需更改，另行取得桌面确认并复检。')
    if hkcu.get('auto_config_url'):
        cause('PAC 生效情况尚不明确','确实读到了 PAC 自动配置地址，而手动代理开关可能同时关闭。',
              '读取配置不等于执行 PAC；缺少域名规则也不等于没有有效系统路由。','核对浏览器对该目标的 PAC 结果，不把 PAC 当固定代理强行执行。')
    if iface.get('has_apipa_ipv4'):
        cause('IPv4 地址获取可能异常','接口存在 APIPA 或缺少 IPv4 网关。',
              '仍可能有可用 IPv6；租约时间缺失不能当作已证明 DHCP 服务关闭。','分别验证 IPv4 与 IPv6 目标访问，再核对地址分配。')
    pings=[t for t in traces if t['tool']=='measure_latency_and_loss' and not t['result'].get('error')]
    if request.measure_link:
        ping_facts=[]
        for t in pings:
            d=t['result'].get('data') or {}
            ping_facts.append(f"{d.get('host','目标')}：发送 {d.get('sent','?')} 包，丢失 {d.get('lost','?')} 包，丢包率 {d.get('loss_rate_pct','?')}%，平均 ICMP RTT {d.get('icmp_rtt_avg_ms','未知')} ms。")
            if (d.get('loss_rate_pct') or 0)>0:
                cause('链路质量或 ICMP 响应异常',ping_facts[-1],
                      '强信号不排除丢包；ICMP 限速/过滤也会丢回应，小样本不能定位拥塞或故障跳点。','结合连续短测、应用访问和 DNS 结果复检，不单凭 ping 判根因。')
        layer('链路短测','warning' if any((t['result'].get('data') or {}).get('loss_rate_pct',0)>0 for t in pings) else 'ok' if pings else 'unknown',
              ping_facts or ['链路测量未完成；不虚构丢包率或延迟。'],pings)
    if len(causes)<2:
        cause('无线接入或地址问题','需结合当前接口、地址及链路观测；不能只看代理开关。',
              '若已连接且有有效地址，本轮证据不支持把“无线未连接”作为确定根因。','需要时复检连接与链路；有网站目标时继续检查该目标。')
    if len(causes)<2:
        cause('特定网站或访问路径问题','受测端点响应只覆盖这些目标与这些路径。',
              '没有出现相关失败时不能编造网站封禁；单端点成功也不代表所有网站正常。','提供打不开的具体网站，分别核对解析、传输、HTTP 和域名规则。')
    headline=('已核对可信校园认证跳转，尚未完成账号登录。' if portal else
              '目标在两条路径均返回 403，优先检查访问策略，不把它当作物理断网。' if 403 in direct and 403 in proxied else
              '观察到同一网站的访问路径差异，需要按已配置规则处理。' if any(s in {200,204} for s in direct) and any(s and s>=400 for s in proxied) else
              '存在需要进一步排查的观测，当前不能确定唯一根因。' if any(l['status']=='warning' for l in layers) else
              '本轮连接及受测端点未见明显异常；不能保证所有网站都正常。' if iface.get('status')=='connected' and healthy_http else
              '关键观测尚不完整，当前不能判定网络正常。')
    if model_error:
        unknown.append('模型环节未完成；以下保留当前只读检测结果，不声称模型已形成完整分析。')
    unknown.extend(layer['name']+'：仍缺少能够确认该层状态的观测。' for layer in layers if layer['status']=='unknown')
    return {'schema_version':'westie-diagnosis/1','report_id':uuid.uuid4().hex[:12],
            'timestamp':datetime.now(timezone.utc).isoformat(timespec='seconds'),'mode':mode,
            'question':request.question,'target_url':request.target_url,'headline':headline,
            'layers':layers,'possible_causes':causes,'missing_evidence':list(dict.fromkeys(unknown)),
            'network_modified':False,'model_error':model_error,
            'model_tool_calls':sum(t.get('origin')=='model' for t in traces),
            'host_completion_calls':sum(t.get('origin')=='host_completion' for t in traces),
            'host_preflight_calls':sum(t.get('origin')=='host_preflight' for t in traces),
            'traces':traces}


def diagnosis_details_text(report: dict) -> str:
    prefix='【课程演示：以下是模拟数据，不是你的电脑实测】\n' if report['mode']=='mock' else ''
    lines=[prefix+report['headline'], '分层检查：']
    status={'ok':'已观察','warning':'需关注','unknown':'待确认','skipped':'未执行'}
    for layer in report['layers']:
        lines.append(f"• {layer['name']}（{status[layer['status']]}）："+' '.join(layer['facts']))
    lines.append('可能原因与证据：')
    for i,cause in enumerate(report['possible_causes'],1):
        lines.append(f"{i}. {cause['name']}\n依据：{cause['support']}\n限制/反面证据：{cause['against']}\n下一步：{cause['next_step']}")
    if report['missing_evidence']:
        lines.append('尚未确认：'+'；'.join(report['missing_evidence']))
    lines.append('本次仅检查，尚未执行网络修改。可点击“导出排障报告”保存本轮结果。')
    return '\n\n'.join(lines)


def diagnosis_text(report: dict) -> str:
    """Everyday guidance from the same evidence used by the detailed report.

    Do not paraphrase a model draft or display tool fields. Technical evidence
    remains in the report; advice must not invent a root cause or recovery.
    """
    traces=report.get('traces') or []
    target=report.get('target_url')
    def latest(name):
        return next((t['result'].get('data') or {} for t in reversed(traces)
                     if t['tool']==name and not t['result'].get('error')),{})
    snapshot=latest('get_network_snapshot')
    iface=snapshot.get('selected_interface') or {}
    interfaces=[iface,*(snapshot.get('candidate_interfaces') or [])]
    connected=any(i.get('status')=='connected' for i in interfaces)
    from .offline import disconnected_snapshot
    known_offline=disconnected_snapshot({'success':bool(snapshot),'data':snapshot})
    attempts=[]
    errors=[]
    for trace in traces:
        if trace['tool']!='probe_connectivity':continue
        arguments=trace.get('arguments') or {}
        if arguments.get('target_url')!=target:continue
        result=trace['result']
        if result.get('error'):errors.append(result)
        data=result.get('data') or {}
        attempts.extend((data.get('route'),a) for a in data.get('attempts') or [])
    def good(status):return isinstance(status,int) and 200<=status<300
    direct=[a.get('http_status') for route,a in attempts if route=='direct']
    proxied=[a.get('http_status') for route,a in attempts if route=='configured_proxy']
    portal=next((a for _,a in attempts if a.get('portal_state')=='confirmed' and a.get('login_url_trusted')),None)
    loss=next((t['result'].get('data') or {} for t in traces if t['tool']=='measure_latency_and_loss'
               and not t['result'].get('error') and ((t['result'].get('data') or {}).get('loss_rate_pct') or 0)>0),None)
    tls_error=any(a.get('outcome')=='tls_error' or re.search(r'SSL|TLS|certificate',str(a.get('error_detail') or ''),re.I) for _,a in attempts)
    reachable=any(good(a.get('http_status')) for _,a in attempts)
    dns_timeout=any(q.get('status')=='TIMEOUT' for t in traces if t['tool']=='query_dns'
                    for q in (t['result'].get('data') or {}).get('queries') or [])
    if known_offline:
        body='电脑目前没有连上网络。\n\n1. 点击 Windows 右下角的网络图标，打开 Wi-Fi，选择你要连接的网络。\n2. 如果使用校园网，连接后在浏览器完成学校的上网登录，再点“网络检查”。'
    elif portal:
        body='校园网看起来还需要登录。连上 Wi-Fi 后，要再完成一次学校的上网认证。\n\n1. 在浏览器打开学校的上网登录页，用校园账号登录。\n2. 登录后，再试刚才打不开的网站。'
        if portal.get('candidate_login_url'):
            body+='\n\n检测到的学校登录页：'+portal['candidate_login_url']
    elif target and any(good(s) for s in direct) and any(isinstance(s,int) and s>=400 for s in proxied):
        body='这个网站可能受第三方 VPN/代理影响：米花直接访问时有回复，经过代理时出现了错误。\n\n1. 如果你开着第三方 VPN/代理，可以先暂停它，再刷新这个网站。\n2. 如果仍打不开，告诉我页面提示，我继续检查。\n\n我还没有替你更改设置。'
    elif target and 403 in direct+proxied:
        body='网站有回复，但没有允许这次访问。\n\n1. 确认已用学校账号登录，并按学校要求使用校园网或学校 WebVPN。\n2. 如果仍不行，把页面提示发给我，或联系学校的网站管理员。\n\n这不代表你的电脑权限被改了，单纯开关 VPN 也不一定能解决。'
    elif loss:
        body='检查时有几次没有收到回应，可能与你遇到的卡顿有关，现在还不能确定原因。\n\n1. 先暂停正在进行的大文件下载，再试一次原来的网页或视频。\n2. 如果仍卡顿，告诉我是所有应用都卡，还是只有一个网站，我继续帮你查。'
    elif target and reachable:
        body='我收到了这个网站的回复。请在浏览器里再试一次。\n\n如果仍打不开，把页面上的提示发给我；我会接着检查，不用先修改电脑权限。'
    elif tls_error:
        body='这次没有通过网站的安全连接检查。\n\n1. 先看看电脑的日期和时间是否正确，再刷新网页。\n2. 如果仍出现安全提示，把提示内容发给我；先不要关闭浏览器的安全检查。'
    elif errors and not attempts:
        body='这次米花没能完成网页检查，还不能说网站或你的电脑出了问题。\n\n先用浏览器试着打开；如果浏览器能正常访问，把这个情况告诉我，我会继续检查米花这边。'
    elif iface.get('has_apipa_ipv4') or any(l.get('status')=='unknown' for l in report.get('layers',[])):
        body=('电脑显示已连接，但有些上网信息还没查清。' if connected else '这次还没能确认电脑的上网状态。')
        body+='\n\n1. 先用浏览器打开一个平时能打开的网站。\n2. 如果都打不开，检查 Windows 右下角是否已连接网络；如果只有某个网站打不开，把网址发给我，我再单独检查。'
    elif dns_timeout and not reachable:
        body='查找网站地址时有几次没有成功，可能让网页打不开。\n\n1. 先刷新页面，再试另一个常用网站。\n2. 把仍打不开的网址发给我，我帮你单独检查。'
    elif reachable and connected:
        body='检查完啦，电脑已经连接上网络，测试的网页也有正常回复。\n\n如果你要用的网站仍打不开，把网址发给我，我再单独检查。'
    else:
        body='电脑的基本检查做完了，但网页能不能正常打开还没确认。\n\n1. 先在浏览器里试一下学校的上网登录页，看看是否需要登录。\n2. 如果仍打不开，把具体网址或页面提示发给我，我继续帮你查。'
    prefix='【课程演示：以下是模拟数据，不是你的电脑实测】\n' if report.get('mode')=='mock' else ''
    if report.get('model_error'):
        prefix+=(report.get('model_notice') or '这次智能分析暂时没完成，我先把基础检查的结果告诉你。')+'\n\n'
    elif report.get('analysis_source')=='local_basic':prefix+='基础检查完成啦，下面是本机检查结果。\n\n'
    return prefix+body
