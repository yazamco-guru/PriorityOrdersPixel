"""פענוח כללי של הזמנה מ-PDF דיגיטלי: כותרת (מס' הזמנה, תאריכים, חברה, ח.פ) ושורות מטבלת הפריטים."""
import re
from datetime import date

# מילות מפתח לכותרות עמודות בטבלת הפריטים. הסדר חשוב: "קוד פריט" הוא מק"ט, לא תיאור
COLUMNS = [
    ('sku', ['מקט', 'קוד פריט', 'קוד מוצר', 'מספר פריט', 'מס פריט', 'ברקוד', 'קטלוגי', 'קוד',
             'sku', 'item code', 'item no', 'part no', 'part number', 'catalog', 'code']),
    ('qty', ['כמות', 'כמ', 'יחידות', 'qty', 'quantity', 'units']),
    ('price', ['מחיר ליחידה', 'מחיר יח', 'מחיר', 'unit price', 'price']),
    ('description', ['תיאור', 'שם פריט', 'שם מוצר', 'פריט', 'מוצר', 'description', 'item', 'product']),
]
TOTAL_WORDS = ('סהכ', 'סך הכל', 'total', 'subtotal')

DATE_RE = re.compile(r'(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?!\d)')
ORDER_RE = re.compile(
    r'(?:הזמנת\s+רכש|הזמנה|הזמנת|purchase\s+order|order|p\.?o\.?)\s*'
    r'(?:מס(?:פר)?[\'׳.]?|no\.?|number|#)?\s*[:#.\-]?\s*([A-Za-z]{0,4}[-/]?\d[\w\-/]*)',
    re.I)
VAT_RE = re.compile(r'(?:ח\.?\s?פ\.?|ח["״]פ|ע\.?\s?מ\.?|ע["״]מ|עוסק\s+מורשה|מספר\s+עוסק|vat(?:\s+no)?|company\s+id)\D{0,6}(\d{8,9})', re.I)
COMPANY_RE = re.compile(r'(.{2,60}?)\s*(?:בע["״]?מ|בעמ|ltd\.?|limited|inc\.?)', re.I)


def _key(s):
    return ' '.join(re.sub(r'["\'`״׳.]', '', str(s).lower()).split())


def number(s):
    """'1,234.50' / '₪ 12' -> float, אחרת None"""
    t = re.sub(r'[^\d.,\-]', '', str(s or ''))
    if not t or not re.search(r'\d', t):
        return None
    t = t.replace(',', '')
    try:
        return float(t)
    except ValueError:
        return None


def to_date(d, m, y):
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return date(y, int(m), int(d)).isoformat()
    except ValueError:
        return None


def _map_header(row):
    cols = {}
    for i, cell in enumerate(row):
        k = _key(cell)
        if not k or any(w in k for w in TOTAL_WORDS):
            continue
        for field, words in COLUMNS:
            if field not in cols and any(w in k for w in words):
                cols[field] = i
                break
    return cols if 'qty' in cols and ('sku' in cols or 'description' in cols) else None


def _lines_from_tables(tables):
    lines = []
    for t in tables:
        cols, header_at = None, -1
        for i, row in enumerate(t):
            cols = _map_header(row)
            if cols:
                header_at = i
                break
        if not cols:
            continue
        for row in t[header_at + 1:]:
            cell = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else ''
            if any(w in _key(' '.join(row)) for w in TOTAL_WORDS):
                break
            qty = number(cell('qty'))
            sku, desc = cell('sku').strip(), cell('description').strip()
            if not qty or not (sku or desc):
                continue
            lines.append({'sku': sku, 'description': desc, 'qty': qty, 'price': number(cell('price'))})
    return lines


def _find_date(lines, words):
    for ln in lines:
        if any(w in ln.lower() for w in words):
            m = DATE_RE.search(ln)
            if m:
                return to_date(*m.groups())
    return None


def parse(doc, mail):
    lines, text = doc['lines'], doc['text']

    m = ORDER_RE.search(text)
    order_num = m.group(1) if m else ''

    due = _find_date(lines, ('אספקה', 'משלוח', 'delivery', 'due'))
    order_date = _find_date([ln for ln in lines if 'אספקה' not in ln], ('תאריך', 'date'))
    if not order_date:
        m = DATE_RE.search(text)
        order_date = to_date(*m.groups()) if m else None

    companies = []
    for ln in lines:
        for c in COMPANY_RE.findall(ln):
            c = c.strip(' :-,')
            if c and c not in companies:
                companies.append(c)

    return {
        'orderNum': order_num,
        'orderDate': order_date,
        'dueDate': due,
        'companies': companies,
        'vatnums': list(dict.fromkeys(VAT_RE.findall(text))),
        'notes': '',
        'lines': _lines_from_tables(doc['tables']),
    }
