/* rosscope dashboard. Plain ES, no build step.
   Live data over /ws/live; history + health over the REST API. */
"use strict";

const ROBOT_COLORS = ["#4fb8d4", "#ff8a3d", "#5fb98e", "#c08af0", "#e0b341"];
const colorFor = (() => {
  const map = new Map();
  return (id) => {
    if (!map.has(id)) map.set(id, ROBOT_COLORS[map.size % ROBOT_COLORS.length]);
    return map.get(id);
  };
})();

// ---- shared state -------------------------------------------------------- //
const robots = new Map();           // id -> { lastMetrics, buffers, trail, marker, line }
const SERIES = ["voltage", "cpu_temp"];
const BUF = 600;                    // samples kept per chart series
let selected = null;
let msgWindow = [];                  // timestamps for msg/s readout
let mode = "live";                   // "live" | "replay"
let replay = null;                   // { data, t0, t1, cur, playing, speed, timer }

function robot(id) {
  if (!robots.has(id)) {
    robots.set(id, {
      buffers: { voltage: { t: [], v: [] }, cpu_temp: { t: [], v: [] } },
      trail: [],
    });
    refreshRobotSelect();
    rebuildScene();
  }
  return robots.get(id);
}

// ---- websocket ----------------------------------------------------------- //
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  const conn = document.getElementById("conn");

  ws.onopen = () => {
    conn.className = "conn conn--up"; conn.innerHTML = "<i></i>live";
    document.getElementById("offline").hidden = true;
  };
  ws.onclose = () => {
    conn.className = "conn conn--down"; conn.innerHTML = "<i></i>reconnecting";
    document.getElementById("offline").hidden = false;
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "sample") onSample(m.data);
    else if (m.type === "alert") onAlert(m.data, true);
  };
}

function onSample(s) {
  const now = performance.now();
  msgWindow.push(now);
  const r = robot(s.robot_id);

  if (s.kind === "scalar") {
    for (const [metric, value] of Object.entries(s.metrics)) {
      const buf = r.buffers[metric];
      if (!buf) continue;
      buf.t.push(s.ts); buf.v.push(value);
      if (buf.t.length > BUF) { buf.t.shift(); buf.v.shift(); }
    }
  } else if (s.kind === "pose") {
    r.pose = s.pose;
    r.trail.push([s.pose.x, s.pose.z, -s.pose.y]); // map ROS xyz -> three xyz
    if (r.trail.length > 400) r.trail.shift();
  } else if (s.kind === "map") {
    setMap(s.map);
  } else if (s.kind === "scan") {
    updateScan(r, s.scan, s.robot_id);
  }
}

// ---- fleet KPIs ---------------------------------------------------------- //
setInterval(() => {
  const cut = performance.now() - 1000;
  msgWindow = msgWindow.filter((t) => t > cut);
  if (mode === "replay") return;
  document.getElementById("kpi-rate").textContent = msgWindow.length;
}, 500);

async function pollSummary() {
  if (mode === "replay") return;
  try {
    const s = await (await fetch("/api/summary")).json();
    document.getElementById("kpi-online").textContent = s.robots_online;

    const alerts = document.getElementById("kpi-alerts");
    alerts.textContent = s.active_alerts;
    alerts.className = "kpi__val" + (s.active_alerts > 0 ? " warn" : "");

    const batt = document.getElementById("kpi-batt");
    if (s.min_voltage == null) { batt.textContent = "—"; batt.className = "kpi__val"; }
    else {
      batt.textContent = s.min_voltage.toFixed(1) + " V";
      batt.className = "kpi__val" + (s.min_voltage < 20.5 ? " alarm" : s.min_voltage < 22 ? " warn" : "");
    }
  } catch (_) {}
}
setInterval(pollSummary, 2000);

// ---- robot selector ------------------------------------------------------ //
function refreshRobotSelect() {
  const sel = document.getElementById("robot-select");
  const ids = [...robots.keys()].sort();
  sel.innerHTML = ids.map((id) => `<option value="${id}">${id}</option>`).join("");
  if (!selected || !robots.has(selected)) { selected = ids[0]; sel.value = selected; }
}
document.getElementById("robot-select").addEventListener("change", (e) => { selected = e.target.value; renderLegend(); });

// ---- charts (uPlot) ------------------------------------------------------ //
function makeChart(el, stroke) {
  const fill = (u) => {
    const g = u.ctx.createLinearGradient(0, u.bbox.top, 0, u.bbox.top + u.bbox.height);
    g.addColorStop(0, stroke + "44");   // tinted near the line
    g.addColorStop(1, stroke + "00");   // fading to transparent
    return g;
  };
  const opts = {
    width: el.clientWidth || 360, height: 120,
    cursor: { show: true }, legend: { show: false },
    scales: { x: { time: true } },
    axes: [
      { stroke: "#6b7b8a", grid: { stroke: "#24303b" }, ticks: { stroke: "#24303b" }, size: 30 },
      { stroke: "#6b7b8a", grid: { stroke: "#24303b" }, ticks: { stroke: "#24303b" }, size: 44 },
    ],
    series: [{}, { stroke, width: 2, fill, points: { show: false } }],
  };
  return new uPlot(opts, [[], []], el);
}
const chartBatt = makeChart(document.getElementById("chart-batt"), "#4fb8d4");
const chartTemp = makeChart(document.getElementById("chart-temp"), "#ff8a3d");

setInterval(() => {
  if (mode === "replay") return;            // replay drives charts on its own tick
  if (!selected || !robots.has(selected)) return;
  const r = robots.get(selected);
  chartBatt.setData([r.buffers.voltage.t, r.buffers.voltage.v]);
  chartTemp.setData([r.buffers.cpu_temp.t, r.buffers.cpu_temp.v]);
}, 300);
window.addEventListener("resize", () => {
  [["chart-batt", chartBatt], ["chart-temp", chartTemp]].forEach(([id, c]) =>
    c.setSize({ width: document.getElementById(id).clientWidth, height: 120 }));
  resizeScene();
});

// ---- 3D pose viewer (Three.js) ------------------------------------------ //
let scene, camera, renderer, grid;
let mapMesh = null;
let az = 0.7, el = 0.9, radius = 16, autorotate = true;

function initScene() {
  const host = document.getElementById("scene");
  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0e1419, 20, 60);
  camera = new THREE.PerspectiveCamera(50, host.clientWidth / host.clientHeight, 0.1, 200);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(host.clientWidth, host.clientHeight);
  host.appendChild(renderer.domElement);

  grid = new THREE.GridHelper(40, 40, 0x33424f, 0x202b34);
  scene.add(grid);
  scene.add(new THREE.AmbientLight(0xffffff, 0.8));
  const dir = new THREE.DirectionalLight(0xffffff, 0.55);
  dir.position.set(5, 12, 8); scene.add(dir);

  // drag to orbit
  let dragging = false, lx = 0, ly = 0;
  host.addEventListener("pointerdown", (e) => { dragging = true; autorotate = false; lx = e.clientX; ly = e.clientY; });
  window.addEventListener("pointerup", () => { dragging = false; });
  window.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    az -= (e.clientX - lx) * 0.01; el = Math.max(0.15, Math.min(1.5, el - (e.clientY - ly) * 0.01));
    lx = e.clientX; ly = e.clientY;
  });
  host.addEventListener("wheel", (e) => { e.preventDefault(); radius = Math.max(6, Math.min(40, radius + e.deltaY * 0.02)); }, { passive: false });
  animate();
}

function ensureRobotVisual(id) {
  const r = robot(id);
  if (r.marker) return r;
  const c = new THREE.Color(colorFor(id));
  const cone = new THREE.Mesh(
    new THREE.ConeGeometry(0.45, 1.2, 16),
    new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.6 }));
  cone.rotation.x = Math.PI / 2;
  r.marker = cone; scene.add(cone);
  r.line = new THREE.Line(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.9 }));
  scene.add(r.line);
  return r;
}

// Build a trail that fades from dim (oldest) to bright (newest) via vertex colors.
function setTrail(line, pts, hex) {
  const n = pts.length;
  if (n < 2) return;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const base = new THREE.Color(hex);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = pts[i].x; pos[i * 3 + 1] = pts[i].y; pos[i * 3 + 2] = pts[i].z;
    const f = 0.12 + 0.88 * (i / (n - 1));   // brightness ramp tail -> head
    col[i * 3] = base.r * f; col[i * 3 + 1] = base.g * f; col[i * 3 + 2] = base.b * f;
  }
  const g = line.geometry;
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  g.setDrawRange(0, n);
  g.computeBoundingSphere();
}

// Render an occupancy grid as a textured floor plane aligned to world coords.
function setMap(map) {
  if (!scene || !map || !map.data || !map.width) return;
  const { width: W, height: H, resolution: res, origin_x: ox, origin_y: oy, data } = map;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d");
  const img = ctx.createImageData(W, H);
  for (let j = 0; j < H; j++) {
    for (let i = 0; i < W; i++) {
      const v = data[j * W + i];
      // free -> dark slate, occupied -> bright wall, unknown -> mid.
      let cr = 22, cg = 30, cb = 38, a = 235;
      if (v < 0) { cr = 28; cg = 36; cb = 44; a = 120; }
      else if (v >= 65) { cr = 95; cg = 150; cb = 180; a = 255; }
      else if (v > 0) { cr = 60; cg = 90; cb = 110; }
      const px = i, py = H - 1 - j;          // flip rows: grid j=0 is origin (bottom)
      const idx = (py * W + px) * 4;
      img.data[idx] = cr; img.data[idx + 1] = cg; img.data[idx + 2] = cb; img.data[idx + 3] = a;
    }
  }
  ctx.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(cv);
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.LinearFilter;

  if (mapMesh) { scene.remove(mapMesh); mapMesh.geometry.dispose(); mapMesh.material.dispose(); }
  const geo = new THREE.PlaneGeometry(W * res, H * res);
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true });
  mapMesh = new THREE.Mesh(geo, mat);
  mapMesh.rotation.x = -Math.PI / 2;
  mapMesh.position.set(ox + W * res / 2, -0.02, -(oy + H * res / 2));  // just below the grid
  scene.add(mapMesh);
}

// Render a laser scan as a point cloud around the robot (robot-frame -> world).
function updateScan(r, scan, id) {
  if (!scene || !r.pose || !scan || !scan.ranges) return;
  const yaw = 2 * Math.atan2(r.pose.qz || 0, r.pose.qw || 1);
  const { angle_min: a0, angle_increment: inc, range_max: rmax, ranges } = scan;
  const pts = [];
  for (let k = 0; k < ranges.length; k++) {
    const d = ranges[k];
    if (!(d > 0) || d >= (rmax || 1e9) * 0.999) continue;   // skip no-return beams
    const a = yaw + a0 + k * inc;
    const wx = r.pose.x + d * Math.cos(a);
    const wy = r.pose.y + d * Math.sin(a);
    pts.push(wx, 0.06, -wy);                                  // ROS xy -> three xz
  }
  if (!r.scanPoints) {
    r.scanPoints = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({ color: colorFor(id), size: 0.14,
        sizeAttenuation: true, transparent: true, opacity: 0.85 }));
    scene.add(r.scanPoints);
  }
  const arr = new Float32Array(pts);
  r.scanPoints.geometry.setAttribute("position", new THREE.BufferAttribute(arr, 3));
  r.scanPoints.geometry.setDrawRange(0, pts.length / 3);
  r.scanPoints.geometry.computeBoundingSphere();
}

function rebuildScene() {
  if (!scene) return;
  for (const id of robots.keys()) ensureRobotVisual(id);
  renderLegend();
}

function renderLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = [...robots.keys()].sort()
    .map((id) => `<span data-robot="${id}" class="${id === selected ? "active" : ""}">
      <b style="background:${colorFor(id)}"></b>${id}</span>`).join("");
  el.querySelectorAll("span[data-robot]").forEach((span) => {
    span.onclick = () => {
      selected = span.dataset.robot;
      document.getElementById("robot-select").value = selected;
      renderLegend();
    };
  });
}

function animate() {
  requestAnimationFrame(animate);
  if (autorotate) az += 0.0015;
  camera.position.set(radius * Math.cos(az) * Math.sin(el), radius * Math.cos(el), radius * Math.sin(az) * Math.sin(el));
  camera.lookAt(0, 0, 0);
  if (mode === "replay") {
    for (const [, r] of robots) if (r.scanPoints) r.scanPoints.visible = false;
    renderReplayScene();
  } else {
    const pulse = 1 + 0.06 * Math.sin(performance.now() * 0.004);
    for (const [id, r] of robots) {
      if (r.scanPoints) r.scanPoints.visible = true;
      if (r.marker && r.pose) {
        r.marker.position.set(r.pose.x, 0.6, -r.pose.y);
        r.marker.scale.setScalar(pulse);
      }
      if (r.line && r.trail.length > 1) {
        setTrail(r.line, r.trail.map((p) => new THREE.Vector3(p[0], 0.05, p[2])), colorFor(id));
      }
    }
  }
  renderer.render(scene, camera);
}
function resizeScene() {
  if (!renderer) return;
  const host = document.getElementById("scene");
  camera.aspect = host.clientWidth / host.clientHeight; camera.updateProjectionMatrix();
  renderer.setSize(host.clientWidth, host.clientHeight);
}

// ---- topic health -------------------------------------------------------- //
async function pollHealth() {
  if (mode === "replay") return;
  try {
    const rows = await (await fetch("/api/health")).json();
    const now = Date.now() / 1000;
    document.getElementById("health").innerHTML = rows.map((c) => {
      const gap = now - c.last_seen;
      const cls = gap > 3 ? "stale" : (c.hz < 1 ? "warn" : "");
      return `<div class="channel ${cls}">
        <div class="channel__top"><span class="channel__robot">${c.robot_id}</span>
        <span class="channel__hz">${gap > 3 ? "stale" : c.hz + " Hz"}</span></div>
        <div class="channel__topic">${c.topic}</div></div>`;
    }).join("") || `<div class="empty">waiting for telemetry…</div>`;
  } catch (_) {}
}
setInterval(pollHealth, 2000);

// ---- alerts -------------------------------------------------------------- //
let alertCount = 0;
function onAlert(a, live) {
  if (live && mode === "replay") return;       // replay rebuilds the feed itself
  alertCount++;
  document.getElementById("alert-badge").textContent = alertCount;
  const ul = document.getElementById("alerts");
  const empty = ul.querySelector(".empty"); if (empty) empty.remove();
  const t = new Date((a.time || a.ts) * 1000).toLocaleTimeString();
  const li = document.createElement("li");
  li.className = `alert ${a.severity}`;
  li.innerHTML = `<i></i><div><div class="alert__msg">${a.message}</div>
    <div class="alert__meta">${a.robot_id}${a.topic ? " · " + a.topic : ""} · ${a.rule}</div></div>
    <span class="alert__time">${t}</span>`;
  ul.prepend(li);
  while (ul.children.length > 60) ul.lastChild.remove();
}

async function loadAlerts() {
  try {
    const rows = await (await fetch("/api/alerts?limit=20")).json();
    rows.reverse().forEach((a) => onAlert(a, false));
  } catch (_) {}
}

// ---- sessions: record & replay ------------------------------------------ //
const fmt = (s) => { s = Math.max(0, Math.floor(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };

let recId = null;
const recBtn = document.getElementById("rec-btn");
recBtn.onclick = async () => {
  if (recId == null) {
    const name = "session " + new Date().toLocaleTimeString();
    try {
      const r = await (await fetch("/api/sessions/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }) })).json();
      recId = r.id;
    } catch (_) { return; }
    recBtn.classList.add("recording"); recBtn.textContent = "■ STOP";
  } else {
    try { await fetch(`/api/sessions/${recId}/stop`, { method: "POST" }); } catch (_) {}
    recId = null; recBtn.classList.remove("recording"); recBtn.textContent = "● REC";
    loadSessions();
  }
};

async function loadSessions() {
  try {
    const rows = await (await fetch("/api/sessions")).json();
    const sel = document.getElementById("session-select");
    const cur = sel.value;
    sel.innerHTML = `<option value="">live view</option>` +
      rows.filter((s) => s.end).map((s) => `<option value="${s.id}">▶ ${s.name} (${fmt(s.seconds)})</option>`).join("");
    sel.value = cur;
  } catch (_) {}
}

document.getElementById("session-select").addEventListener("change", (e) => {
  if (!e.target.value) exitReplay(); else enterReplay(e.target.value);
});

async function enterReplay(id) {
  let data;
  try { data = await (await fetch(`/api/sessions/${id}/data`)).json(); } catch (_) { return; }
  if (!data || data.error || !data.robots.length) return;
  mode = "replay";
  replay = { data, t0: data.start, t1: data.end, cur: data.start, playing: true, speed: 1,
             last: performance.now(), lastAlertN: -1 };
  document.getElementById("replay-bar").hidden = false;
  document.getElementById("rp-name").textContent = `${data.name} · ${fmt(data.end - data.start)}`;
  document.getElementById("rp-speed").value = "1";
  const sel = document.getElementById("robot-select");
  sel.innerHTML = data.robots.map((r) => `<option value="${r}">${r}</option>`).join("");
  selected = data.robots[0]; sel.value = selected;
  data.robots.forEach(ensureRobotVisual);
  updatePlayBtn();
}

function exitReplay() {
  mode = "live"; replay = null;
  document.getElementById("replay-bar").hidden = true;
  document.getElementById("session-select").value = "";
  refreshRobotSelect();
  alertCount = 0;
  document.getElementById("alerts").innerHTML = `<li class="empty">no alerts yet</li>`;
  loadAlerts();
}

function updatePlayBtn() {
  document.getElementById("rp-play").textContent = (replay && replay.playing) ? "❚❚" : "▶";
}
document.getElementById("rp-play").onclick = () => {
  if (!replay) return;
  if (replay.cur >= replay.t1) replay.cur = replay.t0;
  replay.playing = !replay.playing; replay.last = performance.now(); updatePlayBtn();
};
document.getElementById("rp-exit").onclick = exitReplay;
document.getElementById("rp-speed").onchange = (e) => { if (replay) replay.speed = parseFloat(e.target.value); };
document.getElementById("rp-seek").oninput = (e) => {
  if (!replay) return;
  replay.cur = replay.t0 + (e.target.value / 1000) * (replay.t1 - replay.t0);
  replay.lastAlertN = -1;
};

function renderReplayScene() {
  if (!replay) return;
  for (const id of replay.data.robots) {
    const r = ensureRobotVisual(id);
    const pts = replay.data.poses[id] || [];
    const upto = [];
    for (const p of pts) { if (p.t <= replay.cur) upto.push(p); else break; }
    if (!upto.length) continue;
    const last = upto[upto.length - 1];
    r.marker.position.set(last.x, 0.6, -last.y);
    const trail = upto.slice(-400).map((p) => new THREE.Vector3(p.x, 0.05, -p.y));
    if (trail.length > 1) setTrail(r.line, trail, colorFor(id));
  }
}

function replayCharts() {
  const s = replay.data.series[selected];
  const upto = (arr) => {
    const t = [], v = [];
    for (const p of arr || []) { if (p.t <= replay.cur) { t.push(p.t); v.push(p.v); } else break; }
    return [t, v];
  };
  chartBatt.setData(s ? upto(s.voltage) : [[], []]);
  chartTemp.setData(s ? upto(s.cpu_temp) : [[], []]);
}

function replayAlertsAndKpis() {
  const shown = replay.data.alerts.filter((a) => a.t <= replay.cur);
  if (shown.length !== replay.lastAlertN) {
    replay.lastAlertN = shown.length;
    const ul = document.getElementById("alerts");
    ul.innerHTML = shown.length
      ? shown.slice().reverse().slice(0, 60).map((a) => `<li class="alert ${a.severity}"><i></i>
          <div><div class="alert__msg">${a.message}</div>
          <div class="alert__meta">${a.robot_id}${a.topic ? " · " + a.topic : ""} · ${a.rule}</div></div>
          <span class="alert__time">${fmt(a.t - replay.t0)}</span></li>`).join("")
      : `<li class="empty">no alerts yet</li>`;
    document.getElementById("alert-badge").textContent = shown.length;
  }
  document.getElementById("kpi-online").textContent = replay.data.robots.length;
  const ae = document.getElementById("kpi-alerts");
  ae.textContent = shown.length; ae.className = "kpi__val" + (shown.length ? " warn" : "");
  let minV = null;
  for (const id of replay.data.robots) {
    const arr = (replay.data.series[id] || {}).voltage || [];
    for (const p of arr) { if (p.t <= replay.cur) { if (minV == null || p.v < minV) minV = p.v; } else break; }
  }
  const be = document.getElementById("kpi-batt");
  if (minV == null) { be.textContent = "—"; be.className = "kpi__val"; }
  else { be.textContent = minV.toFixed(1) + " V"; be.className = "kpi__val" + (minV < 20.5 ? " alarm" : minV < 22 ? " warn" : ""); }
  document.getElementById("kpi-rate").textContent = replay.playing ? replay.speed + "×" : "paused";
}

setInterval(() => {
  if (mode !== "replay" || !replay) return;
  const now = performance.now();
  const dt = (now - replay.last) / 1000; replay.last = now;
  if (replay.playing) {
    replay.cur = Math.min(replay.t1, replay.cur + dt * replay.speed);
    if (replay.cur >= replay.t1) { replay.playing = false; updatePlayBtn(); }
  }
  const frac = (replay.cur - replay.t0) / Math.max(0.001, replay.t1 - replay.t0);
  document.getElementById("rp-seek").value = Math.round(frac * 1000);
  document.getElementById("rp-time").textContent = `${fmt(replay.cur - replay.t0)} / ${fmt(replay.t1 - replay.t0)}`;
  replayCharts();
  replayAlertsAndKpis();
}, 50);

// ---- boot ---------------------------------------------------------------- //
async function loadMap() {
  try {
    const res = await (await fetch("/api/map")).json();
    if (res && res.map) setMap(res.map);
  } catch (_) { /* map appears on the next periodic publish */ }
}

document.getElementById("alerts").innerHTML = `<li class="empty">no alerts yet</li>`;
initScene();
loadMap();
loadAlerts();
pollHealth();
pollSummary();
loadSessions();
setInterval(loadSessions, 5000);
connect();