import os
import uuid

from flask import Flask, abort, jsonify, request, send_from_directory

from . import config, db, gmail, matching, priority, worker

STATIC = os.path.join(os.path.dirname(__file__), 'static')
app = Flask(__name__, static_folder=STATIC, static_url_path='/static')
app.json.ensure_ascii = False


def err(msg, code=400):
    return jsonify({'error': str(msg)}), code


def summary(o):
    d = o['data']
    return {
        'id': o['id'], 'received': o['received'], 'source': o['source'],
        'sender_email': o['sender_email'], 'sender_name': o['sender_name'], 'subject': o['subject'],
        'pdf_name': o['pdf_name'], 'status': o['status'], 'problems': o['problems'],
        'ordname': o['ordname'], 'error': o['error'],
        'customer': d.get('customer'), 'orderNum': d.get('header', {}).get('orderNum'),
        'lines': len(d.get('lines', [])),
    }


@app.get('/')
def home():
    return send_from_directory(STATIC, 'index.html')


@app.get('/api/status')
def status():
    cfg, idx = config.load(), worker.index()
    cache = priority.load_cache(cfg) or {}
    return jsonify({
        'gmail': {'configured': gmail.configured(cfg), 'user': cfg['gmail']['user'],
                  'pollMinutes': cfg['gmail']['pollMinutes'], 'last': db.meta_get('poll')},
        'priority': {'configured': priority.configured(cfg), 'date': cache.get('date'),
                     'parts': len(idx.parts), 'customers': len(idx.customers), 'contacts': len(idx.contacts)},
    })


@app.get('/api/orders')
def orders():
    return jsonify([summary(o) for o in db.list_orders()])


@app.get('/api/orders/<int:oid>')
def order(oid):
    o = db.get_order(oid) or abort(404)
    return jsonify(o)


@app.put('/api/orders/<int:oid>')
def update(oid):
    """עדכון ידני מהדשבורד: header, customer, lines"""
    o = db.get_order(oid) or abort(404)
    if o['ordname']:
        return err('ההזמנה כבר נקלטה בפריוריטי')
    body, d = request.get_json(force=True), o['data']
    if 'header' in body:
        d['header'].update({k: body['header'].get(k, '') for k in ('orderNum', 'orderDate', 'dueDate', 'notes')})
    if 'customer' in body:
        d['customer'] = body['customer']
        d['customerManual'] = bool(body['customer'])
        if body['customer']:
            for comp in d.get('parsed', {}).get('companies', []):
                db.mem_set('customer', 'c:' + matching.company_text(comp), body['customer']['code'])
            if o['sender_email']:
                db.mem_set('customer', 'e:' + o['sender_email'].lower(), body['customer']['code'])
    if 'lines' in body:
        lines = []
        for ln in body['lines']:
            ln = {k: ln.get(k) for k in ('sku', 'description', 'qty', 'price', 'part', 'manual', 'candidates')}
            ln['manual'] = bool(ln.get('manual') and ln.get('part'))
            if ln['manual']:
                for key in matching.part_mem_keys(ln):
                    db.mem_set('part', key, ln['part']['code'])
            lines.append(ln)
        d['lines'] = lines
    o['status'] = 'stuck' if o['status'] == 'error' else o['status']
    db.update_order(oid, data=d, status=o['status'], error=None)
    return jsonify(worker.analyze(db.get_order(oid)))


@app.post('/api/orders/<int:oid>/send')
def send(oid):
    try:
        return jsonify(worker.send(oid))
    except Exception as e:
        return err(e)


@app.post('/api/orders/<int:oid>/ignore')
def ignore(oid):
    o = db.get_order(oid) or abort(404)
    if o['ordname']:
        return err('ההזמנה כבר נקלטה בפריוריטי')
    hide = request.get_json(force=True).get('ignore', True)
    db.update_order(oid, status='ignored' if hide else 'stuck')
    return jsonify(worker.analyze(db.get_order(oid)))


@app.get('/api/orders/<int:oid>/pdf')
def pdf(oid):
    o = db.get_order(oid) or abort(404)
    return send_from_directory(db.ATTACH_DIR, o['pdf_file'], mimetype='application/pdf')


@app.post('/api/upload')
def upload():
    files = request.files.getlist('files')
    if not files:
        return err('לא נבחר קובץ')
    mail = {'sender_email': request.form.get('sender_email', '').strip().lower(),
            'sender_name': request.form.get('sender_name', '').strip(),
            'subject': 'העלאה ידנית', 'received': db.now()}
    out = []
    for f in files:
        out.append(summary(worker.add_pdf(mail, f.filename, f.read(), f'upload:{uuid.uuid4().hex}', 'upload')))
    return jsonify(out)


@app.post('/api/poll')
def poll():
    try:
        return jsonify({'added': worker.poll_once()})
    except Exception as e:
        return err(e)


@app.post('/api/refresh')
def refresh():
    try:
        return jsonify(worker.refresh_priority())
    except Exception as e:
        return err(e)


@app.get('/api/search/customers')
def search_customers():
    return jsonify(matching.search_customers(worker.index(), request.args.get('q', '')))


@app.get('/api/search/parts')
def search_parts():
    return jsonify(matching.search_parts(worker.index(), request.args.get('q', '')))
