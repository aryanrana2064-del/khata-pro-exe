'use strict';

const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const express = require('express');
const { WebSocketServer } = require('ws');
const QRCode = require('qrcode');

const execFileAsync = promisify(execFile);

function normalizeIp(ip) {
  return String(ip || '').replace(/^::ffff:/, '');
}

function isLocal(ip) {
  const s = normalizeIp(ip);
  return s === '127.0.0.1' || s === '::1';
}

function isTailscale(ip) {
  const s = normalizeIp(ip);
  return /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(s) || /^fd7a:115c:a1e0:/i.test(s);
}

function isPrivateLan(ip) {
  const s = normalizeIp(ip);
  if (s.startsWith('10.') || s.startsWith('192.168.')) return true;
  const m = s.match(/^172\.(\d+)\./);
  if (m) {
    const second = Number(m[1]);
    if (second >= 16 && second <= 31) return true;
  }
  return /^169\.254\./.test(s);
}

function isAllowed(ip) {
  return isLocal(ip) || isTailscale(ip) || isPrivateLan(ip);
}

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function createServer({ config, actions, ai, agent, telegramState, log = () => {} }) {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '8mb' }));

  app.use((req, res, next) => {
    if (!isAllowed(req.socket.remoteAddress)) {
      log(`blocked request from ${req.socket.remoteAddress}`);
      return res.status(403).json({ error: 'Sirf same Wi-Fi se connect karo' });
    }
    if (!isLocal(req.socket.remoteAddress)) {
      fs.appendFile(path.join(config.dataDir, 'phone-access.log'),
        `${new Date().toISOString()} ${req.socket.remoteAddress} ${req.method} ${req.url.split('?')[0]}\n`,
        () => {});
    }
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    next();
  });

  const publicDir = path.join(__dirname, '..', 'public');
  app.use(express.static(publicDir, {
    index: 'index.html',
    etag: false,
    lastModified: false,
    setHeaders: (res) => res.setHeader('Cache-Control', 'no-store'),
  }));

  app.get('/api/health', (_req, res) => res.json({ ok: true }));

  function tokenValid(token) {
    const expected = config.getConfig().webToken;
    if (typeof token !== 'string' || token.length !== expected.length) return false;
    try {
      return crypto.timingSafeEqual(Buffer.from(token), Buffer.from(expected));
    } catch {
      return false;
    }
  }

  function authApi(req, res, next) {
    const header = req.headers.authorization || '';
    const token = header.startsWith('Bearer ') ? header.slice(7) : req.query.t;
    if (!tokenValid(token)) return res.status(401).json({ error: 'unauthorized' });
    next();
  }

  let tailscaleCache = { at: 0, ip: null };
  async function tailscaleIp(force = false) {
    if (!force && Date.now() - tailscaleCache.at < 60000) return tailscaleCache.ip;
    const candidates = ['tailscale', 'C:\\Program Files\\Tailscale\\tailscale.exe'];
    let ip = null;
    for (const bin of candidates) {
      try {
        const { stdout } = await execFileAsync(bin, ['ip', '-4'], { timeout: 3000, windowsHide: true });
        const first = stdout.trim().split(/\r?\n/)[0].trim();
        if (/^\d+\.\d+\.\d+\.\d+$/.test(first)) { ip = first; break; }
      } catch { /* try next */ }
    }
    tailscaleCache = { at: Date.now(), ip };
    return ip;
  }

  function lanIp() {
    try {
      for (const list of Object.values(os.networkInterfaces())) {
        for (const nic of list || []) {
          if (nic.family === 'IPv4' && !nic.internal && isPrivateLan(nic.address)) return nic.address;
        }
      }
    } catch { /* ignore */ }
    return null;
  }

  function phoneUrl(ip) {
    const cfg = config.getConfig();
    return `http://${ip || '127.0.0.1'}:${config.port}/?t=${cfg.webToken}`;
  }

  app.get('/api/desktop/info', async (req, res) => {
    if (!isLocal(req.socket.remoteAddress)) return res.status(403).json({ error: 'local only' });
    const cfg = config.getConfig();
    const tsIp = await tailscaleIp();
    const lan = lanIp();
    const ip = lan || tsIp;
    const url = phoneUrl(ip);
    let qr = null;
    try {
      qr = await QRCode.toDataURL(url, { margin: 1, width: 240, color: { dark: '#0b0b0f', light: '#ffffff' } });
    } catch { /* ignore */ }
    res.json({
      port: config.port,
      webToken: cfg.webToken,
      pairCode: cfg.pairCode,
      pairedChatId: cfg.pairedChatId || null,
      phoneUrl: url,
      qr,
      tailscaleIp: tsIp,
      lanIp: lan,
      bot: telegramState,
      agentReady: Boolean(agent && agent.ready),
      hasTelegramToken: Boolean(config.telegramToken),
      hasGeminiKey: Boolean(config.geminiKey),
      hasDeepseekKey: Boolean(config.deepseekKey),
      geminiModel: config.geminiModel,
      envFile: config.envFile,
      configPath: config.configPath,
      dataDir: config.dataDir,
    });
  });

  app.post('/api/desktop/regenerate-token', (req, res) => {
    if (!isLocal(req.socket.remoteAddress)) return res.status(403).json({ error: 'local only' });
    const token = config.regenerateToken();
    log('web token regenerated');
    res.json({ webToken: token });
  });

  app.get('/api/desktop/logs', (req, res) => {
    if (!isLocal(req.socket.remoteAddress)) return res.status(403).json({ error: 'local only' });
    try {
      const file = path.join(config.logDir, 'commands.log');
      const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
      res.json({ lines: text.trim().split('\n').slice(-60) });
    } catch (error) {
      res.json({ lines: ['log read error: ' + error.message] });
    }
  });

  app.get('/api/status', authApi, async (_req, res) => {
    const status = await actions.execute('get_status', {}, { channel: 'web' });
    res.json({ status, bot: telegramState, agentReady: Boolean(agent && agent.ready), tailscaleIp: await tailscaleIp() });
  });

  // ---- Khata Pro proxy (the login page's token is the gate) -----------------
  const khata = require('./khata');
  const apkFile = path.join(__dirname, '..', 'VX7-Remote.apk');
  let apkVersion = '1.0';
  try {
    apkVersion = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'apk-version.json'), 'utf8')).version
      || apkVersion;
  } catch { /* keep default */ }

  function khataSend(res, result) {
    if (result.ok) return res.json(result.data);
    return res.status(502).json({ ok: false, error: result.error });
  }

  app.get('/api/khata/health', authApi, async (_req, res) =>
    res.json({ ok: true, bridge: khata.available(), apkVersion }));

  app.get('/api/apk', authApi, (_req, res) => {
    if (!fs.existsSync(apkFile)) return res.status(404).json({ ok: false, error: 'APK missing' });
    res.setHeader('Content-Type', 'application/vnd.android.package-archive');
    res.setHeader('Content-Disposition', 'attachment; filename="VX7-Remote.apk"');
    fs.createReadStream(apkFile).pipe(res);
  });

  app.get('/api/khata/items', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/items'));
  });

  app.get('/api/khata/aging', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/aging'));
  });

  app.get('/api/khata/ledger.pdf', authApi, async (req, res) => {
    const q = new URLSearchParams();
    if (req.query.customer_id) q.set('customer_id', String(req.query.customer_id));
    if (req.query.customer) q.set('customer', String(req.query.customer));
    const result = await khata.request('GET', '/khata/ledger.pdf?' + q.toString());
    if (!result.ok || !result.buffer) {
      return res.status(502).json({ ok: false, error: result.error || 'PDF ban nahi paya' });
    }
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', 'attachment; filename="statement.pdf"');
    res.send(result.buffer);
  });

  app.get('/api/khata/report.pdf', authApi, async (req, res) => {
    const q = new URLSearchParams();
    if (req.query.from) q.set('from', String(req.query.from));
    if (req.query.to) q.set('to', String(req.query.to));
    const result = await khata.request('GET', '/khata/report.pdf?' + q.toString());
    if (!result.ok || !result.buffer) {
      return res.status(502).json({ ok: false, error: result.error || 'PDF ban nahi paya' });
    }
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', 'attachment; filename="register.pdf"');
    res.send(result.buffer);
  });

  app.post('/api/khata/pending/:id/edit', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', `/khata/pending/${Number(req.params.id)}/edit`, req.body || {}));
  });

  app.get('/api/khata/shop', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/shop'));
  });

  app.get('/api/khata/reminders', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/reminders'));
  });

  app.post('/api/khata/reminders', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/reminders', req.body || {}));
  });

  app.post('/api/khata/reminders/delete', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/reminders/delete', req.body || {}));
  });

  app.get('/api/khata/trend', authApi, async (req, res) => {
    khataSend(res, await khata.request('GET', '/khata/trend?days=' + Number(req.query.days || 7)));
  });

  app.get('/api/khata/items-report', authApi, async (req, res) => {
    khataSend(res, await khata.request('GET', '/khata/items-report?days=' + Number(req.query.days || 30)));
  });

  app.get('/api/khata/expenses', authApi, async (req, res) => {
    const q = req.query.date ? '?date=' + encodeURIComponent(String(req.query.date)) : '';
    khataSend(res, await khata.request('GET', '/khata/expenses' + q));
  });

  app.post('/api/khata/expenses', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/expenses', req.body || {}));
  });

  app.get('/api/khata/backup', authApi, async (_req, res) => {
    const result = await khata.request('GET', '/khata/backup');
    if (!result.ok || !result.buffer) {
      return res.status(502).json({ ok: false, error: result.error || 'Backup ban nahi paya' });
    }
    res.setHeader('Content-Type', 'application/x-sqlite3');
    res.setHeader('Content-Disposition', 'attachment; filename="khata-backup.db"');
    res.send(result.buffer);
  });

  app.get('/api/upi-qr', authApi, async (req, res) => {
    const shop = await khata.request('GET', '/khata/shop');
    const upi = shop.ok ? shop.data.upi_id : '';
    if (!upi) {
      return res.status(400).json({ ok: false, error: 'UPI ID set nahi hai — laptop: Phone Remote page me daalo' });
    }
    const amount = Number(req.query.amount || 0);
    if (!(amount > 0)) return res.status(400).json({ ok: false, error: 'Amount do' });
    const name = (shop.data && shop.data.shop_name) || 'Khata';
    const note = String(req.query.note || 'Khata payment');
    const uri = `upi://pay?pa=${encodeURIComponent(upi)}&pn=${encodeURIComponent(name)}`
      + `&am=${Number(amount).toFixed(2)}&cu=INR&tn=${encodeURIComponent(note)}`;
    const qr = await QRCode.toDataURL(uri, { margin: 1, width: 280 });
    res.json({ ok: true, qr, uri });
  });

  app.get('/api/khata/customers', authApi, async (req, res) => {
    khataSend(res, await khata.request('GET', '/khata/customers?search=' + encodeURIComponent(String(req.query.search || ''))));
  });

  app.get('/api/khata/summary', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/summary'));
  });

  app.get('/api/khata/pending', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/pending'));
  });

  app.post('/api/khata/entries', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/entries', req.body || {}));
  });

  app.post('/api/khata/pending/:id/post', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', `/khata/pending/${Number(req.params.id)}/post`));
  });

  app.post('/api/khata/pending/:id/delete', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', `/khata/pending/${Number(req.params.id)}/delete`));
  });

  app.post('/api/khata/customers', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/customers', req.body || {}));
  });

  app.get('/api/khata/overdue', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/overdue'));
  });

  app.get('/api/khata/top', authApi, async (req, res) => {
    khataSend(res, await khata.request('GET', '/khata/top?limit=' + Number(req.query.limit || 8)));
  });

  app.get('/api/khata/ledger', authApi, async (req, res) => {
    const q = new URLSearchParams();
    if (req.query.customer_id) q.set('customer_id', String(req.query.customer_id));
    if (req.query.customer) q.set('customer', String(req.query.customer));
    khataSend(res, await khata.request('GET', '/khata/ledger?' + q.toString()));
  });

  app.get('/api/khata/report', authApi, async (req, res) => {
    const q = new URLSearchParams();
    if (req.query.from) q.set('from', String(req.query.from));
    if (req.query.to) q.set('to', String(req.query.to));
    khataSend(res, await khata.request('GET', '/khata/report?' + q.toString()));
  });

  app.get('/api/khata/stats', authApi, async (_req, res) => {
    khataSend(res, await khata.request('GET', '/khata/stats'));
  });

  app.post('/api/khata/undo-last', authApi, async (_req, res) => {
    khataSend(res, await khata.request('POST', '/khata/undo-last'));
  });

  app.post('/api/khata/pin', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/pin', req.body || {}));
  });

  app.post('/api/khata/pin/verify', authApi, async (req, res) => {
    khataSend(res, await khata.request('POST', '/khata/pin/verify', req.body || {}));
  });

  app.post('/api/khata/scan', authApi, async (req, res) => {
    try {
      const image = String((req.body || {}).image || '');
      if (!image.startsWith('data:image/')) return res.status(400).json({ ok: false, error: 'image missing' });
      const comma = image.indexOf(',');
      const mimeType = image.slice(5, image.indexOf(';', 5));
      const buffer = Buffer.from(image.slice(comma + 1), 'base64');
      if (buffer.length > 6 * 1024 * 1024) return res.status(413).json({ ok: false, error: 'image too big' });
      const text = await ai.askVision({
        buffer,
        mimeType,
        prompt: 'This is a shop bill or parchi photo. Extract ONLY strict JSON: '
          + '{"customer": "name or empty", "item": "items summary", "quantity": "qty or empty", '
          + '"rate": "number or empty", "amount": "total number or empty", "type": "UDHAAR"}. '
          + 'Amounts in rupees, plain numbers, no symbols, no markdown.',
      });
      let draft = {};
      try { draft = JSON.parse(String(text).replace(/```json|```/gi, '').trim()); } catch { draft = {}; }
      res.json({ ok: true, draft, raw: String(text).slice(0, 300) });
    } catch (error) {
      res.status(500).json({ ok: false, error: error.message });
    }
  });

  // Evening closing (20:00 local): computed from the shared summary, kept for the phone banner.
  const closingFile = path.join(config.dataDir, 'closing.json');
  async function writeClosing() {
    const result = await khata.request('GET', '/khata/summary');
    if (!result.ok) return;
    fs.writeFileSync(closingFile, JSON.stringify({ at: new Date().toISOString(), ...result.data }));
  }
  const closingTimer = setInterval(() => {
    const now = new Date();
    if (now.getHours() === 20 && now.getMinutes() < 6) writeClosing().catch(() => {});
  }, 5 * 60 * 1000);
  if (closingTimer.unref) closingTimer.unref();

  app.get('/api/khata/closing', authApi, (_req, res) => {
    try {
      res.json({ ok: true, closing: JSON.parse(fs.readFileSync(closingFile, 'utf8')) });
    } catch {
      res.json({ ok: true, closing: null });
    }
  });

  const server = http.createServer(app);
  const wss = new WebSocketServer({ noServer: true });

  server.on('upgrade', (req, socket, head) => {
    if (!isAllowed(req.socket.remoteAddress)) return socket.destroy();
    let url;
    try { url = new URL(req.url, 'http://localhost'); } catch { return socket.destroy(); }
    if (url.pathname !== '/ws') return socket.destroy();
    if (!tokenValid(url.searchParams.get('t'))) return socket.destroy();
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws, req));
  });

  wss.on('connection', (ws) => {
    ws.stream = { on: false, timer: null };
    ws.chatTimes = [];
    ws.msgTimes = [];
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });

    safeSend(ws, { type: 'hello', agentReady: Boolean(agent && agent.ready) });

    ws.on('message', async (raw) => {
      let msg;
      try { msg = JSON.parse(raw.toString()); } catch { return; }
      if (!allowMessage(ws)) return;
      try {
        await handleMessage(ws, msg);
      } catch (error) {
        log('ws error: ' + error.message);
        safeSend(ws, { type: 'error', message: error.message });
      }
    });

    ws.on('close', () => stopStream(ws));
    ws.on('error', () => stopStream(ws));
  });

  function allowMessage(ws) {
    const now = Date.now();
    ws.msgTimes = ws.msgTimes.filter((t) => now - t < 10000);
    if (ws.msgTimes.length > 800) return false;
    ws.msgTimes.push(now);
    return true;
  }

  async function handleMessage(ws, msg) {
    switch (msg.type) {
      case 'ping':
        safeSend(ws, { type: 'pong', ts: Date.now() });
        return;

      case 'stream':
        if (msg.on) startStream(ws, clamp(Number(msg.fps || 1), 1, 3));
        else stopStream(ws);
        safeSend(ws, { type: 'stream_state', on: Boolean(msg.on) });
        return;

      case 'mouse': {
        const move = msg.move === 'absolute' ? 'absolute' : 'relative';
        if (move === 'absolute') {
          await agent.call('mouse', { move, x: clamp(Number(msg.x || 0), 0, 1), y: clamp(Number(msg.y || 0), 0, 1) });
        } else {
          await agent.call('mouse', { move, dx: clamp(Number(msg.dx || 0), -500, 500), dy: clamp(Number(msg.dy || 0), -500, 500) });
        }
        return;
      }

      case 'click':
        await agent.call('click', { button: msg.button === 'right' ? 'right' : 'left', count: clamp(Number(msg.count || 1), 1, 3) });
        return;

      case 'scroll':
        await agent.call('scroll', { amount: clamp(Number(msg.amount || 1), -20, 20) });
        return;

      case 'keys':
        await agent.call('keys', { combo: String(msg.combo || '').slice(0, 60) });
        return;

      case 'type':
        await agent.call('type', { text: String(msg.text || '').slice(0, 4000) }, 20000);
        return;

      case 'shot': {
        const shot = await agent.call('screenshot', { quality: 55, maxW: 1600 }, 20000);
        if (shot.ok) safeSend(ws, { type: 'shot', jpeg: shot.data.base64, w: shot.data.width, h: shot.data.height });
        else safeSend(ws, { type: 'error', message: shot.error });
        return;
      }

      case 'action': {
        const result = await actions.execute(String(msg.name || ''), msg.params || {}, {
          channel: 'web',
          confirmed: Boolean(msg.confirmed),
        });
        if (result.needsConfirm) {
          safeSend(ws, { type: 'needs_confirm', action: result.action, params: result.params, label: actions.describe(result.action, result.params) });
          return;
        }
        safeSend(ws, {
          type: 'action_result',
          ok: result.ok,
          error: result.error,
          text: actionText(result),
          screenshot: result.ok && result.base64 ? result.base64 : null,
        });
        return;
      }

      case 'chat': {
        const now = Date.now();
        ws.chatTimes = ws.chatTimes.filter((t) => now - t < 60000);
        if (ws.chatTimes.length >= 20) {
          safeSend(ws, { type: 'chat_reply', text: 'Rate limit. Thodi der baad try karo.' });
          return;
        }
        ws.chatTimes.push(now);
        const answer = await ai.ask({ text: String(msg.text || '').slice(0, 2000), channel: 'web' });
        safeSend(ws, {
          type: 'chat_reply',
          text: answer.reply,
          pending: answer.pending || null,
          screenshots: (answer.screenshots || []).map((s) => `data:image/jpeg;base64,${s.buffer.toString('base64')}`),
        });
        return;
      }

      case 'confirm': {
        const result = await actions.execute(String(msg.action || ''), msg.params || {}, { channel: 'web', confirmed: true });
        safeSend(ws, {
          type: 'action_result',
          ok: result.ok,
          error: result.error,
          text: actionText(result),
          screenshot: result.ok && result.base64 ? result.base64 : null,
        });
        return;
      }

      default:
        safeSend(ws, { type: 'error', message: 'unknown message type' });
    }
  }

  function actionText(result) {
    if (!result.ok) return 'Failed: ' + result.error;
    if (result.opened) return 'Opened ' + result.opened;
    if (result.closed) return `Closed ${result.app}`;
    if (result.locked) return 'Laptop locked';
    if (result.shutdownIn !== undefined) return `Shutdown in ${result.shutdownIn}s`;
    if (result.restartIn !== undefined) return `Restart in ${result.restartIn}s`;
    if (result.cancelled) return 'Shutdown cancelled';
    if (result.volume !== undefined) return `Volume ${result.volume}%${result.muted ? ' (muted)' : ''}`;
    if (result.typed !== undefined) return `Typed ${result.typed} chars`;
    if (result.pressed) return 'Pressed ' + result.pressed;
    if (result.buffer) return 'Screenshot ready';
    return 'Done';
  }

  function startStream(ws, fps) {
    stopStream(ws);
    ws.stream = { on: true, timer: null };
    const interval = 1000 / fps;
    const tick = async () => {
      if (!ws.stream || !ws.stream.on || ws.readyState !== 1) return;
      if (ws.bufferedAmount > 1500000) {
        schedule();
        return;
      }
      const shot = await agent.call('screenshot', { quality: 45, maxW: 1280 }, 20000);
      if (shot.ok && ws.readyState === 1) {
        safeSend(ws, { type: 'frame', jpeg: shot.data.base64, w: shot.data.width, h: shot.data.height, ts: Date.now() });
      }
      schedule();
    };
    const schedule = () => {
      if (ws.stream && ws.stream.on) ws.stream.timer = setTimeout(tick, interval);
    };
    tick();
  }

  function stopStream(ws) {
    if (ws.stream) {
      ws.stream.on = false;
      clearTimeout(ws.stream.timer);
    }
  }

  function safeSend(ws, payload) {
    if (ws.readyState === 1) {
      try { ws.send(JSON.stringify(payload)); } catch { /* ignore */ }
    }
  }

  const heartbeat = setInterval(() => {
    wss.clients.forEach((ws) => {
      if (ws.isAlive === false) return ws.terminate();
      ws.isAlive = false;
      try { ws.ping(); } catch { /* ignore */ }
    });
  }, 30000);
  heartbeat.unref();

  const port = config.port;
  server.listen(port, () => log(`server listening on ${port} (localhost + same Wi-Fi)`));

  return {
    server,
    wss,
    port,
    tailscaleIp,
    close() {
      clearInterval(heartbeat);
      try { wss.close(); } catch { /* ignore */ }
      try { server.close(); } catch { /* ignore */ }
    },
  };
}

module.exports = { createServer, isAllowed, isLocal, isTailscale };
