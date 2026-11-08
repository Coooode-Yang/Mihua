"""Local connectivity decisions. Unknown status must not masquerade as offline."""
from datetime import datetime


def disconnected_snapshot(result):
    if not result or not result.get('success') or result.get('error'):return False
    data=result.get('data') or {}
    interfaces=[data.get('selected_interface') or {},*(data.get('candidate_interfaces') or [])]
    known=[i for i in interfaces if i]
    if any(i.get('status')=='connected' for i in known):return False
    if any(i.get('status') not in {'disconnected','down','disabled','not_present'} for i in known):return False
    return bool(known) or ('candidate_interfaces' in data and not data['candidate_interfaces'])


def recent_offline_observation(observation,max_age_s=45):
    if not observation or observation.get('state')!='disconnected':return False
    try:
        measured=datetime.fromisoformat(observation['checked_at'])
        age=(datetime.now().astimezone()-measured).total_seconds()
        return 0<=age<=max_age_s
    except (ValueError,TypeError,KeyError):return False


OFFLINE_NOTICE='电脑当前没有连上网络，我先用本机工具检查；在线对话需要联网后才能使用。'
BASIC_NOTICE='这是本机基础检查结果，未调用在线模型。'
