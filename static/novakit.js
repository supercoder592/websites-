// NovaKit —— N.O.V.A. 專案模式的遊戲工具包（ES module，以 'novakit' 匯入）
// 手機／筆電雙模式操控、渲染器、素材載入、HUD、音效與錯誤回報；API 盡量「不丟錯」，缺素材或按鈕時給合理預設。
// 慣例：input.move.y > 0 = 往前（W／搖桿往上）、move.x > 0 = 往右；
//       input.look 是「本幀」視角變化量（約為弧度）：look.x > 0 = 往右轉、look.y > 0 = 往下看。
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const W = window, D = document;
const BTN = ['a', 'b', 'x', 'y'];
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, Number(v) || 0));
const random = (a, b) => {
  if (a === undefined) return Math.random();
  if (b === undefined) { b = a; a = 0; }
  return a + Math.random() * (b - a);
};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
// 沙箱（opaque origin）裡讀 localStorage 會丟 SecurityError —— 一律包起來
const storage = {
  get(k, d = null) { try { const v = W.localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { W.localStorage.setItem(k, String(v)); } catch { /* 沙箱內無法儲存 */ } },
};

// ---------------------------------------------------------------- 錯誤回報（匯入即生效）
const seenErr = new Set();
// 打包後 three／novakit 是 data: URL 模組，堆疊裡會出現好幾 MB 的 base64：換成 importmap 裡的名稱
let modNames = null;
function modName(url) {
  if (!modNames) {
    modNames = new Map();
    try {
      for (const s of D.querySelectorAll('script[type=importmap]')) {
        for (const [k, v] of Object.entries(JSON.parse(s.textContent).imports || {})) if (typeof v === 'string' && v.startsWith('data:')) modNames.set(v.slice(0, 200), k);
      }
    } catch { /* 忽略 */ }
  }
  return modNames.get(url.slice(0, 200)) || 'data:…';
}
const cleanText = s => String(s ?? '')
  .replace(/data:[\w/+.-]*(?:;[\w=.-]+)*;base64,[A-Za-z0-9+/=]+/g, modName)
  .replace(/data:[^,\s]*,[^\s)'"]{120,}/g, modName);
function reportError(message, stack = '') {
  message = cleanText(message || '未知錯誤').slice(0, 600);
  stack = cleanText(stack);
  if (/ResizeObserver loop/i.test(message) || seenErr.has(message) || seenErr.size > 24) return;
  seenErr.add(message);
  try { (W.parent || W).postMessage({ type: 'nova-error', message, stack: String(stack || '').slice(0, 4000) }, '*'); } catch { /* 忽略 */ }
  showError(message);
}
W.addEventListener('error', e => {
  if (e.target && e.target !== W) return;   // 資源載入失敗（img/audio）不算程式錯誤
  const err = e.error;
  reportError(err?.message ? `${err.name || 'Error'}: ${err.message}` : e.message,
    err?.stack || `${e.filename || ''}:${e.lineno || 0}:${e.colno || 0}`);
});
W.addEventListener('unhandledrejection', e => {
  const r = e.reason;
  reportError(r?.message ? `${r.name || 'Error'}: ${r.message}` : `Unhandled rejection: ${String(r)}`, r?.stack || '');
});

// ---------------------------------------------------------------- 樣式
const CSS = `
html,body{margin:0;height:100%;overflow:hidden;background:#000;overscroll-behavior:none;touch-action:none;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;-webkit-tap-highlight-color:transparent}
.nk-canvas{position:fixed;inset:0;display:block;width:100%;height:100%;touch-action:none;outline:none;z-index:0}
.nk-root{--nk-c:#38e6ff;--nk-c2:#8a5cff;--nk-bad:#ff4d6d;--nk-f:Orbitron,Eurostile,'Bank Gothic','Segoe UI','Microsoft JhengHei','PingFang TC','Noto Sans TC',system-ui,sans-serif;
 --nk-st:env(safe-area-inset-top,0px);--nk-sr:env(safe-area-inset-right,0px);--nk-sb:env(safe-area-inset-bottom,0px);--nk-sl:env(safe-area-inset-left,0px);
 position:fixed;inset:0;z-index:20;pointer-events:none;font-family:var(--nk-f);color:#d8f6ff;touch-action:none;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;-webkit-tap-highlight-color:transparent}
.nk-root *{box-sizing:border-box;touch-action:none}
.nk-i{pointer-events:auto}
.nk-root button{font-family:var(--nk-f);color:inherit;outline:none}
.nk-top{position:absolute;z-index:6;top:0;left:0;right:0;display:flex;align-items:center;gap:8px;padding:calc(8px + var(--nk-st)) calc(10px + var(--nk-sr)) 6px calc(10px + var(--nk-sl));pointer-events:none}
.nk-title{display:flex;align-items:center;gap:9px;min-width:0;padding:7px 18px 7px 11px;background:linear-gradient(90deg,rgba(4,16,28,.88),rgba(4,16,28,.3));border-left:3px solid var(--nk-c);clip-path:polygon(0 0,100% 0,calc(100% - 12px) 100%,0 100%);font-weight:700;font-size:13px;letter-spacing:.14em;text-transform:uppercase;text-shadow:0 0 10px rgba(56,230,255,.6)}
.nk-title span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nk-title i{width:8px;height:8px;flex:none;background:var(--nk-c);transform:rotate(45deg);box-shadow:0 0 8px var(--nk-c)}
.nk-tools{margin-left:auto;display:flex;gap:6px;flex:none}
.nk-tb{height:34px;min-width:34px;padding:0 9px;border:1px solid rgba(56,230,255,.42);background:rgba(4,16,28,.72);font-weight:600;font-size:12px;letter-spacing:.06em;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:5px;white-space:nowrap;clip-path:polygon(7px 0,100% 0,100% calc(100% - 7px),calc(100% - 7px) 100%,0 100%,0 7px);transition:background .15s,border-color .15s}
.nk-tb:hover{background:rgba(56,230,255,.18);border-color:var(--nk-c)}
.nk-tb:active{transform:scale(.94)}
.nk-tb svg{width:16px;height:16px;flex:none}
.nk-seg{display:flex;border:1px solid rgba(56,230,255,.42);background:rgba(4,16,28,.72);clip-path:polygon(7px 0,100% 0,100% calc(100% - 7px),calc(100% - 7px) 100%,0 100%,0 7px)}
.nk-seg .nk-tb{border:0;background:transparent;clip-path:none;opacity:.55}
.nk-seg .nk-tb.on{opacity:1;background:linear-gradient(180deg,rgba(56,230,255,.28),rgba(138,92,255,.22));text-shadow:0 0 8px var(--nk-c)}
.nk-side{position:absolute;top:calc(54px + var(--nk-st));left:calc(10px + var(--nk-sl));width:min(250px,46vw);display:flex;flex-direction:column;gap:7px;pointer-events:none}
.nk-bar{font-size:10px;letter-spacing:.14em;text-transform:uppercase}
.nk-bar-h{display:flex;justify-content:space-between;gap:8px;margin:0 0 3px 2px;opacity:.9;text-shadow:0 0 6px rgba(0,0,0,.9)}
.nk-bar-h b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nk-bar-t{height:9px;position:relative;overflow:hidden;background:rgba(4,16,28,.78);border:1px solid rgba(56,230,255,.28);transform:skewX(-24deg)}
.nk-bar-f{position:absolute;inset:0;transform-origin:left center;background:linear-gradient(90deg,color-mix(in srgb,var(--nk-bc) 60%,#000),var(--nk-bc));box-shadow:0 0 10px var(--nk-bc);transition:transform .12s linear}
.nk-bar-t::after{content:'';position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 9px,rgba(0,0,0,.45) 9px 11px)}
.nk-bar.nk-low .nk-bar-f{animation:nk-blink .45s infinite alternate;will-change:opacity}
@keyframes nk-blink{to{opacity:.35}}
.nk-hud{display:none;padding:7px 11px;background:rgba(4,16,28,.62);border-left:2px solid var(--nk-c2);font:600 12px/1.6 var(--nk-f);letter-spacing:.06em;white-space:pre-line;text-shadow:0 0 6px rgba(0,0,0,.9)}
.nk-hud b{color:var(--nk-c);font-weight:700}
.nk-toasts{position:absolute;z-index:5;top:24%;left:50%;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:6px;width:max-content;max-width:88vw;pointer-events:none}
.nk-toast{padding:8px 20px;background:linear-gradient(90deg,rgba(4,16,28,.3),rgba(4,16,28,.88),rgba(4,16,28,.3));border-top:1px solid rgba(56,230,255,.6);border-bottom:1px solid rgba(56,230,255,.6);font-weight:700;font-size:15px;letter-spacing:.16em;text-align:center;color:#fff;text-shadow:0 0 12px var(--nk-c);animation:nk-in .25s ease-out;transition:opacity .4s,transform .4s}
.nk-toast.out{opacity:0;transform:translateY(-10px)}
@keyframes nk-in{from{opacity:0;transform:scale(1.25)}}
.nk-help{position:absolute;left:50%;bottom:calc(14px + var(--nk-sb));transform:translateX(-50%);max-width:92vw;padding:7px 14px;background:rgba(4,16,28,.7);border:1px solid rgba(56,230,255,.25);font:600 11px/1.5 var(--nk-f);letter-spacing:.08em;color:#b9e6f5;text-align:center;opacity:0;transition:opacity .6s;pointer-events:none}
.nk-help.show{opacity:1}
.nk-mobile .nk-help{bottom:calc(236px + var(--nk-sb))}
@media (max-height:540px){.nk-mobile .nk-help{bottom:calc(10px + var(--nk-sb));max-width:40vw}}
.nk-joy{position:absolute;left:calc(30px + var(--nk-sl));bottom:calc(30px + var(--nk-sb));width:128px;height:128px;border-radius:50%;border:2px solid rgba(56,230,255,.45);background:radial-gradient(circle,rgba(56,230,255,.12),rgba(4,16,28,.38) 70%);box-shadow:0 0 18px rgba(56,230,255,.2),inset 0 0 18px rgba(56,230,255,.15);display:none;opacity:.5;transition:opacity .2s;pointer-events:none}
.nk-joy.on{opacity:1}
.nk-joy::before{content:'';position:absolute;inset:18px;border-radius:50%;border:1px dashed rgba(56,230,255,.25)}
.nk-knob{position:absolute;left:50%;top:50%;width:56px;height:56px;margin:-28px 0 0 -28px;border-radius:50%;background:radial-gradient(circle at 40% 35%,#c9f8ff,#38e6ff 45%,#16627d);box-shadow:0 0 16px #38e6ff}
.nk-look{position:absolute;right:calc(18px + var(--nk-sr));top:30%;display:none;padding:6px 12px;font:600 11px var(--nk-f);letter-spacing:.1em;color:rgba(216,246,255,.6);border:1px dashed rgba(56,230,255,.3);border-radius:18px;transition:opacity .8s;pointer-events:none}
.nk-look.gone{opacity:0}
.nk-btns{position:absolute;right:calc(14px + var(--nk-sr));bottom:calc(18px + var(--nk-sb));width:210px;height:200px;display:none;pointer-events:none}
.nk-mobile .nk-joy,.nk-mobile .nk-btns,.nk-mobile .nk-look{display:block}
.nk-ab{position:absolute;border-radius:50%;border:2px solid var(--nk-bc);background:radial-gradient(circle at 50% 38%,rgba(255,255,255,.16),rgba(4,16,28,.6) 66%);font-weight:700;font-size:14px;letter-spacing:.02em;line-height:1.05;padding:4px;display:none;align-items:center;justify-content:center;text-align:center;word-break:break-all;box-shadow:0 0 14px color-mix(in srgb,var(--nk-bc) 45%,transparent),inset 0 0 12px color-mix(in srgb,var(--nk-bc) 30%,transparent);text-shadow:0 0 8px var(--nk-bc);pointer-events:auto;transition:transform .06s}
.nk-ab.show{display:flex}
.nk-ab.down{transform:scale(.88);background:var(--nk-bc);color:#021;text-shadow:none;box-shadow:0 0 28px var(--nk-bc)}
.nk-ab[data-b=a]{width:84px;height:84px;right:0;bottom:6px;--nk-bc:#38e6ff;font-size:16px}
.nk-ab[data-b=b]{width:64px;height:64px;right:96px;bottom:0;--nk-bc:#8a5cff}
.nk-ab[data-b=x]{width:64px;height:64px;right:10px;bottom:102px;--nk-bc:#ffc46b}
.nk-ab[data-b=y]{width:56px;height:56px;right:88px;bottom:80px;--nk-bc:#5dff9e}
.nk-dlg-wrap{position:absolute;z-index:4;inset:0;display:none;align-items:center;justify-content:center;padding:20px;background:radial-gradient(ellipse at center,rgba(1,6,14,.5),rgba(1,4,10,.9));pointer-events:auto}
.nk-dlg-wrap.show{display:flex;animation:nk-fade .35s}
@keyframes nk-fade{from{opacity:0}}
.nk-dlg{width:min(470px,92vw);padding:28px 24px 22px;text-align:center;background:linear-gradient(160deg,rgba(8,24,40,.96),rgba(12,6,30,.96));border:1px solid rgba(56,230,255,.5);box-shadow:inset 0 0 40px rgba(138,92,255,.16);clip-path:polygon(18px 0,100% 0,100% calc(100% - 18px),calc(100% - 18px) 100%,0 100%,0 18px);animation:nk-pop .35s cubic-bezier(.2,1.4,.4,1)}
@keyframes nk-pop{from{transform:scale(.85);opacity:0}}
.nk-dlg h2{margin:0 0 12px;font-size:clamp(20px,5.5vw,28px);font-weight:800;letter-spacing:.16em;color:var(--nk-c);text-shadow:0 0 18px rgba(56,230,255,.7)}
.nk-dlg.nk-over h2{color:var(--nk-bad);text-shadow:0 0 18px rgba(255,77,109,.7)}
.nk-dlg.nk-over{border-color:rgba(255,77,109,.55)}
.nk-dlg.nk-win h2{color:#ffd36b;text-shadow:0 0 18px rgba(255,200,90,.7)}
.nk-dlg-t{margin:0 0 20px;font:500 14px/1.75 'Segoe UI','Microsoft JhengHei','PingFang TC','Noto Sans TC',system-ui,sans-serif;color:#b9dcea;max-height:48vh;overflow:auto;touch-action:pan-y}
.nk-dlg-t:empty{display:none}
.nk-dlg-b{min-width:190px;padding:13px 28px;border:1px solid var(--nk-c);background:linear-gradient(180deg,rgba(56,230,255,.3),rgba(138,92,255,.3));font-weight:700;font-size:15px;letter-spacing:.2em;cursor:pointer;text-shadow:0 0 10px var(--nk-c);clip-path:polygon(10px 0,100% 0,100% calc(100% - 10px),calc(100% - 10px) 100%,0 100%,0 10px);box-shadow:inset 0 0 18px rgba(56,230,255,.35)}
.nk-dlg-b:hover,.nk-dlg-b:active{background:linear-gradient(180deg,rgba(56,230,255,.5),rgba(138,92,255,.45))}
.nk-err{position:absolute;top:calc(54px + var(--nk-st));left:50%;transform:translateX(-50%);width:min(560px,92vw);display:flex;flex-direction:column;gap:6px;z-index:8;pointer-events:none}
.nk-err div{position:relative;padding:8px 30px 8px 12px;background:rgba(40,4,12,.9);border:1px solid var(--nk-bad);border-left:3px solid var(--nk-bad);font:12px/1.45 Consolas,'Cascadia Mono',monospace;color:#ffd0d8;word-break:break-word;pointer-events:auto;box-shadow:0 0 18px rgba(255,77,109,.3)}
.nk-err b{display:block;font:700 11px var(--nk-f);letter-spacing:.12em;color:var(--nk-bad);margin-bottom:2px}
.nk-err button{position:absolute;top:4px;right:4px;width:22px;height:22px;border:0;background:none;color:#ffd0d8;cursor:pointer;font-size:14px}
@media (max-width:560px){.nk-tb .nk-tx{display:none}.nk-title{font-size:11px;letter-spacing:.1em}}
`;

// ---------------------------------------------------------------- 圖示
const ICON = {
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18h2"/></svg>',
  laptop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="5" width="16" height="10.5" rx="1"/><path d="M2 19h20"/></svg>',
  full: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  on: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>',
  off: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9l5 6M22 9l-5 6"/></svg>',
};

// ---------------------------------------------------------------- 狀態
const K = {};                         // 對外的 NovaKit 物件
const UI = {};                        // DOM 參照
const R = { renderer: null, scene: null, camera: null, ratio: 1, baseRatio: 1, minRatio: 0.55, adaptive: true, autoRender: true };
let built = false, dialogOpen = false, dlg = null, lockWanted = false, locked = false;
const labels = { a: 'A', b: 'B', x: '', y: '' };
const modeFns = new Set();
const AU = { ctx: null, master: null, sfx: null, music: null, noise: null, muted: storage.get('novakit-mute') === '1', last: {}, want: null, track: null, buffers: new Map(), el: null, resume: false };

// ---------------------------------------------------------------- 輸入
const keys = new Set();               // 按住中的 e.code
let keyPend = new Set(), keyNow = new Set();
const pend = { a: false, b: false, x: false, y: false };      // 兩幀之間出現過的按下
const nowP = { a: false, b: false, x: false, y: false };      // 本幀剛按下
const nowR = { a: false, b: false, x: false, y: false };      // 本幀剛放開
const prev = { a: false, b: false, x: false, y: false };
const mouseBtn = { a: false };
const touchBtn = { a: new Set(), b: new Set(), x: new Set(), y: new Set() };
const acc = { x: 0, y: 0, wheel: 0 };
const joy = { id: null, cx: 0, cy: 0, x: 0, y: 0 };
const lookP = { id: null, x: 0, y: 0 };
const gp = { on: false, a: false, b: false, x: false, y: false, mx: 0, my: 0, lx: 0, ly: 0, start: false, startPrev: true };
let mouseDrag = false, lastMX = 0, lastMY = 0;
const KEYBTN = { Space: 'a', KeyJ: 'a', ShiftLeft: 'b', ShiftRight: 'b', KeyK: 'b', KeyE: 'x', KeyL: 'x', KeyQ: 'y', KeyI: 'y' };
const NOSCROLL = new Set(['Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End']);
const SENS = { mouse: 0.005, lock: 0.0025, touch: 0.0075, pad: 0.06 };
const ALIAS = {
  a: 'a', attack: 'a', fire: 'a', shoot: 'a', action: 'a', jump: 'a', space: 'a', primary: 'a', ok: 'a', confirm: 'a', '攻擊': 'a', '射擊': 'a', '跳躍': 'a',
  b: 'b', dash: 'b', boost: 'b', run: 'b', sprint: 'b', shift: 'b', secondary: 'b', dodge: 'b', '衝刺': 'b', '閃避': 'b',
  x: 'x', special: 'x', skill: 'x', ultimate: 'x', e: 'x', '必殺': 'x', '技能': 'x',
  y: 'y', q: 'y', item: 'y', shield: 'y', '道具': 'y',
};
const btnName = n => ALIAS[String(n ?? '').toLowerCase()] || ALIAS[String(n ?? '')] || null;

const input = {
  move: { x: 0, y: 0 },
  look: { x: 0, y: 0 },
  wheel: 0,
  buttons: { a: false, b: false, x: false, y: false },
  pressed(n) { const b = btnName(n); if (b) return nowP[b]; return keyNow.has(n) || keyNow.has(codeOf(n)); },
  released(n) { const b = btnName(n); return b ? nowR[b] : false; },
  held(n) { const b = btnName(n); if (b) return input.buttons[b]; return keys.has(n) || keys.has(codeOf(n)); },
  key(code) { return keys.has(code) || keys.has(codeOf(code)); },
  any() { return BTN.some(n => input.buttons[n] || nowP[n]) || Math.hypot(input.move.x, input.move.y) > 0.15; },
  get pointerLocked() { return locked; },
};
const codeOf = n => { const s = String(n ?? ''); return s.length === 1 ? (/\d/.test(s) ? 'Digit' + s : 'Key' + s.toUpperCase()) : s; };

function heldNow(n) {
  for (const c of keys) if (KEYBTN[c] === n) return true;
  return !!mouseBtn[n] || touchBtn[n].size > 0 || gp[n];
}
function updateInput() {
  if (gp.on) pollPad();
  const I = input;
  if (dialogOpen) {
    I.move.x = I.move.y = I.look.x = I.look.y = I.wheel = acc.x = acc.y = acc.wheel = 0;
    for (const n of BTN) { I.buttons[n] = nowP[n] = nowR[n] = pend[n] = false; }
    keyNow = new Set(); keyPend.clear();
    if (gp.start && !gp.startPrev) closeDialog();
    gp.startPrev = gp.start;
    return;
  }
  const k = c => keys.has(c) ? 1 : 0;
  let mx = Math.min(1, k('KeyD') + k('ArrowRight')) - Math.min(1, k('KeyA') + k('ArrowLeft'));
  let my = Math.min(1, k('KeyW') + k('ArrowUp')) - Math.min(1, k('KeyS') + k('ArrowDown'));
  let l = Math.hypot(mx, my); if (l > 1) { mx /= l; my /= l; }
  mx += joy.x + gp.mx; my += joy.y + gp.my;
  l = Math.hypot(mx, my); if (l > 1) { mx /= l; my /= l; }
  I.move.x = mx; I.move.y = my;
  I.look.x = acc.x + gp.lx; I.look.y = acc.y + gp.ly; I.wheel = acc.wheel;
  acc.x = acc.y = acc.wheel = 0;
  for (const n of BTN) {
    const h = heldNow(n);
    nowP[n] = (h && !prev[n]) || pend[n];
    nowR[n] = !h && prev[n];
    prev[n] = h; I.buttons[n] = h; pend[n] = false;
  }
  keyNow = keyPend; keyPend = new Set();
}
function resetInput(all = true) {
  if (all) keys.clear();
  mouseBtn.a = false; mouseDrag = false;
  for (const n of BTN) { touchBtn[n].clear(); pend[n] = false; prev[n] = heldNow(n); }
  UI.btns?.querySelectorAll('.down').forEach(b => b.classList.remove('down'));
  endJoy(); lookP.id = null; acc.x = acc.y = acc.wheel = 0;
}
function pollPad() {
  let pads = [];
  try { pads = navigator.getGamepads ? [...navigator.getGamepads()] : []; } catch { pads = []; }
  const p = pads.find(g => g && g.connected);
  gp.startPrev = gp.start;
  gp.a = gp.b = gp.x = gp.y = gp.start = false; gp.mx = gp.my = gp.lx = gp.ly = 0;
  if (!p) return;
  const ax = i => { const v = p.axes[i] || 0; return Math.abs(v) < 0.15 ? 0 : v; };
  const bt = i => !!p.buttons[i]?.pressed;
  gp.mx = ax(0) + (bt(15) ? 1 : 0) - (bt(14) ? 1 : 0);
  gp.my = -ax(1) + (bt(12) ? 1 : 0) - (bt(13) ? 1 : 0);
  gp.lx = ax(2) * SENS.pad; gp.ly = ax(3) * SENS.pad;
  gp.a = bt(0) || bt(7); gp.b = bt(1) || bt(6); gp.x = bt(2); gp.y = bt(3);
  gp.start = bt(9) || bt(0);
}
const isUI = t => !!(t && t.closest && t.closest('.nk-i'));
const isTyping = t => !!(t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || '')));

// 鍵盤
W.addEventListener('keydown', e => {
  if (isTyping(e.target)) return;
  const c = e.code || e.key;
  if (NOSCROLL.has(c) || KEYBTN[c]) e.preventDefault();
  if (dialogOpen && (c === 'Enter' || c === 'Space' || c === 'NumpadEnter') && !e.repeat) { closeDialog(); return; }
  if (!keys.has(c)) { keys.add(c); keyPend.add(c); const b = KEYBTN[c]; if (b) pend[b] = true; }
});
W.addEventListener('keyup', e => {
  const c = e.code || e.key;
  keys.delete(c);
  if (NOSCROLL.has(c) || KEYBTN[c]) e.preventDefault();
});
W.addEventListener('blur', () => resetInput());
D.addEventListener('visibilitychange', () => { if (D.hidden) resetInput(); });

// 滑鼠（筆電模式）
W.addEventListener('mousedown', e => {
  if (K.mode === 'mobile' || isUI(e.target) || dialogOpen) return;
  if (e.button === 0) { mouseBtn.a = true; pend.a = true; }
  mouseDrag = true; lastMX = e.clientX; lastMY = e.clientY;
  if (lockWanted && !locked && R.renderer && e.target === R.renderer.domElement) lockPointer();
});
W.addEventListener('mousemove', e => {
  if (K.mode === 'mobile') return;
  if (locked) { acc.x += (e.movementX || 0) * SENS.lock; acc.y += (e.movementY || 0) * SENS.lock; return; }
  if (!mouseDrag) return;
  if (!e.buttons) { mouseDrag = false; mouseBtn.a = false; return; }
  acc.x += (e.clientX - lastMX) * SENS.mouse; acc.y += (e.clientY - lastMY) * SENS.mouse;
  lastMX = e.clientX; lastMY = e.clientY;
});
W.addEventListener('mouseup', e => {
  if (e.button === 0) mouseBtn.a = false;
  if (!e.buttons) mouseDrag = false;
});
W.addEventListener('wheel', e => {
  if (isUI(e.target)) return;
  acc.wheel += e.deltaY;
  if (e.cancelable) e.preventDefault();
}, { passive: false });
W.addEventListener('contextmenu', e => { if (!isTyping(e.target)) e.preventDefault(); });

// 觸控／手機模式的指標：左側 = 搖桿、右側 = 拖曳轉視角（多指可同時）
W.addEventListener('pointerdown', e => {
  if (e.pointerType === 'mouse' && K.mode !== 'mobile') return;   // 筆電模式滑鼠由 mouse* 處理
  if (isUI(e.target) || dialogOpen) return;
  if (K.mode === 'mobile' && joy.id === null && e.clientX < W.innerWidth * 0.45 && e.clientY > 60) startJoy(e);
  else if (lookP.id === null) { lookP.id = e.pointerId; lookP.x = e.clientX; lookP.y = e.clientY; }
});
W.addEventListener('pointermove', e => {
  if (e.pointerId === joy.id) moveJoy(e.clientX, e.clientY);
  else if (e.pointerId === lookP.id) {
    acc.x += (e.clientX - lookP.x) * SENS.touch; acc.y += (e.clientY - lookP.y) * SENS.touch;
    lookP.x = e.clientX; lookP.y = e.clientY;
    UI.look?.classList.add('gone');
  }
});
const ptrUp = e => {
  if (e.pointerId === joy.id) endJoy();
  if (e.pointerId === lookP.id) lookP.id = null;
};
W.addEventListener('pointerup', ptrUp);
W.addEventListener('pointercancel', ptrUp);
// 擋掉頁面捲動、雙指縮放、雙擊放大、iOS 手勢
D.addEventListener('touchmove', e => { if (e.cancelable && !(e.target.closest && e.target.closest('.nk-dlg-t'))) e.preventDefault(); }, { passive: false });
D.addEventListener('dblclick', e => e.preventDefault());
for (const g of ['gesturestart', 'gesturechange', 'gestureend']) D.addEventListener(g, e => e.preventDefault());
W.addEventListener('gamepadconnected', () => { gp.on = true; });

const JR = 64;   // 搖桿半徑（px）
function startJoy(e) {
  joy.id = e.pointerId;
  joy.cx = clamp(e.clientX, JR + 8, W.innerWidth * 0.5);
  joy.cy = clamp(e.clientY, JR + 8, W.innerHeight - JR - 8);
  if (UI.joy) {
    Object.assign(UI.joy.style, { left: joy.cx - JR + 'px', top: joy.cy - JR + 'px', bottom: 'auto' });
    UI.joy.classList.add('on');
  }
  moveJoy(e.clientX, e.clientY);
}
function moveJoy(x, y) {
  let dx = x - joy.cx, dy = y - joy.cy;
  const len = Math.hypot(dx, dy), k = len > JR ? JR / len : 1;
  dx *= k; dy *= k;
  if (UI.knob) UI.knob.style.transform = `translate(${dx}px,${dy}px)`;
  let vx = dx / JR, vy = -dy / JR;
  const m = Math.hypot(vx, vy);
  if (m < 0.12) vx = vy = 0;
  else { const s = Math.min(1, (m - 0.12) / 0.88) / m; vx *= s; vy *= s; }
  joy.x = vx; joy.y = vy;
}
function endJoy() {
  joy.id = null; joy.x = joy.y = 0;
  if (UI.joy) { UI.joy.style.left = UI.joy.style.top = UI.joy.style.bottom = ''; UI.joy.classList.remove('on'); }
  if (UI.knob) UI.knob.style.transform = '';
}
function lockPointer() {
  const el = R.renderer?.domElement || D.body;
  try { const p = el.requestPointerLock?.(); if (p && p.catch) p.catch(() => {}); } catch { /* 沙箱不允許 */ }
}
D.addEventListener('pointerlockchange', () => { locked = !!D.pointerLockElement; });
D.addEventListener('pointerlockerror', () => { locked = false; });

// ---------------------------------------------------------------- 模式
function detectMode() {
  const touch = (navigator.maxTouchPoints || 0) > 0 || 'ontouchstart' in W;
  let coarse = false;
  try { coarse = W.matchMedia('(pointer: coarse)').matches; } catch { /* 舊瀏覽器 */ }
  return touch && (coarse || W.innerWidth < 900) ? 'mobile' : 'desktop';
}
function savedMode() { const m = storage.get('novakit-mode'); return m === 'mobile' || m === 'desktop' ? m : null; }
// 網址指定（預覽框用）：…html#mode=mobile ／ ?mode=desktop（也接受 phone／laptop）
function urlMode() {
  let s = '';
  try { s = (W.location.hash || '') + '&' + (W.location.search || ''); } catch { /* 忽略 */ }
  const m = /(?:^|[#?&])(?:nk-?|novakit-)?mode=(mobile|phone|touch|desktop|laptop|pc)\b/i.exec(s);
  return m ? (/^(mobile|phone|touch)$/i.test(m[1]) ? 'mobile' : 'desktop') : null;
}
let override = urlMode();             // 網址或使用者手動切換過：視窗縮放時不再自動判斷
K.mode = override || savedMode() || detectMode();
function setMode(m, persist = true) {
  m = m === 'mobile' ? 'mobile' : 'desktop';
  if (persist) { storage.set('novakit-mode', m); override = m; }
  if (m === K.mode) { syncModeUI(); return K; }
  K.mode = m;
  resetInput(false);
  syncModeUI();
  showHelp();
  for (const fn of modeFns) { try { fn(m); } catch (e) { reportError(e?.message || e, e?.stack); } }
  return K;
}
function syncModeUI() {
  if (!built) return;
  UI.root.classList.toggle('nk-mobile', K.mode === 'mobile');
  UI.seg.querySelectorAll('[data-m]').forEach(b => b.classList.toggle('on', b.dataset.m === K.mode));
  syncButtons();
}
W.addEventListener('resize', () => {
  if (!override && !savedMode()) { const m = detectMode(); if (m !== K.mode) setMode(m, false); }
  if (R.renderer) fit();
});
W.addEventListener('hashchange', () => { const m = urlMode(); if (m) { override = m; setMode(m, false); } });

// ---------------------------------------------------------------- UI 建立
function h(tag, cls, html) { const e = D.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function build() {
  if (built) return;
  if (!D.body) return;
  built = true;
  const st = h('style'); st.id = 'novakit-style'; st.textContent = CSS; D.head.appendChild(st);
  if (!D.querySelector('meta[name=viewport]')) {
    const m = h('meta'); m.name = 'viewport'; m.content = 'width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover';
    D.head.appendChild(m);
  }
  const root = h('div', 'nk-root');
  root.innerHTML = `
    <div class="nk-top">
      <div class="nk-title"><i></i><span></span></div>
      <div class="nk-tools nk-i">
        <button class="nk-tb" data-act="help" title="操作說明">?</button>
        <button class="nk-tb" data-act="mute" title="音效開關"></button>
        <div class="nk-seg" title="切換操控模式">
          <button class="nk-tb" data-m="mobile">${ICON.phone}<span class="nk-tx">手機</span></button>
          <button class="nk-tb" data-m="desktop">${ICON.laptop}<span class="nk-tx">筆電</span></button>
        </div>
        <button class="nk-tb" data-act="fs" title="全螢幕">${ICON.full}</button>
      </div>
    </div>
    <div class="nk-side"><div class="nk-bars"></div><div class="nk-hud"></div></div>
    <div class="nk-toasts"></div>
    <div class="nk-help"></div>
    <div class="nk-look">⟲ 右側滑動轉視角</div>
    <div class="nk-joy"><div class="nk-knob"></div></div>
    <div class="nk-btns">${BTN.map(n => `<button class="nk-ab nk-i" data-b="${n}"></button>`).join('')}</div>
    <div class="nk-dlg-wrap nk-i"><div class="nk-dlg"><h2></h2><div class="nk-dlg-t"></div><button class="nk-dlg-b"></button></div></div>
    <div class="nk-err"></div>`;
  D.body.appendChild(root);
  const q = s => root.querySelector(s);
  Object.assign(UI, {
    root, title: q('.nk-title span'), seg: q('.nk-seg'), mute: q('[data-act=mute]'), fs: q('[data-act=fs]'),
    bars: q('.nk-bars'), hud: q('.nk-hud'), toasts: q('.nk-toasts'), help: q('.nk-help'), look: q('.nk-look'),
    joy: q('.nk-joy'), knob: q('.nk-knob'), btns: q('.nk-btns'),
    dlgWrap: q('.nk-dlg-wrap'), dlg: q('.nk-dlg'), dlgTitle: q('.nk-dlg h2'), dlgText: q('.nk-dlg-t'), dlgBtn: q('.nk-dlg-b'), err: q('.nk-err'),
  });
  // 工具列：不搶焦點（避免空白鍵再次觸發按鈕）
  root.querySelectorAll('button').forEach(b => { b.tabIndex = -1; b.addEventListener('mousedown', e => e.preventDefault()); });
  UI.seg.addEventListener('click', e => {
    const b = e.target.closest('[data-m]'); if (!b) return;
    const was = K.mode; setMode(b.dataset.m);
    if (was !== K.mode) toast(K.mode === 'mobile' ? '已切換為手機模式' : '已切換為筆電模式', 1400);
  });
  q('[data-act=help]').addEventListener('click', () => showHelp(true));
  UI.mute.addEventListener('click', () => setMuted(!AU.muted));
  const fsOK = D.fullscreenEnabled || D.webkitFullscreenEnabled;
  if (!fsOK) UI.fs.style.display = 'none';
  UI.fs.addEventListener('click', toggleFullscreen);
  UI.dlgBtn.addEventListener('click', () => closeDialog());
  // 手機動作鍵：每顆按鈕各自追蹤手指，可與搖桿、視角同時操作
  UI.btns.querySelectorAll('.nk-ab').forEach(el => {
    const n = el.dataset.b;
    el.addEventListener('pointerdown', e => {
      e.preventDefault(); e.stopPropagation();
      if (dialogOpen) return;
      try { el.setPointerCapture(e.pointerId); } catch { /* 忽略 */ }
      touchBtn[n].add(e.pointerId); pend[n] = true; el.classList.add('down');
      // 第一次觸控前沒有「使用者啟用」，這時呼叫 vibrate 會在主控台留下錯誤
      try { if (navigator.userActivation?.hasBeenActive) navigator.vibrate?.(8); } catch { /* 忽略 */ }
    });
    const up = e => { touchBtn[n].delete(e.pointerId); if (!touchBtn[n].size) el.classList.remove('down'); };
    el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up); el.addEventListener('lostpointercapture', up);
  });
  syncMute(); syncModeUI(); setTitle(K._title);
  setTimeout(() => showHelp(), 400);
}
function setTitle(t) {
  K._title = String(t || (K.meta || {}).title || D.title || 'N.O.V.A. GAME');
  if (!D.title) D.title = K._title;
  if (UI.title) UI.title.textContent = K._title;
  return K;
}
function syncButtons() {
  if (!UI.btns) return;
  UI.btns.querySelectorAll('.nk-ab').forEach(el => {
    const t = labels[el.dataset.b] || '';
    el.textContent = t;
    el.classList.toggle('show', !!t);
    el.style.fontSize = t.length <= 2 ? '' : t.length <= 4 ? '13px' : '11px';
  });
}
function setButtons(map = {}) {
  if (Array.isArray(map)) map = Object.fromEntries(map.slice(0, 4).map((v, i) => [BTN[i], v]));
  if (!map || typeof map !== 'object') return K;
  const next = { a: '', b: '', x: '', y: '' };
  for (const [k, v] of Object.entries(map)) { const b = btnName(k); if (b && v) next[b] = String(v).slice(0, 8); }
  if (!BTN.some(n => next[n])) next.a = 'A';
  Object.assign(labels, next);
  syncButtons(); showHelp();
  return K;
}
let helpTimer = 0;
function helpText() {
  const acts = BTN.filter(n => labels[n]);
  if (K.mode === 'mobile') return `左側搖桿 移動　右側滑動 轉視角${acts.length ? '　按鈕 ' + acts.map(n => labels[n]).join('／') : ''}`;
  const key = { a: '空白鍵／左鍵', b: 'Shift', x: 'E', y: 'Q' };
  return 'WASD／方向鍵 移動　拖曳滑鼠 轉視角' + acts.map(n => `　${key[n]} ${labels[n]}`).join('');
}
let helpPending = false;
function showHelp(force) {
  if (!built || (!force && K._noHelp)) return;
  if (dialogOpen) { helpPending = true; return; }   // 對話框開著時先不蓋在上面，關掉後再顯示
  helpPending = false;
  UI.help.textContent = helpText();
  UI.help.classList.add('show');
  clearTimeout(helpTimer);
  helpTimer = setTimeout(() => UI.help.classList.remove('show'), force ? 5000 : 6500);
}
function toggleFullscreen() {
  const el = D.documentElement, fsEl = D.fullscreenElement || D.webkitFullscreenElement;
  try {
    const p = fsEl ? (D.exitFullscreen || D.webkitExitFullscreen).call(D) : (el.requestFullscreen || el.webkitRequestFullscreen).call(el, { navigationUI: 'hide' });
    if (p && p.catch) p.catch(() => toast('此環境不支援全螢幕', 1500));
  } catch { toast('此環境不支援全螢幕', 1500); }
}
function showError(msg) {
  if (!D.body) { W.addEventListener('DOMContentLoaded', () => showError(msg), { once: true }); return; }
  build();
  const box = h('div', 'nk-i', `<b>⚠ 遊戲發生錯誤</b>${esc(msg)}<button title="關閉">✕</button>`);
  box.querySelector('button').onclick = () => box.remove();
  UI.err.appendChild(box);
  while (UI.err.children.length > 3) UI.err.firstChild.remove();
}

// ---------------------------------------------------------------- HUD／提示／對話框
let hudLast = null;
function hud(content) {
  build(); if (!UI.hud) return K;
  let html;
  if (content == null || content === '' || content === false) html = '';
  else if (typeof content === 'object') html = Object.entries(content).map(([k, v]) => `<b>${esc(k)}</b> ${esc(v)}`).join('\n');
  else html = String(content);
  if (html !== hudLast) { hudLast = html; UI.hud.innerHTML = html; UI.hud.style.display = html ? 'block' : 'none'; }
  return K;
}
const bars = new Map();
const BAR_COLORS = ['#38e6ff', '#8a5cff', '#ffc46b', '#5dff9e', '#ff4d6d'];
function bar(name, value, o = {}) {
  build(); if (!UI.bars) return K;
  name = String(name ?? 'bar');
  let b = bars.get(name);
  if (value === null || value === false) { b?.el.remove(); bars.delete(name); return K; }
  if (typeof o === 'string') o = { label: o };
  if (!b) {
    const el = h('div', 'nk-bar', '<div class="nk-bar-h"><b></b><span></span></div><div class="nk-bar-t"><div class="nk-bar-f"></div></div>');
    b = { el, lab: el.querySelector('b'), val: el.querySelector('span'), fill: el.querySelector('.nk-bar-f'), v: -1 };
    const auto = /hp|health|life|血|生命/i.test(name) ? '#5dff9e' : /boss|敵|王/i.test(name) ? '#ff4d6d' : BAR_COLORS[bars.size % BAR_COLORS.length];
    el.style.setProperty('--nk-bc', auto);
    b.lab.textContent = name;
    UI.bars.appendChild(el); bars.set(name, b);
  }
  let v = Number(value);
  if (!Number.isFinite(v)) v = 0;
  if (Number(o.max) > 0) v /= Number(o.max);
  else if (v > 1.0001) v /= 100;
  v = clamp(v, 0, 1);
  if (o.label !== undefined && b.label !== String(o.label)) b.lab.textContent = b.label = String(o.label);
  if (o.color && b.color !== o.color) { b.color = o.color; b.el.style.setProperty('--nk-bc', o.color); }
  if (Math.abs(v - b.v) > 0.0015) {
    b.v = v; b.fill.style.transform = `scaleX(${v})`;
    b.el.classList.toggle('nk-low', v < 0.25 && o.blink !== false);
  }
  const vt = String(o.text ?? Math.round(v * 100) + '%');
  if (b.text !== vt) b.val.textContent = b.text = vt;
  return K;
}
function toast(text, ms = 1800) {
  build(); if (!UI.toasts) return K;
  const t = h('div', 'nk-toast'); t.textContent = String(text ?? '');
  UI.toasts.appendChild(t);
  while (UI.toasts.children.length > 3) UI.toasts.firstChild.remove();
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 450); }, Math.max(300, Number(ms) || 1800));
  return K;
}
function dialog(o = {}, cls = '') {
  build();
  if (typeof o === 'string') o = { text: o };
  const title = o.title ?? '', text = o.text ?? o.message ?? '', button = o.button ?? '繼續';
  if (!UI.dlg) return Promise.resolve(true);
  const key = cls + '|' + title;
  // 同一個對話框重複呼叫（例如每幀都呼叫 gameOver）：只更新內容，回傳同一個 Promise
  const html = String(text).replace(/\n/g, '<br>');
  if (UI.dlgHtml !== html) { UI.dlgHtml = html; UI.dlgText.innerHTML = html; }
  if (UI.dlgBtn.textContent !== String(button)) UI.dlgBtn.textContent = button;
  if (dlg && dlg.key === key) return gate(dlg);
  UI.dlg.className = 'nk-dlg ' + cls;
  UI.dlgTitle.textContent = title; UI.dlgTitle.style.display = title ? '' : 'none';
  UI.dlgWrap.classList.add('show');
  if (UI.help.classList.contains('show')) { UI.help.classList.remove('show'); helpPending = true; }
  if (!dlg) dlg = { res: [], at: performance.now() };
  dlg.key = key;
  dialogOpen = K.dialogOpen = true;
  if (locked) try { D.exitPointerLock?.(); } catch { /* 忽略 */ }
  return gate(dlg);
}
// 小模型常在每一幀都呼叫 gameOver(...).then(reset)：同一個對話框只讓第一個 .then 生效，避免重開時 reset 跑幾百次。
// （await 不受影響；已經有人接手後的重複呼叫拿到永遠等待的 Promise）
const PENDING = new Promise(() => {});
function gate(cur) {
  if (cur.hooked || cur.res.length > 200) return PENDING;
  const p = new Promise(r => cur.res.push(r)), then = p.then;
  p.then = function (f, r) {
    if (typeof f === 'function') { if (cur.hooked) return PENDING; cur.hooked = true; }
    return then.call(this, f, r);
  };
  return p;
}
function closeDialog() {
  if (!dlg || performance.now() - dlg.at < 600) return;   // 防止連打攻擊鍵誤關
  const res = dlg.res; dlg = null;
  dialogOpen = K.dialogOpen = false;
  UI.dlgWrap?.classList.remove('show');
  resetInput(false);
  unlockAudio();
  if (helpPending) showHelp();
  for (const r of res) { try { r(true); } catch { /* 忽略 */ } }
}
const gameOver = (o = {}) => dialog({ title: o.title ?? 'GAME OVER', text: o.text ?? (o.score != null ? `分數 ${o.score}` : ''), button: o.button ?? '重新開始' }, 'nk-over');
const victory = (o = {}) => dialog({ title: o.title ?? 'MISSION COMPLETE', text: o.text ?? (o.score != null ? `分數 ${o.score}` : ''), button: o.button ?? '再玩一次' }, 'nk-win');

// ---------------------------------------------------------------- 渲染器與主迴圈
function fit(renderer = R.renderer, camera = R.camera) {
  const w = Math.max(1, W.innerWidth), hh = Math.max(1, W.innerHeight);
  try { renderer?.setSize?.(w, hh); } catch { /* 忽略 */ }
  if (camera?.isPerspectiveCamera) { camera.aspect = w / hh; camera.updateProjectionMatrix(); }
  else if (camera?.isOrthographicCamera) {
    const half = (camera.top - camera.bottom) / 2 || 10;
    camera.left = -half * w / hh; camera.right = half * w / hh; camera.updateProjectionMatrix();
  }
  return K;
}
// 偵測顯示卡：軟體繪圖（SwiftShader、llvmpipe…）要關 MSAA、降解析度
let gpuInfo = null;
function detectGPU() {
  if (gpuInfo) return gpuInfo;
  let name = '';
  try {
    const c = D.createElement('canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      name = String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || '');
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  } catch { /* 忽略 */ }
  const software = /swiftshader|llvmpipe|softpipe|software|basic render|microsoft basic/i.test(name);
  gpuInfo = { renderer: name, software, lowPower: software };
  return gpuInfo;
}
let rendered = false;
function createRenderer(o = {}) {
  init();
  const mobile = K.mode === 'mobile', gpu = detectGPU(), dpr = W.devicePixelRatio || 1;
  if (!R.renderer) {
    let r;
    // MSAA 只在一般桌機顯示卡、低 DPR 時開（高 DPR 本身就夠細，軟體繪圖開了會慢一倍）
    const aa = gpu.software ? false : (o.antialias ?? (!mobile && dpr < 2));
    try { r = new THREE.WebGLRenderer({ antialias: aa, powerPreference: 'high-performance', alpha: !!o.alpha }); }
    catch (e) { reportError('此裝置或瀏覽器無法啟用 WebGL：' + (e?.message || e)); throw e; }
    r.domElement.className = 'nk-canvas';
    r.domElement.addEventListener('webglcontextlost', e => { e.preventDefault(); toast('繪圖環境重置中…', 1500); });
    D.body.prepend(r.domElement);
    const render = r.render.bind(r);
    r.render = (s, c) => { rendered = true; return render(s, c); };
    R.renderer = r;
  }
  R.baseRatio = Math.min(dpr, Number(o.maxPixelRatio) || (gpu.software ? 1 : mobile ? 1.5 : 2));
  R.ratio = gpu.software ? Math.min(R.baseRatio, 0.75) : R.baseRatio;
  R.minRatio = Math.min(R.baseRatio, Number(o.minPixelRatio) || (gpu.software ? 0.45 : 0.7));   // 真的顯示卡不要糊到看不清
  R.adaptive = o.adaptive !== false;
  R.renderer.setPixelRatio(R.ratio);
  // 重複呼叫時沿用同一個 WebGL 畫布（避免耗盡 context），但給新的 scene 與 camera
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(o.background ?? 0x03070f);
  const camera = new THREE.PerspectiveCamera(o.fov ?? 60, W.innerWidth / Math.max(1, W.innerHeight), o.near ?? 0.1, o.far ?? 2000);
  camera.position.set(0, 4, 10); camera.lookAt(0, 1, 0);
  if (o.lights !== false) {
    const hemi = new THREE.HemisphereLight(0xcfefff, 0x201436, 0.9); hemi.name = 'novakit-hemi';
    const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(6, 12, 8); sun.name = 'novakit-sun';
    scene.add(hemi, sun);
  }
  R.scene = scene; R.camera = camera;
  K.renderer = R.renderer; K.scene = scene; K.camera = camera;
  fit();
  return { renderer: R.renderer, scene, camera };
}
let loopFn = null, loopT = 0, last = 0, adWin = 0, adFrames = 0, adRounds = 0, adUp = 0;
function loop(fn) {
  init();
  loopFn = typeof fn === 'function' ? fn : null;
  loopT = 0;
  const me = loopFn;
  return { stop() { if (loopFn === me) loopFn = null; } };
}
function tick(now) {
  requestAnimationFrame(tick);
  if (D.hidden) { last = 0; return; }
  const raw = last ? (now - last) / 1000 : 1 / 60;
  last = now;
  const dt = Math.min(0.05, Math.max(0, raw));
  updateInput();
  if (raw > 0 && raw < 1) K.fps = K.fps ? K.fps * 0.94 + (1 / raw) * 0.06 : 1 / raw;
  if (loopFn) {
    loopT += dt; rendered = false;
    try { loopFn(dt, loopT); } catch (e) {
      const msg = e?.message ? `${e.name || 'Error'}: ${e.message}` : String(e);
      if (!seenErr.has(cleanText(msg).slice(0, 600))) console.error(e);
      reportError(msg, e?.stack);
    }
    if (!rendered && R.autoRender && R.renderer && R.scene && R.camera) { try { R.renderer.render(R.scene, R.camera); } catch { /* 忽略 */ } }
  }
  adapt(raw);
}
// 自動畫質：幀率太低就降解析度（手機、內顯、軟體繪圖都能順）
function adapt(raw) {
  const r = R.renderer;
  if (!r || !R.adaptive || !loopFn || raw <= 0 || raw > 0.5) return;
  adWin += raw; adFrames++;
  if (adWin < 1.2) return;
  const fps = adFrames / adWin; adWin = 0; adFrames = 0;
  if (++adRounds < 2) return;   // 第一輪多半在編譯著色器，不算
  let pr = R.ratio;
  // 填色成本約與 pr² 成正比：依實際 fps 一次調到位，減少 setSize（重配緩衝區）的次數
  if (fps < 28 && pr > R.minRatio + 0.01) { pr = Math.max(R.minRatio, pr * Math.min(0.9, Math.max(0.6, Math.sqrt(fps / 40)))); adUp = 0; }
  else if (fps > 55 && pr < R.baseRatio - 0.01 && ++adUp >= 3) { pr = Math.min(R.baseRatio, pr * 1.12); adUp = 0; }
  if (Math.abs(pr - R.ratio) > 0.01) { R.ratio = pr; r.setPixelRatio(pr); fit(); }
}
requestAnimationFrame(tick);

// ---------------------------------------------------------------- 素材
const assetMap = () => { const a = W.NOVA_ASSETS; return a && typeof a === 'object' ? a : {}; };
const assetSrc = v => typeof v === 'string' ? v : (v && typeof v === 'object' ? String(v.url || v.data || v.src || '') : '');
const norm = s => String(s).toLowerCase().replace(/\.(glb|gltf|png|jpe?g|webp|gif|wav|mp3|ogg|m4a)$/, '').replace(/[^a-z0-9㐀-鿿]/g, '');
const EXT = { glb: 'model', gltf: 'model', png: 'image', jpg: 'image', jpeg: 'image', webp: 'image', gif: 'image', wav: 'audio', mp3: 'audio', ogg: 'audio', m4a: 'audio' };
const KIND_ZH = { model: '3D 模型', image: '圖片', audio: '音樂', '': '' };
const kindMemo = new Map();
function kindOf(src) {
  if (!src) return '';
  if (!src.startsWith('data:')) { const e = (src.split(/[?#]/)[0].match(/\.([a-z0-9]+)$/i) || [])[1]; return EXT[(e || '').toLowerCase()] || ''; }
  const key = src.slice(0, 64);
  if (kindMemo.has(key)) return kindMemo.get(key);
  const c = src.indexOf(','), mime = src.slice(5, c).split(';')[0].toLowerCase();
  let k = '';
  if (/gltf|glb/.test(mime)) k = 'model';
  else if (mime.startsWith('image/')) k = 'image';
  else if (mime.startsWith('audio/')) k = 'audio';
  else if (mime.startsWith('video/')) k = 'video';
  else {
    try {   // application/octet-stream 等：看檔頭
      const hd = /;base64/i.test(src.slice(0, c)) ? atob(src.slice(c + 1, c + 25)) : decodeURIComponent(src.slice(c + 1, c + 40));
      const b0 = hd.charCodeAt(0), b1 = hd.charCodeAt(1), riff = hd.startsWith('RIFF') ? hd.slice(8, 12) : '';
      if (hd.startsWith('glTF') || hd.trimStart().startsWith('{')) k = 'model';
      else if (hd.startsWith('\x89PNG') || (b0 === 0xFF && b1 === 0xD8) || hd.startsWith('GIF8') || riff === 'WEBP') k = 'image';
      else if (riff === 'WAVE' || hd.startsWith('ID3') || hd.startsWith('OggS') || hd.startsWith('fLaC') || (b0 === 0xFF && (b1 & 0xE0) === 0xE0)) k = 'audio';
    } catch { /* 忽略 */ }
  }
  kindMemo.set(key, k);
  return k;
}
function assetNames(kind) {
  const A = assetMap();
  return Object.keys(A).filter(k => { const s = assetSrc(A[k]); if (!s) return false; const t = kindOf(s); return !kind || t === kind || t === ''; });
}
function findAsset(name, kind) {
  if (typeof name === 'string' && /^(data:|blob:|https?:|\.{0,2}\/)/i.test(name)) return name;
  if (name && typeof name === 'object') return findAsset(name.name ?? name.url, kind);
  const A = assetMap(), keys = assetNames(kind);
  if (name == null || name === '') return keys.length ? assetSrc(A[keys[0]]) : null;
  const n = norm(name);
  const k = keys.find(k => k === name) || (n && (keys.find(k => norm(k) === n) ||
    keys.find(k => { const m = norm(k); return m.length > 1 && n.length > 1 && (m.includes(n) || n.includes(m)); })));
  return k ? assetSrc(A[k]) : null;
}
const missing = (name, kind) => new Error(`NovaKit：找不到${KIND_ZH[kind] || ''}素材「${name}」（可用：${assetNames(kind).join(', ') || '無'}）`);
function dataToBuffer(src) {
  const c = src.indexOf(','), meta = src.slice(5, c), body = src.slice(c + 1);
  if (/;base64/i.test(meta)) {
    const bin = atob(body.replace(/\s+/g, '')), u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u.buffer;
  }
  return new TextEncoder().encode(decodeURIComponent(body)).buffer;
}
const gltfLoader = new GLTFLoader();
const modelCache = new Map();
function parseModel(src) {
  return new Promise((res, rej) => {
    try {
      if (src.startsWith('data:')) gltfLoader.parse(dataToBuffer(src), '', res, rej);
      else gltfLoader.load(src, res, undefined, rej);
    } catch (e) { rej(e); }
  }).then(g => {
    const root = g.scene || g.scenes?.[0];
    if (!root) throw new Error('模型內容是空的');
    root.traverse(o => {
      if (!o.isMesh) return;
      const geo = o.geometry, old = Array.isArray(o.material) ? o.material[0] : o.material;
      if (geo && !geo.attributes.normal) geo.computeVertexNormals();
      const vc = !!geo?.attributes.color;
      o.material = new THREE.MeshStandardMaterial({
        color: vc ? 0xffffff : (old?.color ? old.color.clone() : new THREE.Color(0xb8c4d0)),
        vertexColors: vc, map: old?.map || null, roughness: 0.6, metalness: 0.15, side: THREE.DoubleSide,
        transparent: !!old?.transparent, opacity: old?.opacity ?? 1,
      });
    });
    root.userData.animations = g.animations || [];
    return root;
  });
}
function instModel(tpl, name, o) {
  const m = tpl.clone(true);
  m.traverse(x => { if (x.isMesh && x.material) x.material = Array.isArray(x.material) ? x.material.map(mm => mm.clone()) : x.material.clone(); });
  const box = new THREE.Box3().setFromObject(m), size = box.getSize(new THREE.Vector3());
  const target = Number(o.height) > 0 ? Number(o.height) : 1;
  const maxDim = Math.max(size.x, size.y, size.z, 1e-6);
  const s = (o.fit === 'max' || size.y < maxDim * 0.08) ? target / maxDim : target / Math.max(size.y, 1e-6);
  m.scale.multiplyScalar(s);
  box.setFromObject(m);
  const c = box.getCenter(new THREE.Vector3());
  m.position.x -= c.x; m.position.z -= c.z; m.position.y -= box.min.y;
  const g = new THREE.Group();
  g.name = String(name ?? 'model'); g.add(m);
  g.userData.size = box.getSize(new THREE.Vector3());
  g.userData.model = m; g.userData.animations = tpl.userData.animations;
  return g;
}
function fallbackModel(name, o) {
  const f = o.fallback;
  let obj = typeof f === 'function' ? f() : (f && f.isObject3D ? f : null);
  if (!obj || !obj.isObject3D) {
    const hgt = Number(o.height) > 0 ? Number(o.height) : 1;
    obj = new THREE.Mesh(new THREE.BoxGeometry(hgt * 0.6, hgt, hgt * 0.6), new THREE.MeshStandardMaterial({ color: 0x38e6ff, roughness: 0.4, metalness: 0.3 }));
    obj.position.y = hgt / 2;
  }
  const g = new THREE.Group(); g.name = String(name ?? 'model'); g.add(obj); g.userData.fallback = true;
  return g;
}
function loadModel(name, o = {}) {
  if (typeof o === 'number') o = { height: o };
  o = o || {};
  const src = findAsset(name, 'model');
  if (!src) return o.fallback ? Promise.resolve(fallbackModel(name, o)) : Promise.reject(missing(name, 'model'));
  let p = modelCache.get(src);
  if (!p) { p = parseModel(src); modelCache.set(src, p); p.catch(() => modelCache.delete(src)); }
  return p.then(tpl => instModel(tpl, name, o), e => {
    if (o.fallback) return fallbackModel(name, o);
    throw new Error(`NovaKit：模型「${name}」載入失敗：${e?.message || e}`);
  });
}
const texCache = new Map();
function loadTexture(name, o = {}) {
  const src = findAsset(name, 'image');
  if (!src) return Promise.reject(missing(name, 'image'));
  let p = texCache.get(src);
  if (!p) {
    p = new Promise((res, rej) => new THREE.TextureLoader().load(src, t => {
      t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; res(t);
    }, undefined, () => rej(new Error(`NovaKit：圖片「${name}」載入失敗`))));
    texCache.set(src, p); p.catch(() => texCache.delete(src));
  }
  return o.clone ? p.then(t => { const c = t.clone(); c.needsUpdate = true; return c; }) : p;
}

// ---------------------------------------------------------------- 音效（WebAudio 合成）與音樂
function audio(create) {
  if (AU.ctx || !create) return AU.ctx;
  const AC = W.AudioContext || W.webkitAudioContext;
  if (!AC) return null;
  try {
    const c = new AC(), comp = c.createDynamicsCompressor();
    comp.threshold.value = -12; comp.ratio.value = 5;
    AU.master = c.createGain(); AU.master.gain.value = AU.muted ? 0 : 0.9;
    AU.sfx = c.createGain(); AU.sfx.gain.value = 0.7;
    AU.music = c.createGain();
    AU.sfx.connect(AU.master); AU.music.connect(AU.master); AU.master.connect(comp); comp.connect(c.destination);
    const n = c.sampleRate * 2, buf = c.createBuffer(1, n, c.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    AU.noise = buf; AU.ctx = c;
    c.onstatechange = () => { if (c.state === 'running') flushMusic(); };
    return c;
  } catch { return null; }
}
function unlockAudio() {
  const c = audio(true);
  if (!c) return;
  if (c.state !== 'running' && !D.hidden) c.resume().then(flushMusic, () => {});
  else flushMusic();
}
for (const ev of ['pointerup', 'touchend', 'click', 'keydown', 'mousedown']) W.addEventListener(ev, unlockAudio, true);
D.addEventListener('visibilitychange', () => {
  const c = AU.ctx; if (!c) return;
  if (D.hidden) { AU.resume = c.state === 'running'; if (AU.resume) c.suspend().catch(() => {}); AU.el?.pause(); }
  else { if (AU.resume) c.resume().catch(() => {}); if (AU.el && AU.want) AU.el.play().catch(() => {}); }
});
function env(g, t, vol, dur) {
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0002, vol), t + 0.006);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
}
function tone(type, f0, f1, dur, vol, delay = 0) {
  const c = AU.ctx, t = c.currentTime + delay, o = c.createOscillator(), g = c.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f0, t);
  if (f1 !== f0) o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
  env(g, t, vol, dur);
  o.connect(g); g.connect(AU.sfx); o.start(t); o.stop(t + dur + 0.03);
}
function noise(dur, vol, f0, f1, delay = 0, type = 'lowpass') {
  const c = AU.ctx, t = c.currentTime + delay, s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
  s.buffer = AU.noise; f.type = type; f.Q.value = 0.9;
  f.frequency.setValueAtTime(f0, t); f.frequency.exponentialRampToValueAtTime(Math.max(30, f1), t + dur);
  env(g, t, vol, dur);
  s.connect(f); f.connect(g); g.connect(AU.sfx); s.start(t, Math.random()); s.stop(t + dur + 0.03);
}
const SFX_ALIAS = { laser: 'shoot', fire: 'shoot', shot: 'shoot', beam: 'shoot', attack: 'shoot', damage: 'hit', hurt: 'hit', impact: 'hit', boom: 'explode', explosion: 'explode', die: 'explode', death: 'explode', coin: 'pickup', collect: 'pickup', item: 'pickup', heal: 'pickup', warning: 'alarm', alert: 'alarm', siren: 'alarm', powerup: 'power', charge: 'power', levelup: 'power', dash: 'jump', boost: 'jump' };
function sfx(kind = 'blip', o = {}) {
  const c = AU.ctx;
  if (!c || c.state !== 'running' || AU.muted) return K;
  kind = String(kind).toLowerCase(); kind = SFX_ALIAS[kind] || kind;
  const tn = performance.now();
  if (tn - (AU.last[kind] || 0) < 45) return K;
  AU.last[kind] = tn;
  const v = clamp(o.volume ?? 1, 0, 2), p = clamp(o.pitch ?? 1, 0.25, 4);
  try {
    switch (kind) {
      case 'shoot': tone('square', 900 * p, 240 * p, 0.12, 0.16 * v); tone('sawtooth', 1500 * p, 500 * p, 0.07, 0.05 * v); break;
      case 'hit': noise(0.12, 0.35 * v, 3200, 500); tone('triangle', 190 * p, 70 * p, 0.12, 0.3 * v); break;
      case 'explode': noise(0.75, 0.6 * v, 1800, 70); tone('sine', 95 * p, 32 * p, 0.6, 0.5 * v); break;
      case 'jump': tone('square', 240 * p, 760 * p, 0.17, 0.12 * v); noise(0.18, 0.12 * v, 800, 5000, 0, 'bandpass'); break;
      case 'pickup': tone('triangle', 880 * p, 880 * p, 0.08, 0.22 * v); tone('triangle', 1320 * p, 1320 * p, 0.14, 0.22 * v, 0.07); break;
      case 'alarm': for (let i = 0; i < 2; i++) { tone('square', 760 * p, 760 * p, 0.17, 0.12 * v, i * 0.36); tone('square', 540 * p, 540 * p, 0.17, 0.12 * v, i * 0.36 + 0.18); } break;
      case 'power': tone('sawtooth', 110 * p, 880 * p, 0.55, 0.12 * v); tone('sine', 220 * p, 1760 * p, 0.55, 0.12 * v); noise(0.5, 0.1 * v, 400, 6000, 0, 'bandpass'); break;
      default: tone('sine', 660 * p, 660 * p, 0.08, 0.15 * v);
    }
  } catch { /* 忽略 */ }
  return K;
}
function playMusic(name, o = {}) {
  if (typeof o === 'number') o = { volume: o };
  const src = findAsset(name, 'audio');
  if (!src) { if (name) console.warn(missing(name, 'audio').message); return Promise.resolve(false); }
  AU.want = { src, volume: clamp(o?.volume ?? 0.5, 0, 1), loop: o?.loop !== false };
  return flushMusic();
}
async function flushMusic() {
  const w = AU.want, c = AU.ctx;
  if (!w || !c || c.state !== 'running') return false;
  if (AU.track && AU.track.src === w.src) { if (AU.track.gain) AU.track.gain.gain.value = w.volume; if (AU.el) AU.el.volume = w.volume; return true; }
  stopTrack();
  const tr = AU.track = { src: w.src, node: null, gain: null };
  try {
    let buf = AU.buffers.get(w.src);
    if (!buf) {
      const ab = w.src.startsWith('data:') ? dataToBuffer(w.src) : await (await fetch(w.src)).arrayBuffer();
      buf = await new Promise((res, rej) => { const pr = c.decodeAudioData(ab, res, rej); if (pr && pr.then) pr.then(res, rej); });
      AU.buffers.set(w.src, buf);
    }
    if (AU.track !== tr) return false;
    const s = c.createBufferSource(), g = c.createGain();
    s.buffer = buf; s.loop = w.loop; g.gain.value = w.volume;
    s.connect(g); g.connect(AU.music); s.start();
    tr.node = s; tr.gain = g;
    return true;
  } catch {
    if (AU.track !== tr) return false;
    try {   // 解碼失敗：退回 <audio>
      const a = new Audio(w.src); a.loop = w.loop; a.volume = w.volume; a.muted = AU.muted;
      AU.el = a; await a.play(); return true;
    } catch { return false; }
  }
}
function stopTrack() {
  try { AU.track?.node?.stop(); } catch { /* 忽略 */ }
  if (AU.el) { try { AU.el.pause(); } catch { /* 忽略 */ } AU.el = null; }
  AU.track = null;
}
function stopMusic() { AU.want = null; stopTrack(); return K; }
function setMuted(m) {
  AU.muted = !!m; storage.set('novakit-mute', AU.muted ? '1' : '0');
  if (AU.master) AU.master.gain.value = AU.muted ? 0 : 0.9;
  if (AU.el) AU.el.muted = AU.muted;
  syncMute();
  return K;
}
function syncMute() { if (UI.mute) UI.mute.innerHTML = AU.muted ? ICON.off : ICON.on; }

// ---------------------------------------------------------------- 初始化與匯出
function init(o = {}) {
  if (typeof o === 'string') o = { title: o };
  o = o || {};
  if (o.help === false) K._noHelp = true;
  if (o.pointerLock) lockWanted = true;
  if (o.title || !K._title) K._title = o.title || K._title;
  build();
  setTitle(K._title);
  if (o.buttons) setButtons(o.buttons);
  if (o.mode === 'mobile' || o.mode === 'desktop') setMode(o.mode, false);
  return K;
}
Object.assign(K, {
  version: '1.0', THREE, input, storage, sensitivity: SENS, fps: 0, dialogOpen: false,
  renderer: null, scene: null, camera: null,
  init, setTitle, setMode, onModeChange(fn) { if (typeof fn === 'function') modeFns.add(fn); return () => modeFns.delete(fn); },
  setButtons, showHelp: () => showHelp(true), lockPointer: () => { lockWanted = true; lockPointer(); return K; },
  createRenderer, fit, loop, stop() { loopFn = null; return K; },
  hud, bar, toast, dialog, gameOver, victory, closeDialog,
  loadModel, loadTexture, findAsset, assetNames, hasAsset: (n, k) => !!findAsset(n, k),
  sfx, playMusic, stopMusic, setMuted, get muted() { return AU.muted; },
  random, randomInt: (a, b) => (b === undefined ? Math.floor(Math.random() * (Number(a) || 0)) : Math.floor(random(a, b + 1))), pick: arr => (arr && arr.length ? arr[Math.floor(Math.random() * arr.length)] : undefined),
  clamp, lerp: (a, b, t) => a + (b - a) * t, reportError,
});
Object.defineProperty(K, 'assets', { get: assetMap, enumerable: true });
Object.defineProperty(K, 'meta', { get: () => (W.NOVA_META && typeof W.NOVA_META === 'object' ? W.NOVA_META : {}), enumerable: true });
Object.defineProperty(K, 'pixelRatio', { get: () => R.ratio, enumerable: true });
Object.defineProperty(K, 'gpu', { get: detectGPU, enumerable: true });
Object.defineProperty(K, 'adaptive', { get: () => R.adaptive, set: v => { R.adaptive = !!v; }, enumerable: true });
Object.defineProperty(K, 'autoRender', { get: () => R.autoRender, set: v => { R.autoRender = !!v; }, enumerable: true });

export const NovaKit = K;
export { THREE };
export default K;
