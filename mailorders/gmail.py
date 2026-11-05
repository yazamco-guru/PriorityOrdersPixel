"""משיכת מיילים עם PDF מתיבת Gmail (IMAP + סיסמת אפליקציה). התיבה נפתחת לקריאה בלבד – המיילים לא משתנים."""
import html
import imaplib
import re
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

HOST = 'imap.gmail.com'


def configured(cfg):
    g = cfg['gmail']
    return bool(g['user'] and g['appPassword'])


def _html_to_text(s):
    s = re.sub(r'(?is)<(script|style).*?</\1>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>', '\n', s)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    return '\n'.join(' '.join(ln.split()) for ln in s.splitlines() if ln.strip())


def parse_message(raw, gmid):
    msg = message_from_bytes(raw, policy=policy.default)
    addr = msg['from'].addresses[0] if msg['from'] and msg['from'].addresses else None
    try:
        received = parsedate_to_datetime(msg['date']).astimezone().isoformat(timespec='seconds')
    except (TypeError, ValueError):
        received = None

    body = ''
    part = msg.get_body(preferencelist=('plain', 'html'))
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == 'text/html':
            body = _html_to_text(body)

    pdfs = []
    for att in msg.iter_attachments():
        name = att.get_filename() or ''
        if att.get_content_type() == 'application/pdf' or name.lower().endswith('.pdf'):
            pdfs.append((name or 'order.pdf', att.get_payload(decode=True)))

    return {
        'gmid': gmid,
        'sender_email': addr.addr_spec.lower() if addr else '',
        'sender_name': addr.display_name if addr else '',
        'subject': str(msg['subject'] or ''),
        'received': received,
        'body': body.strip(),
        'pdfs': pdfs,
    }


def fetch_new(cfg, is_seen):
    """מחזיר את המיילים עם PDF מהימים האחרונים שעוד לא טופלו (לפי X-GM-MSGID)."""
    g = cfg['gmail']
    m = imaplib.IMAP4_SSL(HOST, 993, timeout=60)
    try:
        m.login(g['user'], g['appPassword'].replace(' ', ''))
        typ, _ = m.select(f'"{g["folder"]}"', readonly=True)
        if typ != 'OK':
            raise RuntimeError(f'התיקייה {g["folder"]} לא נמצאה בתיבה')
        # filter – חיפוש Gmail נוסף לסינון מיילים שאינם הזמנות, לדוגמה label:orders או subject:הזמנה
        query = f'has:attachment filename:pdf newer_than:{int(g["sinceDays"])}d {g.get("filter") or ""}'.strip()
        m.literal = query.encode('utf-8')  # כ-literal כדי שגם עברית תעבור
        typ, data = m.uid('SEARCH', 'CHARSET', 'UTF-8', 'X-GM-RAW')
        uids = data[0].split() if data and data[0] else []
        if not uids:
            return []

        typ, data = m.uid('FETCH', b','.join(uids), '(X-GM-MSGID)')
        new = []
        for item in data:
            s = item.decode() if isinstance(item, bytes) else ''
            gm, uid = re.search(r'X-GM-MSGID (\d+)', s), re.search(r'UID (\d+)', s)
            if gm and uid and not is_seen(gm.group(1)):
                new.append((uid.group(1), gm.group(1)))

        out = []
        for uid, gmid in new:
            typ, data = m.uid('FETCH', uid, '(BODY.PEEK[])')
            raw = next((x[1] for x in data if isinstance(x, tuple)), None)
            if raw:
                out.append(parse_message(raw, gmid))
        return out
    finally:
        try:
            m.logout()
        except Exception:
            pass
