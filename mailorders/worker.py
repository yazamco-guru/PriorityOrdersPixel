"""הצינור: מייל / PDF ← פענוח ← זיהוי לקוח ופריטים ← שמירה בדשבורד; ושליחה לפריוריטי."""
import os
import re
import threading
import traceback
import uuid

from . import config, db, gmail, matching, parsers, pdftext, priority

_lock = threading.RLock()
_wake = threading.Event()
_index = {'idx': None}


# ---------- נתוני פריוריטי ----------

def index():
    if _index['idx'] is None:
        _index['idx'] = matching.Index(priority.load_cache(config.load()))
    return _index['idx']


def refresh_priority():
    cfg = config.load()
    cache, warnings = priority.refresh_cache(cfg)
    with _lock:
        _index['idx'] = matching.Index(cache)
        rematch_all()
    return {'parts': len(cache['parts']), 'customers': len(cache['customers']),
            'contacts': len(cache['contacts']), 'warnings': warnings}


# ---------- ניתוח הזמנה ----------

def problems_of(order, idx):
    d = order['data']
    out = []
    if d.get('scanned'):
        out.append('PDF סרוק (תמונה) – אין בו טקסט לקריאה')
    if not idx.loaded:
        out.append('לא נטענו נתונים מפריוריטי')
    if not d.get('customer'):
        out.append('לקוח לא זוהה')
    if not d.get('lines'):
        out.append('לא נמצאו שורות פריטים')
    for i, ln in enumerate(d.get('lines', []), 1):
        if not ln.get('part'):
            out.append(f'שורה {i}: פריט לא זוהה ({ln.get("description") or ln.get("sku") or "—"})')
        if not ln.get('qty') or ln['qty'] <= 0:
            out.append(f'שורה {i}: חסרה כמות')
    return out


def analyze(order, idx=None, cfg=None):
    """מחשב התאמות (בלי לדרוס בחירות ידניות), בעיות וסטטוס, ושומר."""
    idx, cfg = idx or index(), cfg or config.load()
    d = order['data']
    if order.get('ordname'):
        return order
    if not d.get('customerManual'):
        d['customer'], d['customerCandidates'] = matching.match_customer(
            idx, cfg, d.get('parsed', {}), order.get('sender_email'), order.get('sender_name'))
    for ln in d.get('lines', []):
        if not ln.get('manual'):
            ln['part'], ln['candidates'] = matching.match_part(idx, cfg, ln)
    problems = problems_of(order, idx)
    status = order.get('status')
    if status not in ('ignored', 'error'):
        status = 'stuck' if problems else 'ready'
    order.update(status=status, problems=problems)
    db.update_order(order['id'], data=d, status=status, problems=problems)
    return order


def rematch_all():
    idx, cfg = index(), config.load()
    for o in db.list_orders():
        if not o['ordname']:
            analyze(o, idx, cfg)


def _own_filter(cfg, parsed):
    own = cfg['ownCompany']
    own_names = {matching.company_text(n) for n in own['names']}
    own_vats = {matching.digits(v) for v in own['vatnums']}
    parsed['companies'] = [c for c in parsed.get('companies', []) if matching.company_text(c) not in own_names]
    parsed['vatnums'] = [v for v in parsed.get('vatnums', []) if matching.digits(v) not in own_vats]
    return parsed


def add_pdf(mail, pdf_name, pdf_bytes, mail_key, source='gmail'):
    cfg = config.load()
    safe = re.sub(r'[^\w.\-]+', '_', pdf_name)[-80:]
    path = os.path.join(db.ATTACH_DIR, f'{uuid.uuid4().hex[:10]}_{safe}')
    with open(path, 'wb') as f:
        f.write(pdf_bytes)

    data = {}
    try:
        doc = pdftext.extract(path)
        parsed = _own_filter(cfg, parsers.parse(doc, mail))
        data['scanned'] = doc['scanned']
        data['pdfText'] = doc['text'][:20000]
    except Exception as e:
        parsed = {'lines': [], 'companies': [], 'vatnums': [], 'parser': 'שגיאה'}
        data['parseError'] = f'קריאת ה-PDF נכשלה: {e}'
    data['parsed'] = parsed
    data['header'] = {k: parsed.get(k) or '' for k in ('orderNum', 'orderDate', 'dueDate', 'notes')}
    data['lines'] = [{k: ln.get(k) for k in ('sku', 'description', 'qty', 'price')} for ln in parsed.get('lines', [])]

    oid = db.insert_order(mail_key=mail_key, source=source, received=mail.get('received') or db.now(),
                          sender_email=mail.get('sender_email', ''), sender_name=mail.get('sender_name', ''),
                          subject=mail.get('subject', ''), body=mail.get('body', ''),
                          pdf_file=os.path.basename(path), pdf_name=pdf_name, status='stuck', problems=[], data=data)
    return analyze(db.get_order(oid))


# ---------- משיכת מיילים ----------

def poll_once():
    cfg = config.load()
    if not gmail.configured(cfg):
        db.meta_set('poll', {'at': db.now(), 'ok': False, 'message': 'לא הוגדר חשבון Gmail ב-config.json'})
        return 0
    with _lock:
        try:
            added = 0
            for mail in gmail.fetch_new(cfg, db.mail_seen):
                for i, (name, content) in enumerate(mail['pdfs']):
                    add_pdf(mail, name, content, f'gmail:{mail["gmid"]}:{i}')
                    added += 1
                db.mark_mail_seen(mail['gmid'])
            db.meta_set('poll', {'at': db.now(), 'ok': True, 'message': f'נוספו {added} הזמנות'})
            return added
        except Exception as e:
            traceback.print_exc()
            db.meta_set('poll', {'at': db.now(), 'ok': False, 'message': f'בדיקת המייל נכשלה: {e}'})
            raise


def poll_now():
    _wake.set()


def loop():
    while True:
        try:
            poll_once()
        except Exception:
            pass
        minutes = max(1, float(config.load()['gmail']['pollMinutes'] or 5))
        _wake.wait(minutes * 60)
        _wake.clear()


def start():
    threading.Thread(target=loop, name='gmail-poll', daemon=True).start()


# ---------- שליחה לפריוריטי ----------

def learn(order):
    d = order['data']
    cust = d['customer']['code']
    for comp in d.get('parsed', {}).get('companies', []):
        db.mem_set('customer', 'c:' + matching.company_text(comp), cust)
    if order.get('sender_email'):
        db.mem_set('customer', 'e:' + order['sender_email'].lower(), cust)
    for ln in d['lines']:
        for key in matching.part_mem_keys(ln):
            db.mem_set('part', key, ln['part']['code'])


def send(order_id):
    with _lock:
        order = analyze(db.get_order(order_id))
        if order['ordname']:
            return order
        if order['status'] == 'stuck':
            raise ValueError('ההזמנה תקועה: ' + '; '.join(order['problems']))
        cfg = config.load()
        try:
            ordname, warnings = priority.create_order(cfg, order, os.path.join(db.ATTACH_DIR, order['pdf_file']))
        except Exception as e:
            db.update_order(order_id, status='error', error=str(e))
            raise
        learn(order)
        db.update_order(order_id, status='sent', ordname=ordname, error='; '.join(warnings) or None, problems=[])
        return db.get_order(order_id)
