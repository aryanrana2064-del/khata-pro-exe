'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const dotenv = require('dotenv');

const dataDir = path.join(process.env.APPDATA || os.homedir(), 'vx7-remote');
fs.mkdirSync(path.join(dataDir, 'logs'), { recursive: true });

function findEnvFile() {
  const candidates = [];
  if (process.env.VX7_ENV_FILE) candidates.push(process.env.VX7_ENV_FILE);
  candidates.push(path.join(__dirname, '..', '.env'));
  if (process.env.PORTABLE_EXECUTABLE_DIR) {
    candidates.push(path.join(process.env.PORTABLE_EXECUTABLE_DIR, '.env'));
  }
  try {
    candidates.push(path.join(path.dirname(process.execPath), '.env'));
  } catch { /* ignore */ }
  if (process.resourcesPath) candidates.push(path.join(process.resourcesPath, '..', '.env'));
  candidates.push(path.join(dataDir, '.env'));
  for (const file of candidates) {
    try { if (fs.existsSync(file)) return file; } catch { /* ignore */ }
  }
  return null;
}

const envFile = findEnvFile();
if (envFile) dotenv.config({ path: envFile, override: false });

function resourcesDir() {
  const packaged = process.resourcesPath && path.join(process.resourcesPath, 'resources', 'apps.json');
  if (packaged && fs.existsSync(packaged)) return path.join(process.resourcesPath, 'resources');
  return path.join(__dirname, '..', 'resources');
}

function loadApps() {
  try {
    return JSON.parse(fs.readFileSync(path.join(resourcesDir(), 'apps.json'), 'utf8'));
  } catch {
    return {};
  }
}

function newToken() {
  return crypto.randomBytes(24).toString('base64url');
}

function newPairCode() {
  return String(crypto.randomInt(100000, 999999));
}

const configPath = path.join(dataDir, 'config.json');
let state = {};
try { state = JSON.parse(fs.readFileSync(configPath, 'utf8')); } catch { state = {}; }
if (!state.webToken) state.webToken = newToken();
if (!state.pairCode) state.pairCode = newPairCode();
if (!state.createdAt) state.createdAt = new Date().toISOString();
save();

function save() {
  try {
    fs.writeFileSync(configPath, JSON.stringify(state, null, 2));
  } catch (error) {
    console.error('[config] save failed:', error.message);
  }
}

function getConfig() {
  return state;
}

function updateConfig(patch) {
  state = { ...state, ...patch };
  save();
  return state;
}

function regenerateToken() {
  updateConfig({ webToken: newToken() });
  return state.webToken;
}

module.exports = {
  dataDir,
  envFile,
  resourcesDir,
  configPath,
  logDir: path.join(dataDir, 'logs'),
  apps: loadApps(),
  port: Number(process.env.PORT || 8765),
  getConfig,
  updateConfig,
  regenerateToken,
  telegramToken: process.env.TELEGRAM_BOT_TOKEN || '',
  geminiKey: process.env.GEMINI_API_KEY || '',
  deepseekKey: process.env.DEEPSEEK_API_KEY || '',
  geminiModel: process.env.GEMINI_MODEL || 'gemini-3.1-flash-lite',
};
