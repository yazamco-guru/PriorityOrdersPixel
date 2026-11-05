"""חיבור ל-REST API של פריוריטי: טעינת לקוחות / אנשי קשר / פריטים ופתיחת הזמנות."""
import base64
import json
import re
import os
from datetime import date, datetime, timedelta
from urllib.parse import quote

import requests

from .config import DATA

CACHE_PATH = os.path.join(DATA, 'priority_cache.json')
PAGE = 1000


class PriorityError(Exception):
    pass


def configured(cfg):
    p = cfg['priority']
    return bool(p['baseUrl'] and p['user'])


def _base(cfg):
    return cfg['priority']['baseUrl'].rstrip('/') + '/'


def _session(cfg):
    p = cfg['priority']
    s = requests.Session()
    s.auth = (p['user'], p['password'])
    s.headers.update({'Accept': 'application/json', 'Content-Type': 'application/json; charset=utf-8'})
    if p.get('appId'):
        s.headers['X-App-Id'] = p['appId']
    if p.get('appKey'):
        s.headers['X-App-Key'] = p['appKey']
    return s


def error_text(res):
    try:
        d = res.json()
    except ValueError:
        return f'HTTP {res.status_code}: ' + ' '.join(re.sub(r'<[^>]+>', ' ', res.text).split())[:400]
    if isinstance(d, dict):
        err = d.get('error')
        if err:
            msg = err.get('message')
            return msg.get('value') if isinstance(msg, dict) else str(msg or err)
        ie = (d.get('FORM') or {}).get('InterfaceErrors')
        if ie:
            t = ie.get('text')
            if isinstance(t, list):
                return '; '.join(map(str, t))
            return str(t.get('#text') if isinstance(t, dict) else t or ie)
    return json.dumps(d, ensure_ascii=False)[:400]


def call(cfg, method, path, body=None, session=None):
    if not configured(cfg):
        raise PriorityError('לא הוגדר חיבור לפריוריטי ב-config.json')
    s = session or _session(cfg)
    res = s.request(method, _base(cfg) + path, data=json.dumps(body, ensure_ascii=False).encode() if body else None,
                    timeout=120)
    if not res.ok:
        raise PriorityError(error_text(res))
    return res.json() if res.content else None


def get_all(cfg, entity, select, filter_=None, session=None):
    s = session or _session(cfg)
    out, skip = [], 0
    while True:
        q = f'{entity}?$select={select}&$top={PAGE}&$skip={skip}'
        if filter_:
            q += '&$filter=' + quote(filter_)
        v = (call(cfg, 'GET', q, session=s) or {}).get('value', [])
        out += v
        if len(v) < PAGE:
            return out
        skip += PAGE


def _first_working(cfg, s, entity, selects):
    # שדות שלא קיימים בסביבה מסוימת מכשילים את כל הבקשה – מנסים מהרשימה המלאה למצומצמת
    last = None
    for sel in selects:
        try:
            return get_all(cfg, entity, sel, session=s), None
        except PriorityError as e:
            last = e
    return [], f'{entity}: {last}'


def refresh_cache(cfg):
    s = _session(cfg)
    warnings = []
    parts = get_all(cfg, 'LOGPART', 'PARTNAME,PARTDES,BARCODE', session=s)
    customers, w = _first_working(cfg, s, 'CUSTOMERS', ['CUSTNAME,CUSTDES,EMAIL,VATNUM', 'CUSTNAME,CUSTDES,EMAIL', 'CUSTNAME,CUSTDES'])
    if w:
        raise PriorityError(w)
    contacts, w = _first_working(cfg, s, 'PHONEBOOK', ['CUSTNAME,NAME,EMAIL'])
    if w:
        warnings.append('אנשי קשר לא נטענו – ' + w)
    cache = {'baseUrl': cfg['priority']['baseUrl'], 'date': datetime.now().isoformat(timespec='seconds'),
             'parts': parts, 'customers': customers, 'contacts': contacts}
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    return cache, warnings


def load_cache(cfg):
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH, encoding='utf-8') as f:
        cache = json.load(f)
    return cache if cache.get('baseUrl') == cfg['priority']['baseUrl'] else None


def _pri_date(iso):
    d = date.fromisoformat(iso) if iso else date.today()
    off = datetime.now().astimezone().strftime('%z')
    return f'{d.isoformat()}T00:00:00{off[:3]}:{off[3:]}'


def _odq(s):
    return str(s).replace("'", "''")


def _cut(v, n):
    return str(v or '')[:n]


def find_existing(cfg, custname, reference):
    f = f"CUSTNAME eq '{_odq(custname)}' and REFERENCE eq '{_odq(_cut(reference, 16))}'"
    v = (call(cfg, 'GET', f'ORDERS?$select=ORDNAME&$filter={quote(f)}') or {}).get('value', [])
    return v[0]['ORDNAME'] if v else None


def build_payload(cfg, order):
    d, o = order['data'], cfg['orders']
    h = d['header']
    due = h.get('dueDate') or (date.today() + timedelta(days=int(o.get('dueDays') or 0))).isoformat()
    items = []
    for ln in d['lines']:
        it = {'PARTNAME': ln['part']['code'], 'TQUANT': ln['qty'], 'DUEDATE': _pri_date(due)}
        if ln.get('price') and o.get('priceField'):
            it[o['priceField']] = ln['price']
        items.append(it)
    body = {'CUSTNAME': d['customer']['code'], 'CURDATE': _pri_date(h.get('orderDate')), 'ORDERITEMS_SUBFORM': items}
    if h.get('orderNum'):
        body['REFERENCE'] = _cut(h['orderNum'], 16)
    details = h.get('notes') or (f"מייל: {order['sender_email']}" if order.get('sender_email') else '')
    if details:
        body['DETAILS'] = _cut(details, 32)
    if o.get('typeCode'):
        body['TYPECODE'] = o['typeCode']
    return body


def create_order(cfg, order, pdf_path=None):
    """פותח הזמנה ומצרף את ה-PDF המקורי. מחזיר (ORDNAME, אזהרות)."""
    d = order['data']
    ref = d['header'].get('orderNum')
    if ref:
        existing = find_existing(cfg, d['customer']['code'], ref)
        if existing:
            raise PriorityError(f'כבר קיימת הזמנה {existing} ללקוח זה עם אותו מס\' הזמנה (הזמנתכם)')
    res = call(cfg, 'POST', 'ORDERS', build_payload(cfg, order))
    ordname = res.get('ORDNAME')
    warnings = []
    if pdf_path and cfg['orders'].get('attachPdf') and os.path.exists(pdf_path):
        try:
            with open(pdf_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            call(cfg, 'POST', f"ORDERS('{_odq(ordname)}')/EXTFILES_SUBFORM",
                 {'EXTFILEDES': _cut(order.get('pdf_name') or 'הזמנה.pdf', 64),
                  'EXTFILENAME': f'data:application/pdf;base64,{b64}'})
        except PriorityError as e:
            warnings.append(f'ההזמנה נפתחה, אך צירוף ה-PDF נכשל: {e}')
    return ordname, warnings
