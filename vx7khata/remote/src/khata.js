'use strict';

// Client for the localhost Khata bridge (started inside the Khata Pro Python app).
// The URL and token arrive as env vars when Khata spawns this server.

const base = process.env.KHATA_BRIDGE_URL || '';
const token = process.env.KHATA_BRIDGE_TOKEN || '';

function available() {
  return Boolean(base && token);
}

async function request(method, path, body) {
  if (!available()) {
    return { ok: false, error: 'Khata bridge available nahi hai (server Khata Pro ke bahar chal raha hai).' };
  }
  try {
    const response = await fetch(base + path, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Bridge-Token': token },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(12000),
    });
    if ((response.headers.get('content-type') || '').match(/pdf|sqlite|octet-stream/)) {
      const buffer = Buffer.from(await response.arrayBuffer());
      return { ok: true, buffer, filename: 'document.pdf' };
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      return { ok: false, error: data.error || `HTTP ${response.status}`, needsOld: data.needs_old };
    }
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: 'Khata se connect nahi hua: ' + (error.message || error) };
  }
}

function callTool(name, args = {}) {
  switch (name) {
    case 'khata_add_entry':
      return request('POST', '/khata/entries', args);
    case 'khata_customers':
      return request('GET', '/khata/customers?search=' + encodeURIComponent(args.search || ''));
    case 'khata_summary':
      return request('GET', '/khata/summary');
    case 'khata_pending':
      return request('GET', '/khata/pending');
    case 'khata_post_pending':
      return request('POST', `/khata/pending/${Number(args.id)}/post`);
    case 'khata_delete_pending':
      return request('POST', `/khata/pending/${Number(args.id)}/delete`);
    case 'khata_add_customer':
      return request('POST', '/khata/customers', args);
    case 'khata_report': {
      const q = new URLSearchParams();
      if (args.from) q.set('from', args.from);
      if (args.to) q.set('to', args.to);
      return request('GET', '/khata/report?' + q.toString());
    }
    case 'khata_overdue':
      return request('GET', '/khata/overdue');
    case 'khata_top':
      return request('GET', '/khata/top?limit=' + Number(args.limit || 8));
    case 'khata_aging':
      return request('GET', '/khata/aging');
    default:
      return Promise.resolve({ ok: false, error: 'unknown khata tool' });
  }
}

module.exports = { available, request, callTool };
