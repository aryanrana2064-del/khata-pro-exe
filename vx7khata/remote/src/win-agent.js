'use strict';

const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { spawn } = require('node:child_process');

class WinAgent {
  constructor({ exePath, onLog = () => {} }) {
    this.exePath = exePath;
    this.onLog = onLog;
    this.child = null;
    this.pending = new Map();
    this.seq = 0;
    this.ready = false;
    this.startPromise = null;
    this.stopped = false;
    this.restartTimer = null;
  }

  exists() {
    return fs.existsSync(this.exePath);
  }

  start() {
    if (this.ready && this.child) return Promise.resolve();
    if (this.startPromise) return this.startPromise;
    if (!this.exists()) {
      return Promise.reject(new Error(`Helper missing: ${this.exePath}. Run: npm run build:agent`));
    }

    this.startPromise = new Promise((resolve, reject) => {
      const child = spawn(this.exePath, [], {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
      this.child = child;

      const readyTimer = setTimeout(() => {
        this.onLog('agent ready timeout');
        try { child.kill(); } catch { /* ignore */ }
        reject(new Error('Helper did not start'));
      }, 10000);

      const rl = readline.createInterface({ input: child.stdout });
      rl.on('line', (line) => {
        let msg;
        try { msg = JSON.parse(line); } catch { return; }
        if (msg.event === 'ready') {
          this.ready = true;
          clearTimeout(readyTimer);
          this.onLog(`agent ready pid ${msg.pid}`);
          resolve();
          return;
        }
        const entry = this.pending.get(msg.id);
        if (!entry) return;
        this.pending.delete(msg.id);
        clearTimeout(entry.timer);
        entry.resolve(msg);
      });

      child.stderr.on('data', (data) => this.onLog('agent stderr: ' + data.toString().trim()));
      child.on('error', (error) => {
        this.onLog('agent spawn error: ' + error.message);
        clearTimeout(readyTimer);
        reject(error);
      });
      child.on('exit', (code) => {
        this.ready = false;
        this.child = null;
        this.startPromise = null;
        clearTimeout(readyTimer);
        for (const [, entry] of this.pending) {
          clearTimeout(entry.timer);
          entry.resolve({ ok: false, error: 'helper exited' });
        }
        this.pending.clear();
        if (!this.stopped) {
          this.onLog(`agent exited (${code}), restarting in 2s`);
          clearTimeout(this.restartTimer);
          this.restartTimer = setTimeout(() => {
            this.start().catch((error) => this.onLog('agent restart failed: ' + error.message));
          }, 2000);
        }
      });
    }).finally(() => {
      this.startPromise = null;
    });

    return this.startPromise;
  }

  async call(op, params = {}, timeout = 10000) {
    await this.start();
    const id = ++this.seq;
    const payload = JSON.stringify({ id, op, ...params });
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ ok: false, error: `${op} timed out` });
      }, timeout);
      this.pending.set(id, { resolve, timer });
      try {
        this.child.stdin.write(payload + '\n');
      } catch (error) {
        this.pending.delete(id);
        clearTimeout(timer);
        resolve({ ok: false, error: error.message });
      }
    });
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.restartTimer);
    if (this.child) {
      try { this.child.stdin.end(); } catch { /* ignore */ }
      try { this.child.kill(); } catch { /* ignore */ }
    }
  }
}

module.exports = { WinAgent };
