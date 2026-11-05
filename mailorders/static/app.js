'use strict';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const STATUS = {
  ready: 'מוכנה לשליחה',
  stuck: 'תקועה',
  error: 'שגיאה',
  sent: 'נקלטה',
  ignored: 'הוסתרה',
};
const TABS = [['all', 'הכל'], ['ready', 'מוכנות לשליחה'], ['stuck', 'תקועות'], ['error', 'שגיאות'], ['sent', 'נקלטו'], ['ignored', 'הוסתרו']];

const S = { orders: [], tab: 'all', selected: new Set(), current: null };

async function api(path, opts = {}) {
  const res = await fetch(path, opts.body && !(opts.body instanceof FormData)
    ? { ...opts, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) } : opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, isErr ? 7000 : 3500);
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}

const fmtDateTime = s => {
  if (!s) return '';
  const d = new Date(s);
  return isNaN(d) ? s : d.toLocaleString('he-IL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

/* ---------- סטטוס חיבורים ---------- */

async function loadStatus() {
  const st = await api('/api/status');
  const g = st.gmail, p = st.priority;
  const pg = $('#pillGmail');
  if (!g.configured) { pg.className = 'pill warn'; pg.textContent = 'Gmail לא מוגדר'; }
  else if (g.last && !g.last.ok) { pg.className = 'pill err'; pg.textContent = g.last.message; }
  else { pg.className = 'pill ok'; pg.textContent = `${g.user} · נבדק ${fmtDateTime(g.last?.at)} · כל ${g.pollMinutes} דק'`; }
  const pp = $('#pillPriority');
  if (!p.configured) { pp.className = 'pill warn'; pp.textContent = 'פריוריטי לא מוגדר'; }
  else if (!p.date) { pp.className = 'pill warn'; pp.textContent = 'פריוריטי: יש לרענן נתונים'; }
  else { pp.className = 'pill ok'; pp.textContent = `פריוריטי: ${p.customers.toLocaleString()} לקוחות · ${p.parts.toLocaleString()} פריטים · ${fmtDateTime(p.date)}`; }
}

/* ---------- רשימת הזמנות ---------- */

async function loadOrders() {
  S.orders = await api('/api/orders');
  const ids = new Set(S.orders.filter(o => o.status === 'ready').map(o => o.id));
  S.selected = new Set([...S.selected].filter(id => ids.has(id)));
  render();
}

function visible() {
  const q = $('#search').value.trim().toLowerCase();
  return S.orders.filter(o => {
    if (S.tab === 'all' ? o.status === 'ignored' : o.status !== S.tab) return false;
    if (!q) return true;
    return [o.sender_email, o.sender_name, o.subject, o.pdf_name, o.orderNum, o.ordname, o.customer?.code, o.customer?.des]
      .some(v => String(v ?? '').toLowerCase().includes(q));
  });
}

function render() {
  const count = k => S.orders.filter(o => (k === 'all' ? o.status !== 'ignored' : o.status === k)).length;
  $('#tabs').innerHTML = TABS.map(([k, label]) =>
    `<button class="tab ${k} ${S.tab === k ? 'active' : ''}" data-tab="${k}"><b>${count(k)}</b><span>${label}</span></button>`).join('');

  const list = visible();
  $('#empty').hidden = list.length > 0;
  $('#rows').innerHTML = list.map(o => {
    const why = o.status === 'error' ? o.error : o.status === 'stuck' ? o.problems.slice(0, 2).join(' · ') + (o.problems.length > 2 ? ` (+${o.problems.length - 2})` : '') : '';
    const cust = o.customer ? `${esc(o.customer.des)} <div class="sub">${esc(o.customer.code)}</div>` : '<span class="sub">—</span>';
    return `<tr data-id="${o.id}">
      <td class="chk">${o.status === 'ready' ? `<input type="checkbox" data-sel="${o.id}" ${S.selected.has(o.id) ? 'checked' : ''}>` : ''}</td>
      <td>${fmtDateTime(o.received)}</td>
      <td>${esc(o.sender_name || o.sender_email || '—')}<div class="sub">${esc(o.sender_name ? o.sender_email : '')}</div></td>
      <td>${esc(o.pdf_name)}<div class="sub">${esc(o.subject)}</div></td>
      <td>${cust}</td>
      <td>${esc(o.orderNum || '')}</td>
      <td>${o.lines}</td>
      <td><span class="badge ${o.status}">${STATUS[o.status] || o.status}${o.ordname ? ' · ' + esc(o.ordname) : ''}</span>
          ${why ? `<div class="why ${o.status === 'error' ? 'error' : ''}">${esc(why)}</div>` : ''}</td>
    </tr>`;
  }).join('');
  const ready = list.filter(o => o.status === 'ready');
  $('#chkAll').checked = ready.length > 0 && ready.every(o => S.selected.has(o.id));
  $('#btnSendSel').disabled = S.selected.size === 0;
  $('#btnSendSel').textContent = `שלח מסומנות לפריוריטי${S.selected.size ? ` (${S.selected.size})` : ''}`;
}

async function sendSelected() {
  const ids = [...S.selected];
  if (!confirm(`לפתוח ${ids.length} הזמנות בפריוריטי?`)) return;
  let ok = 0;
  const errors = [];
  for (const id of ids) {
    try { await api(`/api/orders/${id}/send`, { method: 'POST' }); ok++; } catch (e) { errors.push(e.message); }
    S.selected.delete(id);
  }
  await loadOrders();
  toast(`נפתחו ${ok} הזמנות` + (errors.length ? ` · ${errors.length} נכשלו: ${errors[0]}` : ''), errors.length > 0);
}

/* ---------- מסך הזמנה ---------- */

async function openOrder(id) {
  S.current = await api(`/api/orders/${id}`);
  $('#dPdf').src = `/api/orders/${id}/pdf`;
  $('#detail').hidden = false;
  renderDetail();
}

function matchHtml(m, kind, idx = '') {
  const pick = `<button class="link" data-pick="${kind}" data-i="${idx}">${m ? 'החלפה' : 'בחירה'}</button>`;
  if (!m) return `<span class="match"><span class="chip none">לא זוהה</span>${pick}</span>`;
  const how = m.method + (m.score < 1 ? ` · ${Math.round(m.score * 100)}%` : '');
  return `<span class="match"><span class="chip">${esc(m.code)} – ${esc(m.des)} <small>(${esc(how)})</small></span>${pick}</span>`;
}

function candsHtml(cands, kind, idx = '') {
  if (!cands?.length) return '';
  return `<div class="cands">${cands.map((c, j) =>
    `<button class="cand" data-cand="${kind}" data-i="${idx}" data-j="${j}">${esc(c.code)} – ${esc(c.des)} (${Math.round(c.score * 100)}%)</button>`).join('')}</div>`;
}

function renderDetail() {
  const o = S.current, d = o.data, h = d.header, locked = !!o.ordname;
  $('#dTitle').textContent = `${o.pdf_name || 'הזמנה'} · ${STATUS[o.status] || ''}${o.ordname ? ' ' + o.ordname : ''}`;
  const parsed = d.parsed || {};
  const problems = o.status === 'error' ? [o.error] : (o.problems || []);
  const ro = locked ? 'disabled' : '';

  $('#dForm').innerHTML = `
    ${problems.length && !locked ? `<div class="problems ${o.status === 'error' ? 'error' : ''}"><b>${o.status === 'error' ? 'השליחה נכשלה' : 'מה חסר כדי לשלוח'}</b>
      <ul>${problems.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>` : ''}
    ${locked && o.error ? `<div class="problems">${esc(o.error)}</div>` : ''}
    ${d.parseError ? `<div class="problems error">${esc(d.parseError)}</div>` : ''}

    <div class="box">
      <h3>המייל</h3>
      <dl class="mail-meta">
        <dt>שולח</dt><dd>${esc(o.sender_name)} ${o.sender_email ? `&lt;${esc(o.sender_email)}&gt;` : ''}</dd>
        <dt>נושא</dt><dd>${esc(o.subject)}</dd>
        <dt>התקבל</dt><dd>${fmtDateTime(o.received)}</dd>
        <dt>זוהה במסמך</dt><dd>${esc([...(parsed.companies || []), ...(parsed.vatnums || []).map(v => 'ח.פ ' + v)].join(' · ') || '—')} <span class="sub">(פענוח: ${esc(parsed.parser || '')})</span></dd>
      </dl>
      ${o.body ? `<details><summary>גוף המייל</summary><pre>${esc(o.body)}</pre></details>` : ''}
      ${d.pdfText ? `<details><summary>הטקסט שנקרא מה-PDF</summary><pre>${esc(d.pdfText)}</pre></details>` : ''}
    </div>

    <div class="box">
      <h3>לקוח</h3>
      ${locked ? matchHtml(d.customer).replace(/<button.*?<\/button>/, '') : matchHtml(d.customer, 'customer') + (d.customer ? '' : candsHtml(d.customerCandidates, 'customer'))}
    </div>

    <div class="box">
      <h3>פרטי הזמנה</h3>
      <div class="fields">
        <div><label>מס' הזמנה (הזמנתכם)</label><input type="text" data-h="orderNum" value="${esc(h.orderNum)}" ${ro}></div>
        <div><label>תאריך הזמנה</label><input type="date" data-h="orderDate" value="${esc(h.orderDate)}" ${ro}></div>
        <div><label>תאריך אספקה</label><input type="date" data-h="dueDate" value="${esc(h.dueDate)}" ${ro}></div>
        <div><label>פרטים</label><input type="text" data-h="notes" value="${esc(h.notes)}" maxlength="32" ${ro}></div>
      </div>
    </div>

    <div class="box">
      <h3>שורות</h3>
      <table class="lines">
        <thead><tr><th>#</th><th>מק"ט במסמך</th><th>תיאור במסמך</th><th>כמות</th><th>מחיר</th><th>פריט בפריוריטי</th><th></th></tr></thead>
        <tbody>
          ${d.lines.map((ln, i) => `<tr>
            <td>${i + 1}</td>
            <td><input type="text" data-l="sku" data-i="${i}" value="${esc(ln.sku)}" ${ro}></td>
            <td><input type="text" data-l="description" data-i="${i}" value="${esc(ln.description)}" ${ro}></td>
            <td class="num"><input type="number" step="any" data-l="qty" data-i="${i}" value="${ln.qty ?? ''}" ${ro}></td>
            <td class="num"><input type="number" step="any" data-l="price" data-i="${i}" value="${ln.price ?? ''}" ${ro}></td>
            <td class="part">${locked ? esc(ln.part ? `${ln.part.code} – ${ln.part.des}` : '') : matchHtml(ln.part, 'part', i) + (ln.part ? '' : candsHtml(ln.candidates, 'part', i))}</td>
            <td>${locked ? '' : `<button class="ghost" title="מחיקת שורה" data-del="${i}">🗑</button>`}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      ${locked ? '' : '<button class="link" id="addLine">+ הוספת שורה</button>'}
    </div>`;

  $('#dFoot').innerHTML = locked ? '<span class="sub">ההזמנה נקלטה בפריוריטי</span>' : `
    <button class="primary" id="dSend">${o.status === 'ready' ? 'שלח לפריוריטי' : 'שמור ושלח לפריוריטי'}</button>
    <button id="dSave">שמור</button>
    <span class="spacer"></span>
    <button class="danger" id="dIgnore">${o.status === 'ignored' ? 'החזר לרשימה' : 'הסתר (לא הזמנה)'}</button>`;
}

// קריאת השדות מהטופס לתוך ההזמנה הנוכחית
function collect() {
  const d = S.current.data;
  document.querySelectorAll('[data-h]').forEach(el => { d.header[el.dataset.h] = el.value; });
  document.querySelectorAll('[data-l]').forEach(el => {
    const ln = d.lines[+el.dataset.i], f = el.dataset.l;
    ln[f] = ['qty', 'price'].includes(f) ? (el.value === '' ? null : Number(el.value)) : el.value;
  });
}

async function save(extra = {}) {
  collect();
  const d = S.current.data;
  S.current = await api(`/api/orders/${S.current.id}`, { method: 'PUT', body: { header: d.header, lines: d.lines, ...extra } });
  renderDetail();
  loadOrders();
}

async function setCustomer(c) {
  await save({ customer: c && { ...c, method: 'בחירה ידנית', score: 1, certain: true } });
}

async function setPart(i, p) {
  collect();
  Object.assign(S.current.data.lines[i], { part: { ...p, method: 'בחירה ידנית', score: 1, certain: true }, manual: true, candidates: [] });
  await save();
}

/* ---------- חיפוש לקוח / פריט ---------- */

function openPicker(kind, i) {
  $('#pTitle').textContent = kind === 'customer' ? 'בחירת לקוח' : `בחירת פריט לשורה ${+i + 1}`;
  const input = $('#pSearch');
  const ln = kind === 'part' ? S.current.data.lines[i] : null;
  input.value = kind === 'customer' ? (S.current.data.parsed?.companies?.[0] || S.current.sender_name || '') : (ln.description || ln.sku || '');
  S.picker = { kind, i };
  $('#picker').hidden = false;
  input.focus();
  input.select();
  searchPicker();
}

let pickTimer;
async function searchPicker() {
  clearTimeout(pickTimer);
  pickTimer = setTimeout(async () => {
    const { kind } = S.picker;
    const res = await api(`/api/search/${kind === 'customer' ? 'customers' : 'parts'}?q=${encodeURIComponent($('#pSearch').value)}`);
    S.picker.results = res;
    $('#pList').innerHTML = res.length ? res.map((r, j) => `<li data-r="${j}"><b>${esc(r.code)}</b>${esc(r.des)}</li>`).join('')
      : '<li class="sub">לא נמצאו תוצאות</li>';
  }, 200);
}

/* ---------- אירועים ---------- */

function bind() {
  $('#tabs').addEventListener('click', e => {
    const b = e.target.closest('[data-tab]');
    if (b) { S.tab = b.dataset.tab; render(); }
  });
  $('#search').addEventListener('input', render);
  $('#rows').addEventListener('click', e => {
    const sel = e.target.closest('[data-sel]');
    if (sel) { sel.checked ? S.selected.add(+sel.dataset.sel) : S.selected.delete(+sel.dataset.sel); render(); return; }
    const tr = e.target.closest('tr[data-id]');
    if (tr) openOrder(+tr.dataset.id).catch(err => toast(err.message, true));
  });
  $('#chkAll').addEventListener('change', e => {
    visible().filter(o => o.status === 'ready').forEach(o => (e.target.checked ? S.selected.add(o.id) : S.selected.delete(o.id)));
    render();
  });
  $('#btnSendSel').addEventListener('click', e => busy(e.target, sendSelected));

  $('#btnPoll').addEventListener('click', e => busy(e.target, async () => {
    const r = await api('/api/poll', { method: 'POST' });
    toast(r.added ? `נוספו ${r.added} הזמנות חדשות` : 'אין מיילים חדשים עם PDF');
    await Promise.all([loadOrders(), loadStatus()]);
  }));
  $('#btnRefresh').addEventListener('click', e => busy(e.target, async () => {
    const r = await api('/api/refresh', { method: 'POST' });
    toast(`נטענו ${r.customers} לקוחות, ${r.contacts} אנשי קשר ו-${r.parts} פריטים` + (r.warnings.length ? ` · ${r.warnings[0]}` : ''));
    await Promise.all([loadOrders(), loadStatus()]);
  }));
  $('#fileInput').addEventListener('change', async e => {
    const fd = new FormData();
    [...e.target.files].forEach(f => fd.append('files', f));
    e.target.value = '';
    try {
      const r = await api('/api/upload', { method: 'POST', body: fd });
      toast(`נוספו ${r.length} הזמנות`);
      await loadOrders();
      if (r.length === 1) openOrder(r[0].id);
    } catch (err) { toast(err.message, true); }
  });

  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => {
    b.closest('.overlay').hidden = true;
    if (b.closest('#detail')) { S.current = null; $('#dPdf').src = 'about:blank'; }
  }));

  $('#dForm').addEventListener('click', e => {
    const t = e.target;
    if (t.dataset.pick) openPicker(t.dataset.pick, t.dataset.i);
    else if (t.dataset.cand) {
      const c = t.dataset.cand === 'customer' ? S.current.data.customerCandidates[+t.dataset.j]
        : S.current.data.lines[+t.dataset.i].candidates[+t.dataset.j];
      (t.dataset.cand === 'customer' ? setCustomer(c) : setPart(+t.dataset.i, c)).catch(err => toast(err.message, true));
    } else if (t.dataset.del !== undefined) {
      collect();
      S.current.data.lines.splice(+t.dataset.del, 1);
      save().catch(err => toast(err.message, true));
    } else if (t.id === 'addLine') {
      collect();
      S.current.data.lines.push({ sku: '', description: '', qty: 1, price: null });
      renderDetail();
    }
  });

  $('#dFoot').addEventListener('click', e => {
    const t = e.target;
    if (t.id === 'dSave') busy(t, async () => { await save(); toast('נשמר'); });
    if (t.id === 'dSend') busy(t, async () => {
      await save();
      if (S.current.status !== 'ready') { toast('אי אפשר לשלוח עדיין: ' + S.current.problems.join(' · '), true); return; }
      S.current = await api(`/api/orders/${S.current.id}/send`, { method: 'POST' }).catch(async err => {
        S.current = await api(`/api/orders/${S.current.id}`);
        throw err;
      }).finally(() => { renderDetail(); loadOrders(); });
      toast(`נפתחה הזמנה ${S.current.ordname}` + (S.current.error ? ` · ${S.current.error}` : ''));
    });
    if (t.id === 'dIgnore') busy(t, async () => {
      S.current = await api(`/api/orders/${S.current.id}/ignore`, { method: 'POST', body: { ignore: S.current.status !== 'ignored' } });
      renderDetail();
      loadOrders();
    });
  });

  $('#pSearch').addEventListener('input', searchPicker);
  $('#pList').addEventListener('click', e => {
    const li = e.target.closest('[data-r]');
    if (!li) return;
    const r = S.picker.results[+li.dataset.r];
    $('#picker').hidden = true;
    (S.picker.kind === 'customer' ? setCustomer(r) : setPart(+S.picker.i, r)).catch(err => toast(err.message, true));
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!$('#picker').hidden) $('#picker').hidden = true;
    else if (!$('#detail').hidden) $('#detail [data-close]').click();
  });
}

bind();
loadStatus().catch(e => toast(e.message, true));
loadOrders().catch(e => toast(e.message, true));
setInterval(() => {
  if (!S.current) loadOrders().catch(() => {});
  loadStatus().catch(() => {});
}, 30000);
