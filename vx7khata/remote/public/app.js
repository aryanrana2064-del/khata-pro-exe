'use strict';

const $ = (id) => document.getElementById(id);

let token = new URLSearchParams(location.search).get('t') || localStorage.getItem('vx7_token') || '';
let ws = null;
let connected = false;
let streamOn = false;
let streamDesired = false;
let pendingConfirm = null;
let muted = false;
let logoutOnClose = false;

function send(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function setStatus(kind, text) {
  const dot = $('dot');
  dot.classList.remove('ok', 'bad');
  if (kind === 'ok') dot.classList.add('ok');
  if (kind === 'bad') dot.classList.add('bad');
  $('connText').textContent = text;
}

let toastTimer = null;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2600);
}

function showLogin(message) {
  $('login').classList.remove('hidden');
  $('loginErr').textContent = message || '';
}

function hideLogin() {
  $('login').classList.add('hidden');
  $('loginErr').textContent = '';
}

function connectWS() {
  if (!token) {
    showLogin('');
    return;
  }
  logoutOnClose = false;
  setStatus('wait', 'Connecting…');
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?t=${encodeURIComponent(token)}`);
  let gotHello = false;

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.type === 'hello') {
      gotHello = true;
      connected = true;
      setStatus('ok', 'Connected');
      if (streamDesired) send({ type: 'stream', on: true, fps: 1 });
    }
    handleMessage(msg);
  };

  ws.onclose = () => {
    connected = false;
    setStatus('bad', 'Disconnected');
    if (!gotHello) {
      logoutOnClose = true;
      showLogin('Token galat hai ya Wi-Fi network off hai');
      return;
    }
    if (streamOn) {
      streamOn = false;
      updateStreamButton();
    }
    setTimeout(() => {
      if (!logoutOnClose) connectWS();
    }, 2500);
  };

  ws.onerror = () => { /* onclose handles it */ };
}

function updateStreamButton() {
  const btn = $('streamBtn');
  btn.textContent = streamOn ? 'Live screen: ON' : 'Live screen: OFF';
  btn.classList.toggle('accent', !streamOn);
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'frame':
      $('screen').src = 'data:image/jpeg;base64,' + msg.jpeg;
      $('screenMsg').classList.add('hidden');
      return;
    case 'shot':
      $('screen').src = 'data:image/jpeg;base64,' + msg.jpeg;
      $('screenMsg').classList.add('hidden');
      switchTab('remote');
      toast('Screenshot aa gaya');
      return;
    case 'chat_reply':
      addBubble(msg.text || '…', 'bot');
      for (const src of msg.screenshots || []) {
        const img = document.createElement('img');
        img.src = src;
        const bubble = document.createElement('div');
        bubble.className = 'bubble bot';
        bubble.appendChild(img);
        $('chatLog').appendChild(bubble);
      }
      if (msg.pending) showConfirm(msg.pending.action, msg.pending.params);
      scrollChat();
      return;
    case 'action_result':
      toast(msg.ok ? (msg.text || 'Done') : ('Failed: ' + (msg.error || 'unknown')));
      addBubble(msg.ok ? '✅ ' + (msg.text || 'Done') : '❌ ' + (msg.error || 'Failed'), 'bot');
      if (msg.screenshot) {
        const img = document.createElement('img');
        img.src = 'data:image/jpeg;base64,' + msg.screenshot;
        const bubble = document.createElement('div');
        bubble.className = 'bubble bot';
        bubble.appendChild(img);
        $('chatLog').appendChild(bubble);
      }
      scrollChat();
      return;
    case 'needs_confirm':
      showConfirm(msg.action, msg.params);
      switchTab('chat');
      toast('Confirm karna hoga: ' + (msg.label || msg.action));
      return;
    case 'error':
      toast(msg.message || 'Error');
      return;
    case 'stream_state':
      streamOn = Boolean(msg.on) && streamDesired;
      updateStreamButton();
      return;
    default:
      return;
  }
}

function addBubble(text, who) {
  const div = document.createElement('div');
  div.className = 'bubble ' + who;
  div.textContent = text;
  $('chatLog').appendChild(div);
  scrollChat();
}

function scrollChat() {
  const log = $('chatLog');
  log.scrollTop = log.scrollHeight;
}

function showConfirm(action, params) {
  pendingConfirm = { action, params };
  $('confirmLabel').textContent = `${action.replace(/_/g, ' ')} — confirm?`;
  $('confirmBar').classList.remove('hidden');
}

function hideConfirm() {
  pendingConfirm = null;
  $('confirmBar').classList.add('hidden');
}

function switchTab(name) {
  document.querySelectorAll('.tabs button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.id === 'tab-' + name);
  });
  if (name === 'pad') setTimeout(() => $('touchpad').focus(), 50);
  if (name === 'khata') loadKhata();
}

document.querySelectorAll('.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// ------------------------------------------------------------ Khata tab
let khataSearchTimer = null;

function money(paise) {
  const value = (Number(paise) || 0) / 100;
  return '₹' + value.toLocaleString('en-IN', {
    minimumFractionDigits: value % 1 ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

async function khataApi(path, options = {}) {
  const sep = path.includes('?') ? '&' : '?';
  const response = await fetch(`${path}${sep}t=${encodeURIComponent(token)}`, {
    method: options.method || 'GET',
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + response.status));
  return data;
}

function renderKhataSummary(data) {
  $('kSummary').textContent = `${data.day}  •  Udhaar ${money(data.total_udhaar_paise)}  •  `
    + `Jama ${money(data.total_jama_paise)}  •  Net ${money(data.net_paise)}  •  ${data.transaction_count} entries`;
}

function renderKhataCustomers(rows) {
  const list = $('kList');
  const options = $('kCustomersList');
  list.innerHTML = '';
  options.innerHTML = '';
  if (!rows.length) {
    list.textContent = 'Koi customer nahi mila.';
    return;
  }
  for (const c of rows) {
    const opt = document.createElement('option');
    opt.value = c.name;
    options.appendChild(opt);

    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const name = document.createElement('div');
    name.textContent = c.name;
    const meta = document.createElement('small');
    meta.textContent = [c.mobile, `${c.txn_count} entries`].filter(Boolean).join('  •  ');
    info.append(name, meta);
    const amt = document.createElement('span');
    amt.className = c.balance_paise > 0 ? 'amt-dr' : c.balance_paise < 0 ? 'amt-cr' : '';
    amt.textContent = c.balance_paise === 0 ? 'Settled'
      : money(Math.abs(c.balance_paise)) + (c.balance_paise > 0 ? ' Dr' : ' Cr');
    item.append(info, amt);
    const ledgerBtn = document.createElement('button');
    ledgerBtn.textContent = '›';
    ledgerBtn.title = 'Ledger dekho';
    ledgerBtn.addEventListener('click', (event) => {
      event.stopPropagation();
      openCustomerDetail(c.id, c.name);
    });
    item.append(ledgerBtn);
    item.addEventListener('click', () => {
      $('kCustomer').value = c.name;
      $('kItem').focus();
    });
    list.appendChild(item);
  }
}

function renderKhataPending(rows) {
  const list = $('kPending');
  list.innerHTML = '';
  $('kPendingCount').textContent = rows.length ? `(${rows.length})` : '';
  if (!rows.length) {
    list.textContent = 'Kuch pending nahi.';
    return;
  }
  for (const p of rows) {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = `${p.customer_name} — ${p.item}`;
    const meta = document.createElement('small');
    meta.textContent = `${p.txn_type === 'JAMA' ? 'Jama' : 'Udhaar'} ${money(p.amount_paise)}  •  ${p.txn_date}`
      + `  •  saves ${String(p.post_at).slice(0, 10)}`;
    info.append(title, meta);
    const btns = document.createElement('div');
    btns.className = 'k-btns';
    const post = document.createElement('button');
    post.textContent = 'Post';
    post.addEventListener('click', () => khataPendingAction(p.id, 'post'));
    const edit = document.createElement('button');
    edit.textContent = 'Edit';
    edit.addEventListener('click', () => startEditPending(p));
    const del = document.createElement('button');
    del.className = 'danger';
    del.textContent = 'Del';
    del.addEventListener('click', () => khataPendingAction(p.id, 'delete'));
    btns.append(post, edit, del);
    item.append(info, btns);
    list.appendChild(item);
  }
}

async function khataPendingAction(id, action) {
  try {
    const data = await khataApi(`/api/khata/pending/${id}/${action}`, { method: 'POST' });
    toast(data.message || 'Ho gaya');
    loadKhata();
  } catch (error) {
    toast(error.message);
  }
}

async function loadKhata() {
  if (!token) return;
  try {
    $('kSummary').textContent = 'Load ho raha hai…';
    const [summary, customers, pending, overdue, top, closing, aging, items, health] = await Promise.all([
      khataApi('/api/khata/summary'),
      khataApi('/api/khata/customers'),
      khataApi('/api/khata/pending'),
      khataApi('/api/khata/overdue'),
      khataApi('/api/khata/top'),
      khataApi('/api/khata/closing').catch(() => ({ closing: null })),
      khataApi('/api/khata/aging').catch(() => ({ buckets: {} })),
      khataApi('/api/khata/items').catch(() => ({ items: [] })),
      khataApi('/api/khata/health').catch(() => ({})),
    ]);
    renderKhataSummary(summary);
    renderKhataCustomers(customers.customers || []);
    renderKhataPending(pending.pending || []);
    renderKhataOverdue(overdue.overdue || []);
    renderKhataTop(top.top || []);
    renderClosing(closing.closing);
    renderAging(aging.buckets || {});
    setItemPrices(items.items || []);
    checkApk(health.apkVersion || '');
    renderRecent();
    renderSyncChip();
    localStorage.setItem('vx7_cache', JSON.stringify({
      summary,
      customers: customers.customers || [],
      pending: pending.pending || [],
      at: Date.now(),
    }));
    Promise.all([
      khataApi('/api/khata/shop').catch(() => ({})),
      khataApi('/api/khata/reminders').catch(() => ({ reminders: [] })),
      khataApi('/api/khata/trend').catch(() => ({ days: [] })),
      khataApi('/api/khata/items-report').catch(() => ({ items: [] })),
      khataApi('/api/khata/expenses').catch(() => ({})),
    ]).then(([shop, reminders, trend, itemsReport, expenses]) => {
      shopInfo = shop || {};
      renderReminders(reminders.reminders || []);
      renderTrend(trend.days || []);
      renderItemsReport(itemsReport.items || []);
      renderExpenses(expenses || {});
    });
  } catch (error) {
    $('kSummary').textContent = 'Khata: ' + error.message;
  }
}

$('kSave').addEventListener('click', () => saveMain());

$('kSearch').addEventListener('input', () => {
  clearTimeout(khataSearchTimer);
  khataSearchTimer = setTimeout(async () => {
    try {
      const data = await khataApi('/api/khata/customers?search=' + encodeURIComponent($('kSearch').value.trim()));
      renderKhataCustomers(data.customers || []);
    } catch (error) {
      toast(error.message);
    }
  }, 300);
});

$('kAddCustomer').addEventListener('click', async () => {
  const name = $('kNewName').value.trim();
  if (!name) return toast('Naam daalo');
  try {
    await khataApi('/api/khata/customers', {
      method: 'POST',
      body: {
        name,
        mobile: $('kNewMobile').value.trim() || undefined,
        address: $('kNewAddress').value.trim() || undefined,
        credit_limit: $('kNewLimit').value.trim() || undefined,
      },
    });
    toast('Customer add ho gaya: ' + name);
    $('kNewName').value = '';
    $('kNewMobile').value = '';
    $('kNewAddress').value = '';
    $('kNewLimit').value = '';
    loadKhata();
  } catch (error) {
    toast(error.message);
  }
});

$('loginBtn').addEventListener('click', () => {
  const value = $('tokenInput').value.trim();
  if (!value) {
    $('loginErr').textContent = 'Token daalo.';
    return;
  }
  token = value;
  localStorage.setItem('vx7_token', token);
  hideLogin();
  connectWS();
});

$('tokenInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('loginBtn').click();
});

$('streamBtn').addEventListener('click', () => {
  streamDesired = !streamDesired;
  streamOn = streamDesired;
  send({ type: 'stream', on: streamDesired, fps: 1 });
  updateStreamButton();
  if (streamDesired) $('screenMsg').classList.add('hidden');
});

$('shotBtn').addEventListener('click', () => send({ type: 'shot' }));

$('lockBtn').addEventListener('click', () => {
  if (confirm('Laptop lock kar de?')) {
    send({ type: 'action', name: 'lock_pc', params: {}, confirmed: true });
  }
});

$('volUp').addEventListener('click', () => send({ type: 'action', name: 'change_volume', params: { direction: 'up', steps: 5 }, confirmed: true }));
$('volDown').addEventListener('click', () => send({ type: 'action', name: 'change_volume', params: { direction: 'down', steps: 5 }, confirmed: true }));
$('muteBtn').addEventListener('click', () => {
  muted = !muted;
  send({ type: 'action', name: 'mute', params: { on: muted }, confirmed: true });
  $('muteBtn').textContent = muted ? 'Unmute' : 'Mute';
});

$('cancelShutdownBtn').addEventListener('click', () => {
  send({ type: 'action', name: 'cancel_shutdown', params: {}, confirmed: true });
});

const holdBtn = $('shutdownBtn');
let holdTimer = null;
function startHold(event) {
  event.preventDefault();
  holdBtn.classList.add('arming');
  holdTimer = setTimeout(() => {
    holdBtn.classList.remove('arming');
    holdTimer = null;
    send({ type: 'action', name: 'shutdown_pc', params: { seconds: 60 }, confirmed: true });
  }, 2000);
}
function cancelHold() {
  holdBtn.classList.remove('arming');
  clearTimeout(holdTimer);
  holdTimer = null;
}
holdBtn.addEventListener('pointerdown', startHold);
holdBtn.addEventListener('pointerup', cancelHold);
holdBtn.addEventListener('pointerleave', cancelHold);
holdBtn.addEventListener('pointercancel', cancelHold);

// Remote screen tap -> click at that location
function contentRect(img) {
  const rect = img.getBoundingClientRect();
  const naturalRatio = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : rect.width / rect.height;
  let width = rect.width;
  let height = width / naturalRatio;
  if (height > rect.height) {
    height = rect.height;
    width = height * naturalRatio;
  }
  return {
    left: rect.left + (rect.width - width) / 2,
    top: rect.top + (rect.height - height) / 2,
    width,
    height,
  };
}

$('screen').addEventListener('click', (event) => {
  const img = $('screen');
  if (!img.naturalWidth) return;
  const rect = contentRect(img);
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (x < 0 || y < 0 || x > 1 || y > 1) return;
  send({ type: 'mouse', move: 'absolute', x, y });
  setTimeout(() => send({ type: 'click', button: 'left' }), 80);
});

// Touchpad
const pad = $('touchpad');
const pointers = new Map();
let lastMove = null;
let tapStart = null;
let twoFingerLastY = null;

pad.addEventListener('pointerdown', (event) => {
  pad.setPointerCapture(event.pointerId);
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size === 1) {
    tapStart = { x: event.clientX, y: event.clientY, at: Date.now(), moved: 0 };
    lastMove = { x: event.clientX, y: event.clientY };
    twoFingerLastY = null;
  } else if (pointers.size === 2) {
    tapStart = null;
    const ys = [...pointers.values()].map((p) => p.y);
    twoFingerLastY = (ys[0] + ys[1]) / 2;
  }
});

pad.addEventListener('pointermove', (event) => {
  if (!pointers.has(event.pointerId)) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size >= 2) {
    const ys = [...pointers.values()].map((p) => p.y);
    const avg = (ys[0] + ys[1]) / 2;
    if (twoFingerLastY !== null) {
      const delta = avg - twoFingerLastY;
      const ticks = Math.trunc(-delta / 14);
      if (ticks !== 0) send({ type: 'scroll', amount: Math.max(-8, Math.min(8, ticks)) });
    }
    twoFingerLastY = avg;
    return;
  }
  if (!lastMove) {
    lastMove = { x: event.clientX, y: event.clientY };
    return;
  }
  const dx = event.clientX - lastMove.x;
  const dy = event.clientY - lastMove.y;
  lastMove = { x: event.clientX, y: event.clientY };
  if (tapStart) tapStart.moved += Math.abs(dx) + Math.abs(dy);
  if (dx || dy) send({ type: 'mouse', move: 'relative', dx: Math.round(dx * 1.6), dy: Math.round(dy * 1.6) });
});

function endPointer(event) {
  pointers.delete(event.pointerId);
  if (pointers.size === 0) {
    if (tapStart) {
      const duration = Date.now() - tapStart.at;
      if (tapStart.moved < 12) {
        if (duration >= 600) send({ type: 'click', button: 'right' });
        else send({ type: 'click', button: 'left' });
      }
    }
    tapStart = null;
    lastMove = null;
    twoFingerLastY = null;
  } else if (pointers.size === 1) {
    const remaining = [...pointers.values()][0];
    lastMove = { x: remaining.x, y: remaining.y };
    twoFingerLastY = null;
  }
}
pad.addEventListener('pointerup', endPointer);
pad.addEventListener('pointercancel', endPointer);

document.querySelectorAll('.quick-keys button').forEach((btn) => {
  btn.addEventListener('click', () => send({ type: 'keys', combo: btn.dataset.key }));
});

$('leftClick').addEventListener('click', () => send({ type: 'click', button: 'left' }));
$('rightClick').addEventListener('click', () => send({ type: 'click', button: 'right' }));

$('keyboardBtn').addEventListener('click', () => {
  const input = $('hiddenInput');
  input.value = '';
  input.focus();
});

$('hiddenInput').addEventListener('input', (event) => {
  const text = event.target.value;
  if (text) send({ type: 'type', text });
  event.target.value = '';
});

$('hiddenInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    send({ type: 'keys', combo: 'enter' });
  }
});

$('chatForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('chatInput');
  const text = input.value.trim();
  if (!text) return;
  addBubble(text, 'me');
  send({ type: 'chat', text });
  input.value = '';
});

$('confirmYes').addEventListener('click', () => {
  if (!pendingConfirm) return;
  send({ type: 'confirm', action: pendingConfirm.action, params: pendingConfirm.params });
  addBubble('✅ Confirm: ' + pendingConfirm.action.replace(/_/g, ' '), 'me');
  hideConfirm();
});

$('confirmNo').addEventListener('click', () => {
  hideConfirm();
  addBubble('❌ Cancelled', 'bot');
});

const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Speech && window.isSecureContext) {
  const recognition = new Speech();
  recognition.lang = 'en-IN';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  $('micBtn').classList.remove('hidden');
  $('micBtn').addEventListener('click', () => {
    try {
      recognition.start();
      toast('Bol rahe ho…');
    } catch { /* already running */ }
  });
  recognition.onresult = (event) => {
    const text = event.results[0][0].transcript;
    $('chatInput').value = text;
    $('chatForm').dispatchEvent(new Event('submit', { cancelable: true }));
  };
  recognition.onerror = () => toast('Voice samajh nahi aayi');
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden && streamOn) {
    send({ type: 'stream', on: false });
    streamOn = false;
  } else if (!document.hidden && streamDesired && !streamOn) {
    send({ type: 'stream', on: true, fps: 1 });
    streamOn = true;
  }
});

setInterval(() => send({ type: 'ping' }), 25000);

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js').catch(() => { /* offline shell optional */ });
}

(function init() {
  if (token) {
    localStorage.setItem('vx7_token', token);
    try {
      const url = new URL(location.href);
      url.searchParams.delete('t');
      history.replaceState(null, '', url.pathname + url.search);
    } catch { /* ignore */ }
    hideLogin();
    connectWS();
  } else {
    showLogin('');
  }
})();

// ------------------------------------------------------------ Khata extras
let lastEntry = null;

function renderClosing(closing) {
  const box = $('kClosing');
  if (!closing) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  box.textContent = `Closing (${String(closing.at || '').slice(0, 10)}): `
    + `Udhaar ${money(closing.total_udhaar_paise)} • Jama ${money(closing.total_jama_paise)}`
    + ` • Net ${money(closing.net_paise)} • ${closing.transaction_count} entries`;
}

function renderKhataTop(rows) {
  const box = $('kTop');
  box.innerHTML = '';
  for (const r of rows) {
    const chip = document.createElement('button');
    chip.className = 'k-chip';
    chip.textContent = r.name;
    const small = document.createElement('small');
    small.textContent = money(r.balance_paise) + ' Dr';
    chip.appendChild(small);
    chip.addEventListener('click', () => openCustomerDetail(r.id, r.name));
    box.appendChild(chip);
  }
}

function renderKhataOverdue(rows) {
  const list = $('kOverdue');
  list.innerHTML = '';
  if (!rows.length) {
    list.textContent = 'Koi overdue nahi.';
    return;
  }
  for (const r of rows) {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = r.name;
    const meta = document.createElement('small');
    meta.textContent = `${r.entries} entries overdue`;
    info.append(title, meta);
    const amt = document.createElement('span');
    amt.className = 'amt-dr';
    amt.textContent = money(r.due_paise);
    item.append(info, amt);
    item.addEventListener('click', () => openCustomerDetail(r.customer_id, r.name));
    list.appendChild(item);
  }
}

function openCustomerDetail(id, name) {
  const panel = $('kDetail');
  panel.classList.remove('hidden');
  panel.innerHTML = '';

  const head = document.createElement('h3');
  head.textContent = name;
  panel.appendChild(head);
  const bal = document.createElement('div');
  bal.className = 'k-detail-balance';
  bal.textContent = 'Load ho raha hai…';
  panel.appendChild(bal);
  const list = document.createElement('div');
  list.className = 'k-list';
  panel.appendChild(list);
  const close = document.createElement('button');
  close.textContent = '✕ Band karo';
  close.addEventListener('click', () => panel.classList.add('hidden'));
  panel.appendChild(close);

  (async () => {
    try {
      const data = await khataApi('/api/khata/ledger?customer_id=' + encodeURIComponent(id));
      head.textContent = data.customer.name;
      bal.textContent = `Balance ${money(Math.abs(data.balance_paise))}`
        + ` ${data.balance_paise > 0 ? 'Dr' : data.balance_paise < 0 ? 'Cr' : ''}`
        + ` • Udhaar ${money(data.total_udhaar_paise)} • Jama ${money(data.total_jama_paise)}`;
      const rows = (data.rows || []).slice().reverse();
      if (!rows.length) list.textContent = 'Koi entry nahi.';
      for (const r of rows) {
        const item = document.createElement('div');
        item.className = 'k-item';
        const info = document.createElement('div');
        const title = document.createElement('div');
        title.textContent = r.item + (r.quantity ? ' • ' + r.quantity : '');
        const meta = document.createElement('small');
        meta.textContent = `${r.date} • ${r.type === 'JAMA' ? 'Jama' : 'Udhaar'}`
          + (r.due_date ? ' • due ' + r.due_date : '');
        info.append(title, meta);
        const amt = document.createElement('span');
        amt.className = r.type === 'JAMA' ? 'amt-cr' : 'amt-dr';
        amt.textContent = money(r.amount_paise);
        item.append(info, amt);
        list.appendChild(item);
      }
      const mobile = (data.customer.mobile || '').replace(/\D/g, '');
      const btns = document.createElement('div');
      btns.className = 'k-btns';
      const pdf = document.createElement('button');
      pdf.textContent = 'PDF';
      pdf.addEventListener('click', () => openDoc(`/api/khata/ledger.pdf?customer_id=${encodeURIComponent(id)}`));
      btns.appendChild(pdf);
      const upi = document.createElement('button');
      upi.textContent = 'UPI collect';
      upi.addEventListener('click', async () => {
        const amount = window.prompt('Kitna collect karna hai? (₹)',
          String(Math.abs(data.balance_paise / 100) || ''));
        if (!amount) return;
        try {
          const result = await khataApi('/api/upi-qr?amount=' + encodeURIComponent(amount));
          const box = document.createElement('div');
          box.className = 'k-upi';
          const img = document.createElement('img');
          img.src = result.qr;
          const note = document.createElement('small');
          note.textContent = result.uri;
          box.append(img, note);
          panel.appendChild(box);
        } catch (error) {
          toast(error.message);
        }
      });
      btns.appendChild(upi);
      const reminderBtn = document.createElement('button');
      reminderBtn.textContent = 'Reminder set';
      reminderBtn.addEventListener('click', async () => {
        const date = window.prompt('Reminder date (YYYY-MM-DD)',
          new Date().toISOString().slice(0, 10));
        if (!date) return;
        try {
          await khataApi('/api/khata/reminders', {
            method: 'POST',
            body: { customer: data.customer.name, date },
          });
          toast('Reminder set: ' + date);
          loadKhata();
        } catch (error) {
          toast(error.message);
        }
      });
      btns.appendChild(reminderBtn);
      if (mobile) {
        const call = document.createElement('button');
        call.textContent = 'Call';
        call.addEventListener('click', () => {
          window.location.href = `tel:+${mobile.length === 10 ? '91' + mobile : mobile}`;
        });
        btns.appendChild(call);
        const reminder = document.createElement('button');
        reminder.textContent = 'Reminder';
        reminder.addEventListener('click', () => {
          const full = mobile.length === 10 ? '91' + mobile : mobile;
          const text = `Namaste ${data.customer.name} ji, aapka khata balance `
            + `₹${Math.abs(data.balance_paise / 100).toFixed(2)} baaki hai. Kripya jaldi settle kar dijiye. — Khata`;
          window.open(`https://wa.me/${full}?text=${encodeURIComponent(text)}`, '_blank');
        });
        btns.appendChild(reminder);
      }
      if (btns.children.length) panel.appendChild(btns);
      if (mobile) {
        const wa = document.createElement('button');
        wa.className = 'accent';
        wa.textContent = 'WhatsApp pe hisaab bhejo';
        const lines = rows.slice(0, 12)
          .map((r) => `${r.date} ${r.item} ${r.type === 'JAMA' ? 'Jama' : 'Udhaar'} ₹${(r.amount_paise / 100).toFixed(0)}`)
          .join('\n');
        const text = `Namaste ${data.customer.name} ji,\nAapka khata:\n${lines}\n`
          + `Balance: ₹${Math.abs(data.balance_paise / 100).toFixed(2)}`
          + ` ${data.balance_paise > 0 ? 'baaki (Dr)' : data.balance_paise < 0 ? 'jama (Cr)' : ''}\n— Khata`;
        wa.addEventListener('click', () => {
          const full = mobile.length === 10 ? '91' + mobile : mobile;
          window.open(`https://wa.me/${full}?text=${encodeURIComponent(text)}`, '_blank');
        });
        panel.appendChild(wa);
      }
    } catch (error) {
      bal.textContent = 'Error: ' + error.message;
    }
  })();
}

$('kRepeatBtn').addEventListener('click', () => {
  if (!lastEntry) {
    toast('Abhi koi entry nahi bheji');
    return;
  }
  $('kCustomer').value = lastEntry.customer;
  $('kType').value = lastEntry.type;
  $('kItem').value = lastEntry.item;
  $('kQty').value = lastEntry.quantity || '';
  $('kRate').value = lastEntry.rate || '';
  $('kAmount').value = lastEntry.amount || '';
  toast('Pichhli entry bhar di — check karke Save karo');
});

$('kUndoBtn').addEventListener('click', async () => {
  if (!window.confirm('Last entry undo karni hai?')) return;
  try {
    const data = await khataApi('/api/khata/undo-last', { method: 'POST' });
    toast(data.message || 'Undo ho gaya');
    loadKhata();
  } catch (error) {
    toast(error.message);
  }
});

$('kPhotoBtn').addEventListener('click', () => $('kPhotoInput').click());

$('kPhotoInput').addEventListener('change', async () => {
  const file = $('kPhotoInput').files[0];
  if (!file) return;
  if (file.size > 6 * 1024 * 1024) {
    toast('Photo 6MB se choti bhejo');
    return;
  }
  toast('Parchi padh raha hoon…');
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  try {
    const data = await khataApi('/api/khata/scan', { method: 'POST', body: { image: dataUrl } });
    const draft = data.draft || {};
    if (draft.customer) $('kCustomer').value = draft.customer;
    if (draft.item) $('kItem').value = draft.item;
    if (draft.quantity) $('kQty').value = String(draft.quantity);
    if (draft.rate) $('kRate').value = String(draft.rate);
    if (draft.amount) $('kAmount').value = String(draft.amount);
    toast('Parchi padh li — fields check karke Save karo');
  } catch (error) {
    toast(error.message);
  }
  $('kPhotoInput').value = '';
});

// ------------------------------------------------------------ PIN lock
let pinSet = false;
let pinInitialized = false;
let lastActivity = Date.now();

function showLock() {
  $('pinLock').classList.remove('hidden');
  $('pinInput').value = '';
  $('pinErr').textContent = '';
}

async function verifyPin() {
  try {
    const data = await khataApi('/api/khata/pin/verify', {
      method: 'POST',
      body: { pin: $('pinInput').value.trim() },
    });
    if (data.match) {
      sessionStorage.setItem('vx7_pin_ok', '1');
      $('pinLock').classList.add('hidden');
      lastActivity = Date.now();
    } else {
      $('pinErr').textContent = 'PIN galat hai';
    }
  } catch (error) {
    $('pinErr').textContent = error.message;
  }
}

async function initPin() {
  if (!token || pinInitialized) return;
  try {
    const stats = await khataApi('/api/khata/stats');
    pinInitialized = true;
    pinSet = Boolean(stats.pin_set);
    if (pinSet && sessionStorage.getItem('vx7_pin_ok') !== '1') showLock();
  } catch {
    /* bridge off — retry on the next tick */
  }
}

$('pinBtn').addEventListener('click', verifyPin);
$('pinInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') verifyPin();
});
document.addEventListener('pointerdown', () => { lastActivity = Date.now(); });
document.addEventListener('keydown', () => { lastActivity = Date.now(); });
setInterval(() => {
  if (!pinSet) return;
  if (Date.now() - lastActivity > 5 * 60 * 1000 && $('pinLock').classList.contains('hidden')) {
    sessionStorage.removeItem('vx7_pin_ok');
    showLock();
  }
}, 30000);
setInterval(initPin, 4000);
initPin();

// ------------------------------------------------------------ Khata extras v2
let cart = [];
let editingPendingId = null;
let itemPrices = {};
let recentEntries = [];
try {
  recentEntries = JSON.parse(localStorage.getItem('vx7_recent') || '[]') || [];
} catch {
  recentEntries = [];
}

function readForm() {
  return {
    customer: $('kCustomer').value.trim(),
    type: $('kType').value,
    item: $('kItem').value.trim(),
    quantity: $('kQty').value.trim(),
    rate: $('kRate').value.trim(),
    amount: $('kAmount').value.trim(),
    mode: $('kMode').value,
    due_date: $('kDue').value || undefined,
  };
}

function clearItemFields() {
  $('kItem').value = '';
  $('kQty').value = '';
  $('kRate').value = '';
  $('kAmount').value = '';
  $('kDue').value = '';
}

function openDoc(path) {
  const sep = path.includes('?') ? '&' : '?';
  window.open(`${path}${sep}t=${encodeURIComponent(token)}`, '_blank');
}

function queueEntries() {
  try {
    return JSON.parse(localStorage.getItem('vx7_queue') || '[]') || [];
  } catch {
    return [];
  }
}

function saveQueue(queue) {
  localStorage.setItem('vx7_queue', JSON.stringify(queue));
}

function queueEntry(entry) {
  const queue = queueEntries();
  queue.push({ ...entry, queued_at: new Date().toISOString() });
  saveQueue(queue);
}

async function postEntry(entry) {
  try {
    const data = await khataApi('/api/khata/entries', { method: 'POST', body: entry });
    return { ok: true, data };
  } catch (error) {
    if (/connect nahi|fetch|network|failed/i.test(error.message)) {
      queueEntry(entry);
      return { ok: false, queued: true, error: error.message };
    }
    throw error;
  }
}

function renderSyncChip() {
  const queue = queueEntries();
  const chip = $('kSyncChip');
  chip.textContent = `Sync ${queue.length} queued`;
  chip.classList.toggle('hidden', queue.length === 0);
}

async function syncQueue() {
  const queue = queueEntries();
  if (!queue.length) return 0;
  const remain = [];
  let done = 0;
  for (const entry of queue) {
    const body = { ...entry };
    delete body.queued_at;
    try {
      await khataApi('/api/khata/entries', { method: 'POST', body });
      done += 1;
    } catch {
      remain.push(entry);
    }
  }
  saveQueue(remain);
  renderSyncChip();
  if (done) {
    toast(`${done} queued entry sync ho gayi`);
    loadKhata();
  }
  return done;
}

function pushRecent(rows, form) {
  for (const row of rows) {
    recentEntries.unshift({
      customer: form.customer,
      type: form.type,
      item: row.item || '',
      quantity: row.quantity || '',
      rate: row.rate || '',
      amount: row.amount || '',
    });
  }
  recentEntries = recentEntries.slice(0, 3);
  localStorage.setItem('vx7_recent', JSON.stringify(recentEntries));
  lastEntry = recentEntries[0] || null;
  renderRecent();
}

function renderRecent() {
  const box = $('kRecent');
  box.innerHTML = '';
  for (const entry of recentEntries) {
    if (!entry.item && !entry.amount) continue;
    const chip = document.createElement('button');
    chip.className = 'k-chip';
    chip.textContent = entry.item || entry.amount;
    const small = document.createElement('small');
    small.textContent = entry.customer;
    chip.appendChild(small);
    chip.addEventListener('click', () => {
      $('kCustomer').value = entry.customer;
      $('kType').value = entry.type;
      $('kItem').value = entry.item;
      $('kQty').value = entry.quantity;
      $('kRate').value = entry.rate;
      $('kAmount').value = entry.amount;
    });
    box.appendChild(chip);
  }
}

function renderCart() {
  const box = $('kCart');
  box.innerHTML = '';
  cart.forEach((row, index) => {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = `${row.item}${row.quantity ? ' × ' + row.quantity : ''}`;
    const meta = document.createElement('small');
    meta.textContent = row.amount ? `₹${row.amount}` : 'rate se amount';
    info.append(title, meta);
    const del = document.createElement('button');
    del.className = 'danger';
    del.textContent = '×';
    del.addEventListener('click', () => {
      cart.splice(index, 1);
      renderCart();
    });
    item.append(info, del);
    box.appendChild(item);
  });
}

function setItemPrices(items) {
  itemPrices = {};
  let list = document.getElementById('kItemsList');
  if (!list) {
    list = document.createElement('datalist');
    list.id = 'kItemsList';
    document.body.appendChild(list);
    $('kItem').setAttribute('list', 'kItemsList');
  }
  list.innerHTML = '';
  for (const row of items) {
    itemPrices[row.item.trim().toLowerCase()] = row.rate_paise;
    const option = document.createElement('option');
    option.value = row.item;
    list.appendChild(option);
  }
}

function renderAging(buckets) {
  const box = $('kAging');
  box.innerHTML = '';
  const parts = [
    ['0–30 din', buckets.b0_30],
    ['31–60 din', buckets.b31_60],
    ['60+ din', buckets.b61_plus],
  ];
  if (!parts.some(([, value]) => value)) return;
  for (const [label, value] of parts) {
    const chip = document.createElement('span');
    chip.className = 'k-chip';
    chip.textContent = `${label}: ${money(value || 0)}`;
    box.appendChild(chip);
  }
}

function checkApk(serverVersion) {
  const appVersion = (window.Vx7 && window.Vx7.version) ? String(window.Vx7.version()) : '';
  if (serverVersion && appVersion && serverVersion !== appVersion) {
    $('kApkText').textContent = `Naya APK available (v${serverVersion}) — aapke paas v${appVersion}`;
    $('kApkBanner').classList.remove('hidden');
  }
}

$('kApkBtn').addEventListener('click', () => openDoc('/api/apk'));

function startEditPending(pending) {
  editingPendingId = pending.id;
  $('kCustomer').value = pending.customer_name;
  $('kType').value = pending.txn_type;
  $('kItem').value = pending.item;
  $('kQty').value = pending.quantity || '';
  $('kRate').value = pending.rate_paise ? String(pending.rate_paise / 100) : '';
  $('kAmount').value = String(pending.amount_paise / 100);
  $('kSave').textContent = `Update #${pending.id}`;
  toast('Edit mode — change karke Update dabao');
}

async function saveMain() {
  const form = readForm();
  if (!form.customer) return toast('Customer ka naam daalo');
  if (editingPendingId) {
    try {
      const result = await khataApi(`/api/khata/pending/${editingPendingId}/edit`, {
        method: 'POST',
        body: {
          item: form.item || undefined,
          amount: form.amount || undefined,
          quantity: form.quantity || undefined,
          rate: form.rate || undefined,
        },
      });
      toast(result.message || 'Update ho gaya');
      editingPendingId = null;
      $('kSave').textContent = 'Save entry';
      clearItemFields();
      loadKhata();
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  if (!cart.length && !form.amount && !(form.quantity && form.rate)) {
    return toast('Amount do, ya Qty + Rate');
  }
  const rows = cart.length ? cart.slice() : [form];
  let saved = 0;
  let queued = 0;
  let last = null;
  let errorMessage = '';
  for (const row of rows) {
    const entry = {
      customer: form.customer,
      type: form.type,
      mode: form.mode,
      due_date: form.due_date,
      item: row.item || undefined,
      quantity: row.quantity || undefined,
      rate: row.rate || undefined,
      amount: row.amount || undefined,
    };
    const result = await postEntry(entry);
    if (result.ok) {
      saved += 1;
      last = result.data;
    } else if (result.queued) {
      queued += 1;
    } else {
      errorMessage = result.error;
    }
  }
  if (saved) {
    pushRecent(rows, form);
    const extra = last && last.balance_paise !== undefined
      ? `  •  ₹${Math.abs(last.balance_paise / 100).toFixed(0)} ${last.balance_paise > 0 ? 'baaki' : 'jama'}`
      : '';
    toast(`${saved} ${saved === 1 ? 'entry' : 'entries'} save${queued ? ` • ${queued} queued` : ''}${extra}`);
    cart = [];
    renderCart();
    clearItemFields();
    loadKhata();
  } else if (queued) {
    toast(`${queued} entry phone me queue hui — connect hote hi sync hogi`);
    cart = [];
    renderCart();
    clearItemFields();
    renderSyncChip();
  } else if (errorMessage) {
    toast(errorMessage);
  }
}

$('kCartBtn').addEventListener('click', () => {
  const form = readForm();
  if (!form.item) return toast('Item likho');
  if (!form.amount && !(form.quantity && form.rate)) return toast('Amount ya Qty + Rate do');
  cart.push({ item: form.item, quantity: form.quantity, rate: form.rate, amount: form.amount });
  renderCart();
  clearItemFields();
  toast('Cart me add: ' + form.item);
});

$('kItem').addEventListener('input', () => {
  const price = itemPrices[$('kItem').value.trim().toLowerCase()];
  if (price && !$('kRate').value.trim() && !$('kAmount').value.trim()) {
    $('kRate').value = String(price / 100);
  }
});

$('kMonthPdf').addEventListener('click', () => {
  const today = new Date();
  const from = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-01`;
  const to = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  openDoc(`/api/khata/report.pdf?from=${from}&to=${to}`);
});

$('kSyncChip').addEventListener('click', () => syncQueue());

$('kPinSave').addEventListener('click', async () => {
  try {
    const result = await khataApi('/api/khata/pin', {
      method: 'POST',
      body: { pin: $('kPinNew').value.trim(), old_pin: $('kPinOld').value.trim() },
    });
    toast(result.pin_set ? 'PIN set ho gaya' : 'PIN hata diya');
    $('kPinOld').value = '';
    $('kPinNew').value = '';
    pinSet = Boolean(result.pin_set);
    pinInitialized = true;
  } catch (error) {
    toast(error.message);
  }
});

setInterval(() => {
  if (queueEntries().length && connected) syncQueue();
}, 30000);
renderRecent();
renderSyncChip();

// ------------------------------------------------------------ Khata extras v3
let shopInfo = {};

function renderReminders(rows) {
  const list = $('kReminders');
  list.innerHTML = '';
  const due = rows.filter((r) => r.due_today);
  if (!rows.length) {
    list.textContent = 'Koi reminder set nahi.';
    return;
  }
  const ordered = [...due, ...rows.filter((r) => !r.due_today)];
  for (const row of ordered) {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = row.customer;
    const meta = document.createElement('small');
    meta.textContent = `${row.date}${row.due_today ? '  •  AAJ' : ''}${row.note ? '  •  ' + row.note : ''}`;
    info.append(title, meta);
    const btns = document.createElement('div');
    btns.className = 'k-btns';
    const del = document.createElement('button');
    del.className = 'danger';
    del.textContent = '×';
    del.addEventListener('click', async () => {
      try {
        await khataApi('/api/khata/reminders/delete', { method: 'POST', body: { id: row.id } });
        loadKhata();
      } catch (error) {
        toast(error.message);
      }
    });
    btns.appendChild(del);
    item.append(info, btns);
    list.appendChild(item);
  }
}

function renderTrend(days) {
  const box = $('kTrend');
  box.innerHTML = '';
  if (!days.length) return;
  const max = Math.max(...days.map((d) => Math.max(d.udhaar_paise, d.jama_paise)), 1);
  for (const day of days) {
    const bar = document.createElement('div');
    bar.className = 'k-bar';
    const dr = document.createElement('i');
    dr.style.height = `${Math.max(3, (day.udhaar_paise / max) * 44)}%`;
    const cr = document.createElement('i');
    cr.className = 'cr';
    cr.style.height = `${Math.max(3, (day.jama_paise / max) * 44)}%`;
    const label = document.createElement('small');
    label.textContent = day.date.slice(8);
    bar.append(dr, cr, label);
    box.appendChild(bar);
  }
}

function renderItemsReport(items) {
  const list = $('kItemsReport');
  list.innerHTML = '';
  if (!items.length) {
    list.textContent = 'Koi data nahi.';
    return;
  }
  for (const row of items.slice(0, 8)) {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = row.item;
    const meta = document.createElement('small');
    meta.textContent = `${row.count} baar`;
    info.append(title, meta);
    const amt = document.createElement('span');
    amt.className = 'amt-dr';
    amt.textContent = money(row.amount_paise);
    item.append(info, amt);
    list.appendChild(item);
  }
}

function renderExpenses(data) {
  const list = $('kExpenses');
  list.innerHTML = '';
  $('kExpTotal').textContent = data.total_paise ? `(aaj ${money(data.total_paise)})` : '';
  const rows = data.expenses || [];
  if (!rows.length) {
    list.textContent = 'Aaj koi kharcha nahi.';
    return;
  }
  for (const row of rows) {
    const item = document.createElement('div');
    item.className = 'k-item';
    const info = document.createElement('div');
    const title = document.createElement('div');
    title.textContent = row.item;
    info.appendChild(title);
    const amt = document.createElement('span');
    amt.className = 'amt-dr';
    amt.textContent = money(row.amount_paise);
    item.append(info, amt);
    list.appendChild(item);
  }
}

$('kExpAdd').addEventListener('click', async () => {
  const item = $('kExpItem').value.trim();
  const amount = $('kExpAmount').value.trim();
  if (!item || !amount) return toast('Kharcha aur amount likho');
  try {
    await khataApi('/api/khata/expenses', { method: 'POST', body: { item, amount } });
    toast('Kharcha add: ' + item);
    $('kExpItem').value = '';
    $('kExpAmount').value = '';
    loadKhata();
  } catch (error) {
    toast(error.message);
  }
});

$('kBackup').addEventListener('click', () => {
  openDoc('/api/khata/backup');
  toast('Backup download ho raha hai — phone me save karo');
});

const LANG = {
  hi: {
    titles: ['Nayi entry', 'Pending', 'Overdue', 'Reminders', 'Kharcha', 'Top items', 'Customers'],
    save: 'Save entry', cart: '＋ Cart me daalo', photo: '📷 Parchi',
    repeat: 'Phir se same', undo: 'Undo last', month: 'Month PDF',
    backup: 'Backup', lang: 'EN',
  },
  en: {
    titles: ['New entry', 'Pending', 'Overdue', 'Reminders', 'Expenses', 'Top items', 'Customers'],
    save: 'Save entry', cart: '+ Add to cart', photo: '📷 Bill photo',
    repeat: 'Repeat last', undo: 'Undo last', month: 'Month PDF',
    backup: 'Backup', lang: 'HI',
  },
};
let lang = localStorage.getItem('vx7_lang') || 'hi';

function applyLang() {
  const pack = LANG[lang] || LANG.hi;
  const titles = document.querySelectorAll('#tab-khata .k-title');
  titles.forEach((title, index) => {
    if (!pack.titles[index]) return;
    if (title.querySelector('small')) {
      const textNode = Array.from(title.childNodes).find((n) => n.nodeType === 3);
      if (textNode) textNode.nodeValue = pack.titles[index] + ' ';
    } else {
      title.textContent = pack.titles[index];
    }
  });
  $('kSave').textContent = pack.save;
  $('kCartBtn').textContent = pack.cart;
  $('kPhotoBtn').textContent = pack.photo;
  $('kRepeatBtn').textContent = pack.repeat;
  $('kUndoBtn').textContent = pack.undo;
  $('kMonthPdf').textContent = pack.month;
  $('kBackup').textContent = pack.backup;
  $('kLang').textContent = pack.lang;
}

$('kLang').addEventListener('click', () => {
  lang = lang === 'hi' ? 'en' : 'hi';
  localStorage.setItem('vx7_lang', lang);
  applyLang();
});
applyLang();
