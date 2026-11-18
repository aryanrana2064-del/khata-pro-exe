'use strict';

const SITES = {
  chatgpt: { title: 'ChatGPT', url: 'https://chatgpt.com' },
  'chat gpt': { title: 'ChatGPT', url: 'https://chatgpt.com' },
  youtube: { title: 'YouTube', url: 'https://youtube.com' },
  google: { title: 'Google', url: 'https://google.com' },
  gmail: { title: 'Gmail', url: 'https://mail.google.com' },
  instagram: { title: 'Instagram', url: 'https://instagram.com' },
  facebook: { title: 'Facebook', url: 'https://facebook.com' },
  github: { title: 'GitHub', url: 'https://github.com' },
  netflix: { title: 'Netflix', url: 'https://netflix.com' },
  'whatsapp web': { title: 'WhatsApp', url: 'https://web.whatsapp.com' },
  gpt: { title: 'ChatGPT', url: 'https://chatgpt.com' },
};

function normalize(raw) {
  return String(raw || '')
    .toLowerCase()
    .replace(/\bkhol\d+\b/g, 'kholo')
    .replace(/\bkhol\b/g, 'kholo')
    .replace(/\bkro\b/g, 'karo')
    .replace(/\bkr do\b/g, 'kar do')
    .replace(/\b(kikho|kihko|likhao|likh do|likha do)\b/g, 'likho')
    .replace(/\b(plau|playy|palay|plai)\b/g, 'play')
    .replace(/\b(essme|esme|isme|us mein|usmein)\b/g, 'usme')
    .trim()
    .replace(/[.!?]+$/g, '');
}

function buildAskChatGpt(text, lastTarget) {
  const t = normalize(text);
  let question = null;
  let explicit = false;

  let match = t.match(/^(?:chatgpt|chat gpt|gpt)(?:\s+se)?\s+(?:pucho|poocho)\s+(.+)$/);
  if (match) { question = match[1]; explicit = true; }
  if (!question) {
    match = t.match(/^(?:chatgpt|chat gpt|gpt)(?:\s+se)?\s+(.+?)\s+(?:pucho|poocho|puch lo|ask karo|ask)$/);
    if (match) { question = match[1]; explicit = true; }
  }
  if (!question) {
    match = t.match(/^(?:chatgpt|chat gpt|gpt)\s+(?:me|mein|par|pe)\s+(.+?)\s+(?:likho aur bhejo|likh ke bhejo|likho aur send karo|bhejo|send karo)$/);
    if (match) { question = match[1]; explicit = true; }
  }
  if (!question && lastTarget && /chatgpt/i.test(lastTarget.title || '')) {
    match = t.match(/^(?:pucho|poocho)\s+(.+)$/);
    if (match) question = match[1];
  }
  if (!question) return null;
  question = question.trim().replace(/^se\s+/i, '');
  if (question.length < 2 || question.toLowerCase() === 'se') return null;

  const steps = [
    { action: 'ensure_chatgpt', params: {} },
    { action: 'click_at', params: { x: 0.5, y: 0.9 }, delay: 800 },
    { action: 'type_text', params: { text: question }, delay: 500 },
    { action: 'press_keys', params: { combo: 'enter' }, delay: 300 },
    { action: 'screenshot', params: { quality: 60, maxW: 1600 }, delay: 15000 },
  ];

  return { steps, target: { kind: 'site', key: 'chatgpt', title: 'ChatGPT' }, askGpt: true };
}

const CHAT_APPS = {
  whatsapp: { key: 'whatsapp', label: 'WhatsApp' },
  wa: { key: 'whatsapp', label: 'WhatsApp' },
  telegram: { key: 'telegram', label: 'Telegram' },
  tg: { key: 'telegram', label: 'Telegram' },
  instagram: { key: 'instagram', label: 'Instagram' },
  insta: { key: 'instagram', label: 'Instagram' },
  ig: { key: 'instagram', label: 'Instagram' },
};

function parseMessagingCommand(raw) {
  const text = String(raw || '').trim();
  let match = text.match(/^(whatsapp|wa|telegram|tg|instagram|insta|ig)\s+(?:pe|par|me)\s+(.+?)\s+ko\s+(.+?)\s+(?:bhejo|bhej do|send karo|send kar do|message karo|msg karo)$/i);
  let appToken;
  let contact;
  let message;
  if (match) {
    appToken = match[1].toLowerCase();
    contact = match[2].trim();
    message = match[3].trim();
  } else {
    match = text.match(/^(?:message|msg)\s+(.+?)\s+(?:on|ko|to)\s+(whatsapp|wa|telegram|tg|instagram|insta|ig)\s*:?\s*(.+)$/i);
    if (!match) return null;
    contact = match[1].trim();
    appToken = match[2].toLowerCase();
    message = match[3].trim();
  }
  const app = CHAT_APPS[appToken];
  if (!app || !contact || !message) return null;
  return { app: app.key, label: app.label, contact, text: message };
}

function parseChatRead(raw) {
  const text = String(raw || '').trim();
  let match = text.match(/^(whatsapp|wa|telegram|tg|instagram|insta|ig)\s+(?:pe|par|me\s+)?(?:messages?|chats?|unread)?\s*(?:check karo|check kro|dekh lo|batao|dikhao|read karo)$/i);
  if (!match) match = text.match(/^(?:check|dekh)\s+(whatsapp|telegram|instagram)$/i);
  if (!match) return null;
  const app = CHAT_APPS[match[1].toLowerCase()];
  return app ? { app: app.key, label: app.label } : null;
}

function parseQuickCommand(raw, apps) {
  const appNames = Object.keys(apps || {});
  const knownApp = (name) => appNames.includes(name);
  const t = normalize(raw);

  if (/^(screenshot|ss|screen ?shot|screen)( (bhej|lo|le|dikhao|send))?$/.test(t)) return { action: 'screenshot', params: { quality: 60, maxW: 1600 } };
  if (/^(status|system status|battery|ram|cpu)( (batao|dikhao|kya hai|check karo))?$/.test(t)) return { action: 'get_status', params: {} };
  if (/^(windows|open windows|window list)$/.test(t)) return { action: 'list_windows', params: {} };
  if (/^(lock|laptop lock|lock karo|lock kar do)$/.test(t)) return { action: 'lock_pc', params: {}, confirm: true };

  const volSet = t.match(/^(?:volume|vol|awaaz)\s*(?:ko\s*)?(\d{1,3})\s*(?:%|percent|karo|kar do|set|set karo)?$/);
  if (volSet) return { action: 'set_volume', params: { level: Number(volSet[1]) } };
  if (/^(?:volume|vol|awaaz)\s*(?:up|badhao|bada|increase|tez)/.test(t)) return { action: 'change_volume', params: { direction: 'up', steps: 5 } };
  if (/^(?:volume|vol|awaaz)\s*(?:down|kam|ghatao|decrease|dheemi)/.test(t)) return { action: 'change_volume', params: { direction: 'down', steps: 5 } };
  if (/^(?:mute|silent|awaaz band|sound off)( karo| kar do)?$/.test(t)) return { action: 'mute', params: { on: true } };
  if (/^(?:unmute|sound on|awaaz chalu)( karo| kar do)?$/.test(t)) return { action: 'mute', params: { on: false } };
  if (/^(?:play|pause|playpause|resume)(?:\s+(?:tho|to|please|ab|jaldi))?(?:\s+karo|\s+kar do)?$/.test(t)) return { action: 'media_control', params: { action: 'playpause' } };
  if (/^(?:next|agla|agla gaana|next song)( karo)?$/.test(t)) return { action: 'media_control', params: { action: 'next' } };
  if (/^(?:prev|previous|pichla|pichla gaana)( karo)?$/.test(t)) return { action: 'media_control', params: { action: 'prev' } };

  const sd = t.match(/^(?:shutdown|shut down|laptop band|band kar do laptop)(?: karo| kar do)?\s*(\d{1,3})?$/);
  if (sd) return { action: 'shutdown_pc', params: { seconds: Number(sd[1] || 60) }, confirm: true };
  const rs = t.match(/^(?:restart|reboot)(?: karo| kar do)?\s*(\d{1,3})?$/);
  if (rs) return { action: 'restart_pc', params: { seconds: Number(rs[1] || 60) }, confirm: true };
  if (/^(?:cancel shutdown|cancel restart)$/.test(t)) return { action: 'cancel_shutdown', params: {} };

  const settingPages = [
    { keys: /\b(?:wifi|wi-fi|wi fi|internet)\b/, uri: 'ms-settings:network-wifi', title: 'Wi-Fi Settings' },
    { keys: /\bbluetooth\b/, uri: 'ms-settings:bluetooth', title: 'Bluetooth Settings' },
    { keys: /\bdisplay\b/, uri: 'ms-settings:display', title: 'Display Settings' },
    { keys: /\b(?:sound|audio)\b/, uri: 'ms-settings:sound', title: 'Sound Settings' },
    { keys: /\b(?:update|updates)\b/, uri: 'ms-settings:windowsupdate', title: 'Windows Update' },
    { keys: /\bbattery\b/, uri: 'ms-settings:batterysaver', title: 'Battery Settings' },
    { keys: /\bsystem\b/, uri: 'ms-settings:about', title: 'System Settings' },
  ];
  if (/\b(?:kholo|open|karo|kar do|settings?|setting|jao|chalao)\b/.test(t)) {
    for (const page of settingPages) {
      if (page.keys.test(t)) return { action: 'open_path', params: { target: page.uri }, title: page.title };
    }
  }

  const openMatch = t.match(/^(?:open|kholo|khol do|chalu karo|start|launch)\s+([a-z0-9 ._-]+)$/)
    || t.match(/^([a-z0-9 ._-]+)\s+(?:kholo|khol do|open karo|open|chalu karo|launch karo)$/);
  if (openMatch) {
    const name = openMatch[1].trim();
    if (knownApp(name)) return { action: 'open_app', params: { name }, title: (apps[name] && apps[name].title) || name };
    if (SITES[name]) return { action: 'open_path', params: { target: SITES[name].url }, site: name, title: SITES[name].title };
  }

  const closeMatch = t.match(/^(?:close|band karo|band kar do|kill)\s+([a-z0-9 ._-]+)$/)
    || t.match(/^([a-z0-9 ._-]+)\s+(?:band karo|band kar do|close karo|close)$/);
  if (closeMatch) {
    const name = closeMatch[1].trim();
    if (knownApp(name)) return { action: 'close_app', params: { name }, confirm: true };
  }

  const typePrefix = t.match(/^(?:type|likho|type karo)\s+(.+)$/);
  if (typePrefix) return { action: 'type_text', params: { text: typePrefix[1].trim() } };
  const typeSuffix = t.match(/^(.+?)\s+(?:likho|type karo|type)$/);
  if (typeSuffix) return { action: 'type_text', params: { text: typeSuffix[1].trim() } };

  return null;
}

const SONG_FILLERS = /\b(song|songs|gaana|gana|gane|tho|to|please|plz|ab|jaldi|karo|kar do|do|any|koi|bhi|mera|meri|first|pehla|pehli|new|latest)\b/g;

function buildSongPlay(text) {
  const t = normalize(text);
  if (/^(?:tum\s+)?(?:chalao|chalo|bajao)(?:\s+karo|\s+kar do)?$/.test(t)) {
    return { steps: [], target: null, message: 'Kaunsa song? Jaise: "play kesariya".' };
  }
  let match = t.match(/^(?:youtube\s+(?:pe|par|on|me)\s+)?(?:play|bajao|chalao|chala do)\s+(.+)$/);
  if (!match) {
    match = t.match(/^(?:youtube\s+(?:pe|par|on|me)\s+)?(.+?)\s+(?:play|bajao|chalao)(?:\s+karo|\s+kar do)?$/);
  }
  if (!match) return null;
  const song = match[1].replace(SONG_FILLERS, ' ').replace(/\s+/g, ' ').trim();
  if (/^(?:pause|resume|stop|next|prev)$/i.test(song)) return null;
  if (song.length < 2) {
    if (/\b(song|gaana|gana|youtube|yt)\b/.test(t)) {
      return { steps: [], target: null, message: 'Kaunsa song chalaun? Jaise: "play kesariya".' };
    }
    return null;
  }
  return {
    song,
    youtubePlay: true,
    target: { kind: 'site', key: 'youtube', title: 'YouTube' },
    steps: [
      { action: 'play_youtube', params: { query: song } },
      { action: 'screenshot', params: { quality: 60, maxW: 1600 }, delay: 10000 },
    ],
  };
}

function buildQuickSteps(raw, apps, lastTarget) {
  const text = String(raw || '').trim();
  if (!text) return null;
  const askGpt = buildAskChatGpt(text, lastTarget);
  if (askGpt) return askGpt;

  const norm = normalize(text);
  if (/^(?:bhejo|bhej do|send|send karo|send kar do)$/.test(norm)
    && lastTarget && /chatgpt/i.test(lastTarget.title || '')) {
    return {
      steps: [
        { action: 'focus_tab', params: { title: 'ChatGPT' }, optional: true },
        { action: 'press_keys', params: { combo: 'enter' }, delay: 300 },
        { action: 'screenshot', params: { quality: 60, maxW: 1600 }, delay: 12000 },
      ],
      target: lastTarget,
      chatGptWait: true,
    };
  }
  if ((/^(?:clear|saaf)(?: karo| kar do)?$/.test(norm) || /^chatgpt (?:ka )?(?:text |input )?clear(?: karo| kar do)?$/.test(norm))
    && lastTarget && /chatgpt/i.test(lastTarget.title || '')) {
    return {
      steps: [
        { action: 'focus_tab', params: { title: 'ChatGPT' }, optional: true },
        { action: 'click_at', params: { x: 0.5, y: 0.9 }, delay: 300 },
        { action: 'press_keys', params: { combo: 'ctrl+a' }, delay: 150 },
        { action: 'press_keys', params: { combo: 'delete' }, delay: 150 },
      ],
      target: lastTarget,
      message: '🧹 ChatGPT ka input clear kar diya.',
    };
  }
  if (/^(?:close|band)(?: karo| kar do)?$/.test(norm)
    || /^(?:isko|ise)\s+band\s+karo$/.test(norm)
    || /(?:jo abhi\s+)?(?:open|khola|kholi)[^\n]*\b(?:band|close)\b/.test(norm)) {
    if (!lastTarget) return { steps: [], target: null, message: 'Abhi kuch open nahi hai. App ka naam bolo, jaise "chrome band karo".' };
    if (lastTarget.kind === 'app') {
      return { steps: [{ action: 'close_app', params: { name: lastTarget.key }, confirm: true }], target: lastTarget };
    }
    return {
      steps: [
        { action: 'focus_tab', params: { title: lastTarget.title }, optional: true },
        { action: 'press_keys', params: { combo: 'ctrl+w' } },
      ],
      target: lastTarget,
      message: '🗂️ Sirf tab close kar diya (browser open hai).',
    };
  }
  if (/^(?:ye |current |browser )?tab (?:close|band) (?:karo|kar do|kro)$/.test(norm)) {
    const steps = [];
    if (lastTarget && lastTarget.title) steps.push({ action: 'focus_tab', params: { title: lastTarget.title }, optional: true });
    steps.push({ action: 'press_keys', params: { combo: 'ctrl+w' } });
    return { steps, target: lastTarget, message: '🗂️ Tab close kar diya.' };
  }
  if (/^(?:kholo|kajolo|kholu|open|khol do|open karo|khol)$/.test(norm)) {
    return { steps: [], target: lastTarget, message: 'Kaunsa app ya website kholu? Jaise: chrome, youtube, chatgpt, settings.' };
  }
  if (/^(?:jo abhi (?:open|khola|kholi)(?: kiya)?|kya open kiya)$/.test(norm)) {
    return { steps: [], target: lastTarget, message: lastTarget ? `Abhi maine ${lastTarget.title} khola tha.` : 'Abhi kuch nahi khola.' };
  }

  const segments = text.split(/\s+(?:aur|and|then)\s+/i).map((part) => part.trim()).filter(Boolean);
  const steps = [];
  let target = lastTarget || null;
  let message = null;
  let youtubePlay = false;
  let song = null;

  for (const segment of segments) {
    const usme = normalize(segment).match(/^(?:usme|us mein|usmein)\s+(.+)$/i);
    if (usme) {
      if (!target) return null;
      const inner = usme[1].replace(/\s+(?:likho|type karo|type)$/i, '').trim();
      if (!inner) return null;
      const prevAction = steps.length ? steps[steps.length - 1].action : null;
      steps.push({ action: 'focus_window', params: { title: target.title }, optional: true });
      steps.push({ action: 'type_text', params: { text: inner }, delay: prevAction === 'open_app' || prevAction === 'open_path' ? 3800 : 400 });
      continue;
    }

    const songPlay = buildSongPlay(segment);
    if (songPlay) {
      if (songPlay.target) target = songPlay.target;
      if (songPlay.message) message = songPlay.message;
      if (songPlay.youtubePlay) youtubePlay = true;
      if (songPlay.song) song = songPlay.song;
      steps.push(...songPlay.steps);
      continue;
    }

    const parsed = parseQuickCommand(segment, apps);
    if (!parsed) return null;

    if (parsed.action === 'open_app') {
      target = { kind: 'app', key: parsed.params.name, title: parsed.title || parsed.params.name };
    } else if (parsed.action === 'open_path' && parsed.site) {
      target = { kind: 'site', key: parsed.site, title: parsed.title };
    }

    if (parsed.action === 'type_text') {
      const prevAction = steps.length ? steps[steps.length - 1].action : null;
      if (target) steps.push({ action: 'focus_window', params: { title: target.title }, optional: true });
      parsed.delay = prevAction === 'open_app' || prevAction === 'open_path' ? 3800 : 400;
    }
    steps.push(parsed);
  }

  if (!steps.length && !message) return null;
  return { steps, target, message, youtubePlay, song };
}

module.exports = {
  parseQuickCommand,
  buildQuickSteps,
  buildAskChatGpt,
  buildSongPlay,
  parseMessagingCommand,
  parseChatRead,
  SITES,
  CHAT_APPS,
};
