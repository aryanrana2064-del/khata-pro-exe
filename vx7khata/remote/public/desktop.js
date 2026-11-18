'use strict';

const $ = (id) => document.getElementById(id);
let tokenVisible = false;
let latest = null;

function setChip(id, ok, text) {
  const el = $(id);
  el.classList.remove('ok', 'bad');
  el.classList.add(ok ? 'ok' : 'bad');
  if (text) el.textContent = text;
}

async function refresh() {
  try {
    const response = await fetch('/api/desktop/info', { cache: 'no-store' });
    const info = await response.json();
    latest = info;

    $('pairCode').textContent = info.pairCode || '------';
    $('pairCmd').textContent = `/pair ${info.pairCode || '------'}`;

    const bot = info.bot || {};
    if (bot.status === 'polling') {
      $('botStatus').textContent = '@' + (bot.username || 'bot') + ' online';
      setChip('chipToken', true, 'Bot token');
    } else if (bot.status === 'error') {
      $('botStatus').textContent = 'Error: ' + (bot.error || 'start failed');
      setChip('chipToken', true, 'Bot token');
    } else {
      $('botStatus').textContent = 'Token missing';
      setChip('chipToken', false, 'Bot token');
    }

    setChip('chipGemini', info.hasGeminiKey, 'Gemini key');
    setChip('chipDeepseek', info.hasDeepseekKey, 'DeepSeek key');
    setChip('chipPaired', Boolean(info.pairedChatId), info.pairedChatId ? 'Paired' : 'Not paired');
    setChip('chipAgent', info.agentReady, 'Windows helper');
    setChip('chipTailscale', Boolean(info.tailscaleIp), info.tailscaleIp ? 'Tailscale ' + info.tailscaleIp : 'Tailscale off');
    setChip('chipEnv', Boolean(info.envFile), '.env');

    const online = bot.status === 'polling' && info.pairedChatId && info.agentReady && info.tailscaleIp;
    const dot = $('statusDot');
    dot.classList.remove('ok', 'bad');
    dot.classList.add(online ? 'ok' : 'bad');
    $('statusText').textContent = online
      ? 'Ready — phone se control karo'
      : (info.agentReady ? 'Setup incomplete — neeche checks dekho' : 'Helper starting…');

    $('phoneUrl').value = info.phoneUrl || '';
    $('token').value = info.webToken || '';
    $('token').type = tokenVisible ? 'text' : 'password';
    $('revealToken').textContent = tokenVisible ? 'Hide' : 'Show';

    if (info.qr) {
      $('qr').src = info.qr;
      $('qr').style.display = '';
    } else {
      $('qr').style.display = 'none';
    }

    $('tsStatus').textContent = info.tailscaleIp || 'install/login pending';

    const missing = [];
    if (!info.hasTelegramToken) missing.push('TELEGRAM_BOT_TOKEN');
    if (!info.hasGeminiKey && !info.hasDeepseekKey) missing.push('GEMINI_API_KEY ya DEEPSEEK_API_KEY');
    $('envHint').textContent = info.envFile
      ? `Config: ${info.envFile}${missing.length ? ' — missing: ' + missing.join(', ') : ''}`
      : `Koi .env nahi mila. ${configHint(info)}`;
  } catch (error) {
    $('statusText').textContent = 'App backend se connect nahi';
    $('statusDot').classList.remove('ok');
    $('statusDot').classList.add('bad');
  }
}

function configHint(info) {
  return info && info.dataDir
    ? `Yahan .env banao: ${info.dataDir}\\.env (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY)`
    : '.env file me TELEGRAM_BOT_TOKEN aur GEMINI_API_KEY daalo.';
}

async function refreshLogs() {
  try {
    const response = await fetch('/api/desktop/logs', { cache: 'no-store' });
    const data = await response.json();
    $('logLines').textContent = (data.lines || []).join('\n') || 'Abhi koi command nahi.';
  } catch { /* ignore */ }
}

$('copyUrl').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('phoneUrl').value);
    $('copyUrl').textContent = 'Copied';
    setTimeout(() => { $('copyUrl').textContent = 'Copy'; }, 1200);
  } catch { /* ignore */ }
});

$('revealToken').addEventListener('click', () => {
  tokenVisible = !tokenVisible;
  $('token').type = tokenVisible ? 'text' : 'password';
  $('revealToken').textContent = tokenVisible ? 'Hide' : 'Show';
});

$('regenToken').addEventListener('click', async () => {
  if (!confirm('Naya web token banaye? Purana phone link turant band ho jayega.')) return;
  try {
    const response = await fetch('/api/desktop/regenerate-token', { method: 'POST' });
    const data = await response.json();
    if (data.webToken) {
      $('token').value = data.webToken;
      await refresh();
    }
  } catch { /* ignore */ }
});

$('autostart').addEventListener('change', async (event) => {
  const enabled = await window.vx7.setAutostart(event.target.checked);
  event.target.checked = enabled;
});

$('shortcut').addEventListener('click', async () => {
  const result = await window.vx7.createShortcut();
  alert(result.ok ? 'Desktop shortcut ban gaya:\n' + result.path : 'Shortcut nahi bana: ' + (result.error || 'unknown'));
});

$('logs').addEventListener('click', async () => {
  await window.vx7.openLogs();
});

$('quit').addEventListener('click', async () => {
  if (confirm('VX7 Remote band kare? Phone se control nahi hoga.')) {
    await window.vx7.quit();
  }
});

(async () => {
  if (window.vx7) {
    const info = await window.vx7.getInfo();
    $('autostart').checked = Boolean(info.autostart);
  }
  await refresh();
  await refreshLogs();
  setInterval(refresh, 3000);
  setInterval(refreshLogs, 5000);
})();
