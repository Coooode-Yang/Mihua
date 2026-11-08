"""OpenAI-compatible Chat Completions, bounded tests, safe error messages."""
from __future__ import annotations

import asyncio
import copy
import json
import re

from .credentials import validate_api_key
from .runtime import SetupRequired


def api_model_settings(settings):
    result={'max_tokens':settings.response_tokens}
    if settings.api_provider=='deepseek':result['extra_body']={'thinking':{'type':'disabled'}}
    return result


def make_client(settings,key,http_client):
    from openai import AsyncOpenAI
    settings.validate();key=validate_api_key(key)
    return AsyncOpenAI(base_url=settings.api_base_url,api_key=key,http_client=http_client,max_retries=0)


def api_error_message(exc):
    status=getattr(exc,'status_code',None)
    if status in {401,403}:return 'API Key 没有通过验证，请在“模型设置”检查 Key 和服务商。'
    if status==402:return 'API 账户余额不足，请到服务商平台检查。'
    if status==429:return 'API 请求次数或额度暂时受限，请稍后重试，或检查账户额度。'
    if status==404:return '没有找到指定模型或接口，请在“模型设置”核对模型名称与 API 地址。'
    if status==400:return '服务商未接受请求，请检查模型是否支持工具调用、接口地址和模型名称是否正确。'
    if status and 300<=status<400:return 'API 地址发生跳转，请核对服务商给出的接口地址。'
    if status and status>=500:return '模型服务商暂时没有正常响应，请稍后重试。'
    if 'Timeout' in type(exc).__name__:return '等待模型回复超时，请稍后重试，或检查网络。'
    if 'Connection' in type(exc).__name__:return '暂时连不上模型服务，请检查网络或“模型设置”中的代理选项。'
    if isinstance(exc,SetupRequired):return str(exc)
    return '模型调用暂时没有完成，请检查“模型设置”，或稍后重试。'


def cloud_tool_result(result):
    """Keep IPs/domains needed by tool selection; hide recognized identities.
    Local traces retain original observations. This is minimization, not a claim
    that arbitrary user text or all network metadata is anonymous.
    """
    sensitive={'password','passwd','api_key','authorization','approval_token','secret','token',
               'ssid','bssid','mac','mac_address','profile_name','current_profile','profiles','saved_profiles','available_profiles'}
    secrets=set()
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower() in sensitive:
                    if isinstance(item,str) and len(item)>2:secrets.add(item)
                    elif isinstance(item,list):secrets.update(v for v in item if isinstance(v,str) and len(v)>2)
                else:collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    collect(result)
    def walk(value):
        if isinstance(value,dict):return {key:'[已隐藏]' if key.lower() in sensitive or 'password' in key.lower() else walk(item) for key,item in value.items()}
        if isinstance(value,list):return [walk(item) for item in value]
        if isinstance(value,str):
            for secret in sorted(secrets,key=len,reverse=True):value=value.replace(secret,'[已隐藏]')
            return re.sub(r'(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b','[MAC已隐藏]',value)
        return value
    return walk(copy.deepcopy(result))


async def _test_connection(settings,key,transport=None):
    import httpx2
    async with httpx2.AsyncClient(trust_env=settings.api_use_system_proxy,follow_redirects=False,
                                 timeout=min(settings.request_timeout_s,30),transport=transport) as http:
        client=make_client(settings,key,http)
        name='westie_connection_check'
        tools=[{'type':'function','function':{'name':name,'description':'A harmless connection check, no network or system changes.',
               'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}}]
        messages=[{'role':'user','content':'请调用 westie_connection_check，value 填 ping，然后用中文简短回复连接成功。不要请求个人信息。'}]
        options=api_model_settings(settings);options['max_tokens']=128
        first=await client.chat.completions.create(model=settings.model,messages=messages,tools=tools,
            tool_choice={'type':'function','function':{'name':name}},**options)
        if not first.choices:raise SetupRequired('接口未返回有效回复，请检查模型或接口兼容性。')
        answer=first.choices[0].message
        calls=answer.tool_calls or []
        if len(calls)!=1 or calls[0].function.name!=name:
            raise SetupRequired('聊天接口有回复，但没有完成工具调用。请使用支持工具调用的模型。')
        try:arguments=json.loads(calls[0].function.arguments)
        except (ValueError,TypeError):arguments=None
        if arguments!={'value':'ping'}:raise SetupRequired('模型没有返回有效工具参数，请检查模型的工具调用能力。')
        messages.append(answer.model_dump(exclude_none=True))
        messages.append({'role':'tool','tool_call_id':calls[0].id,'content':'{"result":"pong","read_only":true}'})
        second=await client.chat.completions.create(model=settings.model,messages=messages,tools=tools,tool_choice='none',**options)
        if not second.choices or not second.choices[0].message.content:
            raise SetupRequired('工具调用已返回，但模型没有完成最后回复，请稍后重试。')
        return {'chat_ok':True,'tool_call_ok':True,'requests':2,'message':'连接成功，聊天和工具调用都通过了检查。'}


def test_connection(settings,key,transport=None):
    try:return asyncio.run(_test_connection(settings,key,transport))
    except Exception as exc:raise SetupRequired(api_error_message(exc)) from None
