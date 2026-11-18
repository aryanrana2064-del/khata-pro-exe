'use strict';

const khata = require('./khata');

const CONFIRM_NOTE = 'Requires the user to press a confirmation button in the app. Nothing was executed.';

function createAI({ geminiKey, deepseekKey, model, actions, log = () => {} }) {
  const systemPrompt = [
    'You are VX7 Remote, a private assistant controlling the owner\'s own Windows laptop.',
    'The owner speaks English, Hindi or Hinglish. Map requests to tools and call them.',
    'Available apps: chrome, edge, code, whatsapp, telegram, notepad, calculator, settings, explorer, taskmgr, paint, cmd.',
    'When the owner asks to open an app or website, you MUST call a tool (open_app or open_path) — never just answer with text.',
    'Never guess an app name. If the requested app is unclear or not in the list, ask the owner which app they mean.',
    'Use the Recent context (last opened app and recent actions) to resolve follow-ups like "close it" or "jo abhi open kiya".',
    'To play a song, call play_youtube with the song name. To close a browser tab use close_tab; close_app closes the entire app. Never claim you played a song, typed text, or closed an app unless a tool returned success for it.',
    'Common sites: chatgpt.com, youtube.com, google.com, gmail.com, github.com.',
    'KHATA (ledger): the shop ledger runs inside Khata Pro on this PC. Whenever the owner mentions a customer taking goods/money (udhaar) or paying money (jama), call khata_add_entry.',
    'If the customer says they took an item, type is UDHAAR (default). If they paid money, type is JAMA. Put the item name in item, quantity/rate if said, and the final rupee amount in amount.',
    'Entries go to the 24-hour Daily Entry queue by default; only pass mode=direct when the owner says "abhi", "seedha" or "turant".',
    'Before adding to an unknown customer, check khata_customers; if no match, ask the name — never invent a customer.',
    'Balances, pending rows and today summary come ONLY from khata tools — never make up numbers.',
    'Period questions ("kal kitna", "is hafte", "mahine ka total") use khata_report with from/to dates (YYYY-MM-DD). Overdue list → khata_overdue. Top debtors → khata_top.',
    'Due-date reminders: pass due_date (YYYY-MM-DD) on khata_add_entry when the owner sets a payment deadline.',
    'Keep final replies to 1-2 short lines, in the owner\'s language.',
    'Screenshots are delivered to the phone automatically; never describe base64 data.',
    'If a tool returns status PENDING_USER_CONFIRMATION, tell the owner to tap the confirm button.',
    'Never claim an action succeeded if a tool failed. Ask a short question if the request is ambiguous.',
  ].join(' ');

  const declarations = [
    tool('open_app', 'Open an application on the laptop.', { name: { type: 'STRING', description: 'App key or name, e.g. chrome, notepad, whatsapp' } }, ['name']),
    tool('close_app', 'Close an application (requires user confirmation).', { name: { type: 'STRING', description: 'App key or name' } }, ['name']),
    tool('close_tab', 'Close only the current browser tab (Ctrl+W). Use this for "tab close karo" instead of close_app.', {}, []),
    tool('set_volume', 'Set the master volume to an exact percentage.', { level: { type: 'NUMBER', description: '0-100' } }, ['level']),
    tool('change_volume', 'Increase or decrease the volume.', { direction: { type: 'STRING', description: 'up or down' }, steps: { type: 'NUMBER', description: 'How many steps, 1 step = 2 percent' } }, ['direction']),
    tool('mute', 'Mute or unmute the laptop.', { on: { type: 'BOOLEAN', description: 'true to mute, false to unmute' } }, []),
    tool('media_control', 'Control media playback.', { action: { type: 'STRING', description: 'playpause, next, prev or stop' } }, ['action']),
    tool('play_youtube', 'Play a specific song on YouTube: searches the name and opens the first video with autoplay.', { query: { type: 'STRING', description: 'Song or video name' } }, ['query']),
    tool('screenshot', 'Take a screenshot of the laptop screen and send it to the phone.', {}, []),
    tool('lock_pc', 'Lock the Windows session (requires confirmation).', {}, []),
    tool('shutdown_pc', 'Shut down the laptop after a delay (requires confirmation).', { seconds: { type: 'NUMBER', description: 'Delay in seconds, default 60' } }, []),
    tool('restart_pc', 'Restart the laptop after a delay (requires confirmation).', { seconds: { type: 'NUMBER', description: 'Delay in seconds, default 60' } }, []),
    tool('cancel_shutdown', 'Cancel a pending shutdown or restart.', {}, []),
    tool('list_windows', 'List open windows on the laptop.', {}, []),
    tool('focus_window', 'Bring a window to the front by title.', { title: { type: 'STRING', description: 'Part of the window title' } }, ['title']),
    tool('type_text', 'Type text into the focused window on the laptop.', { text: { type: 'STRING' } }, ['text']),
    tool('press_keys', 'Press a key combination, e.g. ctrl+z, alt+tab, win+d, enter.', { combo: { type: 'STRING' } }, ['combo']),
    tool('scroll', 'Scroll the mouse wheel. Positive is up, negative is down.', { amount: { type: 'NUMBER' } }, ['amount']),
    tool('copy_to_clipboard', 'Copy text to the laptop clipboard.', { text: { type: 'STRING' } }, ['text']),
    tool('get_clipboard', 'Read the laptop clipboard.', {}, []),
    tool('search_files', 'Search files by name in Desktop, Documents and Downloads.', { query: { type: 'STRING' } }, ['query']),
    tool('open_path', 'Open a URL, file or folder on the laptop.', { target: { type: 'STRING' } }, ['target']),
    tool('get_status', 'Get battery, CPU, RAM, volume and screen status of the laptop.', {}, []),
  ];

  if (khata.available()) {
    declarations.push(
      tool('khata_add_entry', 'Add a ledger entry in Khata Pro. UDHAAR when the customer took goods/cash, JAMA when they paid. Goes to the 24h Daily Entry queue unless mode is "direct".', {
        customer: { type: 'STRING', description: 'Customer name as spoken' },
        type: { type: 'STRING', description: 'UDHAAR (took) or JAMA (paid)' },
        item: { type: 'STRING', description: 'Item / description, e.g. cheeni 2kg' },
        amount: { type: 'NUMBER', description: 'Final amount in rupees' },
        quantity: { type: 'STRING', description: 'Optional quantity, e.g. 2 kg' },
        rate: { type: 'NUMBER', description: 'Optional rate in rupees' },
        date: { type: 'STRING', description: 'Optional date YYYY-MM-DD, default today' },
        mode: { type: 'STRING', description: 'pending (default) or direct' },
        notes: { type: 'STRING', description: 'Optional note' },
        due_date: { type: 'STRING', description: 'Optional payment deadline YYYY-MM-DD (UDHAAR only)' },
      }, ['customer', 'type', 'item', 'amount']),
      tool('khata_customers', 'List customers with balances, optionally filtered by search text.', { search: { type: 'STRING' } }, []),
      tool('khata_summary', 'Today\'s khata summary: total jama, total udhaar, net and the transaction rows.', {}, []),
      tool('khata_pending', 'List the entries waiting in the 24-hour Daily Entry queue.', {}, []),
      tool('khata_post_pending', 'Save one pending entry into the ledger now.', { id: { type: 'NUMBER' } }, ['id']),
      tool('khata_delete_pending', 'Delete one pending entry.', { id: { type: 'NUMBER' } }, ['id']),
      tool('khata_add_customer', 'Create a new customer in Khata Pro.', {
        name: { type: 'STRING' },
        mobile: { type: 'STRING' },
        address: { type: 'STRING' },
        credit_limit: { type: 'NUMBER', description: 'Optional credit limit in rupees' },
      }, ['name']),
      tool('khata_report', 'Totals for a date range from the ledger register (udhaar, jama, net, rows).', {
        from: { type: 'STRING', description: 'Start date YYYY-MM-DD' },
        to: { type: 'STRING', description: 'End date YYYY-MM-DD' },
      }, []),
      tool('khata_overdue', 'Customers whose udhaar due-date has passed, with overdue amounts.', {}, []),
      tool('khata_top', 'Customers with the highest outstanding balance (top debtors).', {
        limit: { type: 'NUMBER', description: 'How many, default 8' },
      }, []),
      tool('khata_aging', 'Ageing of outstanding udhaar: how much is 0-30, 31-60 and 60+ days old.', {}, []),
    );
  }

  let deepseekBlocked = false;

  async function ask({ text, audio, channel = 'app', history = [], context = '' }) {
    let geminiError = null;
    if (geminiKey) {
      try {
        return await askGemini({ text, audio, channel, history, context });
      } catch (error) {
        geminiError = error.message;
        log('gemini failed: ' + error.message);
      }
    }

    if (deepseekKey && text && !deepseekBlocked) {
      try {
        return await askDeepSeek({ text, channel, history, context });
      } catch (error) {
        if (/insufficient balance|payment required|402/i.test(error.message)) {
          deepseekBlocked = true;
          log('deepseek disabled: no balance');
        } else {
          log('deepseek failed: ' + error.message);
        }
      }
    }

    if (!geminiKey && !deepseekKey) {
      return { reply: 'AI key set nahi hai. .env me GEMINI_API_KEY ya DEEPSEEK_API_KEY daalo. Buttons aur /help commands abhi bhi kaam karte hain.', screenshots: [], pending: null };
    }
    const quota = geminiError && /quota|429|rate limit|exceeded/i.test(geminiError);
    return {
      reply: quota
        ? '⚠️ Gemini ka free quota abhi khatam hai. Fast commands turant chalte hain:\n• chrome kholo\n• chatgpt kholo\n• screenshot bhej\n• volume 40 karo'
        : '⚠️ AI abhi available nahi. Fast commands try karo: "chrome kholo", "screenshot bhej", "volume 40".',
      screenshots: [],
      pending: null,
    };
  }

  async function askGemini({ text, audio, channel, history = [], context = '' }) {
    const userParts = [];
    if (text) userParts.push({ text: String(text).slice(0, 4000) });
    if (audio) {
      userParts.push({ inlineData: { mimeType: audio.mimeType || 'audio/ogg', data: audio.buffer.toString('base64') } });
      if (!text) userParts.push({ text: 'The owner sent a voice note. Understand it and perform the requested action.' });
    }
    if (userParts.length === 0) return { reply: 'Kuch bolo ya likho.', screenshots: [], pending: null, actions: [] };

    const contents = [];
    for (const turn of history.slice(-8)) {
      if (!turn.text) continue;
      contents.push({ role: turn.role === 'user' ? 'user' : 'model', parts: [{ text: String(turn.text).slice(0, 1000) }] });
    }
    contents.push({ role: 'user', parts: userParts });
    const screenshots = [];
    const executed = [];
    let pending = null;
    let replyText = '';
    let workingModel = null;
    let firstResponse = null;
    let lastError = 'Gemini unavailable';

    for (const candidate of [...new Set([model, 'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite', 'gemini-flash-lite-latest'])]) {
      const body = geminiBody(contents, context);
      let result = await geminiCall(body, candidate);
      if (!result.response.ok && result.response.status === 400 && JSON.stringify(result.data).includes('thinking')) {
        delete body.generationConfig.thinkingConfig;
        result = await geminiCall(body, candidate);
      }
      if (result.response.ok) {
        workingModel = candidate;
        firstResponse = result;
        break;
      }
      lastError = (result.data.error && result.data.error.message) || `HTTP ${result.response.status}`;
    }

    if (!workingModel) throw new Error(lastError);

    let nextResponse = firstResponse;
    for (let step = 0; step < 4; step += 1) {
      const { response, data } = nextResponse;
      if (!response.ok) throw new Error((data.error && data.error.message) || `HTTP ${response.status}`);
      const candidate = (data.candidates || [])[0];
      const parts = (candidate && candidate.content && candidate.content.parts) || [];
      const calls = parts.filter((part) => part.functionCall);

      if (calls.length === 0) {
        replyText = parts.filter((part) => part.text && !part.thought).map((part) => part.text).join('\n').trim();
        break;
      }

      contents.push({ role: 'model', parts });
      const functionResponses = [];
      for (const call of calls) {
        const handled = await runTool(call.functionCall.name, call.functionCall.args || {}, channel, screenshots, executed);
        if (handled.pending) pending = handled.pending;
        functionResponses.push({
          functionResponse: { name: call.functionCall.name, response: handled.response },
        });
      }
      contents.push({ role: 'user', parts: functionResponses });

      if (step === 3) break;
      nextResponse = await geminiCall(geminiBody(contents, context), workingModel);
    }

    if (!replyText) {
      replyText = screenshots.length ? 'Screenshot bhej diya.' : pending ? 'Confirm button dabao.' : 'Ho gaya.';
    }
    return { reply: replyText, screenshots, pending, actions: executed };
  }

  async function askVision({ buffer, mimeType = 'image/jpeg', prompt }) {
    if (!geminiKey || !buffer) return '';
    const body = {
      contents: [{
        role: 'user',
        parts: [
          { text: prompt || 'Extract the visible text from this screenshot.' },
          { inlineData: { mimeType, data: buffer.toString('base64') } },
        ],
      }],
      generationConfig: { temperature: 0.1, thinkingConfig: { thinkingBudget: 0 } },
    };
    let lastError = 'vision unavailable';
    for (const candidate of [...new Set([model, 'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite', 'gemini-flash-lite-latest'])]) {
      let result = await geminiCall(body, candidate);
      if (!result.response.ok && result.response.status === 400 && JSON.stringify(result.data).includes('thinking')) {
        delete body.generationConfig.thinkingConfig;
        result = await geminiCall(body, candidate);
      }
      if (result.response.ok) {
        const parts = ((result.data.candidates || [])[0]?.content?.parts) || [];
        return parts.filter((part) => part.text && !part.thought).map((part) => part.text).join('\n').trim();
      }
      lastError = (result.data.error && result.data.error.message) || `HTTP ${result.response.status}`;
    }
    throw new Error(lastError);
  }

  function geminiBody(contents, context = '') {
    return {
      systemInstruction: { parts: [{ text: systemPrompt + (context ? ' Recent context: ' + context : '') }] },
      contents,
      tools: [{ functionDeclarations: declarations }],
      generationConfig: { temperature: 0.2, thinkingConfig: { thinkingBudget: 0 } },
    };
  }

  async function geminiCall(body, requestModel) {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${requestModel}:generateContent`;
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': geminiKey },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    return { response, data };
  }

  async function askDeepSeek({ text, channel, history = [], context = '' }) {
    const messages = [
      { role: 'system', content: systemPrompt + (context ? ' Recent context: ' + context : '') },
    ];
    for (const turn of history.slice(-8)) {
      if (!turn.text) continue;
      messages.push({ role: turn.role === 'user' ? 'user' : 'assistant', content: String(turn.text).slice(0, 1000) });
    }
    messages.push({ role: 'user', content: String(text).slice(0, 4000) });
    const toolList = declarations.map((d) => ({
      type: 'function',
      function: { name: d.name, description: d.description, parameters: lowerTypes(d.parameters) },
    }));

    const screenshots = [];
    const executed = [];
    let pending = null;
    let replyText = '';

    for (let step = 0; step < 4; step += 1) {
      const response = await fetch('https://api.deepseek.com/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${deepseekKey}` },
        body: JSON.stringify({
          model: 'deepseek-chat',
          messages,
          tools: toolList,
          tool_choice: 'auto',
          temperature: 0.2,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = (data.error && data.error.message) || `HTTP ${response.status}`;
        throw new Error(message);
      }
      const choice = (data.choices || [])[0] || {};
      const message = choice.message || {};

      if (message.tool_calls && message.tool_calls.length) {
        messages.push({ role: 'assistant', content: message.content || null, tool_calls: message.tool_calls });
        for (const call of message.tool_calls) {
          let args = {};
          try { args = JSON.parse(call.function.arguments || '{}'); } catch { args = {}; }
          const handled = await runTool(call.function.name, args, channel, screenshots, executed);
          if (handled.pending) pending = handled.pending;
          messages.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify(handled.response),
          });
        }
      } else {
        replyText = (message.content || '').trim();
        break;
      }
    }

    if (!replyText) {
      replyText = screenshots.length ? 'Screenshot bhej diya.' : pending ? 'Confirm button dabao.' : 'Ho gaya.';
    }
    return { reply: replyText, screenshots, pending, actions: executed };
  }

  async function runTool(name, args, channel, screenshots, executed) {
    if (name.startsWith('khata_')) {
      const outcome = await khata.callTool(name, args || {});
      if (outcome.ok) executed.push({ name, args: args || {} });
      return {
        pending: null,
        response: outcome.ok ? { result: outcome.data } : { result: { ok: false, error: outcome.error } },
      };
    }
    const outcome = await actions.execute(name, args || {}, { channel, confirmed: false });
    if (outcome.needsConfirm) {
      return {
        pending: { action: name, params: args || {} },
        response: { status: 'PENDING_USER_CONFIRMATION', note: CONFIRM_NOTE },
      };
    }
    if (outcome.ok) executed.push({ name, args: args || {} });
    if (outcome.ok && name === 'screenshot' && outcome.buffer) {
      screenshots.push({ buffer: outcome.buffer, width: outcome.width, height: outcome.height });
    }
    return {
      pending: null,
      response: outcome.ok ? { result: outcome.summary || { ok: true } } : { result: { ok: false, error: outcome.error } },
    };
  }

  return { ask, askVision };
}

function lowerTypes(schema) {
  if (Array.isArray(schema)) return schema.map(lowerTypes);
  if (schema && typeof schema === 'object') {
    const out = {};
    for (const [key, value] of Object.entries(schema)) {
      if (key === 'type' && typeof value === 'string') out[key] = value.toLowerCase();
      else out[key] = lowerTypes(value);
    }
    return out;
  }
  return schema;
}

function tool(name, description, props, required) {
  return {
    name,
    description,
    parameters: { type: 'OBJECT', properties: props, required },
  };
}

module.exports = { createAI };
