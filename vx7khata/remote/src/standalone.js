'use strict';

const path = require('node:path');
const config = require('./config');
const { WinAgent } = require('./win-agent');
const { createActions } = require('./actions');
const { createAI } = require('./ai');
const { createServer } = require('./server');

function log(message) {
  console.log(`[${new Date().toLocaleTimeString()}] ${message}`);
}

const agent = new WinAgent({ exePath: path.join(config.resourcesDir(), 'win-agent.exe'), onLog: log });
const actions = createActions({ agent, apps: config.apps, logDir: config.logDir });
const ai = createAI({ geminiKey: config.geminiKey, deepseekKey: config.deepseekKey, model: config.geminiModel, actions, log });

log('data dir: ' + config.dataDir);
log('env file: ' + (config.envFile || 'NOT FOUND (.env missing)'));

// This build is phone-only: no Telegram bot runs inside Khata Pro.
const telegramState = { enabled: false, status: 'disabled', username: null, error: null };

const web = createServer({ config, actions, ai, agent, telegramState, log });

web.tailscaleIp().then((ip) => {
  const token = config.getConfig().webToken;
  log('local:      http://127.0.0.1:' + config.port + '/?t=' + token);
  if (ip) log('tailscale:  http://' + ip + ':' + config.port + '/?t=' + token);
  else log('tailscale:  not detected');
  log('pair code:  ' + config.getConfig().pairCode + '  (Telegram: /pair ' + config.getConfig().pairCode + ')');
});

process.on('SIGINT', () => {
  log('shutting down');
  web.close();
  agent.stop();
  process.exit(0);
});

process.stdin.resume();
