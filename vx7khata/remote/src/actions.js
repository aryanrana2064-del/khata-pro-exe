'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');

const CONFIRM_ACTIONS = new Set(['close_app', 'shutdown_pc', 'restart_pc', 'lock_pc']);

const SAFE_LIMITS = {
  type_text: 4000,
  copy_to_clipboard: 200000,
  press_keys: 60,
  text: 2000,
};

function expandEnv(value) {
  return String(value).replace(/%([^%]+)%/g, (_, name) => process.env[name] || `%${name}%`);
}

function startDetached(file, args = []) {
  const child = spawn(file, args, { detached: true, stdio: 'ignore', windowsHide: false });
  child.unref();
}

function startViaCmd(target) {
  const child = spawn('cmd.exe', ['/c', 'start', '', target], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  });
  child.unref();
}

async function resolveYouTubeFirst(query) {
  const searchUrl = 'https://www.youtube.com/results?search_query=' + encodeURIComponent(query);
  try {
    const response = await fetch(searchUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
        'Accept-Language': 'en-IN,en;q=0.9',
      },
      signal: AbortSignal.timeout(20000),
    });
    if (response.ok) {
      const html = await response.text();
      const match = html.match(/"videoId":"([A-Za-z0-9_-]{11})"/);
      if (match) return { url: `https://www.youtube.com/watch?v=${match[1]}&autoplay=1`, videoId: match[1] };
    }
  } catch { /* fall back to search page */ }
  return { url: searchUrl, videoId: null };
}

function createActions({ agent, apps, logDir }) {
  const auditPath = path.join(logDir, 'commands.log');
  let batteryCache = { at: 0, value: null, charging: null };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function focusTabByTitle(title) {
    const needle = String(title || '').toLowerCase();
    if (!needle) return { found: false };
    const readWindows = async () => {
      const res = await agent.call('windows');
      return (res.ok && res.data && res.data.windows) || [];
    };
    let windows = await readWindows();
    const direct = windows.find((w) => w.title.toLowerCase().includes(needle));
    if (direct) {
      await agent.call('focus', { title: direct.title });
      return { found: true, title: direct.title };
    }
    const browser = windows.find((w) => /chrome|edge|brave|firefox/i.test(w.title));
    if (!browser) return { found: false };
    await agent.call('focus', { title: browser.title });
    await sleep(300);
    for (let i = 0; i < 12; i += 1) {
      await agent.call('keys', { combo: 'ctrl+tab' });
      await sleep(300);
      windows = await readWindows();
      const match = windows.find((w) => w.title.toLowerCase().includes(needle));
      if (match) {
        await agent.call('focus', { title: match.title });
        return { found: true, title: match.title };
      }
    }
    return { found: false };
  }

  function audit(channel, action, params, result) {
    try {
      const safe = { ...params };
      if (safe.text) safe.text = `[${String(safe.text).length} chars]`;
      if (safe.base64) safe.base64 = '[image]';
      const line = `${new Date().toISOString()} | ${channel} | ${action} | ${JSON.stringify(safe)} | ${result}\n`;
      fs.appendFileSync(auditPath, line);
    } catch { /* ignore */ }
  }

  function findApp(name) {
    if (!name) return null;
    const needle = String(name).trim().toLowerCase();
    if (apps[needle]) return { key: needle, ...apps[needle] };
    for (const [key, app] of Object.entries(apps)) {
      if (app.title && app.title.toLowerCase().includes(needle)) return { key, ...app };
      if (needle.includes(key)) return { key, ...app };
    }
    return null;
  }

  function listApps() {
    return Object.entries(apps).map(([key, app]) => ({ key, title: app.title || key }));
  }

  function cpuUsage() {
    return new Promise((resolve) => {
      const a = os.cpus();
      setTimeout(() => {
        const b = os.cpus();
        let idle = 0;
        let total = 0;
        for (let i = 0; i < a.length; i += 1) {
          const t1 = a[i].times;
          const t2 = b[i].times;
          idle += t2.idle - t1.idle;
          total += (t2.user - t1.user) + (t2.nice - t1.nice) + (t2.sys - t1.sys) + (t2.idle - t1.idle) + (t2.irq - t1.irq);
        }
        resolve(total > 0 ? Math.round(100 * (1 - idle / total)) : 0);
      }, 300);
    });
  }

  function batteryInfo() {
    if (Date.now() - batteryCache.at < 30000) return Promise.resolve(batteryCache);
    return new Promise((resolve) => {
      const script = 'if ($b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1) { "$($b.EstimatedChargeRemaining)|$($b.BatteryStatus)" }';
      execFile('powershell.exe', ['-NoProfile', '-Command', script], { windowsHide: true, timeout: 6000 }, (error, stdout) => {
        if (error || !stdout.trim()) {
          batteryCache = { at: Date.now(), value: null, charging: null };
          return resolve(batteryCache);
        }
        const [level, status] = stdout.trim().split('|');
        batteryCache = { at: Date.now(), value: Number(level), charging: String(status) === '2' };
        resolve(batteryCache);
      });
    });
  }

  function clipboardGet() {
    return new Promise((resolve) => {
      execFile('powershell.exe', ['-NoProfile', '-Command', 'Get-Clipboard -Raw'], { windowsHide: true, timeout: 6000 }, (error, stdout) => {
        if (error) return resolve({ ok: false, error: 'clipboard read failed' });
        resolve({ ok: true, text: stdout.replace(/\r\n$/, '') });
      });
    });
  }

  function clipboardSet(text) {
    return new Promise((resolve) => {
      const child = spawn('powershell.exe', ['-NoProfile', '-Command', '$t=[Console]::In.ReadToEnd(); Set-Clipboard -Value $t'], {
        stdio: ['pipe', 'ignore', 'pipe'],
        windowsHide: true,
      });
      child.stdin.end(String(text));
      const timer = setTimeout(() => { try { child.kill(); } catch { /* ignore */ } resolve({ ok: false, error: 'clipboard write timeout' }); }, 6000);
      child.on('exit', (code) => {
        clearTimeout(timer);
        resolve(code === 0 ? { ok: true, chars: String(text).length } : { ok: false, error: 'clipboard write failed' });
      });
    });
  }

  async function searchFiles(query) {
    const q = String(query || '').toLowerCase();
    if (!q) return [];
    const roots = ['Desktop', 'Documents', 'Downloads']
      .map((dir) => path.join(os.homedir(), dir))
      .filter((dir) => { try { return fs.statSync(dir).isDirectory(); } catch { return false; } });
    const results = [];
    async function walk(dir, depth) {
      if (results.length >= 20 || depth > 3) return;
      let entries;
      try { entries = await fs.promises.readdir(dir, { withFileTypes: true }); } catch { return; }
      for (const entry of entries) {
        if (results.length >= 20) return;
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(dir, entry.name);
        if (entry.name.toLowerCase().includes(q)) {
          results.push({ name: entry.name, path: full, isDir: entry.isDirectory() });
        }
        if (entry.isDirectory()) await walk(full, depth + 1);
      }
    }
    for (const root of roots) await walk(root, 1);
    return results;
  }

  function summarize(action, result) {
    if (!result.ok) return result;
    if (action === 'screenshot') return { ok: true, width: result.width, height: result.height };
    if (action === 'get_clipboard') return { ok: true, chars: String(result.text || '').length };
    return { ok: true, ...result };
  }

  async function execute(action, params = {}, { channel = 'app', confirmed = false } = {}) {
    const actionName = String(action || '');
    if (CONFIRM_ACTIONS.has(actionName) && !confirmed) {
      return { needsConfirm: true, action: actionName, params };
    }

    let result;
    try {
      switch (actionName) {
        case 'open_app': {
          const app = findApp(params.name);
          if (!app) throw new Error(`App nahi mila: ${params.name}`);
          if (app.shell) {
            startViaCmd(app.shell);
          } else {
            const candidates = (app.launch || []).map(expandEnv);
            const existing = candidates.find((c) => { try { return fs.existsSync(c); } catch { return false; } });
            const target = existing || candidates[0];
            if (!target) throw new Error(`Launch command missing for ${app.key}`);
            if (path.isAbsolute(target)) startDetached(target);
            else startViaCmd(target);
          }
          result = { ok: true, opened: app.title || app.key };
          break;
        }
        case 'close_app': {
          const app = findApp(params.name);
          if (!app) throw new Error(`App nahi mila: ${params.name}`);
          const answer = await agent.call('closeApp', { names: app.proc || [], force: Boolean(params.force) }, 10000);
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, closed: answer.data.closed, app: app.title || app.key };
          break;
        }
        case 'set_volume': {
          const answer = await agent.call('volume', { action: 'set', level: clamp(Number(params.level), 0, 100) });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'change_volume': {
          const direction = params.direction === 'down' ? 'down' : 'up';
          const steps = clamp(Number(params.steps || 5), 1, 50);
          const answer = await agent.call('volume', { action: direction, steps });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'mute': {
          const answer = await agent.call('volume', { action: 'mute', on: params.on !== false });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'focus_tab': {
          const found = await focusTabByTitle(params.title);
          result = { ok: true, ...found };
          break;
        }
        case 'ensure_chatgpt': {
          let found = await focusTabByTitle('ChatGPT');
          if (!found.found) {
            startDetached('rundll32.exe', ['url.dll,FileProtocolHandler', 'https://chatgpt.com']);
            await sleep(7000);
            found = await focusTabByTitle('ChatGPT');
          }
          result = { ok: true, found: found.found, title: found.title || null };
          break;
        }
        case 'close_tab': {
          const answer = await agent.call('keys', { combo: 'ctrl+w' });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, tabClosed: true };
          break;
        }
        case 'play_youtube': {
          const query = String(params.query || '').slice(0, 200).trim();
          if (!query) throw new Error('song name missing');
          const found = await resolveYouTubeFirst(query);
          startDetached('rundll32.exe', ['url.dll,FileProtocolHandler', found.url]);
          result = { ok: true, opened: found.url, query, videoId: found.videoId };
          break;
        }
        case 'media_control': {
          const answer = await agent.call('media', { action: params.action || 'playpause' });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'screenshot': {
          const answer = await agent.call('screenshot', {
            quality: clamp(Number(params.quality || 60), 20, 90),
            maxW: clamp(Number(params.maxW || 1600), 400, 2560),
          }, 25000);
          if (!answer.ok) throw new Error(answer.error);
          const buffer = Buffer.from(answer.data.base64, 'base64');
          result = { ok: true, buffer, base64: answer.data.base64, width: answer.data.width, height: answer.data.height };
          break;
        }
        case 'lock_pc': {
          const answer = await agent.call('lock');
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, locked: true };
          break;
        }
        case 'shutdown_pc': {
          const seconds = clamp(Number(params.seconds || 60), 0, 3600);
          const answer = await agent.call('shutdown', { mode: 'shutdown', seconds });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, shutdownIn: seconds };
          break;
        }
        case 'restart_pc': {
          const seconds = clamp(Number(params.seconds || 60), 0, 3600);
          const answer = await agent.call('shutdown', { mode: 'restart', seconds });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, restartIn: seconds };
          break;
        }
        case 'cancel_shutdown': {
          const answer = await agent.call('cancelShutdown');
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, cancelled: true };
          break;
        }
        case 'list_windows': {
          const answer = await agent.call('windows');
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, windows: answer.data.windows };
          break;
        }
        case 'focus_window': {
          const answer = await agent.call('focus', { title: params.title });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, focused: params.title };
          break;
        }
        case 'type_text': {
          const text = String(params.text || '').slice(0, SAFE_LIMITS.type_text);
          const answer = await agent.call('type', { text }, 20000);
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, typed: text.length };
          break;
        }
        case 'press_keys': {
          const combo = String(params.combo || '').slice(0, SAFE_LIMITS.press_keys);
          const answer = await agent.call('keys', { combo });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, pressed: combo };
          break;
        }
        case 'scroll': {
          const answer = await agent.call('scroll', { amount: clamp(Number(params.amount || 1), -20, 20) });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'move_mouse': {
          const answer = await agent.call('mouse', { move: 'relative', dx: clamp(Number(params.dx || 0), -3000, 3000), dy: clamp(Number(params.dy || 0), -3000, 3000) });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'click': {
          const answer = await agent.call('click', { button: params.button || 'left', count: clamp(Number(params.count || 1), 1, 3) });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, ...answer.data };
          break;
        }
        case 'click_at': {
          const x = clamp(Number(params.x), 0, 1);
          const y = clamp(Number(params.y), 0, 1);
          const moved = await agent.call('mouse', { move: 'absolute', x, y });
          if (!moved.ok) throw new Error(moved.error);
          const answer = await agent.call('click', { button: params.button || 'left', count: 1 });
          if (!answer.ok) throw new Error(answer.error);
          result = { ok: true, clickedAt: { x, y } };
          break;
        }
        case 'copy_to_clipboard': {
          result = await clipboardSet(String(params.text || '').slice(0, SAFE_LIMITS.copy_to_clipboard));
          break;
        }
        case 'get_clipboard': {
          result = await clipboardGet();
          break;
        }
        case 'search_files': {
          const results = await searchFiles(String(params.query || '').slice(0, 100));
          result = { ok: true, results };
          break;
        }
        case 'open_path': {
          const target = String(params.target || '').trim();
          if (!target) throw new Error('target missing');
          if (/^https?:\/\//i.test(target)) {
            startDetached('rundll32.exe', ['url.dll,FileProtocolHandler', target]);
            result = { ok: true, opened: target };
          } else if (/^ms-settings:/i.test(target)) {
            startViaCmd(target);
            result = { ok: true, opened: target };
          } else if (fs.existsSync(target)) {
            const child = spawn('explorer.exe', [target], { detached: true, stdio: 'ignore', windowsHide: false });
            child.unref();
            result = { ok: true, opened: target };
          } else {
            throw new Error(`Path not found: ${target}`);
          }
          break;
        }
        case 'get_status': {
          const [cpu, battery, agentStatus] = await Promise.all([cpuUsage(), batteryInfo(), agent.call('status')]);
          result = {
            ok: true,
            cpu,
            ramUsedGB: round1((os.totalmem() - os.freemem()) / 1024 ** 3),
            ramTotalGB: round1(os.totalmem() / 1024 ** 3),
            uptimeHours: round1(os.uptime() / 3600),
            battery: battery.value,
            charging: battery.charging,
            volume: agentStatus.ok ? agentStatus.data.volume : null,
            muted: agentStatus.ok ? agentStatus.data.muted : null,
            screen: agentStatus.ok ? `${agentStatus.data.screenWidth}x${agentStatus.data.screenHeight}` : null,
          };
          break;
        }
        case 'list_apps': {
          result = { ok: true, apps: listApps() };
          break;
        }
        default:
          throw new Error(`Unknown action: ${actionName}`);
      }
    } catch (error) {
      result = { ok: false, error: error.message };
    }

    audit(channel, actionName, params, result.ok ? 'ok' : `error: ${result.error}`);
    if (result.ok) result.summary = summarize(actionName, result);
    return result;
  }

  function describe(action, params = {}) {
    switch (action) {
      case 'shutdown_pc': return `Laptop shutdown in ${params.seconds || 60}s`;
      case 'restart_pc': return `Laptop restart in ${params.seconds || 60}s`;
      case 'lock_pc': return 'Laptop lock';
      case 'close_app': return `Close app: ${params.name}`;
      default: return action;
    }
  }

  function requiresConfirm(action) {
    return CONFIRM_ACTIONS.has(action);
  }

  return { execute, describe, requiresConfirm, listApps, auditPath };
}

function clamp(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

module.exports = { createActions, CONFIRM_ACTIONS };
