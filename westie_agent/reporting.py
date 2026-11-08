"""Offline, self-contained diagnostic exports. No chat history or credentials."""
from __future__ import annotations

import copy
import html
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SENSITIVE_KEYS = {'password','passwd','authorization','api_key','approval_token','secret','token',
                  'ssid','bssid','mac_address','profile_name','available_profiles'}


def _redact_text(value: str) -> str:
    def url(match):
        try:
            parts=urlsplit(match.group())
            host=parts.hostname or ''
            host='['+host+']' if ':' in host else host
            authority=host+(':'+str(parts.port) if parts.port else '')
            query=urlencode([(k,'[已隐藏]') for k,_ in parse_qsl(parts.query,keep_blank_values=True)])
            return urlunsplit((parts.scheme,authority,parts.path,query,''))
        except ValueError:
            return '[无效地址已隐藏]'
    value=re.sub(r'https?://[^\s<>\"\x27，。；]+',url,value,flags=re.I)
    value=re.sub(r'(?i)(password|passwd|token|secret|api_key|authorization|密码|口令)\s*[:=：]\s*[^\s,;，；]+',r'\1=[已隐藏]',value)
    value=re.sub(r'(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b','[MAC已隐藏]',value)
    def ipv4(match):
        try:
            address=ipaddress.ip_address(match.group())
            return '.'.join(match.group().split('.')[:3])+'.x' if address.is_private else match.group()
        except ValueError:
            return match.group()
    return re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',ipv4,value)


def redacted_report(report: dict) -> dict:
    """Mask known sensitive values everywhere, including copies in summaries."""
    secrets=set()
    ipv6_values={}
    def find(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k.lower() in SENSITIVE_KEYS and isinstance(v,str) and len(v)>2:
                    secrets.add(v)
                else:find(v)
        elif isinstance(value,list):
            for v in value:find(v)
        elif isinstance(value,str):
            try:
                address=ipaddress.ip_address(value)
                if address.version==6:
                    ipv6_values[value]=str(ipaddress.ip_network(value+'/64',strict=False))+'（主机部分已隐藏）'
            except ValueError:pass
    find(report)
    def walk(value,key=''):
        if key.lower() in SENSITIVE_KEYS or 'password' in key.lower() or 'approval_token' in key.lower():
            return '[已隐藏]'
        if isinstance(value,dict):return {k:walk(v,k) for k,v in value.items() if k!='model_draft'}
        if isinstance(value,list):return [walk(v) for v in value]
        if isinstance(value,str):
            for secret in sorted(secrets,key=len,reverse=True):value=value.replace(secret,'[已隐藏]')
            for address in sorted(ipv6_values,key=len,reverse=True):value=value.replace(address,ipv6_values[address])
            try:
                address=ipaddress.ip_address(value)
                if address.version==6:return str(ipaddress.ip_network(value+'/64',strict=False))+'（主机部分已隐藏）'
            except ValueError:pass
            return _redact_text(value)
        return value
    result=walk(copy.deepcopy(report))
    result['privacy_note']='默认隐藏已识别的SSID/MAC、私网IPv4主机部分、独立IPv6主机部分、URL查询值与授权信息；自由文本仍请分享前检查。'
    return result


def report_html(report: dict) -> str:
    data=redacted_report(report)
    esc=lambda value:html.escape(str(value))
    mode='课程模拟数据（不是本机实测）' if data.get('mode')=='mock' else '真实本机只读观测'
    sections=[]
    for layer in data.get('layers',[]):
        sections.append('<tr><th>'+esc(layer['name'])+'</th><td>'+esc(layer['status'])+'</td><td>'+
                        '<br>'.join(esc(x) for x in layer['facts'])+'</td><td>'+esc(', '.join(layer['evidence_ids']))+'</td></tr>')
    causes=''.join('<section><h3>'+esc(c['name'])+'（可能）</h3><p>依据：'+esc(c['support'])+
                   '</p><p>限制与反面证据：'+esc(c['against'])+'</p><p>下一步：'+esc(c['next_step'])+'</p></section>' for c in data.get('possible_causes',[]))
    actions=data.get('actions') or []
    action_html=('<h2>诊断后确认的操作</h2><p>上面的观测属于操作前；以下为后续执行结果，不能把提交请求等同于恢复。</p><pre>'+esc(json.dumps(actions,ensure_ascii=False,indent=2))+'</pre>') if actions else '<p>本轮未执行网络修改。</p>'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>米花排障报告</title>
<style>body{font:15px/1.8 "Microsoft YaHei",sans-serif;color:#343d37;background:#f3f5f1;margin:0}main{max-width:1000px;margin:24px auto;padding:30px;background:#fff;border-radius:12px}h1{font-size:26px}th,td{border:1px solid #dce3db;text-align:left;padding:10px;vertical-align:top}table{border-collapse:collapse;width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f3;padding:14px}p{overflow-wrap:anywhere}.notice{background:#f4ead8;padding:14px}section{border-bottom:1px solid #e7ebe5;padding:8px 0}@media(max-width:650px){main{padding:15px;margin:0}table{font-size:12px}}</style><main>'''+f'''
<h1>米花排障报告</h1><p>编号 {esc(data.get('report_id',''))} · UTC {esc(data.get('timestamp',''))} · {esc(mode)}</p>
<p class="notice">{esc(data.get('headline',''))}</p><p>问题：{esc(data.get('question',''))}</p>
<p>目标：{esc(data.get('target_url') or '已配置联网检查端点')}</p>
<h2>分层观测</h2><table><tr><th>层次</th><th>状态</th><th>实际观测</th><th>依据编号</th></tr>{''.join(sections)}</table>
<h2>可能解释与下一步</h2>{causes}<h2>仍缺少的证据</h2><p>{esc('；'.join(data.get('missing_evidence') or []) or '按本次范围收集；没有证据的项目仍标记为未知，不代表所有网络问题都已排除。')}</p>
<p>模型主动调用：{data.get('model_tool_calls',0)} 次；宿主补齐观测：{data.get('host_completion_calls',0)} 次。补查与结构化解释不等同于模型能力提高。</p>
<p>分析来源：{esc(data.get('analysis_source') or '模型与本机工具')}；本机连接预检：{data.get('host_preflight_calls',0)} 次。基础检查与离线规则不代表已调用云端模型。</p>
{action_html}<details><summary>查看原始检查依据（已脱敏）</summary><pre>{esc(json.dumps(data.get('traces',[]),ensure_ascii=False,indent=2))}</pre></details>
<p>{esc(data['privacy_note'])}</p></main></html>'''


def export_report(report: dict, destination: Path):
    if not isinstance(report,dict) or report.get('schema_version')!='westie-diagnosis/1':
        raise ValueError('请先完成一次网络检查，再导出排障报告。')
    destination=Path(destination)
    if destination.suffix.lower()=='.json':
        content=json.dumps(redacted_report(report),ensure_ascii=False,indent=2)
    elif destination.suffix.lower()=='.html':
        content=report_html(report)
    else:
        raise ValueError('请选择 HTML 或 JSON 格式。')
    destination.write_text(content,encoding='utf-8')
