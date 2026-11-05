"""זיהוי לקוח ופריטים בפריוריטי לפי נתוני ההזמנה."""
import re

from . import db

GENERIC_DOMAINS = {
    'gmail.com', 'googlemail.com', 'walla.co.il', 'walla.com', 'hotmail.com', 'outlook.com', 'live.com',
    'yahoo.com', 'icloud.com', 'me.com', '012.net.il', 'bezeqint.net', 'netvision.net.il', 'zahav.net.il',
}
COMPANY_NOISE = {'בעמ', 'בע', 'ltd', 'limited', 'inc', 'llc', 'חברת', 'חברה'}


def normalize(s):
    t = re.sub(r'["\'`״׳]', '', str(s or '').lower())
    return ' '.join(re.sub(r'[^0-9a-z֐-׿]+', ' ', t).split())


def company_text(s):
    return ' '.join(t for t in normalize(s).split() if t not in COMPANY_NOISE)


def tokens(s):
    return list(dict.fromkeys(t for t in normalize(s).split() if len(t) > 1 or t.isdigit()))


def code_key(s):
    return str(s or '').strip().upper()


def digits(s):
    return re.sub(r'\D', '', str(s or ''))


def weight(t):
    # מילים שהן דגם / מק"ט (אותיות לועזיות + ספרות) מקבלות משקל גבוה
    if re.search(r'\d', t) and re.search(r'[a-z]', t):
        return 3
    if re.fullmatch(r'\d{3,}|[a-z]{3,}', t):
        return 2
    return 1


def make_item(obj, text, extra=''):
    toks = tokens(text)
    return {'obj': obj, 'toks': toks, 'set': set(toks), 'w': sum(map(weight, toks)),
            'compact': normalize(f'{text} {extra}').replace(' ', '')}


def score(q, it):
    """ציון דמיון סימטרי (0..1) בין שני טקסטים"""
    a = b = 0.0
    for t in q['toks']:
        if t in it['set']:
            a += weight(t)
        elif len(t) >= 3 and t in it['compact']:
            a += weight(t) * 0.8
    for t in it['toks']:
        if t in q['set']:
            b += weight(t)
        elif len(t) >= 3 and t in q['compact']:
            b += weight(t) * 0.8
    return (a + b) / ((q['w'] + it['w']) or 1)


def rank(text, items, limit=5, min_score=0.15):
    q = make_item(None, text)
    if not q['toks']:
        return []
    ranked = sorted(((score(q, it), it['obj']) for it in items), key=lambda x: -x[0])
    return [(s, o) for s, o in ranked[:limit] if s > min_score]


def certain(ranked, threshold, margin):
    if not ranked:
        return False
    best = ranked[0][0]
    return best >= threshold and (len(ranked) < 2 or best - ranked[1][0] >= margin)


class Index:
    """אינדקס לקוחות, אנשי קשר ופריטים מהמטמון של פריוריטי."""

    def __init__(self, cache):
        cache = cache or {}
        self.loaded = bool(cache.get('customers') or cache.get('parts'))
        self.parts = cache.get('parts', [])
        self.customers = cache.get('customers', [])
        self.contacts = cache.get('contacts', [])

        self.part_by_code, self.part_by_name = {}, {}
        for p in self.parts:
            for k in (p.get('PARTNAME'), p.get('BARCODE')):
                k = code_key(k)
                if k:
                    lst = self.part_by_code.setdefault(k, [])
                    if p not in lst:
                        lst.append(p)
            self.part_by_name[code_key(p.get('PARTNAME'))] = p
        self.part_items = [make_item(p, p.get('PARTDES', ''), p.get('PARTNAME', '')) for p in self.parts]

        self.cust_by_name = {c['CUSTNAME']: c for c in self.customers}
        self.cust_by_vat, self.cust_by_email, self.cust_by_domain = {}, {}, {}
        for c in self.customers:
            v = digits(c.get('VATNUM'))
            if len(v) >= 8:
                self.cust_by_vat.setdefault(v, set()).add(c['CUSTNAME'])
        for rec in self.customers + self.contacts:
            cust = rec.get('CUSTNAME')
            for email in re.split(r'[;,\s]+', str(rec.get('EMAIL') or '').lower()):
                if '@' not in email or cust not in self.cust_by_name:
                    continue
                self.cust_by_email.setdefault(email, set()).add(cust)
                domain = email.split('@')[1]
                if domain not in GENERIC_DOMAINS:
                    self.cust_by_domain.setdefault(domain, set()).add(cust)
        self.cust_items = [make_item(c, company_text(c.get('CUSTDES')), c['CUSTNAME']) for c in self.customers]
        self.contact_items = [make_item(ct, ct.get('NAME', '')) for ct in self.contacts
                              if ct.get('NAME') and ct.get('CUSTNAME') in self.cust_by_name]


def cust_match(c, method, score_=1.0, sure=True):
    return {'code': c['CUSTNAME'], 'des': c.get('CUSTDES', ''), 'method': method, 'score': round(score_, 3), 'certain': sure}


def part_match(p, method, score_=1.0, sure=True):
    return {'code': p['PARTNAME'], 'des': p.get('PARTDES', ''), 'method': method, 'score': round(score_, 3), 'certain': sure}


def _single(idx, names):
    return idx.cust_by_name.get(next(iter(names))) if names and len(names) == 1 else None


def match_customer(idx, cfg, parsed, sender_email, sender_name):
    """סדר עדיפות: שם החברה במסמך (זיכרון, ח.פ, דמיון שם) ← מייל השולח ← שם השולח.
    מחזיר (התאמה ודאית או None, מועמדים)."""
    m_cfg = cfg['matching']
    thr, margin = m_cfg['customerThreshold'], m_cfg['customerMargin']
    candidates = []
    email = (sender_email or '').lower()

    # 1. שם החברה / ח.פ מתוך ההזמנה
    for comp in parsed.get('companies', []):
        code = db.mem_get('customer', 'c:' + company_text(comp))
        if code and code in idx.cust_by_name:
            return cust_match(idx.cust_by_name[code], 'זיכרון (שם חברה)'), []
    for v in parsed.get('vatnums', []):
        c = _single(idx, idx.cust_by_vat.get(digits(v)))
        if c:
            return cust_match(c, 'ח.פ'), []
    for comp in parsed.get('companies', []):
        ranked = rank(company_text(comp), idx.cust_items)
        if certain(ranked, thr, margin):
            return cust_match(ranked[0][1], f'שם חברה: {comp}', ranked[0][0]), []
        candidates += ranked

    # 2. מייל השולח
    if email:
        code = db.mem_get('customer', 'e:' + email)
        if code and code in idx.cust_by_name:
            return cust_match(idx.cust_by_name[code], 'זיכרון (מייל שולח)'), []
        c = _single(idx, idx.cust_by_email.get(email))
        if c:
            return cust_match(c, 'מייל שולח'), []
        c = _single(idx, idx.cust_by_domain.get(email.split('@')[-1]))
        if c:
            return cust_match(c, 'דומיין מייל שולח', 0.9), []

    # 3. שם השולח – מול אנשי קשר ושמות לקוחות
    if sender_name:
        ranked = [(s, idx.cust_by_name[ct['CUSTNAME']]) for s, ct in rank(sender_name, idx.contact_items)]
        ranked += rank(company_text(sender_name), idx.cust_items)
        ranked.sort(key=lambda x: -x[0])
        if certain(ranked, thr, margin):
            return cust_match(ranked[0][1], f'שם שולח: {sender_name}', ranked[0][0]), []
        candidates += ranked

    seen, out = set(), []
    for s, c in sorted(candidates, key=lambda x: -x[0]):
        if c['CUSTNAME'] not in seen:
            seen.add(c['CUSTNAME'])
            out.append(cust_match(c, 'מועמד', s, False))
    return None, out[:5]


def part_mem_keys(line):
    keys = []
    if line.get('sku'):
        keys.append('s:' + code_key(line['sku']))
    if line.get('description'):
        keys.append('d:' + normalize(line['description']))
    return keys


def match_part(idx, cfg, line):
    """זיכרון ← מק"ט / ברקוד (חד ערכי) ← דמיון תיאור. מחזיר (התאמה ודאית או None, מועמדים)."""
    for key in part_mem_keys(line):
        code = db.mem_get('part', key)
        p = idx.part_by_name.get(code_key(code)) if code else None
        if p:
            return part_match(p, 'זיכרון'), []

    hits = []
    sku = code_key(line.get('sku'))
    if sku:
        hits = idx.part_by_code.get(sku) or idx.part_by_code.get(sku.replace(' ', '')) or []
        if len(hits) == 1:
            return part_match(hits[0], 'מק"ט/ברקוד'), []

    text = line.get('description') or line.get('sku') or ''
    pool = [it for it in idx.part_items if it['obj'] in hits] if len(hits) > 1 else idx.part_items
    ranked = rank(text, pool)
    m_cfg = cfg['matching']
    if certain(ranked, m_cfg['partThreshold'], m_cfg['partMargin']):
        return part_match(ranked[0][1], 'תיאור', ranked[0][0]), []
    return None, [part_match(p, 'מועמד', s, False) for s, p in ranked]


def search_customers(idx, q, limit=15):
    q = (q or '').strip()
    exact = [c for c in idx.customers if code_key(c['CUSTNAME']).startswith(code_key(q))] if q else []
    ranked = [c for _, c in rank(company_text(q), idx.cust_items, limit, 0.05)]
    out = list(dict.fromkeys(c['CUSTNAME'] for c in exact[:5] + ranked))
    return [cust_match(idx.cust_by_name[k], 'חיפוש', 1, True) for k in out[:limit]]


def search_parts(idx, q, limit=15):
    q = (q or '').strip()
    exact = idx.part_by_code.get(code_key(q), []) if q else []
    ranked = [p for _, p in rank(q, idx.part_items, limit, 0.05)]
    out, seen = [], set()
    for p in exact + ranked:
        if p['PARTNAME'] not in seen:
            seen.add(p['PARTNAME'])
            out.append(part_match(p, 'חיפוש'))
    return out[:limit]
