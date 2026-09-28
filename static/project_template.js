// N.O.V.A. 專案範本遊戲：第三人稱機甲競技場（由 window.NOVA_TEMPLATE_CONFIG 設定；只依賴 three 與 novakit）
// 移動＝搖桿／WASD，視角＝右側滑動／拖曳滑鼠，A＝光束（按住連射、自動瞄準），B＝衝刺（短暫無敵），X＝能量全滿時釋放力場爆發。
// 沒有素材時全部以幾何體繪製；有 3D 模型／背景圖／音樂素材就自動套用。
import * as THREE from 'three';
import { NovaKit } from 'novakit';

// ---------------------------------------------------------------- 設定
const RAW = window.NOVA_TEMPLATE_CONFIG && typeof window.NOVA_TEMPLATE_CONFIG === 'object' ? window.NOVA_TEMPLATE_CONFIG : {};
const META = NovaKit.meta || {};
const txt = v => (typeof v === 'string' || typeof v === 'number') ? String(v).trim().slice(0, 160) : '';
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
function color(v, d) {
  if (typeof v === 'number' && Number.isFinite(v)) return new THREE.Color(v);
  const s = txt(v);
  if (/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.test(s)) return new THREE.Color(s[0] === '#' ? s : '#' + s);
  if (s) {
    try {   // 其他 CSS 顏色（red、rgb()…）交給 canvas 解析
      const g = document.createElement('canvas').getContext('2d');
      g.fillStyle = '#010203'; g.fillStyle = s;
      if (g.fillStyle !== '#010203') return new THREE.Color(g.fillStyle.startsWith('#') ? g.fillStyle : g.fillStyle.replace(/rgba\(([^,]+,[^,]+,[^,]+),[^)]+\)/, 'rgb($1)'));
    } catch { /* 用預設 */ }
  }
  return new THREE.Color(d);
}
function vivid(c, minL = 0.32) { const hsl = {}; c.getHSL(hsl); if (hsl.l < minL) c.setHSL(hsl.h, Math.max(hsl.s, 0.4), minL); return c; }
const WAVES = Math.round(Math.min(30, Math.max(3, Number(RAW.waves) || 8)));
const CFG = {
  title: txt(RAW.title) || txt(META.title) || 'N.O.V.A. 機甲防衛戰',
  subtitle: txt(RAW.subtitle) || txt(META.summary) || '',
  primary: vivid(color(RAW.primaryColor, '#7b4dff')),
  accent: vivid(color(RAW.accentColor, '#38e6ff'), 0.45),
  playerName: txt(RAW.playerName) || '機體',
  enemyName: txt(RAW.enemyName) || '入侵者',
  waves: WAVES,
  buttons: { a: txt(RAW.buttons?.a) || '攻擊', b: txt(RAW.buttons?.b) || '衝刺', x: txt(RAW.buttons?.x) || '必殺' },
};
CFG.goalText = txt(RAW.goalText) || `擊退 ${WAVES} 波${CFG.enemyName}，守住城市`;
// 素材指定：明確給 null／'' 代表不用；沒指定就自動挑（第一個模型給玩家、第二個給敵人）
const models = NovaKit.assetNames('model'), images = NovaKit.assetNames('image'), audios = NovaKit.assetNames('audio');
const pickAsset = (v, kind, auto) => (v === null || v === false || v === '') ? null : v !== undefined ? (NovaKit.hasAsset(v, kind) ? v : null) : (auto ?? null);
const autoModels = RAW.playerAsset === undefined && RAW.enemyAsset === undefined;
const ASSET = {
  player: pickAsset(RAW.playerAsset, 'model', autoModels ? models[0] : undefined),
  enemy: pickAsset(RAW.enemyAsset, 'model', autoModels ? models[1] : undefined),
  bg: pickAsset(RAW.backgroundAsset, 'image', images[0]),
  music: pickAsset(RAW.musicAsset, 'audio', audios[0]),
};

// ---------------------------------------------------------------- 場景
NovaKit.init({ title: CFG.title });
NovaKit.setButtons(CFG.buttons);
const { renderer, scene, camera } = NovaKit.createRenderer({ antialias: NovaKit.mode !== 'mobile', lights: false, background: 0x04030a, far: 1000 });
const LOW = !!NovaKit.gpu?.lowPower;   // 軟體繪圖等弱顯示卡：減少特效
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
camera.fov = 62; camera.updateProjectionMatrix();

const ARENA = 38, TAU = Math.PI * 2, UP = new THREE.Vector3(0, 1, 0), FWD = new THREE.Vector3(0, 0, 1);
const V = () => new THREE.Vector3();
const angDiff = (a, b) => { let d = (a - b) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU; return d; };
const WARM = new THREE.Color(0xff6a3d);
const HORIZON = CFG.primary.clone().lerp(WARM, 0.55).multiplyScalar(0.55);
scene.fog = new THREE.Fog(HORIZON, 55, 250);

function canvasTex(w, hgt, draw, rep) {
  const c = document.createElement('canvas'); c.width = w; c.height = hgt;
  draw(c.getContext('2d'), w, hgt);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (rep) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(rep[0], rep[1]); }
  t.anisotropy = 4;
  return t;
}
const radial = (g, w, stops) => { const r = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2); stops.forEach(([o, c]) => r.addColorStop(o, c)); g.fillStyle = r; g.fillRect(0, 0, w, w); };
const glowTex = canvasTex(64, 64, (g, w) => radial(g, w, [[0, 'rgba(255,255,255,1)'], [0.25, 'rgba(255,255,255,.75)'], [1, 'rgba(255,255,255,0)']]));
const shadowTex = canvasTex(64, 64, (g, w) => radial(g, w, [[0, 'rgba(0,0,0,.6)'], [0.6, 'rgba(0,0,0,.35)'], [1, 'rgba(0,0,0,0)']]));
const gridTex = canvasTex(128, 128, g => {
  g.fillStyle = '#141a26'; g.fillRect(0, 0, 128, 128);
  g.strokeStyle = 'rgba(255,255,255,.95)'; g.lineWidth = 2; g.strokeRect(1, 1, 126, 126);
  g.strokeStyle = 'rgba(255,255,255,.22)'; g.lineWidth = 1;
  g.beginPath(); g.moveTo(64, 0); g.lineTo(64, 128); g.moveTo(0, 64); g.lineTo(128, 64); g.stroke();
}, [18, 18]);
const winTex = canvasTex(64, 128, g => {
  g.fillStyle = '#000'; g.fillRect(0, 0, 64, 128);
  for (let y = 4; y < 124; y += 8) for (let x = 4; x < 60; x += 8) {
    if (Math.random() < 0.33) { g.fillStyle = `rgba(255,255,255,${0.25 + Math.random() * 0.75})`; g.fillRect(x, y, 4, 4); }
  }
}, [1, 3]);

// 天空：漸層球（跟著相機）
const sky = new THREE.Mesh(new THREE.SphereGeometry(800, 32, 16), new THREE.ShaderMaterial({
  side: THREE.BackSide, depthWrite: false, fog: false,
  uniforms: { top: { value: new THREE.Color(0x020109) }, mid: { value: CFG.primary.clone().multiplyScalar(0.22) }, hor: { value: HORIZON.clone() } },
  vertexShader: 'varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: `uniform vec3 top; uniform vec3 mid; uniform vec3 hor; varying vec3 vP;
    void main(){
      float y = vP.y;
      vec3 c = mix(mid, top, smoothstep(0.0, 0.65, y));
      c = mix(c, hor, pow(1.0 - clamp(abs(y), 0.0, 1.0), 7.0));
      if (y < 0.0) c = mix(hor, hor * 0.35, smoothstep(0.0, -0.25, y));
      gl_FragColor = vec4(c, 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
}));
sky.renderOrder = 50;   // 不透明物件中最後畫：被地面／建築擋住的像素靠深度測試直接略過
if (LOW) scene.background = HORIZON.clone().multiplyScalar(0.6); else scene.add(sky);

scene.add(new THREE.HemisphereLight(0xa8c8ff, 0x2a1838, 1.3));
const sunLight = new THREE.DirectionalLight(0xffe6cc, 2.4); sunLight.position.set(-30, 50, 25); scene.add(sunLight);
const rimLight = new THREE.DirectionalLight(CFG.accent, 1.3); rimLight.position.set(25, 18, -40);
if (!LOW) scene.add(rimLight);

// 地面、競技場外環、城市剪影
const ground = new THREE.Mesh(new THREE.RingGeometry(ARENA + 4.5, 700, 72, 1).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x080b14 }));
ground.position.y = -0.02; scene.add(ground);
const floor = new THREE.Mesh(new THREE.CircleGeometry(ARENA + 5, 72).rotateX(-Math.PI / 2), LOW
  ? new THREE.MeshBasicMaterial({ color: CFG.accent.clone().multiplyScalar(0.5), map: gridTex })   // 弱顯示卡：不打光
  : new THREE.MeshLambertMaterial({ color: 0x39414f, map: gridTex, emissive: CFG.accent, emissiveMap: gridTex, emissiveIntensity: 0.32 }));
scene.add(floor);
const edge = new THREE.Mesh(new THREE.TorusGeometry(ARENA + 0.6, 0.18, 6, 128).rotateX(Math.PI / 2), new THREE.MeshBasicMaterial({ color: CFG.accent }));
edge.position.y = 0.06; scene.add(edge);
const m4 = new THREE.Matrix4(), q4 = new THREE.Quaternion(), s3 = V(), p3 = V();
{
  const N = 20;
  const pyl = new THREE.InstancedMesh(new THREE.BoxGeometry(0.9, 4, 0.9).translate(0, 2, 0), new THREE.MeshStandardMaterial({ color: 0x1a1f2c, roughness: 0.5, metalness: 0.6 }), N);
  const cap = new THREE.InstancedMesh(new THREE.BoxGeometry(1.05, 0.3, 1.05).translate(0, 4.15, 0), new THREE.MeshBasicMaterial({ color: CFG.accent }), N);
  for (let i = 0; i < N; i++) {
    const a = i / N * TAU; p3.set(Math.cos(a) * (ARENA + 3), 0, Math.sin(a) * (ARENA + 3)); q4.setFromAxisAngle(UP, -a); s3.set(1, 1, 1);
    m4.compose(p3, q4, s3); pyl.setMatrixAt(i, m4); cap.setMatrixAt(i, m4);
  }
  scene.add(pyl, cap);
  const CN = LOW ? 70 : 120;
  const city = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0), new THREE.MeshLambertMaterial({
    color: 0x0d111c, emissive: CFG.accent.clone().lerp(new THREE.Color(0xffc080), 0.55), emissiveMap: winTex, emissiveIntensity: 0.85,
  }), CN);
  for (let i = 0; i < CN; i++) {
    const a = Math.random() * TAU, r = 62 + Math.random() * 75, w = 4 + Math.random() * 8;
    p3.set(Math.cos(a) * r, 0, Math.sin(a) * r); q4.setFromAxisAngle(UP, -a); s3.set(w, 6 + Math.random() ** 2 * 52, w * (0.6 + Math.random() * 0.8));
    city.setMatrixAt(i, m4.compose(p3, q4, s3));
  }
  scene.add(city);
}
function makeBackdrop(tex) {
  tex.wrapS = THREE.MirroredRepeatWrapping; tex.repeat.set(4, 1);
  const fade = canvasTex(4, 64, g => {
    const gr = g.createLinearGradient(0, 0, 0, 64);
    gr.addColorStop(0, '#000'); gr.addColorStop(0.3, '#fff'); gr.addColorStop(0.78, '#fff'); gr.addColorStop(1, '#000');
    g.fillStyle = gr; g.fillRect(0, 0, 4, 64);
  });
  fade.colorSpace = THREE.NoColorSpace;
  const bd = new THREE.Mesh(new THREE.CylinderGeometry(420, 420, 260, 48, 1, true), new THREE.MeshBasicMaterial({
    map: tex, alphaMap: fade, transparent: true, side: THREE.BackSide, fog: false, depthWrite: false, color: 0xa8a8a8,
  }));
  bd.position.y = 70; bd.renderOrder = -9; scene.add(bd);
}

// 影子、粒子、衝擊環
const shadowGeo = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
const shadowMat = new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false });
function blob(size) { const m = new THREE.Mesh(shadowGeo, shadowMat); m.scale.set(size, 1, size); m.position.y = 0.04; m.renderOrder = 1; scene.add(m); return m; }

function particles(max, size) {
  const pos = new Float32Array(max * 3), col = new Float32Array(max * 3), vel = new Float32Array(max * 3);
  const life = new Float32Array(max), maxL = new Float32Array(max), base = new Float32Array(max * 3), grav = new Float32Array(max);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3).setUsage(THREE.DynamicDrawUsage));
  for (let i = 0; i < max; i++) pos[i * 3 + 1] = -999;   // 未使用的粒子放到地底，且不畫（drawRange）
  geo.setDrawRange(0, 0);
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ size, map: glowTex, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false }));
  pts.frustumCulled = false; scene.add(pts);
  let head = 0, live = 0;
  return {
    emit(p, c, n, spd, lf, up = 0.4, g = 8) {
      for (let k = 0; k < n; k++) {
        const i = head; head = (head + 1) % max;
        const u = Math.random() * TAU, v = Math.random() * 2 - 1, s = Math.sqrt(1 - v * v), sp = spd * (0.35 + Math.random() * 0.65);
        pos[i * 3] = p.x; pos[i * 3 + 1] = p.y; pos[i * 3 + 2] = p.z;
        vel[i * 3] = Math.cos(u) * s * sp; vel[i * 3 + 1] = (v + up) * sp; vel[i * 3 + 2] = Math.sin(u) * s * sp;
        life[i] = maxL[i] = lf * (0.6 + Math.random() * 0.4); grav[i] = g;
        base[i * 3] = c.r; base[i * 3 + 1] = c.g; base[i * 3 + 2] = c.b;
      }
      live = max; geo.setDrawRange(0, max);
    },
    update(dt) {
      if (!live) return;
      let any = 0;
      const drag = Math.exp(-2.2 * dt);
      for (let i = 0; i < max; i++) {
        if (life[i] <= 0) continue;
        life[i] -= dt;
        const j = i * 3;
        if (life[i] <= 0) { col[j] = col[j + 1] = col[j + 2] = 0; pos[j + 1] = -999; continue; }
        any++;
        vel[j] *= drag; vel[j + 2] *= drag; vel[j + 1] = vel[j + 1] * drag - grav[i] * dt;
        pos[j] += vel[j] * dt; pos[j + 1] = Math.max(0.05, pos[j + 1] + vel[j + 1] * dt); pos[j + 2] += vel[j + 2] * dt;
        const k = life[i] / maxL[i];
        col[j] = base[j] * k; col[j + 1] = base[j + 1] * k; col[j + 2] = base[j + 2] * k;
      }
      geo.attributes.position.needsUpdate = true; geo.attributes.color.needsUpdate = true;
      if (!any) { live = 0; geo.setDrawRange(0, 0); }
    },
    clear() { life.fill(0); col.fill(0); for (let i = 0; i < max; i++) pos[i * 3 + 1] = -999; geo.attributes.position.needsUpdate = geo.attributes.color.needsUpdate = true; live = 0; geo.setDrawRange(0, 0); },
  };
}
const sparks = particles(LOW || NovaKit.mode === 'mobile' ? 420 : 700, 0.45);
const glows = particles(90, 2.6);
const ringGeo = new THREE.RingGeometry(0.86, 1, 48).rotateX(-Math.PI / 2), octGeo = new THREE.RingGeometry(0.78, 1, 8, 1).rotateX(-Math.PI / 2);
const rings = [];
function ring(p, c, from, to, dur, oct = false) {
  let r = rings.find(x => !x.mesh.visible);
  if (!r) {
    if (rings.length >= 14) r = rings[0];
    else {
      const mesh = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, fog: false }));
      scene.add(mesh); r = { mesh }; rings.push(r);
    }
  }
  r.mesh.geometry = oct ? octGeo : ringGeo;
  r.mesh.material.color.copy(c); r.mesh.material.opacity = 1;
  r.mesh.position.set(p.x, Math.max(0.1, p.y), p.z); r.mesh.scale.setScalar(from); r.mesh.visible = true;
  Object.assign(r, { t: 0, from, to, dur });
}
function updateRings(dt) {
  for (const r of rings) {
    if (!r.mesh.visible) continue;
    r.t += dt;
    const k = Math.min(1, r.t / r.dur), e = 1 - (1 - k) ** 3;
    r.mesh.scale.setScalar(r.from + (r.to - r.from) * e);
    r.mesh.material.opacity = 1 - k;
    if (k >= 1) r.mesh.visible = false;
  }
}

// 受傷時的紅色暈影
const hurtFx = document.createElement('div');
Object.assign(hurtFx.style, { position: 'fixed', inset: '0', pointerEvents: 'none', zIndex: '10', opacity: '0', transition: 'opacity .35s', background: 'radial-gradient(ellipse at center, transparent 50%, rgba(255,30,60,.6))' });
document.body.appendChild(hurtFx);

// ---------------------------------------------------------------- 機甲（內建幾何體版本）
function buildMech(col, acc) {
  const g = new THREE.Group(), arms = [], legs = [];
  const body = new THREE.MeshStandardMaterial({ color: col, roughness: 0.42, metalness: 0.45, flatShading: true });
  const dark = new THREE.MeshStandardMaterial({ color: 0x1b1f2b, roughness: 0.55, metalness: 0.55, flatShading: true });
  const trimC = acc.clone().lerp(new THREE.Color(0x9dff5c), 0.5);
  const trim = new THREE.MeshStandardMaterial({ color: trimC, emissive: trimC, emissiveIntensity: 0.25, roughness: 0.4, metalness: 0.3, flatShading: true });
  const glow = new THREE.MeshBasicMaterial({ color: acc });
  const box = (w, h, d, m, x, y, z, parent) => { const me = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); me.position.set(x, y, z); parent.add(me); return me; };
  box(0.9, 0.45, 0.55, dark, 0, 1.55, 0, g);
  const torso = new THREE.Group(); torso.position.y = 1.8; g.add(torso);
  box(1.1, 0.9, 0.7, body, 0, 0.55, 0, torso);
  box(0.78, 0.46, 0.16, trim, 0, 0.62, 0.38, torso);
  box(0.5, 0.35, 0.5, dark, 0, 0.05, 0, torso);
  const head = new THREE.Group(); head.position.set(0, 1.12, 0.02); torso.add(head);
  box(0.42, 0.42, 0.46, body, 0, 0.2, 0, head);
  box(0.34, 0.08, 0.05, glow, 0, 0.22, 0.24, head);
  const horn = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.6, 4), trim); horn.position.set(0, 0.55, 0.12); horn.rotation.x = 0.35; head.add(horn);
  for (const s of [-1, 1]) {
    box(0.56, 0.5, 0.78, body, s * 0.86, 0.86, 0, torso);
    const arm = new THREE.Group(); arm.position.set(s * 0.86, 0.72, 0); torso.add(arm);
    box(0.28, 0.6, 0.3, dark, 0, -0.35, 0, arm);
    const fore = new THREE.Group(); fore.position.y = -0.68; arm.add(fore);
    box(0.34, 0.62, 0.36, body, 0, -0.3, 0, fore);
    box(0.14, 0.14, 0.14, glow, 0, -0.66, 0.08, fore);
    arms.push(arm);
    const leg = new THREE.Group(); leg.position.set(s * 0.3, 1.45, 0); g.add(leg);
    box(0.36, 0.7, 0.42, dark, 0, -0.35, 0, leg);
    const shin = new THREE.Group(); shin.position.y = -0.72; leg.add(shin);
    box(0.42, 0.72, 0.48, body, 0, -0.34, 0, shin);
    box(0.44, 0.14, 0.72, dark, 0, -0.66, 0.1, shin);
    box(0.2, 0.2, 0.06, glow, 0, -0.18, 0.25, shin);
    legs.push({ leg, shin });
    box(0.22, 0.5, 0.25, dark, s * 0.3, 0.6, -0.45, torso);
    box(0.16, 0.1, 0.1, glow, s * 0.3, 0.3, -0.52, torso);
  }
  return { group: g, torso, head, arms, legs };
}

// ---------------------------------------------------------------- 敵人
const DEF = {
  drone: { hp: 3, speed: 8, r: 1.0, cy: 1.2, dmg: 9, score: 100, h: 2.2, color: 0xff4d6d },
  sentinel: { hp: 7, speed: 4, r: 1.5, cy: 2.6, dmg: 12, score: 250, h: 3.6, color: 0x5d8cff },
  titan: { hp: 70, speed: 2.6, r: 3.0, cy: 3.6, dmg: 22, score: 3000, h: 7.5, color: 0xff3040 },
};
const GEO = {
  ico: new THREE.IcosahedronGeometry(0.8, 0), spike: new THREE.ConeGeometry(0.16, 0.9, 5), eye: new THREE.SphereGeometry(0.2, 10, 8),
  oct: new THREE.OctahedronGeometry(1.25, 0), halo: new THREE.TorusGeometry(1.7, 0.07, 6, 48),
  tBody: new THREE.CapsuleGeometry(1.25, 2.4, 4, 10), tMask: new THREE.SphereGeometry(0.85, 16, 10, 0, TAU, 0, Math.PI * 0.55),
  tCore: new THREE.SphereGeometry(0.5, 16, 12), tArm: new THREE.BoxGeometry(0.45, 2.8, 0.45), tSpike: new THREE.ConeGeometry(0.35, 1.6, 6),
};
const eyeMat = new THREE.MeshBasicMaterial({ color: 0xfff2a8 });
const blackMat = new THREE.MeshBasicMaterial({ color: 0x050505 });
const haloMat = new THREE.MeshBasicMaterial({ color: 0x7fd4ff, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false });
let enemyProto = null;   // 敵人 GLB（高度正規化為 1）
function buildEnemy(type) {
  const obj = new THREE.Group(), mats = [];
  const mat = o => { const m = new THREE.MeshStandardMaterial(o); mats.push(m); return m; };
  let spin = null, halo = null;
  if (enemyProto) {
    const m = enemyProto.clone(true);
    m.traverse(x => { if (x.isMesh) { x.material = x.material.clone(); mats.push(x.material); } });
    m.scale.setScalar(DEF[type].h * (type === 'drone' ? 0.8 : 1));
    obj.add(m); spin = type === 'sentinel' ? m : null;
  } else if (type === 'drone') {
    const shell = mat({ color: 0xc92a44, emissive: 0x2a0008, roughness: 0.35, metalness: 0.55, flatShading: true });
    const core = new THREE.Mesh(GEO.ico, shell); core.position.y = 1.2; obj.add(core);
    for (let i = 0; i < 5; i++) {
      const s = new THREE.Mesh(GEO.spike, shell), d = new THREE.Vector3(Math.cos(i * 1.26), i === 4 ? 1.6 : -0.2, Math.sin(i * 1.26)).normalize();
      s.quaternion.setFromUnitVectors(UP, d); s.position.copy(d).multiplyScalar(0.8); core.add(s);
    }
    const eye = new THREE.Mesh(GEO.eye, eyeMat); eye.position.set(0, 1.25, 0.72); obj.add(eye);
    spin = core;
  } else if (type === 'sentinel') {
    const c = new THREE.Mesh(GEO.oct, mat({ color: 0x3563ff, emissive: 0x0a1c66, roughness: 0.12, metalness: 0.75, flatShading: true }));
    c.scale.y = 1.35; c.position.y = 2.6; obj.add(c);
    halo = new THREE.Mesh(GEO.halo, haloMat); halo.position.y = 2.6; halo.rotation.x = Math.PI / 2; obj.add(halo);
    spin = c;
  } else {
    const skin = mat({ color: 0x1e3a2a, emissive: 0x050d08, roughness: 0.7, metalness: 0.2, flatShading: true });
    const bone = mat({ color: 0xf2efe6, emissive: 0x111111, roughness: 0.5, metalness: 0.1 });
    const core = mat({ color: 0xff2030, emissive: 0xff1020, emissiveIntensity: 1.2, roughness: 0.3 });
    const b = new THREE.Mesh(GEO.tBody, skin); b.position.y = 3.4; b.scale.set(1, 1, 0.8); obj.add(b);
    const mask = new THREE.Mesh(GEO.tMask, bone); mask.position.set(0, 5.2, 0.35); mask.rotation.x = 1.25; mask.scale.set(1, 1, 0.7); obj.add(mask);
    for (const s of [-1, 1]) {
      const e = new THREE.Mesh(GEO.eye, blackMat); e.position.set(s * 0.3, 5.35, 0.95); e.scale.set(0.9, 0.6, 0.5); obj.add(e);
      const arm = new THREE.Mesh(GEO.tArm, skin); arm.position.set(s * 1.65, 3.2, 0.2); arm.rotation.z = s * 0.18; obj.add(arm);
      const sp = new THREE.Mesh(GEO.tSpike, bone); sp.position.set(s * 1.2, 4.9, -0.2); sp.rotation.z = -s * 0.7; obj.add(sp);
    }
    const c = new THREE.Mesh(GEO.tCore, core); c.position.set(0, 3.7, 0.95); obj.add(c);
  }
  for (const m of mats) m.userData.base = m.emissive ? m.emissive.getHex() : 0;
  return { obj, mats, spin, halo };
}

// ---------------------------------------------------------------- 遊戲狀態
const player = { pos: V(), vel: V(), face: 0, hp: 100, maxHp: 100, en: 60, invT: 0, dashT: 0, dashCd: 0, dashV: V(), fireT: 0, side: 1, walk: 0, aimT: 0, alive: true, rig: null, glb: null, obj: new THREE.Group(), shadow: null };
scene.add(player.obj);
player.shadow = blob(3.2);
const cam = { yaw: 0, pitch: 0.38, dist: 11, idle: 9, shake: 0 };
let enemies = [], orbs = [], shots = [], pickups = [];
const stats = { fired: 0, dashes: 0, specials: 0 };   // 自動測試用的統計
let state = 'load', wave = 0, queue = [], spawnT = 0, interT = 0, score = 0, combo = 0, comboT = 0, boss = null, energyWarnT = 0;
const bestKey = 'nova-best-' + CFG.title;
let best = Number(NovaKit.storage.get(bestKey, 0)) || 0;

function useBuiltinMech() {
  player.rig = buildMech(CFG.primary, CFG.accent);
  player.obj.add(player.rig.group);
}
function usePlayerModel(g) {
  const sz = g.userData.size || V().set(1, 3.4, 1), wide = Math.max(sz.x, sz.z);
  if (wide > 4.5) g.scale.multiplyScalar(4.5 / wide);   // 太寬的模型縮小，避免擋住視線
  player.glb = g; player.obj.add(g);
  player.rig = null;
}

// 光束與敵彈：各一個 InstancedMesh（一次繪製呼叫）
const SHOT_MAX = 90, ORB_MAX = 90;
const shotMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(0.2, 0.2, 1.9), new THREE.MeshBasicMaterial({ color: CFG.accent.clone().multiplyScalar(1.5), transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }), SHOT_MAX);
const orbMesh = new THREE.InstancedMesh(new THREE.SphereGeometry(0.42, 10, 8), new THREE.MeshBasicMaterial({ color: 0xff4060, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }), ORB_MAX);
for (const m of [shotMesh, orbMesh]) { m.count = 0; m.frustumCulled = false; m.instanceMatrix.setUsage(THREE.DynamicDrawUsage); scene.add(m); }
const dummy = new THREE.Object3D();

const pickGeo = new THREE.OctahedronGeometry(0.42, 0);
const pickMat = { hp: new THREE.MeshBasicMaterial({ color: 0x5dff9e }), en: new THREE.MeshBasicMaterial({ color: 0xb48cff }) };
const pickGlow = { hp: new THREE.SpriteMaterial({ map: glowTex, color: 0x5dff9e, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }), en: new THREE.SpriteMaterial({ map: glowTex, color: 0xb48cff, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }) };

// ---------------------------------------------------------------- 系統
const tmp = V(), tmp2 = V(), aimDir = V(), muzzle = V();
function camBasis() { return { fx: Math.sin(cam.yaw), fz: Math.cos(cam.yaw), rx: -Math.cos(cam.yaw), rz: Math.sin(cam.yaw) }; }

function hurtPlayer(dmg, from, force = false) {
  if (!player.alive || state !== 'play' || (!force && (player.invT > 0 || player.god))) return;
  player.hp = Math.max(0, player.hp - dmg);
  player.invT = 0.55;
  cam.shake = Math.max(cam.shake, 0.6);
  NovaKit.sfx('hit');
  hurtFx.style.transition = 'none'; hurtFx.style.opacity = '1';
  requestAnimationFrame(() => { hurtFx.style.transition = 'opacity .45s'; hurtFx.style.opacity = '0'; });
  if (from) { tmp.subVectors(player.pos, from).setY(0).normalize().multiplyScalar(12); player.vel.add(tmp); }
  if (player.hp <= 0) killPlayer();
}
function killPlayer() {
  player.alive = false; state = 'dead';
  tmp.copy(player.pos).setY(2);
  sparks.emit(tmp, CFG.accent, 120, 16, 1.4, 0.6); glows.emit(tmp, WARM, 24, 8, 1.2, 0.5, 1);
  ring(player.pos, WARM, 1, 22, 1.1);
  NovaKit.sfx('explode', { volume: 1.4 });
  cam.shake = 1.4;
  player.obj.visible = false; player.shadow.visible = false;
  if (score > best) { best = score; NovaKit.storage.set(bestKey, best); }
  setTimeout(() => {
    NovaKit.gameOver({
      title: '任務失敗',
      text: `到達第 ${wave} 波　分數 <b>${score}</b>\n最高紀錄 ${best}`,
      button: '↻ 重新開始',
    }).then(startGame);
  }, 1300);
}

function fire(target) {
  if (shots.length >= SHOT_MAX) shots.shift();
  const f = player.face, fx = Math.sin(f), fz = Math.cos(f), rx = -Math.cos(f), rz = Math.sin(f);
  player.side = -player.side;
  muzzle.set(player.pos.x + fx * 0.9 + rx * 0.86 * player.side, 2.2, player.pos.z + fz * 0.9 + rz * 0.86 * player.side);
  if (target) aimDir.set(target.obj.position.x, target.cy, target.obj.position.z).sub(muzzle).normalize();
  else aimDir.set(fx, 0, fz);
  shots.push({ p: muzzle.clone(), d: aimDir.clone(), life: 1.1 }); stats.fired++;
  sparks.emit(muzzle, CFG.accent, 4, 5, 0.18, 0, 0);
  NovaKit.sfx('shoot', { volume: 0.7, pitch: 0.9 + Math.random() * 0.2 });
}
function findTarget(fx, fz) {
  const cone = NovaKit.mode === 'mobile' ? 1.05 : 0.8;
  let best = null, bestS = Infinity;
  for (const e of enemies) {
    if (e.spawnT < 0.4) continue;
    const dx = e.obj.position.x - player.pos.x, dz = e.obj.position.z - player.pos.z, d = Math.hypot(dx, dz);
    if (d > 60 || d < 0.01) continue;
    const ang = Math.acos(Math.max(-1, Math.min(1, (dx * fx + dz * fz) / d)));
    if (ang > cone) continue;
    const s = ang * 14 + d * 0.12;
    if (s < bestS) { bestS = s; best = e; }
  }
  return best;
}
function special() {
  if (player.en < 99.5) {
    if (energyWarnT <= 0) { NovaKit.toast('能量不足（需要 100%）', 1100); energyWarnT = 1.2; }
    return;
  }
  player.en = 0; stats.specials++;
  tmp.copy(player.pos).setY(0.3);
  ring(tmp, new THREE.Color(0xff9a2e), 1, 17, 0.8, true);
  ring(tmp, CFG.accent, 1, 14, 0.6, true);
  glows.emit(tmp.setY(2), new THREE.Color(0xff9a2e), 30, 14, 0.8, 0.2, 0);
  NovaKit.sfx('power', { volume: 1.2 });
  cam.shake = 0.9;
  for (const e of [...enemies]) {
    const d = e.obj.position.distanceTo(player.pos);
    if (d < 16) {
      tmp2.subVectors(e.obj.position, player.pos).setY(0).normalize().multiplyScalar(18 / Math.max(1, e.r));
      e.vel.add(tmp2);
      damageEnemy(e, 10);
    }
  }
  for (const o of orbs) o.life = 0;
}
function dash(mx, my) {
  if (player.dashCd > 0) return;
  if (player.en < 20) { if (energyWarnT <= 0) { NovaKit.toast('能量不足', 900); energyWarnT = 1; } return; }
  const { fx, fz, rx, rz } = camBasis();
  let dx = fx * my + rx * mx, dz = fz * my + rz * mx;
  if (Math.hypot(dx, dz) < 0.2) { dx = Math.sin(player.face); dz = Math.cos(player.face); }
  const l = Math.hypot(dx, dz);
  player.dashV.set(dx / l * 36, 0, dz / l * 36);
  player.dashT = 0.22; player.dashCd = 0.45; player.invT = Math.max(player.invT, 0.38); player.en -= 20; stats.dashes++;
  ring(player.pos, CFG.accent, 0.5, 5, 0.35);
  NovaKit.sfx('jump');
}

function spawnEnemy(type) {
  const d = DEF[type] || DEF.drone;
  const b = buildEnemy(type);
  let a = Math.random() * TAU;
  const R = ARENA - 3;
  if (Math.hypot(Math.cos(a) * R - player.pos.x, Math.sin(a) * R - player.pos.z) < 16) a += Math.PI;
  b.obj.position.set(Math.cos(a) * R, 0, Math.sin(a) * R);
  b.obj.scale.setScalar(0.01);
  scene.add(b.obj);
  const mult = 1 + (wave - 1) * 0.12;
  const e = Object.assign(b, { type, hp: Math.ceil(d.hp * mult), maxHp: Math.ceil(d.hp * mult), r: d.r, cy: d.cy, speed: d.speed * (1 + Math.min(0.5, wave * 0.03)), dmg: d.dmg, score: d.score,
    vel: V(), t: Math.random() * 10, spawnT: 0, atkT: 1, fireT: 1.5 + Math.random() * 1.5, flashT: 0, stompT: -1, dir: Math.random() < 0.5 ? 1 : -1,
    shadow: blob(d.r * 2.4), boss: type === 'titan', scale: type === 'titan' ? 1.15 : 1 });
  enemies.push(e);
  ring(e.obj.position, new THREE.Color(d.color), 0.5, d.r * 3, 0.7);
  sparks.emit(tmp.copy(e.obj.position).setY(1), new THREE.Color(d.color), 18, 6, 0.6, 1, 2);
  if (e.boss) { boss = e; NovaKit.toast(`⚠ 巨型${CFG.enemyName}接近`, 2200); NovaKit.sfx('alarm'); }
  return e;
}
function damageEnemy(e, dmg, dir) {
  if (e.hp <= 0) return;
  e.hp -= dmg; e.flashT = 0.08;
  if (dir) e.vel.addScaledVector(dir, e.boss ? 0.6 : 3);
  if (e.hp <= 0) killEnemy(e);
  else NovaKit.sfx('hit', { volume: 0.35, pitch: 1.4 });
}
function killEnemy(e) {
  const c = new THREE.Color(DEF[e.type].color);
  tmp.copy(e.obj.position).setY(e.cy);
  sparks.emit(tmp, c, e.boss ? 160 : 40, e.boss ? 22 : 12, 1, 0.5);
  glows.emit(tmp, c.clone().lerp(WARM, 0.5), e.boss ? 26 : 6, e.boss ? 10 : 5, 0.7, 0.3, 1);
  ring(e.obj.position, c, 1, e.boss ? 20 : 7, e.boss ? 1.2 : 0.5);
  NovaKit.sfx('explode', { volume: e.boss ? 1.5 : 0.7 });
  cam.shake = Math.max(cam.shake, e.boss ? 1.5 : 0.25);
  combo = comboT > 0 ? combo + 1 : 1; comboT = 2.5;
  score += Math.round(e.score * (1 + (combo - 1) * 0.1));
  player.en = Math.min(100, player.en + (e.boss ? 60 : 6));
  const drops = e.boss ? 3 : Math.random() < 0.2 ? 1 : 0;
  for (let i = 0; i < drops; i++) dropPickup(e.obj.position, Math.random() < 0.6 ? 'hp' : 'en');
  removeEnemy(e);
  if (e === boss) { boss = null; NovaKit.bar('boss', null); NovaKit.toast(`巨型${CFG.enemyName}已擊破！`, 2000); }
}
function removeEnemy(e) {
  scene.remove(e.obj); scene.remove(e.shadow);
  for (const m of e.mats) m.dispose();
  enemies = enemies.filter(x => x !== e);
}
function dropPickup(pos, type) {
  const obj = new THREE.Group();
  obj.add(new THREE.Mesh(pickGeo, pickMat[type]));
  const s = new THREE.Sprite(pickGlow[type]); s.scale.setScalar(1.8); obj.add(s);
  obj.position.set(pos.x + (Math.random() - 0.5) * 3, 1, pos.z + (Math.random() - 0.5) * 3);
  scene.add(obj);
  pickups.push({ obj, type, t: 0 });
}
function enemyFire(e, n = 1, spread = 0) {
  const from = tmp.copy(e.obj.position).setY(e.cy);
  const lead = Math.min(0.6, from.distanceTo(player.pos) / 14);
  tmp2.set(player.pos.x + player.vel.x * lead, 1.8, player.pos.z + player.vel.z * lead).sub(from).normalize();
  for (let i = 0; i < n; i++) {
    if (orbs.length >= ORB_MAX) orbs.shift();
    const a = (i - (n - 1) / 2) * spread;
    const d = tmp2.clone().applyAxisAngle(UP, a);
    orbs.push({ p: from.clone(), v: d.multiplyScalar(e.boss ? 11 : 13), life: 4, dmg: e.boss ? 14 : 9 });
  }
  sparks.emit(from, new THREE.Color(0xff4060), 6, 4, 0.3, 0, 0);
  NovaKit.sfx('shoot', { volume: 0.35, pitch: 0.55 });
}

function waveQueue(n) {
  const q = [];
  const drones = Math.min(4 + n * 2, 18), sentinels = n >= 2 ? Math.min(1 + Math.floor(n / 2), 7) : 0;
  for (let i = 0; i < drones; i++) q.push('drone');
  for (let i = 0; i < sentinels; i++) q.splice(Math.floor(Math.random() * (q.length + 1)), 0, 'sentinel');
  if (n % 4 === 0 || n === CFG.waves) q.push('titan');
  return q;
}
function startWave(n) {
  wave = n; queue = waveQueue(n); spawnT = 0.6;
  NovaKit.toast(n > CFG.waves ? `無盡模式　WAVE ${n}` : `WAVE ${n} / ${CFG.waves}`, 2000);
  NovaKit.sfx('alarm', { volume: 0.6 });
}
function clearWorld() {
  for (const e of [...enemies]) removeEnemy(e);
  for (const p of pickups) scene.remove(p.obj);
  enemies = []; orbs = []; shots = []; pickups = []; boss = null;
  sparks.clear(); glows.clear();
  NovaKit.bar('boss', null);
}
function startGame() {
  clearWorld();
  Object.assign(player, { hp: player.maxHp, en: 60, invT: 1.2, dashT: 0, dashCd: 0, fireT: 0, alive: true, face: 0 });
  player.pos.set(0, 0, 0); player.vel.set(0, 0, 0);
  player.obj.visible = true; player.shadow.visible = true;
  cam.yaw = 0; cam.pitch = 0.38;
  score = 0; combo = 0; comboT = 0; interT = 0;
  state = 'play';
  if (ASSET.music) NovaKit.playMusic(ASSET.music, { volume: 0.45 });
  startWave(1);
}

// ---------------------------------------------------------------- 每幀更新
function updatePlayer(dt) {
  const inp = NovaKit.input, mv = inp.move;
  const { fx, fz, rx, rz } = camBasis();
  const playing = state === 'play' && player.alive;
  const mx = playing ? mv.x : 0, my = playing ? mv.y : 0;
  let dx = fx * my + rx * mx, dz = fz * my + rz * mx;
  const mag = Math.min(1, Math.hypot(dx, dz));
  player.invT -= dt; player.dashCd -= dt; player.fireT -= dt; player.aimT -= dt; energyWarnT -= dt;
  if (playing) {
    if (inp.pressed('b')) dash(mx, my);
    if (inp.pressed('x')) special();
    if ((inp.buttons.a || inp.pressed('a')) && player.fireT <= 0) {
      const t = findTarget(fx, fz);
      if (t) player.face = Math.atan2(t.obj.position.x - player.pos.x, t.obj.position.z - player.pos.z);
      else player.face = cam.yaw;
      player.aimT = 0.35; player.fireT = 0.11;
      fire(t);
    }
    player.en = Math.min(100, player.en + 13 * dt);
  }
  const k = 1 - Math.exp(-10 * dt);
  if (player.dashT > 0) {
    player.dashT -= dt; player.vel.copy(player.dashV);
    if (Math.random() < 0.8) sparks.emit(tmp.copy(player.pos).setY(1.5), CFG.accent, 3, 2, 0.35, 0.2, 0);
  } else {
    player.vel.x += (dx * 12 - player.vel.x) * k;
    player.vel.z += (dz * 12 - player.vel.z) * k;
  }
  player.pos.addScaledVector(player.vel, dt);
  const r = Math.hypot(player.pos.x, player.pos.z);
  if (r > ARENA - 1.2) player.pos.multiplyScalar((ARENA - 1.2) / r);
  // 朝向：射擊時面向目標，否則面向移動方向
  if (player.aimT <= 0 && mag > 0.1) player.face += angDiff(Math.atan2(dx, dz), player.face) * Math.min(1, 12 * dt);
  const o = player.obj;
  o.position.copy(player.pos);
  o.rotation.y += angDiff(player.face, o.rotation.y) * Math.min(1, 16 * dt);
  o.visible = player.alive && (player.invT <= 0 || state !== 'play' || Math.floor(player.invT * 20) % 2 === 0 || player.dashT > 0);
  player.shadow.position.set(player.pos.x, 0.04, player.pos.z);
  // 動畫
  const speedK = Math.min(1, Math.hypot(player.vel.x, player.vel.z) / 12);
  player.walk += dt * (3 + 8 * speedK);
  const sw = Math.sin(player.walk) * 0.75 * speedK;
  const rig = player.rig;
  if (rig) {
    rig.legs[0].leg.rotation.x = sw; rig.legs[1].leg.rotation.x = -sw;
    rig.legs[0].shin.rotation.x = Math.max(0, -Math.sin(player.walk)) * speedK;
    rig.legs[1].shin.rotation.x = Math.max(0, Math.sin(player.walk)) * speedK;
    rig.torso.position.y = 1.8 + Math.abs(Math.sin(player.walk)) * 0.07 * speedK + Math.sin(performance.now() / 600) * 0.02;
    rig.torso.rotation.x = 0.12 * speedK + (player.dashT > 0 ? 0.3 : 0);
    rig.arms.forEach((a, i) => {
      const firing = player.aimT > 0 && (i === 0 ? player.side < 0 : player.side > 0);
      const want = player.aimT > 0 ? (firing ? -1.55 : -1.2) : (i === 0 ? -sw : sw) * 0.6;
      a.rotation.x += (want - a.rotation.x) * Math.min(1, 20 * dt);
    });
  } else if (player.glb) {
    player.glb.position.y = Math.abs(Math.sin(player.walk)) * 0.12 * speedK + Math.sin(performance.now() / 500) * 0.05;
    player.glb.rotation.x = 0.14 * speedK + (player.dashT > 0 ? 0.25 : 0);
  }
  // 視角：拖曳轉動；只用鍵盤前進時慢慢轉到角色背後
  cam.yaw -= inp.look.x; cam.pitch = Math.min(1.15, Math.max(0.06, cam.pitch + inp.look.y));
  cam.dist = Math.min(20, Math.max(7, cam.dist + inp.wheel * 0.01));
  cam.idle = Math.abs(inp.look.x) + Math.abs(inp.look.y) > 0.0005 ? 0 : cam.idle + dt;
  if (playing && cam.idle > 1 && my > 0.2 && player.aimT <= 0) cam.yaw += angDiff(player.face, cam.yaw) * Math.min(1, 0.9 * dt) * my;
}
function updateCamera(dt, t) {
  if (state === 'title' || state === 'load') { cam.yaw += dt * 0.12; cam.pitch = 0.3; }
  const { fx, fz } = camBasis();
  // 直式螢幕（手機）：視野加寬、鏡頭拉遠拉高，才看得到左右的敵人
  const portrait = camera.aspect < 0.85, fov = portrait ? 72 : 62;
  if (camera.fov !== fov) { camera.fov = fov; camera.updateProjectionMatrix(); }
  const dist = cam.dist * (portrait ? 1.3 : 1), pitch = Math.min(1.25, cam.pitch + (portrait ? 0.22 : 0));
  const tgt = tmp.set(player.pos.x, 2.4, player.pos.z);
  const cd = dist * Math.cos(pitch);
  tmp2.set(tgt.x - fx * cd, tgt.y + dist * Math.sin(pitch), tgt.z - fz * cd);
  camera.position.lerp(tmp2, 1 - Math.exp(-12 * dt));
  if (cam.shake > 0) {
    cam.shake = Math.max(0, cam.shake - dt * 2.2);
    const s = cam.shake * cam.shake * 0.6;
    camera.position.x += (Math.random() - 0.5) * s; camera.position.y += (Math.random() - 0.5) * s; camera.position.z += (Math.random() - 0.5) * s;
  }
  camera.lookAt(tgt.x + fx * 3, tgt.y, tgt.z + fz * 3);
  sky.position.copy(camera.position);
}
function updateEnemies(dt) {
  const px = player.pos.x, pz = player.pos.z;
  for (const e of enemies) {
    const o = e.obj;
    e.t += dt; e.spawnT += dt; e.atkT -= dt; e.fireT -= dt;
    const sp = Math.min(1, e.spawnT / 0.6);
    o.scale.setScalar(e.scale * (sp < 1 ? 1 - (1 - sp) ** 3 : 1));
    const dx = px - o.position.x, dz = pz - o.position.z, d = Math.hypot(dx, dz) || 1e-6, nx = dx / d, nz = dz / d;
    let wx = 0, wz = 0;
    if (sp >= 1 && state === 'play') {
      if (e.type === 'drone') { wx = nx; wz = nz; }
      else if (e.type === 'sentinel') {
        const want = d > 18 ? 1 : d < 12 ? -1 : 0;
        wx = nx * want - nz * e.dir * 0.7; wz = nz * want + nx * e.dir * 0.7;
        if (Math.random() < dt * 0.3) e.dir = -e.dir;
        if (e.fireT <= 0 && d < 36) { enemyFire(e); e.fireT = 2.1 + Math.random() * 1.2; }
      } else {
        if (d > 8) { wx = nx; wz = nz; }
        if (e.fireT <= 0 && d < 40) { enemyFire(e, 5, 0.18); e.fireT = 3 + Math.random(); }
        if (d < 8.5 && e.stompT < 0 && e.atkT <= 0) { e.stompT = 0.9; ring(o.position, new THREE.Color(0xff3040), 7, 0.5, 0.9); }
      }
    }
    if (e.stompT >= 0) {
      e.stompT -= dt;
      if (e.stompT < 0) {
        ring(o.position, new THREE.Color(0xff6040), 1, 9, 0.5); cam.shake = Math.max(cam.shake, 0.8); NovaKit.sfx('explode', { volume: 0.8, pitch: 0.7 });
        if (d < 8) hurtPlayer(e.dmg, o.position);
        e.atkT = 2.5;
      }
    }
    // 分離：避免疊在一起
    for (const f of enemies) {
      if (f === e) continue;
      const ox = o.position.x - f.obj.position.x, oz = o.position.z - f.obj.position.z, od = Math.hypot(ox, oz), min = e.r + f.r;
      if (od < min && od > 1e-4) { wx += ox / od * (min - od) * 0.8; wz += oz / od * (min - od) * 0.8; }
    }
    const k = 1 - Math.exp(-3 * dt);
    e.vel.x += (wx * e.speed - e.vel.x) * k; e.vel.z += (wz * e.speed - e.vel.z) * k;
    o.position.x += e.vel.x * dt; o.position.z += e.vel.z * dt;
    const r = Math.hypot(o.position.x, o.position.z);
    if (r > ARENA - e.r) o.position.multiplyScalar((ARENA - e.r) / r);
    // 近戰
    if (sp >= 1 && d < e.r + 1.3 && state === 'play') {
      if (e.atkT <= 0 && e.type !== 'titan') { hurtPlayer(e.dmg, o.position); e.atkT = 1; e.vel.set(-nx * 8, 0, -nz * 8); }
      player.pos.x -= nx * (e.r + 1.3 - d) * 0.5; player.pos.z -= nz * (e.r + 1.3 - d) * 0.5;
    }
    o.rotation.y += angDiff(Math.atan2(dx, dz), o.rotation.y) * Math.min(1, 5 * dt);
    o.position.y = e.type === 'titan' ? Math.abs(Math.sin(e.t * 2)) * 0.15 : Math.sin(e.t * 3) * 0.25 + (e.type === 'sentinel' ? 0.6 : 0.2);
    if (e.spin) e.spin.rotation.y += dt * (e.type === 'drone' ? 3 : 0.8);
    if (e.halo) { e.halo.rotation.z += dt * 1.5; e.halo.scale.setScalar(1 + Math.sin(e.t * 4) * 0.06); }
    e.shadow.position.set(o.position.x, 0.04, o.position.z);
    if (e.flashT > 0) { e.flashT -= dt; for (const m of e.mats) if (m.emissive) m.emissive.setHex(e.flashT > 0 ? 0xffffff : m.userData.base); }
  }
}
function updateShots(dt) {
  const a = V(), SPEED = 50;
  for (const s of shots) {
    a.copy(s.p);
    s.p.addScaledVector(s.d, SPEED * dt); s.life -= dt;
    if (s.p.y < 0.1 || Math.hypot(s.p.x, s.p.z) > ARENA + 30) s.life = 0;
    for (const e of enemies) {
      if (s.life <= 0 || e.spawnT < 0.3) continue;
      if (segHit(a, s.p, e.obj.position.x, e.cy * e.scale + e.obj.position.y, e.obj.position.z, e.r + 0.35)) {
        s.life = 0;
        sparks.emit(s.p, CFG.accent, 7, 7, 0.3, 0.3);
        damageEnemy(e, 1, s.d);
      }
    }
  }
  shots = shots.filter(s => s.life > 0);
  shots.forEach((s, i) => {
    dummy.position.copy(s.p); dummy.quaternion.setFromUnitVectors(FWD, s.d); dummy.scale.set(1, 1, 1); dummy.updateMatrix();
    shotMesh.setMatrixAt(i, dummy.matrix);
  });
  shotMesh.count = shots.length; shotMesh.instanceMatrix.needsUpdate = true;
  for (const o of orbs) {
    o.p.addScaledVector(o.v, dt); o.life -= dt;
    if (Math.hypot(o.p.x, o.p.z) > ARENA + 10) o.life = 0;
    if (o.life > 0 && player.alive && Math.hypot(o.p.x - player.pos.x, o.p.y - 1.8, o.p.z - player.pos.z) < 1.35) {
      o.life = 0;
      if (player.invT <= 0) { sparks.emit(o.p, new THREE.Color(0xff4060), 12, 6, 0.4); hurtPlayer(o.dmg, null); }
    }
  }
  orbs = orbs.filter(o => o.life > 0);
  const pulse = 1 + Math.sin(performance.now() / 60) * 0.15;
  orbs.forEach((o, i) => { dummy.position.copy(o.p); dummy.quaternion.identity(); dummy.scale.setScalar(pulse); dummy.updateMatrix(); orbMesh.setMatrixAt(i, dummy.matrix); });
  orbMesh.count = orbs.length; orbMesh.instanceMatrix.needsUpdate = true;
}
function segHit(a, b, cx, cy, cz, r) {
  const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z, l2 = dx * dx + dy * dy + dz * dz || 1e-6;
  let t = ((cx - a.x) * dx + (cy - a.y) * dy + (cz - a.z) * dz) / l2;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const x = a.x + dx * t - cx, y = a.y + dy * t - cy, z = a.z + dz * t - cz;
  return x * x + y * y + z * z <= r * r;
}
function updatePickups(dt) {
  for (const p of pickups) {
    p.t += dt;
    const o = p.obj, d = Math.hypot(player.pos.x - o.position.x, player.pos.z - o.position.z);
    o.rotation.y += dt * 2.5; o.position.y = 1 + Math.sin(p.t * 3) * 0.2;
    if (player.alive && d < 6) { o.position.x += (player.pos.x - o.position.x) * Math.min(1, 6 * dt); o.position.z += (player.pos.z - o.position.z) * Math.min(1, 6 * dt); }
    if (player.alive && d < 1.6) {
      p.t = 99;
      if (p.type === 'hp') { player.hp = Math.min(player.maxHp, player.hp + 20); NovaKit.toast('+20 HP', 800); }
      else { player.en = Math.min(100, player.en + 35); NovaKit.toast('+35 能量', 800); }
      NovaKit.sfx('pickup');
      sparks.emit(o.position, p.type === 'hp' ? new THREE.Color(0x5dff9e) : new THREE.Color(0xb48cff), 16, 5, 0.5, 0.8);
    }
    if (p.t > 14) scene.remove(o);
  }
  pickups = pickups.filter(p => p.t <= 14);
}
function updateWaves(dt) {
  if (state !== 'play') return;
  comboT -= dt;
  const maxAlive = enemyProto ? 8 : 12;
  if (queue.length) {
    spawnT -= dt;
    if (spawnT <= 0 && enemies.length < maxAlive) { spawnEnemy(queue.shift()); spawnT = 0.75; }
  } else if (!enemies.length) {
    if (interT <= 0) {
      interT = 2.6;
      player.hp = Math.min(player.maxHp, player.hp + 15);
      NovaKit.toast('WAVE CLEAR　+15 HP', 1800); NovaKit.sfx('pickup');
    } else {
      interT -= dt;
      if (interT <= 0) {
        if (wave === CFG.waves) {
          state = 'win';
          if (score > best) { best = score; NovaKit.storage.set(bestKey, best); }
          NovaKit.sfx('power');
          NovaKit.victory({ title: '任務完成', text: `${esc(CFG.goalText)}\n分數 <b>${score}</b>　最高 ${best}`, button: '繼續挑戰（無盡模式）' })
            .then(() => { state = 'play'; startWave(wave + 1); });
        } else startWave(wave + 1);
      }
    }
  }
}
function updateHud() {
  if (state === 'load') return;
  const left = enemies.length + queue.length;
  NovaKit.hud(`<b>SCORE</b> ${score}　<b>BEST</b> ${best}\n<b>WAVE</b> ${Math.max(1, wave)}/${CFG.waves}　<b>${esc(CFG.enemyName)}</b> ${left}${combo > 1 && comboT > 0 ? `\n<b>COMBO</b> ×${combo}` : ''}`);
  NovaKit.bar('hp', player.hp / player.maxHp, { label: `${CFG.playerName} HP`, color: '#5dff9e', text: `${Math.ceil(player.hp)}` });
  NovaKit.bar('en', player.en / 100, { label: player.en >= 99.5 ? `ENERGY　${CFG.buttons.x} READY` : 'ENERGY', color: player.en >= 99.5 ? '#ffc46b' : '#8a5cff', blink: false });
  if (boss) NovaKit.bar('boss', boss.hp / boss.maxHp, { label: `巨型${CFG.enemyName}`, color: '#ff4d6d', blink: false });
}
function frame(dt, t) {
  updatePlayer(dt);
  updateWaves(dt);
  updateEnemies(dt);
  updateShots(dt);
  updatePickups(dt);
  sparks.update(dt); glows.update(dt); updateRings(dt);
  edge.material.color.copy(CFG.accent).multiplyScalar(0.8 + Math.sin(t * 2) * 0.2);
  updateCamera(dt, t);
  updateHud();
  renderer.render(scene, camera);
}

// ---------------------------------------------------------------- 啟動
const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('逾時')), ms))]);
async function loadAssets() {
  const jobs = [];
  if (ASSET.player) jobs.push(withTimeout(NovaKit.loadModel(ASSET.player, { height: 3.4 }), 20000).then(usePlayerModel).catch(e => console.warn('玩家模型載入失敗，改用內建機體', e)));
  if (ASSET.enemy) jobs.push(withTimeout(NovaKit.loadModel(ASSET.enemy, { height: 1 }), 20000).then(g => { enemyProto = g; }).catch(e => console.warn('敵人模型載入失敗，改用內建造型', e)));
  if (ASSET.bg) jobs.push(withTimeout(NovaKit.loadTexture(ASSET.bg), 15000).then(makeBackdrop).catch(e => console.warn('背景圖載入失敗', e)));
  await Promise.all(jobs);
  if (!player.glb) useBuiltinMech();
}
function controlsText() {
  const B = CFG.buttons;
  return NovaKit.mode === 'mobile'
    ? `左側搖桿 移動｜右側滑動 轉視角<br>${esc(B.a)}：按住連射（自動瞄準）｜${esc(B.b)}：衝刺閃避｜${esc(B.x)}：能量滿時釋放`
    : `WASD 移動｜拖曳滑鼠 轉視角｜滾輪 縮放<br>空白鍵／左鍵 ${esc(B.a)}｜Shift ${esc(B.b)}｜E ${esc(B.x)}`;
}
const titleDialog = () => NovaKit.dialog({
  title: CFG.title,
  text: `${CFG.subtitle ? esc(CFG.subtitle) + '\n\n' : ''}🎯 ${esc(CFG.goalText)}\n<span style="opacity:.7;font-size:12px">${controlsText()}</span>`,
  button: '▶ 開始任務',
});
// 在標題畫面切換手機／筆電模式：操作說明跟著換
NovaKit.onModeChange(() => { if (state === 'title') titleDialog(); });
async function main() {
  NovaKit.hud('<b>LOADING</b> 素材載入中…');
  NovaKit.loop(frame);
  await loadAssets();
  state = 'title';
  await titleDialog();
  startGame();
}
main().catch(e => NovaKit.reportError(e?.message || String(e), e?.stack));

// 除錯／自動測試用的把手
window.NOVA_GAME = {
  get state() { return state; }, get wave() { return wave; }, get score() { return score; }, get enemies() { return enemies.length; },
  get shots() { return shots.length; }, get orbs() { return orbs.length; }, stats, player, cam, config: CFG, assets: ASSET, scene, renderer, kit: NovaKit,
  get usingModels() { return { player: !!player.glb, enemy: !!enemyProto }; },
  hurt: n => hurtPlayer(n ?? 10, null, true), kill: () => { player.god = false; hurtPlayer(9999, null, true); }, spawn: t => spawnEnemy(t || 'drone'),
  get god() { return !!player.god; }, set god(v) { player.god = !!v; },
};
