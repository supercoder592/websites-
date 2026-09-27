// N.O.V.A. 神經網絡核心 —— 節點、突觸、流動訊號、全息環與光暈
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const STATES = {
  idle:     { activity: 0.25, color: new THREE.Color('#38e6ff'), accent: new THREE.Color('#2a6bff') },
  thinking: { activity: 1.0,  color: new THREE.Color('#9ad8ff'), accent: new THREE.Color('#8a5cff') },
  speaking: { activity: 0.7,  color: new THREE.Color('#5cf2ff'), accent: new THREE.Color('#38e6ff') },
  working:  { activity: 0.85, color: new THREE.Color('#ffc46b'), accent: new THREE.Color('#ff7a3d') },
  error:    { activity: 0.5,  color: new THREE.Color('#ff5a6e'), accent: new THREE.Color('#ff2255') },
};

// 節點與連線共用的「呼吸」位移，確保線條永遠連在節點上
const DISPLACE = /* glsl */`
  uniform float uTime;
  uniform float uAmp;
  vec3 displace(vec3 p) {
    vec3 n = normalize(p);
    float w = sin(dot(n, vec3(3.1, 1.7, 2.3)) * 3.0 + uTime * 2.2)
            + sin(dot(n, vec3(-1.3, 2.9, 0.7)) * 5.0 - uTime * 1.6) * 0.5;
    return p * (1.0 + uAmp * w * 0.09);
  }
`;

export class NeuralCore {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
    this.renderer.setClearColor(0x01040a, 1);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x01040a, 0.035);
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
    this.camera.position.set(0, 0, 9.5);

    this.root = new THREE.Group();
    this.sphere = new THREE.Group();
    this.root.add(this.sphere);
    this.scene.add(this.root);

    this.state = 'idle';
    this.activity = 0.25;
    this.audio = 0;
    this.burst = 0;
    this.progress = -1;
    this.color = STATES.idle.color.clone();
    this.accent = STATES.idle.accent.clone();
    this.offsetX = 0; this.targetOffsetX = 0;
    this.pointer = new THREE.Vector2();
    this.clock = new THREE.Clock();

    this.uniforms = {
      uTime: { value: 0 }, uAmp: { value: 0.2 }, uActivity: { value: 0.25 },
      uColor: { value: this.color }, uAccent: { value: this.accent },
      uPixelRatio: { value: this.renderer.getPixelRatio() },
    };

    this.buildNetwork();
    this.buildCore();
    this.buildRings();
    this.buildDust();

    const hide = new URLSearchParams(location.search).get('hide');
    if (hide) hide.split(',').forEach(k => { if (this[k]) this[k].visible = false; });

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.95, 0.4, 0.18);
    if (!location.search.includes('nobloom')) this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    addEventListener('resize', () => this.resize());
    addEventListener('pointermove', e => {
      this.pointer.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
    });
    this.resize();
    this.loop();
  }

  // ---------------------------------------------------------------- network
  buildNetwork() {
    const R = 2.2, OUTER = 720, INNER = 240;
    const pts = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < OUTER; i++) {
      const y = 1 - (i / (OUTER - 1)) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
      const j = 1 + (Math.random() - 0.5) * 0.07;
      pts.push(new THREE.Vector3(Math.cos(th) * r * R * j, y * R * j, Math.sin(th) * r * R * j));
    }
    for (let i = 0; i < INNER; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(R * (0.45 + Math.random() * 0.3));
      pts.push(v);
    }
    this.nodes = pts;

    // 最近鄰連線
    const edges = new Set();
    const adj = pts.map(() => []);
    const link = (a, b) => {
      const key = a < b ? `${a}-${b}` : `${b}-${a}`;
      if (edges.has(key)) return;
      edges.add(key); adj[a].push(b); adj[b].push(a);
    };
    for (let i = 0; i < pts.length; i++) {
      const k = i < OUTER ? 3 : 2;
      const d = [];
      for (let j = 0; j < pts.length; j++) if (j !== i) d.push([pts[i].distanceToSquared(pts[j]), j]);
      d.sort((a, b) => a[0] - b[0]);
      for (let n = 0; n < k; n++) link(i, d[n][1]);
    }
    // 內外層之間的長突觸
    for (let i = 0; i < 160; i++) {
      const a = OUTER + Math.floor(Math.random() * INNER);
      let best = -1, bd = 1e9;
      for (let t = 0; t < 30; t++) {
        const b = Math.floor(Math.random() * OUTER), dd = pts[a].distanceToSquared(pts[b]);
        if (dd < bd) { bd = dd; best = b; }
      }
      link(a, best);
    }
    this.edgeList = [...edges].map(s => s.split('-').map(Number));
    this.adj = adj;

    // 節點
    const pos = new Float32Array(pts.length * 3), size = new Float32Array(pts.length), seed = new Float32Array(pts.length);
    pts.forEach((p, i) => {
      p.toArray(pos, i * 3);
      size[i] = (i < OUTER ? 0.9 : 1.3) * (0.6 + Math.random() * 0.9) * (Math.random() < 0.05 ? 2.2 : 1);
      seed[i] = Math.random();
    });
    const ng = new THREE.BufferGeometry();
    ng.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    ng.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
    ng.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
    this.nodePoints = new THREE.Points(ng, new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: DISPLACE + /* glsl */`
        uniform float uPixelRatio; uniform float uActivity;
        attribute float aSize; attribute float aSeed;
        varying float vTw; varying float vDepth;
        void main() {
          vec4 mv = modelViewMatrix * vec4(displace(position), 1.0);
          vTw = 0.55 + 0.45 * sin(uTime * (1.5 + aSeed * 4.0) + aSeed * 40.0);
          vTw = mix(vTw, 1.0, uActivity * 0.4);
          vDepth = smoothstep(-13.0, -7.0, mv.z);
          gl_PointSize = aSize * uPixelRatio * (26.0 / -mv.z) * (0.8 + vTw * 0.5 + uActivity * 0.3);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; varying float vTw; varying float vDepth;
        void main() {
          float d = length(gl_PointCoord - 0.5);
          float glow = smoothstep(0.5, 0.0, d);
          float core = smoothstep(0.14, 0.0, d);
          vec3 c = mix(uColor, vec3(1.0), core * 0.8);
          gl_FragColor = vec4(c, (glow * 0.55 + core) * vTw * (0.35 + vDepth * 0.65));
        }`,
    }));
    this.sphere.add(this.nodePoints);

    // 突觸連線（帶有沿表面擴散的脈衝波）
    const E = this.edgeList.length;
    const ep = new Float32Array(E * 6), es = new Float32Array(E * 2);
    this.edgeList.forEach(([a, b], i) => {
      pts[a].toArray(ep, i * 6); pts[b].toArray(ep, i * 6 + 3);
      es[i * 2] = es[i * 2 + 1] = Math.random();
    });
    const eg = new THREE.BufferGeometry();
    eg.setAttribute('position', new THREE.BufferAttribute(ep, 3));
    eg.setAttribute('aSeed', new THREE.BufferAttribute(es, 1));
    this.edges = new THREE.LineSegments(eg, new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: DISPLACE + /* glsl */`
        uniform float uActivity; attribute float aSeed;
        varying float vI; varying float vDepth;
        void main() {
          vec3 n = normalize(position);
          float wave = sin(dot(n, vec3(0.8, 0.5, 0.3)) * 6.0 - uTime * (1.2 + uActivity * 3.0) + aSeed * 2.0);
          vI = pow(max(wave, 0.0), 6.0) * (0.4 + uActivity * 1.2);
          vec4 mv = modelViewMatrix * vec4(displace(position), 1.0);
          vDepth = smoothstep(-13.0, -7.0, mv.z);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; uniform vec3 uAccent; uniform float uActivity;
        varying float vI; varying float vDepth;
        void main() {
          vec3 c = mix(uAccent, uColor, 0.4 + vI);
          float a = (0.07 + uActivity * 0.07 + vI * 0.55) * (0.25 + vDepth * 0.75);
          gl_FragColor = vec4(c, a);
        }`,
    }));
    this.sphere.add(this.edges);

    // 沿突觸流動的訊號
    this.SPARKS = 260;
    this.sparks = [];
    const sp = new Float32Array(this.SPARKS * 3);
    for (let i = 0; i < this.SPARKS; i++) {
      const from = Math.floor(Math.random() * pts.length);
      this.sparks.push({ from, to: this.pickNext(from, -1), t: Math.random(), speed: 0.6 + Math.random() * 1.2 });
    }
    const sg = new THREE.BufferGeometry();
    sg.setAttribute('position', new THREE.BufferAttribute(sp, 3));
    this.sparkPoints = new THREE.Points(sg, new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: DISPLACE + /* glsl */`
        uniform float uPixelRatio; uniform float uActivity;
        void main() {
          vec4 mv = modelViewMatrix * vec4(displace(position), 1.0);
          gl_PointSize = uPixelRatio * (38.0 / -mv.z) * (0.7 + uActivity * 0.6);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; uniform float uActivity;
        void main() {
          float d = length(gl_PointCoord - 0.5);
          float a = smoothstep(0.5, 0.0, d);
          gl_FragColor = vec4(mix(uColor, vec3(1.0), 0.6), a * (0.25 + uActivity * 0.9));
        }`,
    }));
    this.sphere.add(this.sparkPoints);
  }

  pickNext(node, prev) {
    const n = this.adj[node].filter(x => x !== prev);
    const list = n.length ? n : this.adj[node];
    return list[Math.floor(Math.random() * list.length)];
  }

  // ---------------------------------------------------------------- core
  buildCore() {
    const coreMat = new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: /* glsl */`
        varying vec3 vN; varying vec3 vV; varying vec3 vP;
        void main() {
          vN = normalize(normalMatrix * normal);
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vV = normalize(-mv.xyz); vP = position;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */`
        uniform vec3 uColor; uniform vec3 uAccent; uniform float uTime; uniform float uActivity;
        varying vec3 vN; varying vec3 vV; varying vec3 vP;
        void main() {
          float f = pow(1.0 - abs(dot(vN, vV)), 2.2);
          float bands = 0.5 + 0.5 * sin(vP.y * 22.0 + uTime * 3.0);
          float swirl = 0.5 + 0.5 * sin(atan(vP.z, vP.x) * 6.0 + vP.y * 8.0 - uTime * 2.0);
          vec3 c = mix(uAccent, uColor, f) + vec3(1.0) * pow(1.0 - f, 6.0) * 0.35;
          float a = f * 0.9 + (bands * swirl) * 0.12 * (0.5 + uActivity);
          gl_FragColor = vec4(c, a);
        }`,
    });
    this.coreMesh = new THREE.Mesh(new THREE.SphereGeometry(0.55, 64, 64), coreMat);
    this.sphere.add(this.coreMesh);

    const ico = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.95, 1));
    this.ico = new THREE.LineSegments(ico, new THREE.LineBasicMaterial({
      color: this.color, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    this.sphere.add(this.ico);

    const halo = document.createElement('canvas');
    halo.width = halo.height = 256;
    const g = halo.getContext('2d');
    const grd = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    grd.addColorStop(0, 'rgba(255,255,255,1)');
    grd.addColorStop(0.2, 'rgba(255,255,255,0.35)');
    grd.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 256, 256);
    this.halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(halo), color: this.color, transparent: true, opacity: 0.45,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    this.halo.scale.setScalar(3.2);
    this.sphere.add(this.halo);
  }

  // ---------------------------------------------------------------- HUD rings
  buildRings() {
    this.rings = [];
    const mk = (radius, segs, gap, tilt, speed, opacity, ticks = 0) => {
      const p = [];
      const step = (Math.PI * 2) / segs;
      for (let s = 0; s < segs; s++) {
        if (Math.random() < gap) continue;
        const a0 = s * step, a1 = a0 + step * (0.55 + Math.random() * 0.4);
        const n = 12;
        for (let k = 0; k < n; k++) {
          const t0 = a0 + (a1 - a0) * k / n, t1 = a0 + (a1 - a0) * (k + 1) / n;
          p.push(Math.cos(t0) * radius, Math.sin(t0) * radius, 0, Math.cos(t1) * radius, Math.sin(t1) * radius, 0);
        }
      }
      for (let k = 0; k < ticks; k++) {
        const a = k / ticks * Math.PI * 2, l = k % 5 === 0 ? 0.14 : 0.06;
        p.push(Math.cos(a) * radius, Math.sin(a) * radius, 0, Math.cos(a) * (radius + l), Math.sin(a) * (radius + l), 0);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(p, 3));
      const mat = new THREE.LineBasicMaterial({ color: this.color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false });
      const ring = new THREE.LineSegments(geo, mat);
      ring.rotation.set(tilt[0], tilt[1], tilt[2]);
      ring.userData = { speed, baseOpacity: opacity };
      this.root.add(ring);
      this.rings.push(ring);
      return ring;
    };
    mk(2.95, 48, 0.25, [Math.PI / 2 - 0.25, 0, 0], 0.12, 0.45, 0);
    mk(3.25, 6, 0.0, [0, 0, 0], -0.05, 0.25, 120);
    mk(3.6, 90, 0.5, [1.1, 0.4, 0], 0.08, 0.3, 0);
    mk(3.9, 3, 0.0, [0, 0, 0.3], 0.03, 0.18, 0);

    // 進度環（生成任務時顯示）
    const pg = new THREE.RingGeometry(3.38, 3.44, 256, 1, Math.PI / 2, 0.0001);
    this.progressRing = new THREE.Mesh(pg, new THREE.MeshBasicMaterial({
      color: STATES.working.color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, depthWrite: false,
    }));
    this.root.add(this.progressRing);
  }

  buildDust() {
    const N = 1800, p = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      new THREE.Vector3().randomDirection().multiplyScalar(6 + Math.random() * 40).toArray(p, i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(p, 3));
    this.dust = new THREE.Points(g, new THREE.PointsMaterial({
      color: 0x3a8cff, size: 0.05, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    this.scene.add(this.dust);
  }

  // ---------------------------------------------------------------- API
  setState(s) { if (STATES[s]) this.state = s; }
  pulse(v = 0.35) { this.burst = Math.min(1.5, this.burst + v); }
  setAudioLevel(v) { this.audio = v; }
  setProgress(p) { this.progress = p; }
  setOffsetPx(px, scale = 1) { this.targetOffsetPx = px; this.targetScale = scale; }

  resize() {
    const w = innerWidth, h = innerHeight;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.resolution.set(w / 2, h / 2);
    this.camera.aspect = w / h;
    // 小螢幕時把相機拉遠
    this.camera.position.z = w / h < 1 ? 9.5 / Math.max(0.55, w / h) : 9.5;
    this.camera.updateProjectionMatrix();
  }

  loop() {
    requestAnimationFrame(() => this.loop());
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;
    const st = STATES[this.state];

    this.burst *= Math.pow(0.08, dt);
    const targetAct = st.activity + this.audio * 0.9 + this.burst * 0.5;
    this.activity += (targetAct - this.activity) * Math.min(1, dt * 3);
    this.color.lerp(st.color, dt * 2.5);
    this.accent.lerp(st.accent, dt * 2.5);

    const u = this.uniforms;
    u.uTime.value = t;
    u.uActivity.value = this.activity;
    u.uAmp.value = 0.15 + this.activity * 0.6 + this.audio * 1.5;

    // 球體旋轉 + 滑鼠視差
    this.sphere.rotation.y += dt * (0.06 + this.activity * 0.22);
    this.sphere.rotation.x += ((this.pointer.y * 0.25) - this.sphere.rotation.x) * dt * 1.5;
    this.root.rotation.y += ((this.pointer.x * 0.2) - this.root.rotation.y) * dt * 1.5;
    this.ico.rotation.x -= dt * (0.2 + this.activity * 0.6);
    this.ico.rotation.z += dt * 0.15;
    this.ico.material.color.copy(this.color);
    const s = 1 + Math.sin(t * 2) * 0.03 + this.audio * 0.35 + this.burst * 0.08;
    this.coreMesh.scale.setScalar(s);
    this.halo.material.color.copy(this.color);
    this.halo.material.opacity = 0.12 + this.activity * 0.12 + this.audio * 0.35;
    this.halo.scale.setScalar(2.2 + this.activity * 0.5 + this.audio * 1.6);

    for (const r of this.rings) {
      r.rotation.z += dt * r.userData.speed * (1 + this.activity * 2);
      r.material.color.copy(this.color);
      r.material.opacity = r.userData.baseOpacity * (0.7 + this.activity * 0.5);
    }

    // 進度環
    const pr = this.progressRing;
    if (this.progress >= 0) {
      pr.geometry.dispose();
      pr.geometry = new THREE.RingGeometry(3.38, 3.44, 256, 1, Math.PI / 2, -Math.max(0.0001, this.progress) * Math.PI * 2);
      pr.material.opacity += (0.9 - pr.material.opacity) * dt * 4;
    } else {
      pr.material.opacity *= Math.pow(0.02, dt);
    }

    // 訊號流動
    const sp = this.sparkPoints.geometry.attributes.position;
    const speedMul = 0.35 + this.activity * 2.2;
    for (let i = 0; i < this.SPARKS; i++) {
      const k = this.sparks[i];
      k.t += dt * k.speed * speedMul;
      if (k.t >= 1) {
        const prev = k.from;
        k.from = k.to; k.to = this.pickNext(k.from, prev); k.t -= 1;
      }
      const a = this.nodes[k.from], b = this.nodes[k.to];
      sp.setXYZ(i, a.x + (b.x - a.x) * k.t, a.y + (b.y - a.y) * k.t, a.z + (b.z - a.z) * k.t);
    }
    sp.needsUpdate = true;

    this.dust.rotation.y += dt * 0.01;
    this.dust.material.color.copy(this.accent);

    // 面板開啟時，把球移到剩下空間的中央
    const worldPerPx = (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * this.camera.position.z) / innerHeight;
    const target = (this.targetOffsetPx || 0) * worldPerPx;
    this.root.position.x += (target - this.root.position.x) * Math.min(1, dt * 3);
    this.root.position.y = 0.35;
    const sc = this.root.scale.x + ((this.targetScale || 1) - this.root.scale.x) * Math.min(1, dt * 3);
    this.root.scale.setScalar(sc);

    this.composer.render();
  }
}
