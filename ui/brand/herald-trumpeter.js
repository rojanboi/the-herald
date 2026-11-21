/* <herald-trumpeter> — ASCII trumpeter, drawn live in code. No video, no dependencies.
   Attributes: theme="light|soft|reverse"  glyphs="name|ramp|dots"  fps="15" (0 = static)
               cell="9" (glyph width, px)  align="center|left|right"  reserve="0..0.7" (left share kept clear)
               speed="1"  background="#hex|transparent"
   Pauses off-screen and in background tabs; respects prefers-reduced-motion. */
(() => {
  if (window.customElements && customElements.get('herald-trumpeter')) return;
  const TAU = Math.PI * 2, AL = 14;
  const clamp = (x, a = 0, b = 1) => x < a ? a : x > b ? b : x;
  const lerp = (a, b, t) => a + (b - a) * t;
  const ss = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };
  const hash = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  const gr = d => { const v = Math.round(255 * (1 - clamp(d))); return `rgb(${v},${v},${v})`; };
  const THEMES = {
    light: ['#FFFFFF', ['#262B5E', '#6B70A6', '#AEB2D4']],
    soft: ['transparent', ['#6B70A6', '#AEB2D4', '#D8DAF0']],
    reverse: ['#1B1F45', ['#D8DAF0', '#8B90C2', '#4A5090']],
  };
  const NAME = 'THEHERALD', RAMP = ' .:-=+*#%@', DOTS = ' .:ioO0@';
  const CHARS = [...new Set((NAME + RAMP + DOTS).replace(/ /g, ''))].join('');
  const BOX = { x: 230, y: -20, w: 1470, h: 1100 };

  const J = { a: 0, s: 0, c: 0 };
  const j = () => J.a ? (hash(J.s * 917.3 + (++J.c) * 3.71) - .5) * 2 * J.a : 0;
  function ell(x, y, rx, ry, rot = 0) { const p = new Path2D(); p.ellipse(x + j(), y + j(), Math.max(2, rx + j() * .5), Math.max(2, ry + j() * .5), rot, 0, TAU); return p; }
  function poly(cmds) {
    const p = new Path2D();
    for (const k of cmds) {
      if (k[0] === 'M') p.moveTo(k[1] + j(), k[2] + j());
      else if (k[0] === 'L') p.lineTo(k[1] + j(), k[2] + j());
      else if (k[0] === 'Q') p.quadraticCurveTo(k[1] + j(), k[2] + j(), k[3] + j(), k[4] + j());
      else p.closePath();
    }
    return p;
  }
  const OUT = .95, LW = 9;
  const stk = (c, p, d, w) => { c.lineWidth = w; c.strokeStyle = gr(d); c.stroke(p); };
  const fs = (c, p, d, w = LW) => { c.fillStyle = gr(d); c.fill(p); stk(c, p, OUT, w); };
  const tube = (c, p, d, w) => { stk(c, p, OUT, w + LW * 2); stk(c, p, d, w); };
  const rot = (p, o, a) => { const s = Math.sin(a), co = Math.cos(a), dx = p[0] - o[0], dy = p[1] - o[1]; return [o[0] + dx * co - dy * s, o[1] + dx * s + dy * co]; };
  function sleeve(c, x, y, rx, ry, tilt) {
    const p = ell(x, y, rx, ry, tilt);
    c.fillStyle = gr(.62); c.fill(p);
    c.save(); c.clip(p);
    for (let k = -3; k <= 3; k++) stk(c, poly([['M', x + k * 52, y - ry - 10], ['Q', x + k * 72, y, x + k * 52, y + ry + 10]]), .2, 20);
    c.restore(); stk(c, p, OUT, LW);
  }

  // 14 s loop: breathe → raise trumpet → blow → lower
  function pose(t) {
    const e = ss(2.8, 5.0, t) - ss(9.8, 12.0, t), b = ss(5.0, 5.4, t) - ss(9.4, 9.8, t);
    return { t, e, b, oy: 5 * Math.sin(TAU * t * 4 / AL) - 10 * b, hr: .05 * b + .015 * e, fe: -.05 + .06 * Math.sin(TAU * t * 5 / AL) + .05 * b * Math.sin(t * 9), ph: t * 3, blink: (t > 1.3 && t < 1.45) || (t > 13 && t < 13.15) };
  }

  function drawFigure(c, P) {
    const { t, e, b, oy, hr } = P;
    c.lineJoin = 'round'; c.lineCap = 'round';
    c.save(); c.translate(1300, 1080); c.scale(1.12, 1.12); c.translate(-1300, -1080 + oy);
    const torso = poly([['M', 980, 1130], ['L', 1035, 772], ['Q', 1235, 700, 1440, 772], ['L', 1495, 1130], ['Z']]);
    c.fillStyle = gr(.2); c.fill(torso);
    c.save(); c.clip(torso); c.fillStyle = gr(.34); c.fill(poly([['M', 1345, 700], ['L', 1520, 700], ['L', 1520, 1130], ['L', 1385, 1130], ['Z']])); c.restore();
    stk(c, torso, OUT, LW);
    sleeve(c, 1468, 812, 142, 128, .12);
    sleeve(c, 1008, 815, 150, 132, -.12);
    fs(c, poly([['M', 1188, 540], ['L', 1188, 655], ['L', 1290, 655], ['L', 1290, 540], ['Z']]), .1);
    fs(c, poly([['M', 1150, 705], ['Q', 1145, 628, 1180, 612], ['Q', 1240, 642, 1300, 612], ['Q', 1335, 628, 1328, 705], ['Q', 1240, 730, 1150, 705], ['Z']]), .6);
    const piv = [1235, 650];
    c.save(); c.translate(piv[0], piv[1]); c.rotate(hr); c.translate(-piv[0], -piv[1]);
    fs(c, ell(1302, 410, 90, 105), .72);
    fs(c, ell(1342, 462, 26, 40), .1);
    fs(c, ell(1215, 448, 135, 160), .1);
    if (b > .01) fs(c, ell(1110, 508, 10 + 34 * b, 10 + 28 * b), .1);
    fs(c, ell(1078, 462, 28, 34, -.2), .1);
    fs(c, ell(1110, 532, 12, 9), .9, 4);
    c.fillStyle = gr(.28); c.fill(ell(1172, 500, 34 + 14 * b + 3 * Math.sin(t * 40) * b, 28 + 10 * b));
    const eb = -10 * b;
    if (b < .5 && !P.blink) { c.fillStyle = gr(.95); c.fill(ell(1146, 428, 16, 23)); c.fill(ell(1238, 422, 16, 23)); }
    else { stk(c, poly([['M', 1130, 432], ['Q', 1146, 416, 1162, 432]]), .95, 9); stk(c, poly([['M', 1222, 426], ['Q', 1238, 410, 1254, 426]]), .95, 9); }
    stk(c, poly([['M', 1120, 390 + eb], ['L', 1176, 378 + eb]]), .95, 16);
    stk(c, poly([['M', 1214, 376 + eb], ['L', 1270, 382 + eb]]), .95, 16);
    fs(c, ell(1262, 292, 218, 80, -.1), .6);
    fs(c, ell(1238, 340, 175, 26, -.06), .8);
    fs(c, ell(1228, 220, 14, 12), .25, 6);
    c.save(); c.translate(1420, 290); c.rotate(P.fe);
    fs(c, poly([['M', 0, 0], ['Q', 110, -190, 320, -190], ['Q', 190, -60, 0, 0], ['Z']]), .45);
    stk(c, poly([['M', 4, -4], ['Q', 140, -150, 310, -188]]), .95, 6);
    c.restore();
    c.restore();

    const M = rot([1102, 532], piv, hr), ec = clamp(e);
    const dir = lerp(Math.PI - 1.0, Math.PI + .09, e) + .007 * Math.sin(t * 70) * b;
    const mp = [lerp(1040, M[0], ec), lerp(1000, M[1], ec)];
    const L = 720, u = [Math.cos(dir), Math.sin(dir)], v = [u[1], -u[0]];
    const at = (s, o) => [mp[0] + u[0] * s + v[0] * o, mp[1] + u[1] * s + v[1] * o];
    const S = [1000, 840], G = at(.3 * L, 6), l1 = 215, l2 = 215;
    const d = Math.min(Math.hypot(G[0] - S[0], G[1] - S[1]), l1 + l2 - 2), base = Math.atan2(G[1] - S[1], G[0] - S[0]);
    const a = Math.acos(clamp((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1, 1));
    const E1 = [S[0] + l1 * Math.cos(base + a), S[1] + l1 * Math.sin(base + a)], E2 = [S[0] + l1 * Math.cos(base - a), S[1] + l1 * Math.sin(base - a)];
    const El = E1[0] < E2[0] ? E1 : E2;
    tube(c, poly([['M', S[0], S[1]], ['L', El[0], El[1]], ['L', G[0], G[1]]]), .72, 78);
    const A1 = at(.34 * L, 50), A2 = at(.6 * L, 50), amp = 6 + 16 * b, fx = k => Math.sin(P.ph + k * 2.6) * amp * k, Lb = 300;
    const bp = poly([['M', A1[0], A1[1]], ['L', A2[0], A2[1]], ['Q', A2[0] + fx(.5), A2[1] + Lb * .5, A2[0] + fx(1), A2[1] + Lb], ['L', (A1[0] + A2[0]) / 2 + fx(.85), (A1[1] + A2[1]) / 2 + Lb - 70], ['L', A1[0] + fx(1), A1[1] + Lb], ['Q', A1[0] + fx(.5), A1[1] + Lb * .5, A1[0], A1[1]], ['Z']]);
    c.fillStyle = gr(.74); c.fill(bp);
    c.save(); c.clip(bp); stk(c, bp, .12, 32); stk(c, bp, .74, 14); c.restore();
    stk(c, bp, OUT, LW);
    const cx = (A1[0] + A2[0]) / 2 + fx(.45), cy = (A1[1] + A2[1]) / 2 + Lb * .4;
    const em = new Path2D(); em.arc(cx + j(), cy + j(), 46, 0, TAU); stk(c, em, .12, 9);
    c.fillStyle = gr(.12); c.fill(poly([['M', cx - 26, cy + 14], ['L', cx - 26, cy - 12], ['L', cx - 13, cy + 2], ['L', cx, cy - 20], ['L', cx + 13, cy + 2], ['L', cx + 26, cy - 12], ['L', cx + 26, cy + 14], ['Z']]));
    tube(c, poly([['M', ...at(.62 * L, 0)], ['L', ...at(.62 * L, 46)], ['L', ...at(.27 * L, 46)], ['L', ...at(.27 * L, 0)]]), .38, 13);
    tube(c, poly([['M', ...at(0, 0)], ['L', ...at(.8 * L, 0)]]), .38, 28);
    fs(c, poly([['M', ...at(.74 * L, -15)], ['Q', ...at(.93 * L, -22), ...at(L, -80)], ['L', ...at(L, 80)], ['Q', ...at(.93 * L, 22), ...at(.74 * L, 15)], ['Z']]), .38);
    const be = at(L, 0);
    fs(c, ell(be[0], be[1], 18, 82, dir), .88);
    fs(c, ell(...at(-4, 0), 16, 18, dir), .38);
    fs(c, ell(G[0], G[1], 44, 38, dir), .1);
    for (let k = -1; k <= 1; k++) stk(c, poly([['M', ...at(.3 * L + k * 22, -28)], ['L', ...at(.3 * L + k * 22, 30)]]), .9, 5);
    if (b > 0) for (let k = 0; k < 4; k++) {
      const raw = (t - 5.1) * .55 - k / 4; if (raw < 0) continue;
      const q = raw % 1, p = new Path2D(); p.arc(be[0], be[1], 50 + q * 620, dir - .95, dir + .95);
      stk(c, p, .9 * Math.pow(1 - q, .8) * b, 13);
    }
    c.restore();
  }

  function atlas(cw, ch, tones) {
    const bw = Math.ceil(cw) + 2, bh = Math.ceil(ch) + 2;
    const cv = document.createElement('canvas'); cv.width = bw * CHARS.length; cv.height = bh * tones.length;
    const x = cv.getContext('2d'), fz = Math.min(cw / .6, ch * .9);
    x.font = `600 ${fz}px "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace`; x.textAlign = 'center'; x.textBaseline = 'middle';
    tones.forEach((col, ti) => { x.fillStyle = col; for (let i = 0; i < CHARS.length; i++) x.fillText(CHARS[i], i * bw + bw / 2, ti * bh + bh / 2 + fz * .04); });
    const idx = {}; for (let i = 0; i < CHARS.length; i++) idx[CHARS[i]] = i;
    return { cv, bw, bh, idx };
  }

  const ALL = new Set(); let raf = 0;
  const loop = now => { raf = 0; ALL.forEach(el => { try { el.tick(now); } catch (e) { ALL.delete(el); console.error(e); } }); if (ALL.size) raf = requestAnimationFrame(loop); };
  const reduce = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  let fontP = null;
  const fonts = () => fontP || (fontP = Promise.race([document.fonts ? document.fonts.load('600 16px "IBM Plex Mono"') : null, new Promise(r => setTimeout(r, 1500))]).catch(() => {}));

  class HeraldTrumpeter extends HTMLElement {
    static get observedAttributes() { return ['theme', 'glyphs', 'fps', 'cell', 'align', 'reserve', 'speed', 'background']; }
    connectedCallback() {
      if (!this.cv) {
        const s = this.style;
        if (!s.display) s.display = 'block';
        if (!s.width) s.width = '100%';
        if (!s.height) s.height = '100%';
        if (getComputedStyle(this).position === 'static') s.position = 'relative';
        s.overflow = 'hidden';
        this.cv = document.createElement('canvas'); this.cv.setAttribute('aria-hidden', 'true');
        this.cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block';
        this.appendChild(this.cv); this.ctx = this.cv.getContext('2d');
        this.src = document.createElement('canvas'); this.sx = this.src.getContext('2d', { willReadFrequently: true });
      }
      this.t0 = performance.now(); this.last = -1; this.vis = true;
      this.ro = new ResizeObserver(() => this.layout()); this.ro.observe(this);
      this.io = new IntersectionObserver(es => { this.vis = es[es.length - 1].isIntersecting; }); this.io.observe(this);
      fonts().then(() => this.layout());
      if (document.fonts) document.fonts.ready.then(() => this.layout());
      ALL.add(this); if (!raf) raf = requestAnimationFrame(loop);
    }
    disconnectedCallback() { ALL.delete(this); if (this.ro) this.ro.disconnect(); if (this.io) this.io.disconnect(); }
    attributeChangedCallback() { if (this.cv) this.layout(); }
    opt(n, d) { const v = this.getAttribute(n); return v == null || v === '' ? d : v; }
    layout() {
      const w = this.offsetWidth, h = this.offsetHeight; if (!w || !h) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1), cell = Math.max(5, +this.opt('cell', 9) || 9), chh = cell * 1.8;
      const cols = Math.ceil(w / cell), rows = Math.ceil(h / chh);
      Object.assign(this, { w, h, dpr, cell, chh, cols, rows, SW: cols * 2, SH: rows * 2 });
      this.cv.width = Math.round(w * dpr); this.cv.height = Math.round(h * dpr);
      this.src.width = this.SW; this.src.height = this.SH;
      this.D = new Float32Array(cols * rows);
      const rl = clamp(+this.opt('reserve', 0) || 0, 0, .7), aw = w * (1 - rl);
      const bh = lerp(BOX.h, 780, clamp((aw / h - 1.4) / 1.2));
      const s = Math.min(aw / BOX.w, h / bh);
      const al = this.opt('align', 'center'), free = aw - BOX.w * s;
      const left = w * rl + (al === 'right' ? free : al === 'left' ? 0 : free / 2);
      this.cam = { s, vx: BOX.x - left / s, vy: BOX.y + bh - h / s };
      const th = THEMES[this.opt('theme', 'light')] || THEMES.light;
      this.bg = this.opt('background', th[0]);
      this.glyphs = this.opt('glyphs', 'name');
      this.fps = +this.opt('fps', 15); this.speed = +this.opt('speed', 1) || 1;
      this.atlas = atlas(cell * dpr, chh * dpr, th[1]);
      this.last = -1;
    }
    tick(now) {
      if (!this.atlas || !this.vis || document.hidden) return;
      if (reduce.matches || !(this.fps > 0)) { if (this.last !== -2) { this.last = -2; this.render(6.4, 9); } return; }
      const T = Math.floor((now - this.t0) / 1000 * this.speed * this.fps) / this.fps;
      if (T === this.last) return; this.last = T;
      this.render(T % AL, T);
    }
    render(t, T) {
      const { sx, SW, SH, cam, cols, rows, D, atlas: A, dpr } = this;
      J.a = 1.5; J.s = Math.floor(t * 8); J.c = 0;
      sx.setTransform(1, 0, 0, 1, 0, 0); sx.fillStyle = '#fff'; sx.fillRect(0, 0, SW, SH);
      const kx = 2 * cam.s / this.cell, ky = 2 * cam.s / this.chh;
      sx.setTransform(kx, 0, 0, ky, -cam.vx * kx, -cam.vy * ky);
      drawFigure(sx, pose(t)); J.a = 0;
      const px = sx.getImageData(0, 0, SW, SH).data;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const i = (r * 2 * SW + c * 2) * 4, i2 = i + SW * 4;
        D[r * cols + c] = 1 - (px[i] + px[i + 4] + px[i2] + px[i2 + 4]) / 1020;
      }
      const g = this.ctx; g.setTransform(1, 0, 0, 1, 0, 0);
      if (this.bg === 'transparent') g.clearRect(0, 0, this.cv.width, this.cv.height);
      else { g.fillStyle = this.bg; g.fillRect(0, 0, this.cv.width, this.cv.height); }
      const rev = ss(0, 1.4, T), off = Math.floor(t / AL * 36), cw = this.cell * dpr, ch = this.chh * dpr, st = this.glyphs;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const k = r * cols + c, d = D[k];
        if (d < .05 || (rev < 1 && hash(k * .913) > rev)) continue;
        let s, tone;
        if (st === 'ramp') { s = RAMP[Math.min(9, Math.floor(d * 10 + .15))]; tone = d > .25 ? 0 : 1; }
        else if (st === 'dots') { s = DOTS[Math.min(7, Math.floor(d * 8 + .4))]; tone = d > .5 ? 0 : d > .15 ? 1 : 2; }
        else { s = NAME[(c + r * 4 + off) % 9]; tone = d > .42 ? 0 : d > .16 ? 1 : 2; }
        if (s === ' ') continue;
        g.drawImage(A.cv, A.idx[s] * A.bw, tone * A.bh, A.bw, A.bh, c * cw - 1, r * ch - 1, A.bw, A.bh);
      }
    }
  }
  customElements.define('herald-trumpeter', HeraldTrumpeter);
})();
