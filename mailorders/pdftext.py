"""חילוץ טקסט וטבלאות מ-PDF (מקומי, pdfplumber).

מודול נפרד כדי שאפשר יהיה להחליף / להוסיף ספק (OCR, PDF.co, Claude) בלי לגעת בשאר הכלי.
"""
import re

import pdfplumber

HEB_WORD = re.compile(r'[א-ת]{2,}')
FINALS = 'ךםןףץ'
LTR_RUN = re.compile(r'[0-9A-Za-z@][0-9A-Za-z@.,:/\-_]*[0-9A-Za-z]|[0-9A-Za-z]')
MIRROR = str.maketrans('()[]{}<>', ')(][}{><')


def _looks_reversed(lines):
    # בטקסט עברי תקין אותיות סופיות מופיעות בסוף מילה; ב-PDF שנשמר בסדר ויזואלי – בתחילתה
    start = end = 0
    for ln in lines:
        for w in HEB_WORD.findall(ln):
            start += w[0] in FINALS
            end += w[-1] in FINALS
    return start > end


def _flip(s):
    # היפוך שורה; רצפים של ספרות ואותיות לועזיות חוזרים לכיוון המקורי
    if not s:
        return s
    r = s[::-1].translate(MIRROR)
    return LTR_RUN.sub(lambda m: m.group(0)[::-1], r)


def _clean(cell):
    return ' '.join(str(cell).split()) if cell is not None else ''


def extract(path):
    """מחזיר {'text', 'lines', 'tables', 'pages', 'scanned', 'reversed'}.
    tables – רשימת טבלאות, כל טבלה רשימת שורות של תאים (מחרוזות)."""
    lines, tables, pages = [], [], 0
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            lines.extend((page.extract_text() or '').splitlines())
            for t in page.extract_tables() or []:
                tables.append([[_clean(c) for c in row] for row in t])

    reversed_ = _looks_reversed(lines + [c for t in tables for row in t for c in row])
    if reversed_:
        lines = [_flip(ln) for ln in lines]
        tables = [[[_flip(c) for c in row] for row in t] for t in tables]

    lines = [ln.strip() for ln in lines if ln.strip()]
    return {
        'text': '\n'.join(lines),
        'lines': lines,
        'tables': tables,
        'pages': pages,
        'scanned': not lines and not tables,
        'reversed': reversed_,
    }
