import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
CONFIG_PATH = os.path.join(ROOT, 'config.json')

DEFAULTS = {
    'port': 8790,
    'gmail': {
        'user': '',
        'appPassword': '',
        'folder': 'INBOX',
        'pollMinutes': 5,
        'sinceDays': 14,
        'filter': '',
    },
    'priority': {
        'baseUrl': '',
        'user': '',
        'password': '',
        'appId': '',
        'appKey': '',
    },
    # Claude API – לפענוח הזמנות בגוף המייל ו-PDF סרוק (שלב הבא)
    'anthropic': {
        'apiKey': '',
        'model': 'claude-sonnet-5-5',
    },
    'orders': {
        'typeCode': '',
        'dueDays': 0,
        'priceField': 'PRICE',
        'attachPdf': True,
    },
    # החברה שלנו – כדי שהשם / ח.פ שלה ב-PDF לא יזוהו כלקוח
    'ownCompany': {
        'names': [],
        'vatnums': [],
    },
    'matching': {
        'partThreshold': 0.85,
        'partMargin': 0.15,
        'customerThreshold': 0.8,
        'customerMargin': 0.1,
    },
}


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    """config.json נקרא מחדש בכל קריאה – שינוי בקובץ נכנס לתוקף בלי הפעלה מחדש."""
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            _merge(cfg, json.load(f))
    return cfg
