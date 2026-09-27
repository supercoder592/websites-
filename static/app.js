import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { NeuralCore } from './sphere.js';

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const now = () => new Date().toLocaleTimeString('zh-TW', { hour12: false });

// AI 核心的位址：由本機伺服器開的頁面就是同一個網址；放在 GitHub Pages 上時，連回這台電腦的本機核心。
// 也可以用 ?api=http://網址:7860 指定（會記住）。
const REMOTE_UI = location.protocol === 'file:' || /\.github\.io$/i.test(location.hostname);
const API = (() => {
  const q = new URLSearchParams(location.search).get('api');
  if (q) localStorage.setItem('nova-api', q.replace(/\/+$/, ''));
  return REMOTE_UI ? (localStorage.getItem('nova-api') || 'http://127.0.0.1:7860') : '';
})();
const u = p => (typeof p === 'string' && p.startsWith('/') && !p.startsWith('//') ? API + p : p);
const post = (url, body, opts = {}) => fetch(u(url), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), ...opts });

const core = new NeuralCore($('#core'));

// ------------------------------------------------------------------ intents（全部在同一個聊天室）
const ICON = {
  auto: '<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4M5 5l2.8 2.8M16.2 16.2L19 19M5 19l2.8-2.8M16.2 7.8L19 5"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8M8 13h5"/>',
  code: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
  image: '<rect x="3" y="4" width="18" height="16"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  video: '<rect x="3" y="6" width="13" height="12"/><path d="M16 10l5-3v10l-5-3z"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  model3d: '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 22V12M21 7l-9 5-9-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5M8 11h6M11 8v6"/>',
  archive: '<rect x="3" y="4" width="18" height="5"/><path d="M5 9v11h14V9M10 13h4"/>',
};
// zh：句子裡用的名詞（「圖像已完成」）；tag：晶片、回覆標籤、設定標題共用的名稱；en：HUD 英文代號
const INTENT = {
  chat:    { zh: '對話', tag: '對話', en: 'DIALOGUE' },
  code:    { zh: '程式', tag: '程式', en: 'CODE FORGE' },
  image:   { zh: '圖像', tag: '畫圖', en: 'IMAGE SYNTH' },
  video:   { zh: '影片', tag: '影片', en: 'MOTION' },
  music:   { zh: '音樂', tag: '音樂', en: 'SONIC' },
  model3d: { zh: '3D', tag: '3D', en: 'MATTER' },
  search:  { zh: '素材', tag: '找素材', en: 'RECON' },
};
// [意圖, 輸入框提示, 窄螢幕用的短提示（避免提示文字換行被切掉）]
const CHIPS = [
  ['auto', '對 N.O.V.A. 說任何事：聊天、寫程式、畫圖、做影片、作曲、3D 建模、上網找素材…', '對 N.O.V.A. 說任何事…'],
  ['chat', '隨便聊、問問題、翻譯、寫文章…（不會觸發任何生成）', '隨便聊、問問題…'],
  ['image', '描述畫面，例如：賽博龐克城市夜景，霓虹雨，電影感光影', '描述想要的畫面…'],
  ['video', '描述動態場景，例如：星雲在宇宙深處緩慢旋轉', '描述動態場景…'],
  ['music', '描述音樂，例如：史詩感電子配樂，強烈鼓點，120 BPM', '描述想要的音樂…'],
  ['model3d', '描述物件，例如：一把未來科幻手槍', '描述想要的物件…'],
  ['search', '要找什麼素材？例如：星空、城市夜景、海浪影片', '要找什麼素材？'],
  ['code', '描述你要的程式，例如：霓虹風格的貪食蛇網頁遊戲', '描述你要的程式…'],
];
const chipName = k => k === 'auto' ? 'AUTO' : INTENT[k].tag;
// 回覆標籤 / 設定標題：圖示 + 中文 + 英文代號（和晶片同名）
const tagHTML = (k, zh = INTENT[k].tag, en = INTENT[k].en) => `<svg viewBox="0 0 24 24">${ICON[k] || ICON.auto}</svg>${esc(zh)}<small>${esc(en)}</small>`;
const SUGGESTIONS = [
  '畫一張賽博龐克城市夜景，霓虹雨', '做一首 lofi 放鬆音樂', '做一個 3D 太空船模型',
  '寫一個霓虹風格的貪食蛇網頁遊戲', '今天有什麼科技新聞？', '找一些星空的圖片素材',
];

// 生成參數（存在 localStorage；標題沿用晶片名稱）
const SETTINGS = {
  image: [
    { key: 'size', label: '尺寸', value: '512x512', choices: [['512x512', '1:1 方形'], ['768x512', '3:2 橫幅'], ['512x768', '2:3 直幅'], ['640x640', '1:1 高解析']] },
    { key: 'steps', label: '步數', value: '1', choices: [['1', '1 · 極速'], ['2', '2 · 平衡'], ['4', '4 · 精緻']] },
    { key: 'strength', label: '參考圖改動幅度', value: '0.55', choices: [['0.35', '低'], ['0.55', '中'], ['0.75', '高'], ['0.9', '極高']] },
    { key: 'web_ref', label: '先上網找參考素材', value: false, toggle: true },
  ],
  // 真動態影片：auto 由後端依硬體決定（CPU：快速 / 2 秒；Mac、NVIDIA：高畫質 / 4 秒）
  video: [
    { key: 'engine', label: '引擎', value: 'auto', choices: [['auto', 'auto（LTX-Video）'], ['ltx', 'LTX-Video · 高品質'], ['animatediff', 'AnimateDiff · 較快']] },
    { key: 'quality', label: '畫質', value: 'auto', choices: [['auto', 'auto（依硬體自動）'], ['fast', '快速'], ['standard', '標準'], ['high', '高畫質']] },
    { key: 'length', label: '長度', value: 'auto', choices: [['auto', 'auto（依硬體自動）'], ['2s', '約 2 秒'], ['4s', '約 4 秒']] },
    { key: 'web_ref', label: '先上網找參考素材', value: false, toggle: true },
  ],
  music: [
    { key: 'duration', label: '長度', value: '8', choices: [['5', '5 秒'], ['8', '8 秒'], ['12', '12 秒'], ['20', '20 秒'], ['30', '30 秒']] },
  ],
  model3d: [
    { key: 'steps', label: '品質', value: '32', choices: [['16', '快速'], ['32', '標準'], ['64', '精細']] },
  ],
};

const state = {
  history: [],
  attachments: [],
  web: 'auto',
  voice: localStorage.getItem('nova-voice') === '1',
  force: null,
  reference: null,
  lastImage: null,
  streaming: false,
  gotToken: false,
  abort: null,
  jobs: new Map(),
  cfg: {},
};
// 讀回設定：只接受目前還存在的選項（舊版的 seconds / keyframes 等過期值直接丟掉），再存回乾淨的版本
let savedCfg = {};
try { savedCfg = JSON.parse(localStorage.getItem('nova-cfg') || '{}') || {}; } catch { /* 壞掉的設定就用預設 */ }
for (const [k, items] of Object.entries(SETTINGS)) {
  state.cfg[k] = Object.fromEntries(items.map(o => {
    const v = savedCfg[k]?.[o.key];
    const ok = o.toggle ? typeof v === 'boolean' : o.choices.some(([c]) => c === String(v));
    return [o.key, ok ? (o.toggle ? v : String(v)) : o.value];
  }));
}
localStorage.setItem('nova-cfg', JSON.stringify(state.cfg));

function genOpts() {
  const c = state.cfg;
  const [w, h] = c.image.size.split('x').map(Number);
  return {
    image: { width: w, height: h, steps: +c.image.steps, strength: +c.image.strength, web_ref: !!c.image.web_ref },
    video: { engine: c.video.engine, quality: c.video.quality, length: c.video.length, web_ref: !!c.video.web_ref },
    music: { duration: +c.music.duration },
    model3d: { steps: +c.model3d.steps },
  };
}

// ------------------------------------------------------------------ chips / suggestions / settings
function renderChips() {
  const box = $('#opts');
  box.innerHTML = '';
  const active = state.force || 'auto';
  CHIPS.forEach(([key], i) => {
    const b = document.createElement('button');
    b.className = 'opt toggle intent' + (active === key ? ' on' : '');
    b.dataset.intent = key;
    b.setAttribute('aria-pressed', String(active === key));
    b.innerHTML = `<svg viewBox="0 0 24 24">${ICON[key]}</svg>${chipName(key)}`;
    b.title = (key === 'auto' ? '自動判斷你要做什麼' : `下一則訊息強制使用「${chipName(key)} ${INTENT[key].en}」`) + `（Alt+${i}）`;
    b.onclick = () => setForce(key === 'auto' || state.force === key ? null : key);
    box.appendChild(b);
  });
  if (state.reference) {
    const el = document.createElement('div');
    el.className = 'opt ref';
    el.innerHTML = `<img src="${esc(u(state.reference.thumb || state.reference.url))}" referrerpolicy="no-referrer">參考素材<button title="移除">✕</button>`;
    el.querySelector('button').onclick = () => { state.reference = null; renderChips(); };
    box.appendChild(el);
  }
  updatePlaceholder();
}
function updatePlaceholder() {
  const c = CHIPS.find(c => c[0] === (state.force || 'auto'));
  $('#prompt').placeholder = innerWidth <= 640 ? c[2] : c[1];
}
addEventListener('resize', updatePlaceholder);
function setForce(key) {
  state.force = key;
  renderChips();
  $('#prompt').focus();
}

function renderSuggestions() {
  const box = $('#suggest');
  if ($('#feed').children.length) { box.innerHTML = ''; return; }
  box.innerHTML = SUGGESTIONS.map(s => `<button>${esc(s)}</button>`).join('');
  box.querySelectorAll('button').forEach(b => b.onclick = () => { $('#prompt').value = b.textContent; submit(); });
}

function renderSettings() {
  const body = $('#settings-body');
  body.innerHTML = Object.entries(SETTINGS).map(([k, items]) => `<div class="cfg-group"><h4>${tagHTML(k)}</h4>${items.map(o => o.toggle
    ? `<label class="cfg-row"><span>${o.label}</span><input type="checkbox" data-k="${k}" data-o="${o.key}" ${state.cfg[k][o.key] ? 'checked' : ''}></label>`
    : `<label class="cfg-row"><span>${o.label}</span><select data-k="${k}" data-o="${o.key}">${o.choices.map(([v, t]) => `<option value="${v}" ${String(state.cfg[k][o.key]) === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>`).join('')}</div>`).join('')
    + '<p class="cfg-note">這些參數會套用到聊天室裡所有的生成任務。CPU 上步數越高、解析度與幀數越高就越久；auto 會依你的硬體自動挑選。</p>';
  body.querySelectorAll('[data-k]').forEach(el => el.onchange = () => {
    state.cfg[el.dataset.k][el.dataset.o] = el.type === 'checkbox' ? el.checked : el.value;
    localStorage.setItem('nova-cfg', JSON.stringify(state.cfg));
  });
}
const settingsOpen = () => $('#settings').classList.contains('open');
function setSettingsOpen(open, focus = true) {
  if (open) renderSettings();
  $('#settings').classList.toggle('open', open);
  $('#settings-btn').setAttribute('aria-expanded', String(open));
  if (!focus) return;
  if (open) $('#settings-body select')?.focus(); else $('#settings-btn').focus();
}
$('#settings-btn').onclick = () => setSettingsOpen(!settingsOpen());
$('#settings-close').onclick = () => setSettingsOpen(false);
// 點面板以外的地方就關閉（不搶焦點，免得打斷輸入）
document.addEventListener('pointerdown', e => {
  if (settingsOpen() && !e.target.closest('#settings, #settings-btn')) setSettingsOpen(false, false);
});

// ------------------------------------------------------------------ panel / caption
function openPanel(open = true) {
  document.body.classList.toggle('panel-open', open);
  updateOffset();
}
function updateOffset() {
  const open = document.body.classList.contains('panel-open');
  if (!open || innerWidth <= 900) return core.setOffsetPx(0, 1);
  const panelLeft = innerWidth - 18 - Math.min(innerWidth * 0.5, 780);
  // 依剩餘空間縮小，讓外環不壓到面板
  core.setOffsetPx(panelLeft / 2 - innerWidth / 2, Math.min(1, Math.max(0.5, panelLeft / innerHeight * 0.95)));
}
addEventListener('resize', updateOffset);
$('#collapse-btn').onclick = () => openPanel(false);
$('#clear-btn').onclick = () => {
  state.abort?.abort();
  speechSynthesis.cancel();
  // 舊對話裡還在排隊 / 生成中的任務一併取消，別讓它們繼續佔用唯一的工作佇列
  for (const id of state.jobs.keys()) cancelJob(id);
  state.jobs.clear();
  $('#feed').innerHTML = '';
  state.history = [];
  state.lastImage = null;
  state.reference = null;
  state.force = null;
  clearAttachments();
  renderChips();
  renderSuggestions();
  openPanel(false);
  refreshCore();
  caption('新對話已就緒，今天想創造什麼？', 'ALL SYSTEMS NOMINAL');
  toast('已開啟新對話');
};

let captionTimer;
function caption(text, sub) {
  clearInterval(captionTimer);
  const el = $('#caption-text');
  let i = 0;
  captionTimer = setInterval(() => {
    i++;
    el.innerHTML = esc(text.slice(0, i)) + '<span class="cursor"></span>';
    if (i >= text.length) clearInterval(captionTimer);
  }, 28);
  if (sub) $('#caption-sub').textContent = sub;
}

function toast(text) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = text;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2200);
}

const ENGINE_NAMES = { sd: 'SD-TURBO', music: 'MUSICGEN', shape: 'SHAP-E' };
let engineName = 'STANDBY';
function refreshCore() {
  const jobs = [...state.jobs.values()];
  if (state.streaming) core.setState(state.gotToken ? 'speaking' : 'thinking');
  else if (speechSynthesis.speaking) core.setState('speaking');
  else if (jobs.length) core.setState('working');
  else core.setState('idle');
  core.setProgress(jobs.length ? jobs[jobs.length - 1].progress : -1);
  const label = state.streaming ? (state.gotToken ? 'RESPONDING' : 'THINKING') : jobs.length ? 'SYNTHESIZING' : 'ONLINE';
  if (coreOnline !== false) $('#core-state').textContent = label;
  $('#engine-val').textContent = engineName + (jobs.length ? ` · ${jobs.length} JOB${jobs.length > 1 ? 'S' : ''}` : '');
  fitTelemetry();
}
// 遙測列真的放不下時再逐層收合（CSS 斷點之外的保險：標籤變長也不會被裁切或擠掉控制鈕）
const teleEl = $('.telemetry');
function fitTelemetry() {
  teleEl.dataset.fit = 0;
  for (let i = 1; i <= 4 && teleEl.scrollWidth > teleEl.clientWidth + 1; i++) teleEl.dataset.fit = i;
}
addEventListener('resize', fitTelemetry);

// ------------------------------------------------------------------ feed
function addMsg(role) {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.innerHTML = role === 'user'
    ? `<div class="msg-head">${now()} · YOU</div><div class="bubble"></div>`
    : `<div class="msg-head"><span class="tag">N.O.V.A.</span><span class="intent-tag"></span><span class="model-tag"></span><span style="margin-left:auto">${now()}</span></div><div class="bubble"><div class="text"></div><div class="extra"></div></div>`;
  $('#feed').appendChild(el);
  renderSuggestions();
  scrollFeed(true);
  return {
    el, body: el.querySelector('.bubble'), text: el.querySelector('.text'), extra: el.querySelector('.extra'),
    setIntent(k, zh, en) { const t = el.querySelector('.intent-tag'); if (t) t.innerHTML = tagHTML(k, zh, en); },
  };
}
function userMsg(text, images = []) {
  const m = addMsg('user');
  m.body.innerHTML = esc(text).replace(/\n/g, '<br>') + images.map(src => `<br><img class="thumb" src="${esc(src)}">`).join('');
}
function scrollFeed(force) {
  const f = $('#feed');
  if (force || f.scrollHeight - f.scrollTop - f.clientHeight < 160) f.scrollTop = f.scrollHeight;
}

// ------------------------------------------------------------------ markdown
function highlight(code, lang) {
  const hash = /^(py|python|sh|bash|shell|ps1|powershell|rb|ruby|yaml|yml|toml|r)$/i.test(lang || '');
  const re = new RegExp([
    `(\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/|<!--[\\s\\S]*?-->${hash ? '|#[^\\n]*' : ''})`,
    '("(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'|`(?:\\\\.|[^`\\\\])*`)',
    '\\b(\\d+(?:\\.\\d+)?)\\b',
    '\\b(const|let|var|function|return|if|else|elif|for|while|class|import|from|export|def|async|await|new|try|catch|except|finally|in|of|and|or|not|None|True|False|null|undefined|true|false|this|self|public|private|static|void|int|float|string|struct|fn|pub|use|lambda|with|as|yield|break|continue|switch|case|default|print|package|interface|type|extends|implements)\\b',
  ].join('|'), 'g');
  let out = '', last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    const cls = m[1] ? 'c' : m[2] ? 's' : m[3] ? 'n' : 'k';
    out += `<span class="tok-${cls}">${esc(m[0])}</span>`;
    last = re.lastIndex;
  }
  return out + esc(code.slice(last));
}

const emph = s => s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
function inline(s, sources) {
  // 程式碼與連結先換成佔位符，網址裡的 * 才不會被當成斜體
  const codes = [], links = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0001${codes.length - 1}\u0001`; });
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, t, u) => { links.push([t, u]); return `\u0002${links.length - 1}\u0002`; });
  s = emph(s).replace(/\[(\d{1,2})\]/g, (all, n) => {
    const src = sources?.[n - 1];
    return src ? `<sup class="cite"><a href="${esc(src.url)}" target="_blank" title="${esc(src.title)}">${n}</a></sup>` : all;
  });
  s = s.replace(/\u0002(\d+)\u0002/g, (_, i) => `<a href="${links[i][1]}" target="_blank" rel="noopener">${emph(links[i][0])}</a>`);
  return s.replace(/\u0001(\d+)\u0001/g, (_, i) => `<code>${codes[i]}</code>`);
}

function md(src, sources) {
  const blocks = [];
  src = String(src).replace(/[\u0000-\u0002]/g, '');   // 佔位符用的控制字元不能出現在原文裡
  // ``` 必須在行首才算程式碼區塊，句子中間的 ``` 不會吞掉後面的內容
  src = src.replace(/(^|\n)[ \t]*```([\w+#.-]*)[^\n`]*\n([\s\S]*?)(?:\n[ \t]*```[ \t]*(?=\n|$)|$)/g, (_, pre, lang, code) => {
    blocks.push({ lang, code: code.replace(/\n$/, '') });
    return `${pre}\n\u0000${blocks.length - 1}\u0000\n`;
  });
  const lines = esc(src).split('\n');
  let html = '', list = null, para = [], table = [];
  const flush = () => {
    if (para.length) { html += `<p>${para.map(l => inline(l, sources)).join('<br>')}</p>`; para = []; }
    if (list) { html += `</${list}>`; list = null; }
    if (table.length) {
      const rows = table.filter(r => !/^\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\||\|$/g, '').split('|'));
      html += '<table>' + rows.map((r, i) => `<tr>${r.map(c => `<${i ? 'td' : 'th'}>${inline(c.trim(), sources)}</${i ? 'td' : 'th'}>`).join('')}</tr>`).join('') + '</table>';
      table = [];
    }
  };
  for (const line of lines) {
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) {
      flush();
      const b = blocks[m[1]];
      const lang = (b.lang || '').toLowerCase();
      const previewable = ['html', 'htm', 'svg', 'js', 'javascript'].includes(lang) || /^\s*<(!doctype|html)/i.test(b.code);
      html += `<div class="codeblock" data-lang="${esc(lang)}"><div class="codeblock-head"><span>${esc(lang || 'code')}</span><div>`
        + `<button data-act="copy">COPY</button><button data-act="save">SAVE</button>`
        + (previewable ? '<button data-act="preview">PREVIEW ▶</button>' : '')
        + `</div></div><pre><code>${highlight(b.code, lang)}</code></pre></div>`;
    } else if ((m = line.match(/^(#{1,4})\s+(.*)/))) {
      flush(); const n = Math.min(3, m[1].length); html += `<h${n}>${inline(m[2], sources)}</h${n}>`;
    } else if ((m = line.match(/^\s*[-*+]\s+(.*)/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      const type = /^\s*\d/.test(line) ? 'ol' : 'ul';
      if (para.length || table.length) { const l = list; list = null; flush(); list = l; }
      if (list !== type) { if (list) html += `</${list}>`; html += `<${type}>`; list = type; }
      html += `<li>${inline(m[1], sources)}</li>`;
    } else if (/^\s*\|.*\|\s*$/.test(line)) {
      if (!table.length) flush();
      table.push(line.trim());
    } else if ((m = line.match(/^&gt;\s?(.*)/))) {
      flush(); html += `<blockquote>${inline(m[1], sources)}</blockquote>`;
    } else if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
      flush(); html += '<hr style="border:0;border-top:1px solid var(--c-faint)">';
    } else if (!line.trim()) {
      flush();
    } else {
      if (list || table.length) flush();
      para.push(line);
    }
  }
  flush();
  return `<div class="md">${html}</div>`;
}

// code block buttons (event delegation)
$('#feed').addEventListener('click', e => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const block = btn.closest('.codeblock');
  if (!block) return;
  const code = block.querySelector('pre').textContent;
  const lang = block.dataset.lang;
  if (btn.dataset.act === 'copy') { navigator.clipboard.writeText(code); toast('已複製程式碼'); }
  if (btn.dataset.act === 'save') {
    const ext = { javascript: 'js', python: 'py', typescript: 'ts', htm: 'html', bash: 'sh', shell: 'sh', powershell: 'ps1' }[lang] || lang || 'txt';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([code], { type: 'text/plain' }));
    a.download = `nova-code.${ext}`;
    a.click();
  }
  if (btn.dataset.act === 'preview') {
    let doc = code;
    if (lang === 'js' || lang === 'javascript') doc = `<!DOCTYPE html><html><body style="margin:0;background:#111;color:#eee;font-family:sans-serif"><script>${code}<\/script></body></html>`;
    $('#preview-frame').srcdoc = doc;
    $('#preview').classList.add('open');
  }
});
$('#preview-close').onclick = () => { $('#preview').classList.remove('open'); $('#preview-frame').srcdoc = ''; };

// ------------------------------------------------------------------ 單一聊天室：送出訊息
async function sendMessage(text, forceWeb = null) {
  const images = state.attachments.map(a => a.data);
  clearAttachments();
  const force = state.force;
  const reference = state.reference;
  state.force = null;
  renderChips();

  // 綁定送出當下的對話；途中按 NEW CHAT 的話，舊回覆不會混進新對話
  const hist = state.history;
  const entry = { role: 'user', content: text, images: images.length ? images : undefined };
  hist.push(entry);
  userMsg(text, images);
  const m = addMsg('ai');
  m.text.innerHTML = '<div class="status-line">連接神經核心…</div>';

  state.streaming = true; state.gotToken = false;
  setSendStop(true); refreshCore();
  caption('分析指令中…', 'NEURAL INFERENCE');
  const ctrl = new AbortController();
  state.abort = ctrl;

  let acc = '', sources = null, status = '連接神經核心…', scheduled = false, failed = false, intent = 'chat', payload = false;
  const reply = { role: 'assistant', content: '' };
  const render = () => {
    scheduled = false;
    const src = sources ? `<div class="sources">${sources.map((s, i) => `<a href="${esc(s.url)}" target="_blank" title="${esc(s.url)}">[${i + 1}] ${esc(s.title)}</a>`).join('')}</div>` : '';
    m.text.innerHTML = src + (acc ? md(acc, sources) : `<div class="status-line">${esc(status)}</div>`);
    scrollFeed();
  };
  const schedule = () => { if (!scheduled) { scheduled = true; requestAnimationFrame(render); } };

  try {
    const web = forceWeb ?? (state.web === 'auto' ? 'auto' : state.web === 'on');
    const res = await post('/api/chat', {
      web, force, model: $('#model-select').value, opts: genOpts(),
      context: { last_image: state.lastImage, reference: reference?.url },
      // 只有最後一則訊息附圖，避免每次都重送舊圖片
      messages: hist.map(h => ({ role: h.role, content: h.content, images: h === entry ? h.images : undefined })),
    }, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === 'intent') {
          intent = ev.content;
          m.setIntent(intent);
          core.pulse(0.6);
          if (['image', 'video'].includes(intent) && reference) { state.reference = null; renderChips(); }
          if (intent !== 'chat' && intent !== 'code') caption(`${INTENT[intent].zh}模組啟動`, INTENT[intent].en);
        } else if (ev.type === 'status') {
          status = ev.content;
          if (ev.content.includes('上網')) caption('正在搜尋全球網路…', 'WEB RECON');
        } else if (ev.type === 'model') {
          m.el.querySelector('.model-tag').textContent = ` · ${ev.content}`;
        } else if (ev.type === 'sources') {
          sources = ev.content; core.pulse(0.8);
        } else if (ev.type === 'token') {
          if (!state.gotToken) { state.gotToken = true; refreshCore(); if (intent === 'chat' || intent === 'code') caption('回應中…', 'TRANSMITTING'); }
          acc += ev.content; core.pulse(0.06);
        } else if (ev.type === 'job') {
          payload = true;
          pollJob(ev.content, m, text, reply, acc);
        } else if (ev.type === 'search') {
          // 「正在搜尋…」換成結果摘要（不可為空字串，否則會顯示轉圈的連線狀態）
          const c = ev.content, n = c.results.length, what = c.kind === 'videos' ? '影片' : '圖片';
          payload = true;
          renderSearch(c, m);
          const how = matchMedia('(hover: none), (any-pointer: coarse)').matches ? '用縮圖上的按鈕' : '滑過縮圖';
          acc = n ? `找到 **${n}** 筆「${c.query}」的${what}素材` + (c.kind === 'videos' ? '，點擊可開啟來源：' : `，${how}可存素材庫、拿來畫或做影片：`) : `搜尋「${c.query}」完成。`;
          reply.content = `（已搜尋「${c.query}」的素材，找到 ${n} 筆）`;
        } else if (ev.type === 'error') {
          failed = true; acc += `\n\n**⚠ ${ev.content}**`;
        }
        schedule();
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') { failed = true; acc += `\n\n**⚠ 連線中斷：${e.message}**`; }
  }
  const aborted = ctrl.signal.aborted;
  const stale = state.history !== hist;   // 途中按了 NEW CHAT
  // 串流結束卻什麼都沒收到（模型出錯、伺服器沒回應）→ 顯示錯誤，不要留下永遠轉圈的狀態列
  if (!acc && !failed && !aborted && !payload) { failed = true; acc = '**⚠ 沒有收到任何回覆（模型可能發生錯誤，請再試一次）**'; }
  render();
  if (!acc && aborted) m.text.innerHTML = '<p class="hint">（已中止）</p>';
  if (failed) { m.el.classList.add('error'); core.setState('error'); }
  if (!reply.content) reply.content = acc || '（已中止）';
  if (!stale) hist.push(reply);
  state.streaming = false; state.abort = null;
  setSendStop(false);
  setTimeout(refreshCore, failed ? 1500 : 0);
  if (!stale && (intent === 'chat' || intent === 'code' || failed)) {
    caption(failed ? '發生錯誤，請查看訊息。' : aborted ? '已中止回覆。' : '隨時待命。', 'ALL SYSTEMS NOMINAL');
    if (state.voice && !failed && !aborted) speak(acc);
  }
}

function setSendStop(on) {
  const b = $('#send-btn');
  b.classList.toggle('stop', on);
  b.querySelector('span').textContent = on ? 'ABORT' : 'EXECUTE';
  b.title = on ? '中止目前的回覆' : '送出（Enter）';
}

// ------------------------------------------------------------------ voice out
function speak(text) {
  const clean = text.replace(/```[\s\S]*?```/g, '（程式碼略）').replace(/[#*`>|_\[\]()]/g, '').replace(/https?:\S+/g, '').slice(0, 600);
  if (!clean.trim()) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(clean);
  const voices = speechSynthesis.getVoices();
  u.voice = voices.find(v => /zh[-_]TW/i.test(v.lang)) || voices.find(v => /^zh/i.test(v.lang)) || null;
  u.lang = u.voice?.lang || 'zh-TW';
  u.rate = 1.05;
  u.onstart = refreshCore;
  u.onboundary = () => core.pulse(0.3);
  u.onend = () => setTimeout(refreshCore, 50);
  speechSynthesis.speak(u);
}
function updateVoiceBtn() {
  const b = $('#voice-toggle');
  b.classList.toggle('on', state.voice);
  b.setAttribute('aria-pressed', String(state.voice));
  b.querySelector('b').textContent = state.voice ? 'ON' : 'OFF';
}
$('#voice-toggle').onclick = () => {
  state.voice = !state.voice;
  localStorage.setItem('nova-voice', state.voice ? '1' : '0');
  updateVoiceBtn();
  if (state.voice) speak('語音模組已啟動。');
  else speechSynthesis.cancel();
};

// ------------------------------------------------------------------ generation jobs（在同一則訊息裡顯示進度與結果）
const JOB_END = ['done', 'error', 'cancelled'];
// 取消生成任務：排隊中的直接取消；生成中的會在下一個進度回呼停下
async function cancelJob(id) {
  try {
    const res = await post(`/api/jobs/${id}/cancel`, {});
    if (res.status === 404) return 'gone';
    return res.ok ? (await res.json()).status || 'cancelled' : null;
  } catch { return null; }
}

async function pollJob(job, m, prompt, reply, ack = '') {
  const kind = job.kind, id = job.id;
  // 結束時把「收到，正在…」換成結果；第一段之後的補充說明（例如路由提示）保留
  const tail = ack.split('\n\n').slice(1).join('\n\n').trim();
  const setText = head => { m.text.innerHTML = md(head + (tail ? '\n\n' + tail : '')); };
  const box = document.createElement('div');
  box.innerHTML = `<div class="job-progress indet"><div class="track"><i></i></div><div class="meta"><span class="jmsg">${esc(job.message || '準備中…')}</span><span class="jside"><b class="jpct">0%</b><button class="jcancel" title="取消這個生成任務">取消</button></span></div></div>
    <div class="ref-slot"></div><div class="result-slot"></div><div class="en-prompt"></div>`;
  m.extra.appendChild(box);
  scrollFeed();
  const q = s => box.querySelector(s);
  const t0 = Date.now();
  let cancelling = false;
  q('.jcancel').onclick = async () => {
    const b = q('.jcancel');
    cancelling = true; b.disabled = true; b.textContent = '取消中…';
    const r = await cancelJob(id);
    // 404：伺服器不認得這個任務（已結束或已重啟）→ 交給下一次輪詢判斷
    if (r === null || r === 'gone') { cancelling = false; b.disabled = false; b.textContent = '取消'; toast(r ? '伺服器找不到這個任務' : '無法取消，請稍後再試'); }
  };
  state.jobs.set(id, { kind, progress: 0 });
  refreshCore();

  let shownRef = false, misses = 0;
  for (;;) {
    await sleep(900);
    if (!box.isConnected) { state.jobs.delete(id); refreshCore(); return; }
    let res;
    try { res = await fetch(u(`/api/jobs/${id}`)); } catch { if (++misses > 20) break; continue; }
    // 404：伺服器不認得這個任務（例如重新啟動過）→ 視為結束，不要無限輪詢
    if (res.status === 404) { job = { ...job, status: 'error', error: '任務已遺失（伺服器可能已重新啟動）' }; break; }
    try { if (!res.ok) throw new Error(res.status); job = await res.json(); misses = 0; } catch { if (++misses > 20) break; continue; }
    if (!box.isConnected) { state.jobs.delete(id); refreshCore(); return; }   // 輪詢途中按了 NEW CHAT
    const p = job.progress || 0;
    if (state.jobs.has(id)) state.jobs.set(id, { kind, progress: p });
    q('.track i').style.width = `${Math.round(p * 100)}%`;
    q('.job-progress').classList.toggle('indet', p === 0);   // 0% 階段（理解指令、排隊）也要看得出在動
    q('.jpct').textContent = `${Math.round(p * 100)}% · ${Math.round((Date.now() - t0) / 1000)}s`;
    q('.jmsg').textContent = cancelling && job.status === 'running' ? '取消中…（等目前這一步結束）' : job.message || '';
    if (job.prompt_en && job.prompt_en !== prompt) q('.en-prompt').textContent = `PROMPT › ${job.prompt_en}`;
    if (job.reference && !shownRef) {
      shownRef = true;
      const r = job.reference;
      q('.ref-slot').innerHTML = `<div class="ref-note">${r.url ? `<img src="${esc(u(r.url))}" referrerpolicy="no-referrer">` : ''}<span>參考素材：${esc(r.title || '')}${r.source ? ` · <a href="${esc(r.source)}" target="_blank" style="color:var(--c)">來源</a>` : ''}</span></div>`;
    }
    refreshCore();
    if (JOB_END.includes(job.status)) break;
  }
  state.jobs.delete(id);
  refreshCore();
  const zh = INTENT[kind].zh, secs = Math.round((Date.now() - t0) / 1000);
  if (job.status !== 'done') { q('.job-progress').classList.remove('indet'); q('.job-progress').classList.add('ended'); q('.jside').remove(); }
  if (job.status === 'cancelled') {
    // 使用者主動取消：不是錯誤，不用紅色樣式
    q('.job-progress').classList.add('cancelled');
    q('.jmsg').textContent = '已取消';
    setText(`**${zh}已取消**`);
    reply.content = `（${zh}已取消）`;
    caption('已取消生成任務。', 'TASK CANCELLED');
    return;
  }
  if (job.status !== 'done') {
    m.el.classList.add('error');
    q('.jmsg').textContent = '生成失敗：' + (job.error || '與伺服器失去連線');
    setText(`**${zh}生成失敗**`);
    reply.content = `（${zh}生成失敗）`;
    core.setState('error'); setTimeout(refreshCore, 1500);
    caption('生成失敗，請查看訊息。');
    return;
  }
  q('.job-progress').remove();
  setText(`**${zh}已完成**（${secs} 秒）` + (job.warning ? `\n\n⚠ ${job.warning}` : ''));
  renderResult(job.result, q('.result-slot'), prompt);
  reply.content = `（已生成${zh}：${job.prompt_en || prompt}）`;
  core.pulse(1.2);
  caption(`${zh}已完成。`, 'SYNTHESIS COMPLETE');
  if (state.voice) speak(`${zh}已經完成。`);
}

function useAsReference(url, thumb, intent) {
  state.reference = { url, thumb };
  setForce(intent);
  toast(intent === 'video' ? '已設為影片起始畫面，描述想要的動態後送出' : '已設為參考圖，描述想要的畫面後送出');
}

function renderResult(res, slot, prompt = '') {
  // 伺服器回傳 /outputs/… 路徑：顯示用完整網址，回傳給伺服器（參考圖）時保留原路徑
  const r = { ...res, url: u(res.url), poster: res.poster && u(res.poster) };
  const file = r.url.split('/').pop();
  const dl = `<a href="${r.url}" download="${file}">DOWNLOAD</a>`;
  if (r.type === 'image') {
    state.lastImage = res.url;
    slot.innerHTML = `<img class="media" src="${r.url}"><div class="actions">${dl}
      <button data-r="tovideo">讓它動起來</button><button data-r="ref">以此為參考再畫</button><a href="${r.url}" target="_blank">全螢幕</a></div>`;
    slot.querySelector('[data-r=ref]').onclick = () => useAsReference(res.url, r.url, 'image');
    slot.querySelector('[data-r=tovideo]').onclick = () => {
      useAsReference(res.url, r.url, 'video');
      if (!$('#prompt').value) { $('#prompt').value = prompt; autosize(); }
    };
  } else if (r.type === 'video') {
    slot.innerHTML = `<video class="media" src="${r.url}" ${r.poster ? `poster="${r.poster}"` : ''} controls loop autoplay muted playsinline></video><div class="actions">${dl}</div>`;
  } else if (r.type === 'audio') {
    // crossorigin：介面在 GitHub Pages 時，頻譜分析需要 CORS 才讀得到聲音
    slot.innerHTML = `<div class="audio-card"><canvas></canvas><audio src="${r.url}" ${API ? 'crossorigin="anonymous"' : ''} controls></audio></div><div class="actions">${dl}</div>`;
    hookAudio(slot.querySelector('audio'), slot.querySelector('canvas'));
  } else if (r.type === 'model3d') {
    slot.innerHTML = `<canvas class="viewer3d"></canvas><div class="actions">${dl}<button data-r="wire">全息線框</button><button data-r="spin">自動旋轉</button></div>`;
    let v = mountViewer(slot.querySelector('canvas'), r.url, nv => { v = nv; });   // 被回收後重新載入時換成新的檢視器
    slot.querySelector('[data-r=wire]').onclick = () => v.toggleWire();
    slot.querySelector('[data-r=spin]').onclick = () => v.toggleSpin();
  }
  scrollFeed(true);
}

// ------------------------------------------------------------------ 素材搜尋結果
function renderSearch({ kind, query, results }, m) {
  if (!results.length) {
    m.extra.innerHTML = `<p>找不到「${esc(query)}」的相關素材，換個關鍵字試試。</p>`;
    caption('搜尋沒有結果。');
    return;
  }
  caption(`找到 ${results.length} 筆素材。`, 'RECON COMPLETE');
  core.pulse(1);
  if (kind === 'images') {
    m.extra.innerHTML = `<div class="grid">${results.map((r, i) => `
      <div class="tile" data-i="${i}"><img src="${esc(r.thumbnail || r.image)}" loading="lazy" referrerpolicy="no-referrer" title="${esc(r.title)}" onload="if(this.naturalWidth<=80&&this.naturalHeight<=80)this.closest('.tile').remove()" onerror="this.closest('.tile').remove()">
        <div class="tile-ops"><button data-op="save">存素材庫</button><button data-op="image">拿來畫</button><button data-op="video">做影片</button><a href="${esc(r.url)}" target="_blank">來源</a></div></div>`).join('')}</div>`;
    m.extra.querySelectorAll('.tile').forEach(tile => {
      const r = results[tile.dataset.i];
      tile.querySelectorAll('[data-op]').forEach(b => b.onclick = async () => {
        const op = b.dataset.op;
        if (op === 'save') {
          b.textContent = '下載中…';
          try {
            const out = await (await post('/api/assets/save', { url: r.image })).json();
            b.textContent = out.url ? '✓ 已存' : '失敗';
            if (out.url) toast('已存入本機素材庫 outputs/assets');
          } catch { b.textContent = '失敗'; }
        } else {
          useAsReference(r.image, r.thumbnail, op);
        }
      });
    });
  } else {
    m.extra.innerHTML = `<div class="results">${results.map(r => `
      <a class="result" href="${esc(r.url)}" target="_blank" rel="noopener">${r.thumbnail ? `<img src="${esc(r.thumbnail)}" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}
        <div><b>${esc(r.title)}</b><small>${esc(r.source || r.url)}${r.duration ? ' · ' + esc(r.duration) : ''}</small><p>${esc(r.body || '')}</p></div></a>`).join('')}</div>`;
  }
  scrollFeed(true);
}

// ------------------------------------------------------------------ audio visualizer (也會驅動神經球)
let actx = null, playing = null;
function hookAudio(audio, canvas) {
  audio.addEventListener('play', () => {
    actx ??= new AudioContext();
    if (!audio._an) {
      const src = actx.createMediaElementSource(audio);
      audio._an = actx.createAnalyser();
      audio._an.fftSize = 256;
      audio._an.smoothingTimeConstant = 0.8;
      src.connect(audio._an); audio._an.connect(actx.destination);
      audio._data = new Uint8Array(audio._an.frequencyBinCount);
    }
    actx.resume();
    playing = { audio, canvas };
  });
}
(function audioLoop() {
  requestAnimationFrame(audioLoop);
  let level = 0;
  if (playing && !playing.audio.paused && playing.audio.isConnected) {
    const { audio, canvas } = playing;
    audio._an.getByteFrequencyData(audio._data);
    const d = audio._data;
    let sum = 0;
    for (let i = 0; i < 48; i++) sum += d[i];
    level = sum / 48 / 255;
    const w = canvas.width = canvas.clientWidth * devicePixelRatio, h = canvas.height = canvas.clientHeight * devicePixelRatio;
    const g = canvas.getContext('2d');
    const bars = 64, bw = w / bars;
    for (let i = 0; i < bars; i++) {
      const v = d[Math.floor(i * d.length / bars / 1.4)] / 255;
      const bh = Math.max(2, v * h * 0.95);
      const grd = g.createLinearGradient(0, h, 0, h - bh);
      grd.addColorStop(0, 'rgba(56,230,255,.9)'); grd.addColorStop(1, 'rgba(138,92,255,.9)');
      g.fillStyle = grd;
      g.fillRect(i * bw + 1, h - bh, bw - 2, bh);
    }
  } else if (speechSynthesis.speaking) {
    level = 0.12 + Math.random() * 0.12;
  }
  core.setAudioLevel(level);
})();

// ------------------------------------------------------------------ 3D viewer
const liveViewers = [], MAX_VIEWERS = 6;
function mountViewer(canvas, url, onMount) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const cam = new THREE.PerspectiveCamera(40, 4 / 3, 0.01, 100);
  cam.position.set(1.9, 1.3, 2.5);
  scene.add(new THREE.HemisphereLight(0xcff6ff, 0x1a0a30, 1.8));
  const key = new THREE.DirectionalLight(0xffffff, 2); key.position.set(3, 5, 4); scene.add(key);
  const rim = new THREE.DirectionalLight(0x38e6ff, 2); rim.position.set(-4, 2, -3); scene.add(rim);
  const grid = new THREE.PolarGridHelper(1.3, 16, 6, 64, 0x38e6ff, 0x0d4a5a);
  grid.position.y = -0.8;
  scene.add(grid);
  const controls = new OrbitControls(cam, canvas);
  controls.enableDamping = true; controls.autoRotate = true; controls.autoRotateSpeed = 1.6;
  const meshes = [];
  let wire = false, visible = true;

  new GLTFLoader().load(url, gltf => {
    const obj = gltf.scene;
    if (!alive) return obj.traverse(o => o.geometry?.dispose());
    obj.traverse(o => {
      if (!o.isMesh) return;
      const hasColor = !!o.geometry.attributes.color;
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      o.material = new THREE.MeshStandardMaterial({ vertexColors: hasColor, color: hasColor ? 0xffffff : 0x9fdcff, roughness: 0.55, metalness: 0.1 });
      meshes.push(o);
    });
    const box = new THREE.Box3().setFromObject(obj);
    const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
    const s = 1.6 / Math.max(size.x, size.y, size.z, 1e-6);
    const wrap = new THREE.Group();
    obj.position.sub(center);
    wrap.add(obj);
    wrap.scale.setScalar(s);
    grid.position.y = -size.y * s / 2;
    scene.add(wrap);
  }, undefined, err => { canvas.replaceWith(Object.assign(document.createElement('div'), { textContent: '3D 模型載入失敗：' + err.message })); });

  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
  io.observe(canvas);
  let alive = true;
  const api = {
    canvas, url, onMount,
    toggleWire() {
      wire = !wire;
      meshes.forEach(m => {
        m.material.wireframe = wire;
        m.material.emissive = new THREE.Color(wire ? 0x38e6ff : 0x000000);
        m.material.emissiveIntensity = wire ? 0.8 : 0;
      });
    },
    toggleSpin() { controls.autoRotate = !controls.autoRotate; },
    // 完整釋放 WebGL context，避免瀏覽器因 context 太多而把背景神經球的 context 砍掉
    destroy() {
      if (!alive) return;
      alive = false;
      io.disconnect(); controls.dispose();
      scene.traverse(o => { o.geometry?.dispose(); [].concat(o.material || []).forEach(mt => mt.dispose()); });
      renderer.dispose(); renderer.forceContextLoss();
      const i = liveViewers.indexOf(api);
      if (i >= 0) liveViewers.splice(i, 1);
    },
  };
  (function loop() {
    if (!alive) return;
    if (!canvas.isConnected) return api.destroy();
    requestAnimationFrame(loop);
    if (!visible) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== Math.floor(w * renderer.getPixelRatio())) { renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); }
    controls.update();
    renderer.render(scene, cam);
  })();
  // 同時最多保留 MAX_VIEWERS 個 3D 檢視器，最舊的換成「點一下重新載入」
  liveViewers.push(api);
  while (liveViewers.length > MAX_VIEWERS) {
    const old = liveViewers[0];
    old.destroy();
    if (!old.canvas.isConnected) continue;
    const ph = document.createElement('button');
    ph.className = 'viewer3d viewer-ph';
    ph.textContent = '點一下重新載入 3D 模型';
    ph.onclick = () => {
      const c = document.createElement('canvas');
      c.className = 'viewer3d';
      ph.replaceWith(c);
      old.onMount?.(mountViewer(c, old.url, old.onMount));
    };
    old.canvas.replaceWith(ph);
  }
  return api;
}

// ------------------------------------------------------------------ gallery
async function loadGallery() {
  openPanel();
  const m = addMsg('ai');
  m.setIntent('archive', '作品庫', 'ARCHIVE');
  let items = [];
  try { ({ items } = await (await fetch(u('/api/gallery'))).json()); } catch { /* 伺服器離線 */ }
  if (!items.length) { m.text.textContent = '作品庫是空的，先在聊天室裡生成一些東西吧。'; return; }
  m.text.innerHTML = `<p>作品庫共有 ${items.length} 件作品，點一下即可在聊天室中開啟。</p>`;
  const icon = { audio: ICON.music, model3d: ICON.model3d };
  m.extra.innerHTML = `<div class="grid">${items.map((it, i) => `<div class="tile" data-i="${i}" style="cursor:pointer">
    <span class="badge">${it.type.toUpperCase()}</span>
    ${it.type === 'image' ? `<img src="${u(it.url)}" loading="lazy">`
      : it.type === 'video' ? `<video src="${u(it.url)}" ${it.poster ? `poster="${u(it.poster)}"` : ''} muted loop preload="none" onmouseenter="this.play()" onmouseleave="this.pause()"></video>`
      : `<svg viewBox="0 0 24 24" style="width:40%;height:100%;margin:auto;display:block;fill:none;stroke:var(--c);stroke-width:1.2">${icon[it.type]}</svg>`}
  </div>`).join('')}</div>`;
  m.extra.querySelectorAll('.tile').forEach(t => t.onclick = () => {
    const out = addMsg('ai');
    out.setIntent('archive', '作品庫', 'ARCHIVE');
    renderResult(items[t.dataset.i], out.extra);
  });
  caption(`作品庫共有 ${items.length} 件作品。`, 'ARCHIVE');
}
$('#archive-btn').onclick = loadGallery;

// ------------------------------------------------------------------ input
const promptEl = $('#prompt');
function autosize() {
  // 空白時回到 CSS 預設高度（Chromium 的 scrollHeight 會把換行的 placeholder 算進去）
  if (!promptEl.value) { promptEl.style.height = ''; return; }
  promptEl.style.height = 'auto';
  promptEl.style.height = Math.min(180, promptEl.scrollHeight) + 'px';
}
promptEl.addEventListener('input', autosize);
// 指令列的實際高度 → --cmd-h，面板與字幕都跟著它走，多行輸入或附圖時也不會重疊
const cmdEl = $('.command');
new ResizeObserver(() => document.documentElement.style.setProperty('--cmd-h', cmdEl.offsetHeight + 'px')).observe(cmdEl);
promptEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    // Enter 只負責送出，不會中止正在串流的回覆（中止請按 ABORT）
    if (state.streaming) { toast('回覆中，請稍候或按 ABORT 中止'); return; }
    submit();
  }
});
$('#send-btn').onclick = () => state.streaming ? state.abort?.abort() : submit();

function submit() {
  if (state.streaming) return;   // 永遠不會中止；文字留在輸入框
  const text = promptEl.value.trim();
  if (!text) { promptEl.focus(); return; }
  promptEl.value = ''; autosize();
  if (settingsOpen()) setSettingsOpen(false, false);
  openPanel();
  core.pulse(0.8);
  sendMessage(text);
}

// web mode
const WEB_STATES = ['auto', 'on', 'off'];
$('#web-btn').onclick = () => {
  state.web = WEB_STATES[(WEB_STATES.indexOf(state.web) + 1) % 3];
  const b = $('#web-btn');
  b.classList.toggle('on', state.web === 'on');
  b.classList.toggle('off', state.web === 'off');
  $('#web-state').textContent = state.web.toUpperCase();
  toast({ auto: '上網查資料：自動判斷', on: '上網查資料：每次都搜尋', off: '上網查資料：關閉（純本機知識）' }[state.web]);
};

// attachments
function addAttachment(file) {
  if (!file?.type.startsWith('image/')) return;
  const img = new Image();
  img.onload = () => {
    const s = Math.min(1, 1024 / Math.max(img.width, img.height));
    const c = document.createElement('canvas');
    c.width = img.width * s; c.height = img.height * s;
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    state.attachments = [{ data: c.toDataURL('image/jpeg', 0.9) }];
    renderAttachments();
    URL.revokeObjectURL(img.src);
  };
  img.src = URL.createObjectURL(file);
}
function renderAttachments() {
  $('#attachments').innerHTML = state.attachments.map((a, i) => `<div class="att"><img src="${a.data}"><button data-i="${i}">✕</button></div>`).join('');
  $('#attachments').querySelectorAll('button').forEach(b => b.onclick = () => { state.attachments.splice(+b.dataset.i, 1); renderAttachments(); });
}
function clearAttachments() { state.attachments = []; renderAttachments(); }
$('#attach-btn').onclick = () => $('#file-input').click();
$('#file-input').onchange = e => { addAttachment(e.target.files[0]); e.target.value = ''; };
promptEl.addEventListener('paste', e => {
  const f = [...e.clipboardData.files].find(f => f.type.startsWith('image/'));
  if (f) { e.preventDefault(); addAttachment(f); }
});
addEventListener('dragover', e => e.preventDefault());
addEventListener('drop', e => { e.preventDefault(); addAttachment(e.dataTransfer.files[0]); });

// voice input
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
$('#mic-btn').onclick = () => {
  if (!SR) return toast('此瀏覽器不支援語音輸入，請用 Chrome 或 Edge');
  if (rec) { rec.stop(); return; }
  rec = new SR();
  rec.lang = 'zh-TW'; rec.interimResults = true; rec.continuous = false;
  let finalText = '';
  rec.onresult = e => {
    const t = [...e.results].map(r => r[0].transcript).join('');
    promptEl.value = t; autosize();
    if (e.results[e.results.length - 1].isFinal) finalText = t;
    core.pulse(0.15);
  };
  rec.onstart = () => { $('#mic-btn').classList.add('rec'); core.setState('thinking'); caption('聆聽中…', 'VOICE INPUT'); };
  rec.onend = () => { $('#mic-btn').classList.remove('rec'); rec = null; refreshCore(); if (finalText.trim() && !state.streaming) submit(); };
  rec.onerror = e => toast('語音辨識錯誤：' + e.error);
  rec.start();
};

// keyboard shortcuts
addEventListener('keydown', e => {
  if (e.altKey && /^[0-7]$/.test(e.key) && CHIPS[+e.key]) { e.preventDefault(); const k = CHIPS[+e.key][0]; setForce(k === 'auto' ? null : k); }
  if (e.key === 'Escape') {
    if ($('#preview').classList.contains('open')) $('#preview-close').click();
    else if (settingsOpen()) setSettingsOpen(false);
    else openPanel(!document.body.classList.contains('panel-open'));
  }
  if (e.key === '/' && document.activeElement !== promptEl && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName)) { e.preventDefault(); promptEl.focus(); }
});

// ------------------------------------------------------------------ telemetry
let coreOnline = null;
async function pollStatus() {
  try {
    const s = await (await fetch(u('/api/status'))).json();
    $('#cpu-bar').style.width = s.cpu + '%'; $('#cpu-val').textContent = Math.round(s.cpu) + '%';
    $('#ram-bar').style.width = s.ram + '%'; $('#ram-val').textContent = Math.round(s.ram) + '%';
    engineName = ENGINE_NAMES[s.loaded] || 'STANDBY';
    const sel = $('#model-select');
    const models = s.ollama || [];
    if (sel.dataset.list !== models.join()) {
      sel.dataset.list = models.join();
      const saved = localStorage.getItem('nova-model');
      sel.innerHTML = models.length ? models.map(n => `<option ${n === saved ? 'selected' : ''}>${esc(n)}</option>`).join('') : '<option value="">無模型</option>';
    }
    const ok = s.ollama !== null && models.length > 0;
    $('#chip-core .led').className = 'led ' + (ok ? 'ok' : 'bad');
    coreOnline = ok;
    if (!ok) $('#core-state').textContent = s.ollama === null ? 'LLM OFFLINE' : 'NO MODEL';
    refreshCore();
    return s;
  } catch {
    $('#chip-core .led').className = 'led bad';
    $('#core-state').textContent = 'SERVER DOWN';
    coreOnline = false;
    fitTelemetry();
    return null;
  }
}
$('#model-select').onchange = e => localStorage.setItem('nova-model', e.target.value);
setInterval(pollStatus, 2500);

function tickClock() {
  const d = new Date();
  $('#clock-time').textContent = d.toLocaleTimeString('zh-TW', { hour12: false });
  $('#clock-date').textContent = d.toLocaleDateString('en-CA').replace(/-/g, '.') + ' · ' + ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'][d.getDay()];
}
setInterval(tickClock, 1000); tickClock();

// ------------------------------------------------------------------ boot
async function boot() {
  const log = $('#boot-log'), bar = $('.boot-bar i');
  const lines = [
    'INITIALIZING NEURAL MESH', 'LOADING SYNAPTIC WEIGHTS', 'CALIBRATING HOLOGRAPHIC HUD',
    'LINKING GENERATIVE ENGINES [IMAGE · MOTION · SONIC · MATTER]', 'OPENING WEB RECON CHANNEL',
  ];
  for (let i = 0; i < lines.length; i++) {
    log.innerHTML += `> ${lines[i]} … `;
    await sleep(170 + Math.random() * 180);
    log.innerHTML += '<b>OK</b>\n';
    bar.style.width = `${(i + 1) / (lines.length + 1) * 100}%`;
  }
  log.innerHTML += '> PROBING LOCAL LANGUAGE CORE … ';
  const s = await pollStatus();
  const models = s?.ollama || [];
  log.innerHTML += models.length ? `<b>${models.length} MODEL(S)</b>\n` : '<b style="color:#ff5a6e">OFFLINE</b>\n';
  bar.style.width = '100%';
  await sleep(450);
  $('#boot').classList.add('done');
  core.pulse(1.5);

  const h = new Date().getHours();
  const greet = h < 5 ? '夜深了' : h < 12 ? '早安' : h < 18 ? '午安' : '晚安';
  if (!s && REMOTE_UI) {
    caption('AI 核心未連線：請在電腦上執行 start.bat（Mac：./start.sh），再重新整理此頁。', 'CORE OFFLINE');
    toast('瀏覽器若詢問「存取本機網路」，請按允許');
  } else if (!s) caption('無法連線到本機伺服器，請執行 start.bat。', 'SERVER OFFLINE');
  else if (!models.length) caption(`${greet}。語言核心離線，請啟動 Ollama 並下載模型。`, 'LLM OFFLINE');
  else caption(`${greet}，所有系統運作正常。今天想創造什麼？`, 'ALL SYSTEMS NOMINAL');
}

updateVoiceBtn();
renderChips();
renderSuggestions();
boot();
