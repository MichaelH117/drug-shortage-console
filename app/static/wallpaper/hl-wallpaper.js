/* Hanson Labs living wallpaper. Dependency-free. Generalised from the vaccine site's specimen.js.
 *
 * A slow "lava-lamp" field (metaballs drawn at 1/10 resolution and softened with blur) plus a vector layer of
 * motifs from the project's own world. Content always sits on solid paper panels above it.
 *
 * Themes and motifs:
 *   specimen   cells, 96-well plates, registration marks, lab-label callouts
 *   formulary  capsules, scored tablets, auxiliary-label stickers, a blister card
 *   manila     paper sheets, rubber stamps, paper clips, folder tabs
 *   ledger     guilloche rosettes and bands, ledger rules, odometer digits
 *   visor      a motion-tracker radar, hex mesh, HUD corner brackets, telemetry readouts
 *
 * Motion rules (WCAG 2.2.2 and 2.3.1):
 *   - every form takes 20-110 s to drift or change shape, on sine curves; nothing crosses more than ~1/4 of the
 *     screen in 10 s; colours cross-fade; nothing flashes;
 *   - a Pause control (remembered), a still frame under prefers-reduced-motion, it stops in a hidden tab and when
 *     its container is off screen, and it never draws more than `fps` frames a second (default 30).
 *   - mode "story": on by default (landing, overview, story pages).
 *     mode "work":  OFF by default with a "Show background" toggle (working screens: queues, data entry).
 *
 * Use, full page:   <script src="hl-wallpaper.js" data-theme="specimen" data-mode="story" defer></script>
 * Use, in a box:    <div data-hl-wallpaper="visor" data-mode="story" style="position:relative">...</div>
 * Use, from code:   const w = HansonWallpaper.mount({ theme: "ledger", container: el, mode: "work" });
 * Colours come from CSS custom properties (--hlw-paper, --hlw-a/b/c, --hlw-line, --hlw-strength) on the
 * container, so tokens/themes/*.css drives it and light/dark switches are picked up automatically.
 */
(function () {
  "use strict";
  if (window.HansonWallpaper) return;
  const TAU = Math.PI * 2;
  const rm = window.matchMedia("(prefers-reduced-motion: reduce)");
  let PAUSE_KEY = "hl-motion-paused";
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: the page still works */ } }
  };
  let paused = store.get(PAUSE_KEY) === "1";
  const instances = new Set();

  // ---- icons (inline, 256 grid, Phosphor-style geometry) --------------------------------------------------------
  const ICON = {
    pause: '<rect x="56" y="40" width="48" height="176" rx="6"/><rect x="152" y="40" width="48" height="176" rx="6"/>',
    play: '<path d="M80 40v176l136-88z"/>',
    minus: '<rect x="40" y="116" width="176" height="24" rx="6"/>',
    eye: '<path d="M128 56C56 56 24 128 24 128s32 72 104 72 104-72 104-72-32-72-104-72zm0 112a40 40 0 1 1 0-80 40 40 0 0 1 0 80z"/>'
  };
  const icon = (n) => `<svg viewBox="0 0 256 256" aria-hidden="true" focusable="false">${ICON[n]}</svg>`;

  // ---- theme profiles ---------------------------------------------------------------------------------------
  const PROFILES = {
    specimen: { blobs: { n: 9, r: [0.07, 0.14], tint: { 3: "b", 6: "c" }, body: 0.45, rim: 0.9 }, band: 40, still: 23,
      motifs: [["plate", 2], ["reg", 6], ["callout", 6], ["glyph", 1]], labels: ["B07", "OD 0.412", "PLATE-04", "A01", "4PL", "D29"], glyph: "96" },
    formulary: { blobs: { n: 8, r: [0.05, 0.1], tint: { 2: "b", 5: "c" }, body: 0.5, rim: 0.55, pairs: true }, still: 31,
      motifs: [["capsule", 7], ["tablet", 6], ["auxlabel", 4], ["blister", 1]], labels: ["TAKE WITH FOOD", "KEEP COLD", "SHAKE WELL", "DO NOT CRUSH"] },
    manila: { blobs: { n: 6, r: [0.1, 0.18], tint: { 1: "b", 4: "c" }, body: 0.6, rim: 0.3 }, still: 17,
      motifs: [["sheet", 4], ["stamp", 3], ["clip", 3], ["tab", 2]], labels: ["PROCESSED LOCALLY", "NOTHING UPLOADED", "RECEIVED", "ON THIS PC"] },
    ledger: { blobs: { n: 5, r: [0.12, 0.2], tint: { 1: "b", 3: "c" }, body: 0.5, rim: 0.4 }, still: 41,
      motifs: [["rosette", 3], ["ledger", 2], ["guilloche", 3], ["odometer", 2]], labels: ["047219", "112650", "008841"] },
    visor: { blobs: { n: 7, r: [0.06, 0.12], tint: { 2: "b", 5: "c" }, body: 0.35, rim: 0.8 }, still: 9,
      motifs: [["radar", 1], ["hex", 2], ["bracket", 4], ["telemetry", 5]], labels: ["K/D 1.42", "TTK 0.61 s", "SEC-B04", "PING 38", "ROUND 07"] }
  };

  // ---- helpers ------------------------------------------------------------------------------------------------
  const sm = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
  const hex = (h, d) => { h = (h || d).trim().replace("#", ""); if (h.length === 3) h = h.replace(/./g, "$&$&");
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
  const rng = (s) => () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; };

  // motif painters: (ctx, m, t, W, H, col) with the origin at the motif's centre
  const P = {
    plate(c, m) { const g = 14 + 12 * m.depth; c.strokeRect(-g * 6.3, -g * 4.3, g * 12.6, g * 8.6);
      for (let r = 0; r < 8; r++) for (let k = 0; k < 12; k++) { c.beginPath(); c.arc((k - 5.5) * g, (r - 3.5) * g, g * 0.34, 0, TAU); c.stroke(); } },
    reg(c, m) { const R = 9 + 10 * m.s; c.beginPath(); c.arc(0, 0, R, 0, TAU); c.moveTo(-R * 1.8, 0); c.lineTo(R * 1.8, 0); c.moveTo(0, -R * 1.8); c.lineTo(0, R * 1.8); c.stroke(); },
    callout(c, m, t, W, H, col) { c.beginPath(); c.arc(0, 0, 3, 0, TAU); c.fill(); c.beginPath(); c.moveTo(0, 0); c.lineTo(28, -28); c.lineTo(104, -28); c.stroke();
      c.font = `500 12px ${col.mono}`; c.fillText(m.text, 32, -33); },
    glyph(c, m, t, W, H, col) { c.globalAlpha *= 0.55; c.font = `600 ${Math.round(H * 0.42)}px ${col.display}`; c.strokeText(m.text, -H * 0.2, H * 0.15); },
    capsule(c, m) { const L = 54 + 40 * m.s, D = L * 0.38, r = D / 2;
      c.beginPath(); c.roundRect(-L / 2, -r, L, D, r); c.stroke();
      c.beginPath(); c.moveTo(0, -r); c.lineTo(-L / 2 + r, -r); c.arc(-L / 2 + r, 0, r, -Math.PI / 2, Math.PI / 2, true); c.lineTo(0, r); c.closePath();
      const a = c.globalAlpha; c.globalAlpha = a * 0.35; c.fill(); c.globalAlpha = a; c.beginPath(); c.moveTo(0, -r); c.lineTo(0, r); c.stroke(); },
    tablet(c, m) { const R = 13 + 12 * m.s; c.beginPath(); c.arc(0, 0, R, 0, TAU); c.stroke(); c.beginPath(); c.arc(0, 0, R * 0.8, 0, TAU); c.stroke();
      c.beginPath(); c.moveTo(-R * 0.62, 0); c.lineTo(R * 0.62, 0); c.stroke(); },
    auxlabel(c, m, t, W, H, col) { const w = 158, h = 50; c.strokeRect(-w / 2, -h / 2, w, h);
      const a = c.globalAlpha; c.globalAlpha = a * 0.6; c.fillRect(-w / 2, -h / 2, w, 18); c.globalAlpha = a;
      c.save(); c.globalCompositeOperation = "destination-out"; c.font = `700 11px ${col.mono}`; c.fillText(m.text, -w / 2 + 7, -h / 2 + 13); c.restore();
      c.font = `500 11px ${col.mono}`; c.fillText("NDC 0000-0000-00", -w / 2 + 7, h / 2 - 9); },
    blister(c, m) { const g = 30; c.beginPath(); c.roundRect(-g * 2.8, -g * 1.4, g * 5.6, g * 2.8, 6); c.stroke();
      for (let r = 0; r < 2; r++) for (let k = 0; k < 5; k++) { c.beginPath(); c.roundRect((k - 2.5) * g * 1.06 + 3, (r - 1) * g * 1.1 + 4, g * 0.84, g * 0.84, g * 0.42); c.stroke(); } },
    sheet(c, m) { const w = 110 + 60 * m.s, h = w * 1.29, f = 18; c.beginPath(); c.moveTo(-w / 2, -h / 2); c.lineTo(w / 2 - f, -h / 2); c.lineTo(w / 2, -h / 2 + f);
      c.lineTo(w / 2, h / 2); c.lineTo(-w / 2, h / 2); c.closePath(); c.stroke(); c.beginPath(); c.moveTo(w / 2 - f, -h / 2); c.lineTo(w / 2 - f, -h / 2 + f); c.lineTo(w / 2, -h / 2 + f); c.stroke();
      for (let y = -h / 2 + 26, i = 0; y < h / 2 - 12; y += 12, i++) { c.beginPath(); c.moveTo(-w / 2 + 12, y); c.lineTo(-w / 2 + 12 + (w - 24) * (i % 4 === 3 ? 0.55 : 0.92), y); c.stroke(); } },
    stamp(c, m, t, W, H, col) { const R = 42 + 16 * m.s; c.lineWidth = 2; c.beginPath(); c.arc(0, 0, R, 0, TAU); c.stroke(); c.lineWidth = 1;
      c.beginPath(); c.arc(0, 0, R * 0.7, 0, TAU); c.stroke(); c.font = `700 ${Math.round(R * 0.2)}px ${col.mono}`; c.textAlign = "center";
      const s = m.text, step = Math.min(0.32, (TAU * 0.7) / s.length);
      for (let i = 0; i < s.length; i++) { c.save(); c.rotate(-((s.length - 1) * step) / 2 + i * step); c.fillText(s[i], 0, -R * 0.78); c.restore(); }
      c.beginPath(); c.arc(0, R * 0.36, 3, 0, TAU); c.fill(); c.textAlign = "start"; },
    clip(c, m) { c.lineWidth = 1.5; c.beginPath(); c.moveTo(0, 8); c.lineTo(0, 58); c.arc(10, 58, 10, Math.PI, 0, true); c.lineTo(20, 2);
      c.arc(12, 2, 8, 0, Math.PI, true); c.lineTo(4, 50); c.stroke(); c.lineWidth = 1; },
    tab(c, m) { const w = 230, h = 130; c.beginPath(); c.moveTo(-w / 2, -h / 2); c.lineTo(-w / 2 + 16, -h / 2 - 20); c.lineTo(-w / 2 + 92, -h / 2 - 20);
      c.lineTo(-w / 2 + 108, -h / 2); c.lineTo(w / 2, -h / 2); c.lineTo(w / 2, h / 2); c.lineTo(-w / 2, h / 2); c.closePath(); c.stroke(); },
    rosette(c, m, t) { const R = 56 + 50 * m.s, k = 9 + Math.round(m.s * 7);
      for (let j = 0; j < 6; j++) { c.beginPath();
        for (let i = 0; i <= 240; i++) { const a = (i / 240) * TAU, r = R * (0.62 + 0.07 * j) + R * 0.11 * Math.sin(k * a + j * 0.55 + TAU * t / 110);
          const x = r * Math.cos(a), y = r * Math.sin(a); i ? c.lineTo(x, y) : c.moveTo(x, y); }
        c.stroke(); } },
    guilloche(c, m, t, W) { const A = 12 + 10 * m.s, lam = 180 + 120 * m.s;
      for (let j = 0; j < 7; j++) { c.beginPath();
        for (let x = -W; x <= W; x += 6) { const y = A * Math.sin(TAU * x / lam + j * 0.6 + TAU * t / 120) + A * 0.5 * Math.sin(TAU * x / (lam * 0.37) - j * 0.9);
          x === -W ? c.moveTo(x, y) : c.lineTo(x, y); }
        c.stroke(); } },
    ledger(c, m, t, W, H, col) { const w = 320, n = 13, g = 18; for (let i = 0; i <= n; i++) { c.beginPath(); c.moveTo(-w / 2, i * g - n * g / 2); c.lineTo(w / 2, i * g - n * g / 2); c.stroke(); }
      const s = c.strokeStyle; c.strokeStyle = col.c; [-w / 2 + 40, -w / 2 + 44].forEach((x) => { c.beginPath(); c.moveTo(x, -n * g / 2); c.lineTo(x, n * g / 2); c.stroke(); });
      c.strokeStyle = s; c.beginPath(); c.moveTo(w / 2 - 80, -n * g / 2); c.lineTo(w / 2 - 80, n * g / 2); c.stroke(); },
    odometer(c, m, t, W, H, col) { const d = m.text, bw = 24, bh = 34; c.font = `500 20px ${col.mono}`;
      for (let i = 0; i < d.length; i++) { c.strokeRect(i * bw - d.length * bw / 2, -bh / 2, bw - 3, bh); c.fillText(d[i], i * bw - d.length * bw / 2 + 5, 7); } },
    radar(c, m, t, W, H) { const R = Math.min(W, H) * 0.24; [1, 0.66, 0.33].forEach((k) => { c.beginPath(); c.arc(0, 0, R * k, 0, TAU); c.stroke(); });
      c.beginPath(); c.moveTo(-R, 0); c.lineTo(R, 0); c.moveTo(0, -R); c.lineTo(0, R); c.stroke();
      const sw = (TAU * t / 24) % TAU, a0 = c.globalAlpha;
      for (let i = 0; i < 14; i++) { c.globalAlpha = a0 * (1 - i / 14) * 0.9; const a = sw - i * 0.045; c.beginPath(); c.moveTo(0, 0); c.lineTo(R * Math.cos(a), R * Math.sin(a)); c.stroke(); }
      m.blips.forEach((b) => { const since = ((sw - b.a) % TAU + TAU) % TAU; c.globalAlpha = a0 * Math.max(0.15, 1 - since / (TAU * 0.75)) * 1.4;
        c.beginPath(); c.arc(R * b.r * Math.cos(b.a), R * b.r * Math.sin(b.a), 3.5, 0, TAU); c.fill(); });
      c.globalAlpha = a0; },
    hex(c, m) { const s = 13 + 8 * m.depth, hgt = Math.sqrt(3) * s;
      for (let r = 0; r < 5; r++) for (let k = 0; k < 7; k++) { const cx = k * s * 1.5 - 4.5 * s, cy = r * hgt + (k % 2 ? hgt / 2 : 0) - 2.5 * hgt;
        c.beginPath(); for (let i = 0; i < 6; i++) { const a = (i / 6) * TAU; i ? c.lineTo(cx + s * Math.cos(a), cy + s * Math.sin(a)) : c.moveTo(cx + s, cy); } c.closePath(); c.stroke(); } },
    bracket(c, m) { const w = 120 + 80 * m.s, h = w * 0.6, l = 16;
      [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy]) => { c.beginPath(); c.moveTo(sx * w / 2, sy * (h / 2 - l)); c.lineTo(sx * w / 2, sy * h / 2); c.lineTo(sx * (w / 2 - l), sy * h / 2); c.stroke(); }); },
    telemetry(c, m, t, W, H, col) { c.font = `400 13px ${col.mono}`; c.fillText(m.text, 0, 0); c.fillRect(0, 6, 40 + 50 * m.s, 2); }
  };

  // ---- one wallpaper instance ---------------------------------------------------------------------------------
  function mount(opts) {
    const o = Object.assign({ theme: "specimen", container: null, mode: "story", fps: 30, seed: 42, controls: true, scope: location.pathname }, opts || {});
    const prof = PROFILES[o.theme] || PROFILES.specimen;
    const box = o.container || document.body, page = !o.container;
    const host = document.createElement("div");
    host.className = "hlw" + (page ? " hlw-page" : " hlw-box"); host.dataset.theme = o.theme; host.setAttribute("aria-hidden", "true");
    const blobC = document.createElement("canvas"), lineC = document.createElement("canvas");
    blobC.className = "hlw-blobs"; lineC.className = "hlw-lines"; host.append(blobC, lineC); box.prepend(host);
    const bctx = blobC.getContext("2d"), lctx = lineC.getContext("2d");
    const rnd = rng(o.seed);

    // blobs: each centre follows two slow sines; its radius breathes. In "pairs" (formulary) every second blob
    // trails its partner at a fixed offset, so pairs read as capsules.
    const B = prof.blobs, drops = [];
    for (let i = 0; i < B.n; i++) {
      const lead = B.pairs && i % 2 ? drops[i - 1] : null;
      drops.push(lead ? Object.assign({}, lead, { off: [0.05 + 0.03 * rnd(), 0.02 * (rnd() - 0.5)], tint: B.tint[i] || lead.tint }) :
        { cx: 0.1 + 0.8 * rnd(), cy: 0.1 + 0.8 * rnd(), ax: 0.08 + 0.12 * rnd(), ay: 0.07 + 0.11 * rnd(), px: 60 + 50 * rnd(), py: 65 + 45 * rnd(),
          ph: rnd() * TAU, r: B.r[0] + (B.r[1] - B.r[0]) * rnd(), pr: 22 + 20 * rnd(), off: [0, 0], tint: B.tint[i] || "a" });
    }
    const motifs = [];
    prof.motifs.forEach(([type, n]) => { for (let i = 0; i < n; i++) motifs.push({ type, x: 0.06 + 0.88 * rnd(), y: 0.06 + 0.88 * rnd(), ax: 0.03 + 0.05 * rnd(),
      ay: 0.03 + 0.05 * rnd(), p: 55 + 45 * rnd(), ph: rnd() * TAU, s: rnd(), rot: (rnd() - 0.5) * 0.3, depth: 0.3 + 0.7 * rnd(),
      text: (prof.labels || [])[motifs.length % (prof.labels || [""]).length],
      blips: type === "radar" ? Array.from({ length: 6 }, () => ({ a: rnd() * TAU, r: 0.2 + 0.75 * rnd() })) : null }); });
    motifs.forEach((m) => { if (m.type === "glyph") m.text = prof.glyph; if (m.type === "radar") { m.x = 0.78; m.y = 0.3; m.ax = 0.02; m.ay = 0.02; m.rot = 0; } });

    let W = 0, H = 0, bw = 0, bh = 0, img = null, col = null;
    const readColors = () => {
      const cs = getComputedStyle(box), g = (n, d) => cs.getPropertyValue(n).trim() || d;
      col = { paper: hex(g("--hlw-paper", ""), "F5F2EB"), a: hex(g("--hlw-a", ""), "0F5E6B"), b: hex(g("--hlw-b", ""), "E3B23C"), cc: hex(g("--hlw-c", ""), "A4471A"),
        line: g("--hlw-line", "#0F5E6B"), c: g("--hlw-c", "#A4471A"), strength: parseFloat(g("--hlw-strength", ".10")) || 0.1,
        mono: g("--hl-font-mono", "ui-monospace, monospace"), display: g("--hl-font-display", "Georgia, serif") };
      host.style.background = `rgb(${col.paper.join(",")})`;
    };
    const size = () => {
      W = page ? window.innerWidth : box.clientWidth; H = page ? window.innerHeight : box.clientHeight;
      W = Math.max(W, 1); H = Math.max(H, 1);
      bw = Math.max(32, Math.round(W / 10)); bh = Math.max(24, Math.round(H / 10));
      blobC.width = bw; blobC.height = bh; img = bctx.createImageData(bw, bh);
      lineC.width = W; lineC.height = H;     // pixel ratio 1: soft lines are fine and cheap
    };

    function drawBlobs(t) {
      const d = img.data, Pp = col.paper, S = col.strength, breathe = 1 + 0.15 * Math.sin(TAU * t / 30), M = Math.max(bw, bh);
      const pos = drops.map((b) => [(b.cx + b.off[0] + b.ax * Math.sin(TAU * t / b.px + b.ph)) * bw, (b.cy + b.off[1] + b.ay * Math.sin(TAU * t / b.py + b.ph * 1.7)) * bh,
        b.r * (1 + 0.18 * Math.sin(TAU * t / b.pr + b.ph)) * M, b.tint]);
      for (let y = 0, k = 0; y < bh; y++) for (let x = 0; x < bw; x++, k += 4) {
        let f = 0, fb = 0, fc = 0;
        for (let i = 0; i < pos.length; i++) { const p = pos[i], dx = x - p[0], dy = y - p[1], v = (p[2] * p[2]) / (dx * dx + dy * dy + 1);
          f += v; if (p[3] === "b") fb += v; else if (p[3] === "c") fc += v; }
        const body = sm(0.55, 1.15, f), rim = sm(0.75, 1.0, f) * (1 - sm(1.0, 1.5, f));
        const a = Math.min((B.body * body + B.rim * rim) * S * breathe, S * 1.2);
        const wb = Math.min(1, fb / (f + 1e-6)), wc = Math.min(1, fc / (f + 1e-6)), wa = Math.max(0, 1 - wb - wc);
        for (let ch = 0; ch < 3; ch++) { const target = col.a[ch] * wa + col.b[ch] * wb + col.cc[ch] * wc; d[k + ch] = Pp[ch] + (target - Pp[ch]) * a; }
        d[k + 3] = 255;
      }
      bctx.putImageData(img, 0, 0);
    }
    function drawLines(t) {
      const c = lctx, S = col.strength;
      c.clearRect(0, 0, W, H); c.strokeStyle = col.line; c.fillStyle = col.line; c.lineWidth = 1;
      const k = page ? 1 : Math.min(1, Math.max(0.55, W / 900));   // shrink motifs inside small boxes
      for (const m of motifs) {
        const x = (m.x + m.ax * Math.sin(TAU * t / m.p + m.ph)) * W, y = (m.y + m.ay * Math.cos(TAU * t / (m.p * 1.3) + m.ph)) * H;
        c.save(); c.translate(x, y); c.scale(k, k); c.rotate(m.rot + 0.04 * Math.sin(TAU * t / (m.p * 2) + m.ph));
        c.globalAlpha = Math.min(1, S * (0.6 + 0.4 * m.depth) * 1.6);
        P[m.type](c, m, t, W / k, H / k, col);
        c.restore();
      }
      if (prof.band) {   // one slow scan band, top to bottom every `band` seconds, barely there
        const yb = ((t / prof.band) % 1) * (H + 200) - 100, gr = c.createLinearGradient(0, yb - 80, 0, yb + 80);
        gr.addColorStop(0, "rgba(0,0,0,0)"); gr.addColorStop(0.5, col.line); gr.addColorStop(1, "rgba(0,0,0,0)");
        c.globalAlpha = S * 0.25; c.fillStyle = gr; c.fillRect(0, yb - 80, W, 160); c.globalAlpha = 1;
      }
    }

    // ---- state: shown (work mode toggle) x running (pause, reduced motion, hidden tab, off screen) ----------------
    const showKey = "hlw-show:" + o.theme + ":" + o.scope;
    let shown = o.mode !== "work" || store.get(showKey) === "1";
    let onScreen = true, running = false, raf = 0, t = prof.still, lastNow = 0, lastFrame = 0;
    const render = () => { if (shown) { drawBlobs(t); drawLines(t); } };
    const frame = (now) => {
      raf = 0; if (!running) return;
      if (now - lastFrame >= 1000 / Math.min(60, o.fps) - 1) { t += Math.min(0.1, (now - (lastNow || now)) / 1000); lastNow = now; lastFrame = now; render(); }
      raf = requestAnimationFrame(frame);
    };
    const sync = () => {
      host.hidden = !shown;
      const want = shown && !paused && !rm.matches && !document.hidden && onScreen;
      if (want && !running) { running = true; lastNow = 0; raf = requestAnimationFrame(frame); }
      else if (!want && running) { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
      if (!running) { if (rm.matches) t = prof.still; render(); }
      host.classList.toggle("hlw-still", !running);
      updateControls();
    };

    // ---- controls -------------------------------------------------------------------------------------------
    let ctl = null, bPause = null, bShow = null;
    function button(cls) { const b = document.createElement("button"); b.type = "button"; b.className = "hlw-btn " + cls; return b; }
    function setBtn(b, ic, label, pressed, disabled) { b.innerHTML = icon(ic) + `<span>${label}</span>`; b.setAttribute("aria-pressed", String(pressed)); b.disabled = !!disabled; b.title = label; }
    if (o.controls) {
      ctl = document.createElement("div"); ctl.className = "hlw-controls" + (page ? " hlw-controls-page" : ""); ctl.setAttribute("role", "group");
      ctl.setAttribute("aria-label", "Background motion");
      if (o.mode === "work") { bShow = button("hlw-show"); bShow.addEventListener("click", () => { shown = !shown; store.set(showKey, shown ? "1" : "0"); sync(); }); ctl.append(bShow); }
      bPause = button("hlw-pause"); bPause.addEventListener("click", () => setPaused(!paused)); ctl.append(bPause);
      (page ? document.body : box).append(ctl);
    }
    function updateControls() {
      if (bShow) setBtn(bShow, "eye", shown ? "Hide background" : "Show background", shown);
      if (bPause) {
        if (rm.matches) setBtn(bPause, "minus", "Motion off (system setting)", true, true);
        else setBtn(bPause, paused ? "play" : "pause", paused ? "Play motion" : "Pause motion", paused, !shown);
        bPause.hidden = o.mode === "work" && !shown;
      }
    }

    // ---- wiring ---------------------------------------------------------------------------------------------
    readColors(); size();
    const onResize = () => { size(); if (!running) render(); };
    const onTheme = () => { readColors(); if (!running) render(); };
    document.addEventListener("visibilitychange", sync);
    rm.addEventListener ? rm.addEventListener("change", sync) : rm.addListener(sync);
    const darkMQ = window.matchMedia("(prefers-color-scheme: dark)");
    darkMQ.addEventListener ? darkMQ.addEventListener("change", onTheme) : darkMQ.addListener(onTheme);
    const mo = new MutationObserver(onTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-hl-mode", "data-hl-theme", "data-bs-theme"] });
    mo.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    if (!page) mo.observe(box, { attributes: true, attributeFilter: ["class", "data-hl-mode", "data-hl-theme", "style"] });
    let ro = null, io = null;
    if (page) window.addEventListener("resize", onResize);
    else if ("ResizeObserver" in window) { ro = new ResizeObserver(onResize); ro.observe(box); }
    if (!page && "IntersectionObserver" in window) { io = new IntersectionObserver((es) => { onScreen = es[es.length - 1].isIntersecting; sync(); }); io.observe(box); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (!running) render(); });

    const inst = {
      el: host, theme: o.theme, sync,
      show(v) { shown = v !== false; store.set(showKey, shown ? "1" : "0"); sync(); },
      redraw() { onTheme(); },
      destroy() { instances.delete(inst); running = false; if (raf) cancelAnimationFrame(raf); document.removeEventListener("visibilitychange", sync);
        window.removeEventListener("resize", onResize); mo.disconnect(); ro && ro.disconnect(); io && io.disconnect(); host.remove(); ctl && ctl.remove(); }
    };
    instances.add(inst); sync();
    return inst;
  }

  function setPaused(p) {
    paused = !!p; store.set(PAUSE_KEY, paused ? "1" : "0");
    instances.forEach((i) => i.sync());
    window.dispatchEvent(new CustomEvent("hl-motion", { detail: { paused } }));
  }

  // any button on the page with [data-hl-motion-toggle] is also a global pause control
  function wireExternal() {
    const btns = document.querySelectorAll("[data-hl-motion-toggle]");
    const upd = () => btns.forEach((b) => { b.setAttribute("aria-pressed", String(paused || rm.matches)); b.disabled = rm.matches;
      const l = b.querySelector("[data-label]") || b; l.textContent = rm.matches ? "Motion off (system setting)" : paused ? "Play motion" : "Pause motion"; });
    btns.forEach((b) => b.addEventListener("click", () => setPaused(!paused)));
    window.addEventListener("hl-motion", upd); rm.addEventListener && rm.addEventListener("change", upd); upd();
  }

  window.HansonWallpaper = {
    mount, setPaused, isPaused: () => paused, themes: Object.keys(PROFILES),
    usePauseKey(k) { PAUSE_KEY = k; paused = store.get(k) === "1"; instances.forEach((i) => i.sync()); }
  };

  // ---- auto-mount ---------------------------------------------------------------------------------------------
  const me = document.currentScript;
  function auto() {
    if (me && me.dataset.pauseKey) window.HansonWallpaper.usePauseKey(me.dataset.pauseKey);
    if (me && me.dataset.theme) mount({ theme: me.dataset.theme, mode: me.dataset.mode || "story", fps: +me.dataset.fps || 30, controls: me.dataset.controls !== "false" });
    document.querySelectorAll("[data-hl-wallpaper]").forEach((el) => mount({ theme: el.dataset.hlWallpaper, container: el, mode: el.dataset.mode || "story",
      fps: +el.dataset.fps || 30, controls: el.dataset.controls !== "false", scope: el.id || location.pathname }));
    wireExternal();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
})();
