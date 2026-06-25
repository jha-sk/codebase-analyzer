"""Self-contained, modern interactive HTML renderer for the 3-layer graph.

Produces a single ``.html`` file (no external assets, no internet required) that
renders the layered graph with a hand-written SVG force-directed layout and a
polished, modern UI. Clicking a directory node drills into its files
(layer 1 -> 2); clicking a file node drills into its functions (layer 2 -> 3);
a breadcrumb navigates back up.
"""

from __future__ import annotations

import json

from ..analysis.callgraph import LayeredGraph

# The HTML/CSS/JS template. Placeholders __TITLE__ and __DATA__ are substituted
# via str.replace (never str.format) because the JS/CSS body is full of braces.
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
<style>
  :root{
    --bg0:#0a0c12; --bg1:#0f131c; --panel:rgba(22,27,38,.72); --panel-solid:#161b26;
    --line:rgba(255,255,255,.08); --line2:rgba(255,255,255,.14);
    --text:#eef1f7; --muted:#9aa3b6; --faint:#6b7488;
    --dir:#7aa2ff; --dir2:#3b6fff; --file:#5be7c4; --file2:#16b894;
    --func:#ffd35c; --func2:#f3a712; --ext:#7b8499; --ext2:#525c70;
    --edge:#3a4358; --edgeCall:#8b6bff; --edgeBoth:#b98bff; --accent:#ff6b8b;
    --shadow:0 10px 30px rgba(0,0,0,.45);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg0);color:var(--text);
    font-family:"Inter",ui-sans-serif,system-ui,"Segoe UI",Roboto,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;overflow:hidden}
  #app{display:flex;flex-direction:column;height:100vh}

  header{position:relative;z-index:10;display:flex;gap:16px;align-items:center;flex-wrap:wrap;
    padding:12px 18px;background:var(--panel);backdrop-filter:blur(14px);
    -webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
  .brand{display:flex;flex-direction:column;gap:2px;min-width:0}
  .brand h1{font-size:14px;margin:0;font-weight:700;letter-spacing:.2px;
    background:linear-gradient(90deg,#cdd6ff,#8ee9d2);-webkit-background-clip:text;
    background-clip:text;color:transparent;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:320px}
  .brand .stat{font-size:11px;color:var(--muted)}

  .crumbs{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12.5px}
  .crumbs .chip{padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.05);
    border:1px solid var(--line);color:var(--muted);cursor:pointer;transition:.15s;white-space:nowrap}
  .crumbs .chip:hover{color:var(--text);border-color:var(--line2);background:rgba(255,255,255,.09)}
  .crumbs .chip.cur{color:var(--text);background:linear-gradient(180deg,rgba(122,162,255,.22),rgba(122,162,255,.08));
    border-color:rgba(122,162,255,.4);cursor:default}
  .crumbs .sep{color:var(--faint)}

  .spacer{flex:1}
  .seg{display:flex;background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:10px;padding:3px}
  .seg button{appearance:none;background:transparent;border:0;color:var(--muted);font:inherit;
    font-size:12px;padding:6px 12px;border-radius:7px;cursor:pointer;transition:.15s}
  .seg button:hover{color:var(--text)}
  .seg button.active{color:#fff;background:linear-gradient(180deg,rgba(122,162,255,.35),rgba(59,111,255,.22));
    box-shadow:inset 0 0 0 1px rgba(122,162,255,.4)}
  .iconbtn{appearance:none;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--muted);
    width:34px;height:34px;border-radius:9px;cursor:pointer;font-size:15px;transition:.15s}
  .iconbtn:hover{color:var(--text);border-color:var(--line2);background:rgba(255,255,255,.08)}

  .legend{display:flex;gap:14px;font-size:11.5px;color:var(--muted);align-items:center}
  .legend .k{display:flex;align-items:center;gap:6px}
  .legend .dot{width:11px;height:11px;border-radius:50%;box-shadow:0 0 8px rgba(0,0,0,.4)}
  .ld{background:radial-gradient(circle at 30% 30%,var(--dir),var(--dir2))}
  .lf{background:radial-gradient(circle at 30% 30%,var(--file),var(--file2))}
  .ln{background:radial-gradient(circle at 30% 30%,var(--func),var(--func2))}
  .le{background:radial-gradient(circle at 30% 30%,var(--ext),var(--ext2))}

  #stage{position:relative;flex:1;overflow:hidden;
    background:
      radial-gradient(1200px 800px at 78% -10%, rgba(91,231,196,.06), transparent 60%),
      radial-gradient(1000px 700px at 12% 110%, rgba(122,162,255,.08), transparent 60%),
      linear-gradient(180deg,var(--bg1),var(--bg0))}
  #stage::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
    background-image:radial-gradient(rgba(255,255,255,.05) 1px,transparent 1px);
    background-size:26px 26px;mask-image:radial-gradient(ellipse at center,#000 55%,transparent 100%)}
  svg{width:100%;height:100%;display:block;cursor:grab;position:relative;z-index:1}
  svg.panning{cursor:grabbing}

  .link{fill:none;stroke:var(--edge);stroke-opacity:.55;transition:stroke-opacity .15s}
  .link.call{stroke:var(--edgeCall)}
  .link.both{stroke:var(--edgeBoth)}
  .link.hot{stroke-opacity:1;filter:drop-shadow(0 0 4px currentColor)}

  .node{cursor:pointer;transition:opacity .18s}
  .node circle{stroke:rgba(0,0,0,.55);stroke-width:1.5;
    filter:drop-shadow(0 3px 6px rgba(0,0,0,.5));transition:transform .15s}
  .node.ext circle{stroke-dasharray:3 3;opacity:.82}
  .node:hover circle{stroke:#fff;stroke-width:2}
  .node .ring{fill:none;stroke:#fff;stroke-opacity:0;transition:stroke-opacity .15s}
  .node:hover .ring{stroke-opacity:.5}
  .node text{fill:var(--text);font-size:11px;font-weight:500;pointer-events:none;
    paint-order:stroke;stroke:rgba(6,8,12,.9);stroke-width:3.5px}
  .node.ext text{fill:var(--muted);font-weight:400}

  #tip{position:absolute;z-index:20;pointer-events:none;display:none;max-width:360px;
    background:var(--panel);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    border:1px solid var(--line2);border-radius:11px;padding:10px 12px;font-size:12px;
    box-shadow:var(--shadow);color:var(--text)}
  #tip .t-name{font-weight:700;margin-bottom:3px}
  #tip .t-meta{color:var(--muted);font-size:11.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  #tip .t-hint{color:var(--accent);font-size:11px;margin-top:5px}

  .hint{position:absolute;bottom:14px;left:16px;z-index:5;font-size:11px;color:var(--muted);
    background:var(--panel);backdrop-filter:blur(8px);border:1px solid var(--line);
    padding:6px 11px;border-radius:999px;box-shadow:var(--shadow)}
  .empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    color:var(--muted);font-size:14px}
  @keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:scale(1)}}
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="brand">
      <h1>__TITLE__</h1>
      <span class="stat" id="stat"></span>
    </div>
    <nav class="crumbs" id="crumbs"></nav>
    <div class="spacer"></div>
    <div class="legend">
      <span class="k"><span class="dot ld"></span>directory</span>
      <span class="k"><span class="dot lf"></span>file</span>
      <span class="k"><span class="dot ln"></span>function</span>
      <span class="k"><span class="dot le"></span>neighbor</span>
    </div>
    <div class="seg" id="seg">
      <button data-v="1">Directories</button>
      <button data-v="2">Files</button>
      <button data-v="3">Functions</button>
    </div>
    <button class="iconbtn" id="btnFit" title="Fit to screen">⤢</button>
  </header>
  <div id="stage">
    <svg id="svg">
      <defs>
        <radialGradient id="gDir" cx="32%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#a9c3ff"/><stop offset="100%" stop-color="#3b6fff"/>
        </radialGradient>
        <radialGradient id="gFile" cx="32%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#9bf3df"/><stop offset="100%" stop-color="#16b894"/>
        </radialGradient>
        <radialGradient id="gFunc" cx="32%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#ffe49a"/><stop offset="100%" stop-color="#f3a712"/>
        </radialGradient>
        <radialGradient id="gExt" cx="32%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#97a0b5"/><stop offset="100%" stop-color="#525c70"/>
        </radialGradient>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#8b93a7"></path>
        </marker>
      </defs>
      <g id="viewport"><g id="links"></g><g id="nodes"></g></g>
    </svg>
    <div id="tip"></div>
    <div class="hint">Scroll to zoom · drag to pan · drag a node to move · click a directory or file to drill in</div>
  </div>
</div>
<script>
const DATA = __DATA__;
const SVGNS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("svg");
const vp = document.getElementById("viewport");
const gLinks = document.getElementById("links");
const gNodes = document.getElementById("nodes");
const tip = document.getElementById("tip");
const crumbs = document.getElementById("crumbs");
const seg = document.getElementById("seg");
const statEl = document.getElementById("stat");

let view = { layer: 1, scope: null };
let nodes = [], links = [], linkEls = [], neighbors = {};
let transform = { x: 0, y: 0, k: 1 };

const byId = {
  dir: Object.fromEntries(DATA.nodes.directories.map(d => [d.id, d])),
  file: Object.fromEntries(DATA.nodes.files.map(f => [f.id, f])),
  func: Object.fromEntries(DATA.nodes.functions.map(f => [f.id, f])),
};
const dirFiles = DATA.containment.dir_files || {};
const fileFns = DATA.containment.file_functions || {};
const FILL = { dir: "url(#gDir)", file: "url(#gFile)", func: "url(#gFunc)" };

function fileDir(id) { return (byId.file[id] || {}).dir || "."; }
function fnFile(id) { return (byId.func[id] || {}).file || id.split("::")[0]; }
function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

// ---- view builders --------------------------------------------------------
function buildDirView() {
  const ns = DATA.nodes.directories.map(d => mkNode(d.id, d.label || d.id, "dir", true,
    8 + Math.min(26, d.file_count * 2.2),
    `<div class="t-name">${esc(d.id)}</div><div class="t-meta">${d.file_count} files · ${d.func_count} functions</div><div class="t-hint">click to open files</div>`));
  const ls = DATA.edges.directory.map(e => ({ source: e.source, target: e.target, kind: "import", w: e.weight }));
  return finalize(ns, ls);
}
function buildFileView(scopeDir) {
  const inScope = scopeDir ? new Set(dirFiles[scopeDir] || []) : new Set(Object.keys(byId.file));
  const visible = new Set(inScope);
  const ls = [];
  for (const e of DATA.edges.file) {
    const a = inScope.has(e.source), b = inScope.has(e.target);
    if (scopeDir && !a && !b) continue;
    visible.add(e.source); visible.add(e.target);
    ls.push({ source: e.source, target: e.target, kind: e.kind, w: e.weight });
  }
  const ns = [...visible].map(id => {
    const f = byId.file[id] || { id, label: id.split("/").pop(), dir: fileDir(id), func_count: 0 };
    const primary = !scopeDir || inScope.has(id);
    return mkNode(id, f.label, "file", primary, 6 + Math.min(20, (f.func_count || 1) * 1.6),
      `<div class="t-name">${esc(f.label)}</div><div class="t-meta">${esc(id)}<br>${f.language || ""} · ${f.func_count || 0} functions</div><div class="t-hint">click to open functions</div>`);
  });
  return finalize(ns, ls);
}
function buildFuncView(scopeFile) {
  const inScope = scopeFile ? new Set(fileFns[scopeFile] || []) : new Set(Object.keys(byId.func));
  const visible = new Set(inScope);
  const ls = [];
  for (const e of DATA.edges.function) {
    const a = inScope.has(e.source), b = inScope.has(e.target);
    if (scopeFile && !a && !b) continue;
    visible.add(e.source); visible.add(e.target);
    ls.push({ source: e.source, target: e.target, kind: "call", w: e.weight });
  }
  const ns = [...visible].map(id => {
    const fn = byId.func[id] || { id, label: id.split("::").pop(), file: fnFile(id) };
    const primary = !scopeFile || inScope.has(id);
    const sub = primary ? "" : "  ·" + fnFile(id).split("/").pop();
    return mkNode(id, (fn.label || id) + sub, "func", primary, 6,
      `<div class="t-name">${esc(fn.label || id)}</div><div class="t-meta">${esc(fn.file)}${fn.start_line ? ":" + fn.start_line : ""}</div>`);
  });
  return finalize(ns, ls);
}
function mkNode(id, label, kind, primary, r, tipHtml) {
  return { id, label, kind, primary, r, tip: tipHtml, x: 0, y: 0, vx: 0, vy: 0, el: null };
}
function finalize(ns, ls) {
  const ids = new Set(ns.map(n => n.id));
  const filtered = ls.filter(l => ids.has(l.source) && ids.has(l.target) && l.source !== l.target);
  return { nodes: ns, links: filtered };
}

// ---- layout ---------------------------------------------------------------
function layout() {
  const W = svg.clientWidth || 1000, H = svg.clientHeight || 700, n = nodes.length;
  if (!n) return;
  const map = Object.fromEntries(nodes.map(d => [d.id, d]));
  nodes.forEach((d, i) => {
    const a = (i / n) * Math.PI * 2;
    d.x = W / 2 + Math.cos(a) * (120 + n); d.y = H / 2 + Math.sin(a) * (120 + n);
    d.vx = 0; d.vy = 0;
  });
  const k = Math.min(9000, 1400 + n * 32);
  const iters = n > 300 ? 220 : 360;
  for (let t = 0; t < iters; t++) {
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 0.01;
        const f = k / d2, d = Math.sqrt(d2), fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    for (const l of links) {
      const a = map[l.source], b = map[l.target];
      let dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 100) * 0.02, fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
    for (const d of nodes) {
      d.vx += (W / 2 - d.x) * 0.002; d.vy += (H / 2 - d.y) * 0.002;
      d.x += Math.max(-40, Math.min(40, d.vx)); d.y += Math.max(-40, Math.min(40, d.vy));
      d.vx *= 0.82; d.vy *= 0.82;
    }
  }
}

// ---- render ---------------------------------------------------------------
function edgePath(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, dist = Math.hypot(dx, dy) || 1;
  const off = Math.min(46, dist * 0.16);
  const mx = (a.x + b.x) / 2 - (dy / dist) * off, my = (a.y + b.y) / 2 + (dx / dist) * off;
  return `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`;
}
function render() {
  gLinks.textContent = ""; gNodes.textContent = ""; linkEls = []; neighbors = {};
  const map = Object.fromEntries(nodes.map(d => [d.id, d]));
  for (const l of links) {
    (neighbors[l.source] = neighbors[l.source] || new Set()).add(l.target);
    (neighbors[l.target] = neighbors[l.target] || new Set()).add(l.source);
    const a = map[l.source], b = map[l.target];
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("class", "link " + (l.kind || "import"));
    p.setAttribute("d", edgePath(a, b));
    p.setAttribute("stroke-width", Math.min(4, 0.7 + (l.w || 1) * 0.45));
    p.setAttribute("marker-end", "url(#arrow)");
    gLinks.appendChild(p);
    linkEls.push({ el: p, l });
  }
  nodes.forEach((d, i) => {
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "node" + (d.primary ? "" : " ext"));
    g.setAttribute("transform", `translate(${d.x},${d.y})`);
    g.style.animation = "pop .25s ease both";
    g.style.animationDelay = Math.min(0.4, i * 0.004) + "s";
    const ring = document.createElementNS(SVGNS, "circle");
    ring.setAttribute("class", "ring"); ring.setAttribute("r", d.r + 4);
    g.appendChild(ring);
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("r", d.r);
    c.setAttribute("fill", d.primary ? FILL[d.kind] : "url(#gExt)");
    g.appendChild(c);
    const tx = document.createElementNS(SVGNS, "text");
    tx.setAttribute("x", d.r + 5); tx.setAttribute("y", 4);
    tx.textContent = d.label.length > 36 ? d.label.slice(0, 35) + "…" : d.label;
    g.appendChild(tx);
    d.el = g;
    attachNodeEvents(g, d);
    gNodes.appendChild(g);
  });
  applyTransform();
}
function applyTransform() {
  vp.setAttribute("transform", `translate(${transform.x},${transform.y}) scale(${transform.k})`);
}

// ---- focus highlight ------------------------------------------------------
function focusNode(d) {
  const nb = neighbors[d.id] || new Set();
  for (const n of nodes) n.el.style.opacity = (n.id === d.id || nb.has(n.id)) ? "1" : "0.13";
  for (const le of linkEls) {
    const on = le.l.source === d.id || le.l.target === d.id;
    le.el.style.strokeOpacity = on ? "1" : "0.05";
    le.el.classList.toggle("hot", on);
  }
}
function clearFocus() {
  for (const n of nodes) n.el.style.opacity = "";
  for (const le of linkEls) { le.el.style.strokeOpacity = ""; le.el.classList.remove("hot"); }
}

// ---- interaction ----------------------------------------------------------
function attachNodeEvents(g, d) {
  let dragging = false, moved = false, start = null;
  g.addEventListener("pointerdown", (e) => {
    e.stopPropagation(); dragging = true; moved = false;
    start = { px: e.clientX, py: e.clientY, x: d.x, y: d.y };
    g.setPointerCapture(e.pointerId);
  });
  g.addEventListener("pointermove", (e) => {
    if (dragging) {
      const dx = (e.clientX - start.px) / transform.k, dy = (e.clientY - start.py) / transform.k;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      d.x = start.x + dx; d.y = start.y + dy;
      g.setAttribute("transform", `translate(${d.x},${d.y})`);
      updateLinksFor(d);
    }
    moveTip(e);
  });
  g.addEventListener("pointerup", () => { dragging = false; if (!moved) onNodeClick(d); });
  g.addEventListener("pointerenter", (e) => { showTip(e, d.tip); focusNode(d); });
  g.addEventListener("pointerleave", () => { hideTip(); clearFocus(); });
}
function updateLinksFor(d) {
  const map = Object.fromEntries(nodes.map(n => [n.id, n]));
  for (const le of linkEls) {
    if (le.l.source === d.id || le.l.target === d.id) {
      le.el.setAttribute("d", edgePath(map[le.l.source], map[le.l.target]));
    }
  }
}
function onNodeClick(d) {
  if (d.kind === "dir") setView(2, d.id);
  else if (d.kind === "file") setView(3, d.id);
}
function setView(layer, scope) {
  view = { layer, scope };
  let built;
  if (layer === 1) built = buildDirView();
  else if (layer === 2) built = buildFileView(scope);
  else built = buildFuncView(scope);
  nodes = built.nodes; links = built.links;
  layout(); render(); renderCrumbs(); updateSeg(); updateStat();
  fit();
}
function updateStat() {
  statEl.textContent = `${nodes.length} nodes · ${links.length} edges`;
}
function updateSeg() {
  for (const b of seg.querySelectorAll("button")) {
    const v = +b.dataset.v;
    b.classList.toggle("active", v === view.layer && (view.layer === 1 || !view.scope));
  }
}
seg.addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  setView(+b.dataset.v, null);
});

// ---- breadcrumbs ----------------------------------------------------------
function chip(text, fn, cur) {
  const a = document.createElement("span");
  a.className = "chip" + (cur ? " cur" : ""); a.textContent = text;
  if (fn) a.onclick = fn;
  return a;
}
function renderCrumbs() {
  const parts = [];
  if (view.layer === 1) parts.push(chip("Directories", null, true));
  else if (view.layer === 2 && view.scope) {
    parts.push(chip("Directories", () => setView(1, null)));
    parts.push(chip(view.scope, null, true));
  } else if (view.layer === 3 && view.scope) {
    const dir = fileDir(view.scope);
    parts.push(chip("Directories", () => setView(1, null)));
    parts.push(chip(dir, () => setView(2, dir)));
    parts.push(chip(view.scope.split("/").pop(), null, true));
  } else {
    parts.push(chip(view.layer === 2 ? "All files" : "All functions", null, true));
  }
  crumbs.textContent = "";
  parts.forEach((p, i) => {
    if (i) { const s = document.createElement("span"); s.className = "sep"; s.textContent = "›"; crumbs.appendChild(s); }
    crumbs.appendChild(p);
  });
}

// ---- tooltip --------------------------------------------------------------
function showTip(e, html) { if (!html) return; tip.innerHTML = html; tip.style.display = "block"; moveTip(e); }
function moveTip(e) {
  const pad = 16, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > window.innerWidth) x = e.clientX - w - pad;
  if (y + h > window.innerHeight) y = e.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip() { tip.style.display = "none"; }

// ---- pan / zoom -----------------------------------------------------------
let panning = false, panStart = null;
svg.addEventListener("pointerdown", (e) => {
  panning = true; svg.classList.add("panning");
  panStart = { px: e.clientX, py: e.clientY, x: transform.x, y: transform.y };
});
svg.addEventListener("pointermove", (e) => {
  if (!panning) return;
  transform.x = panStart.x + (e.clientX - panStart.px);
  transform.y = panStart.y + (e.clientY - panStart.py); applyTransform();
});
window.addEventListener("pointerup", () => { panning = false; svg.classList.remove("panning"); });
svg.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = svg.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const f = e.deltaY < 0 ? 1.12 : 1 / 1.12, nk = Math.max(0.08, Math.min(4, transform.k * f));
  transform.x = mx - (mx - transform.x) * (nk / transform.k);
  transform.y = my - (my - transform.y) * (nk / transform.k);
  transform.k = nk; applyTransform();
}, { passive: false });

function fit() {
  if (!nodes.length) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const d of nodes) { minX = Math.min(minX, d.x); minY = Math.min(minY, d.y); maxX = Math.max(maxX, d.x); maxY = Math.max(maxY, d.y); }
  const W = svg.clientWidth, H = svg.clientHeight, gw = (maxX - minX) || 1, gh = (maxY - minY) || 1;
  const k = Math.max(0.08, Math.min(1.8, 0.82 * Math.min(W / gw, H / gh)));
  transform.k = k;
  transform.x = W / 2 - ((minX + maxX) / 2) * k;
  transform.y = H / 2 - ((minY + maxY) / 2) * k;
  applyTransform();
}
document.getElementById("btnFit").onclick = fit;
window.addEventListener("resize", fit);

setView(1, null);
</script>
</body>
</html>
"""


def render_layered_html(graph: LayeredGraph, title: str | None = None) -> str:
    """Render a :class:`LayeredGraph` to a self-contained, modern HTML document.

    Args:
        graph: The layered graph model.
        title: Optional page title (defaults to the project root).

    Returns:
        A complete HTML document as a string (no external dependencies).
    """
    data = graph.to_dict()
    page_title = title or f"Dependency Graph — {graph.project_root}"
    payload = json.dumps(data, ensure_ascii=False)
    return _TEMPLATE.replace("__DATA__", payload).replace("__TITLE__", _escape(page_title))


def _escape(text: str) -> str:
    """Escape a string for safe inclusion in HTML text/title.

    Args:
        text: Raw text.

    Returns:
        HTML-escaped text.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
