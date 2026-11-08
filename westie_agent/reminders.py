"""Reminder choices shared by the local editor and scheduler."""
from datetime import datetime

PRIORITIES = {
    3: '重要且紧急',
    2: '紧急但不重要',
    1: '重要但不紧急',
    0: '不紧急也不重要',
}
REMINDER_OPTIONS = {
    'at_deadline': '截止时',
    'before_1m': '提前1分钟',
    'before_10m': '提前10分钟',
    'before_15m': '提前15分钟',
    'before_30m': '提前30分钟',
    'before_1h': '提前1小时',
    'before_90m': '提前1.5小时',
    'before_2h': '提前2小时',
    'before_3h': '提前3小时',
    'same_day_0700': '当天07:00',
}
_SECONDS = dict(zip(list(REMINDER_OPTIONS)[1:9], (60, 600, 900, 1800, 3600, 5400, 7200, 10800)))


def validate_reminders(choices):
    if (not isinstance(choices, (list, tuple)) or len(choices) > 3
            or any(not isinstance(c, str) or c not in REMINDER_OPTIONS for c in choices)
            or len(set(choices)) != len(choices)):
        raise ValueError('每个事项最多选择 3 个不同的提醒时间。')
    return [key for key in REMINDER_OPTIONS if key in choices]


def reminder_time(due_at, option):
    if option == 'at_deadline':
        return due_at
    if option == 'same_day_0700':
        return datetime.fromtimestamp(due_at).replace(hour=7, minute=0, second=0, microsecond=0).timestamp()
    return due_at - _SECONDS[option]
