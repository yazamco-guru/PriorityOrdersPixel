import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import DATA

DB_PATH = os.path.join(DATA, 'orders.db')
ATTACH_DIR = os.path.join(DATA, 'attachments')

SCHEMA = """
create table if not exists orders (
    id integer primary key autoincrement,
    mail_key text unique,
    source text,
    received text,
    sender_email text,
    sender_name text,
    subject text,
    body text,
    pdf_file text,
    pdf_name text,
    status text,
    problems text,
    data text,
    ordname text,
    error text,
    created text,
    updated text
);
create table if not exists mail_seen (gmid text primary key, seen text);
create table if not exists memory (kind text, key text, value text, primary key (kind, key));
create table if not exists meta (key text primary key, value text);
"""

JSON_FIELDS = ('data', 'problems')


def now():
    return datetime.now().isoformat(timespec='seconds')


@contextmanager
def connect():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    os.makedirs(ATTACH_DIR, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d['data'] = json.loads(d['data'] or '{}')
    d['problems'] = json.loads(d['problems'] or '[]')
    return d


def _dump(fields):
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v) for k, v in fields.items()}


def insert_order(**fields):
    fields = _dump({**fields, 'created': now(), 'updated': now()})
    cols = ', '.join(fields)
    marks = ', '.join('?' for _ in fields)
    with connect() as c:
        cur = c.execute(f'insert into orders ({cols}) values ({marks})', list(fields.values()))
        return cur.lastrowid


def update_order(order_id, **fields):
    fields = _dump({**fields, 'updated': now()})
    sets = ', '.join(f'{k} = ?' for k in fields)
    with connect() as c:
        c.execute(f'update orders set {sets} where id = ?', [*fields.values(), order_id])


def get_order(order_id):
    with connect() as c:
        return _row(c.execute('select * from orders where id = ?', (order_id,)).fetchone())


def list_orders():
    with connect() as c:
        return [_row(r) for r in c.execute('select * from orders order by received desc, id desc')]


def mail_seen(gmid):
    with connect() as c:
        return c.execute('select 1 from mail_seen where gmid = ?', (gmid,)).fetchone() is not None


def mark_mail_seen(gmid):
    with connect() as c:
        c.execute('insert or ignore into mail_seen values (?, ?)', (gmid, now()))


def mem_get(kind, key):
    with connect() as c:
        r = c.execute('select value from memory where kind = ? and key = ?', (kind, key)).fetchone()
        return r['value'] if r else None


def mem_set(kind, key, value):
    if not key:
        return
    with connect() as c:
        c.execute('insert or replace into memory values (?, ?, ?)', (kind, key, value))


def meta_get(key, default=None):
    with connect() as c:
        r = c.execute('select value from meta where key = ?', (key,)).fetchone()
        return json.loads(r['value']) if r else default


def meta_set(key, value):
    with connect() as c:
        c.execute('insert or replace into meta values (?, ?)', (key, json.dumps(value, ensure_ascii=False)))
