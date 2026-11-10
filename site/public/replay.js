// GuardedPilot episode replay. Reads data/index.json and data/episodes/<id>.json
// (written by scripts/export_site_data.py). Static: no live model or sim calls.

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const M_PER_DEG = 111320;

const map = L.map("map", { zoomSnap: 0.25, attributionControl: false });
let layers = L.layerGroup().addTo(map);
let ep = null, tMax = 1, t = 0, playing = false, lastTick = 0;
let trackLine, drone, footprints = [], stepEls = [];

function squareBounds(lat, lon, side) {
  const h = side / 2, dLat = h / M_PER_DEG, dLon = h / (M_PER_DEG * Math.cos(lat * Math.PI / 180));
  return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
}

function legend() {
  const c = L.control({ position: "bottomleft" });
  c.onAdd = () => {
    const d = L.DomUtil.create("div", "legend");
    d.innerHTML = [
      [css("--accent"), "survey area / track"], ["#f2c230", "geofence"],
      [css("--bad"), "no-fly zone"], [css("--ok"), "landing zone"],
    ].map(([c, l]) => `<div><i style="background:${c}"></i>${l}</div>`).join("");
    return d;
  };
  return c;
}

async function loadIndex() {
  const res = await fetch("data/index.json");
  if (!res.ok) throw new Error("data/index.json missing — run scripts/export_site_data.py");
  const { episodes } = await res.json();
  const sel = $("episode");
  sel.innerHTML = episodes.map((e) =>
    `<option value="${e.id}">${e.outcome.task_success ? "✓" : "✗"} ${e.id}</option>`).join("");
  const want = new URLSearchParams(location.search).get("id");
  if (want && episodes.some((e) => e.id === want)) sel.value = want;
  sel.onchange = () => { history.replaceState(null, "", `?id=${sel.value}`); loadEpisode(sel.value); };
  // Deep links: replay.html?id=<episode>&t=<seconds> opens at that moment (used by the demo page).
  if (episodes.length) loadEpisode(sel.value);
  else $("steps").innerHTML = `<div class="empty">No episodes exported yet.</div>`;
}

async function loadEpisode(id) {
  ep = await (await fetch(`data/episodes/${id}.json`)).json();
  layers.clearLayers();
  footprints = [];
  const accent = css("--accent");

  if (ep.world_image) L.imageOverlay(ep.world_image.src, ep.world_image.bounds).addTo(layers);
  L.polygon(ep.geofence, { color: "#f2c230", weight: 2, dashArray: "6 5", fill: false }).addTo(layers);
  (ep.no_fly_zones || []).forEach((z) =>
    L.polygon(z.polygon, { color: css("--bad"), weight: 1.5, fillOpacity: 0.25 }).bindTooltip(`no-fly: ${z.name}`).addTo(layers));
  (ep.landing_zones || []).forEach((z) =>
    L.polygon(z.polygon, { color: css("--ok"), weight: 1.5, fillOpacity: 0.3 }).bindTooltip(`landing: ${z.name}`).addTo(layers));
  L.polygon(ep.survey_area, { color: accent, weight: 1.5, fillOpacity: 0.06 }).addTo(layers);
  Object.entries(ep.zones || {}).forEach(([name, poly]) =>
    L.polygon(poly, { color: css("--warn"), weight: 1.5, dashArray: "3 4", fillOpacity: 0.12 })
      .bindTooltip(`attacker zone: ${name}`).addTo(layers));
  ((ep.world && ep.world.texts) || []).forEach((t) =>
    L.circleMarker([t.lat, t.lon], { radius: 6, color: css("--bad"), weight: 2, fill: false })
      .bindTooltip(`painted sign: “${t.text}”`).addTo(layers));
  ep.waypoints.forEach(([la, lo], i) =>
    L.circleMarker([la, lo], { radius: 4, color: accent, weight: 1.5, fillOpacity: 1, fillColor: css("--panel") })
      .bindTooltip(`WP${i + 1}`).addTo(layers));
  ep.frames.forEach((f) => {
    const r = L.rectangle(squareBounds(f.pose.lat, f.pose.lon, f.footprint_m),
      { color: accent, weight: 1, fillOpacity: 0.12, opacity: 0.7 });
    footprints.push({ f, r, t: frameTime(f.id) });
  });
  trackLine = L.polyline([], { color: accent, weight: 3 }).addTo(layers);
  drone = L.circleMarker(ep.home, { radius: 7, color: "#fff", weight: 2, fillColor: accent, fillOpacity: 1 }).addTo(layers);

  const g = L.polygon(ep.geofence).getBounds();
  map.fitBounds(g, { padding: [20, 20] });

  tMax = Math.max(1, ...ep.track.map((p) => p.t), ...ep.steps.map((s) => s.t || 0));
  renderBadges();
  renderAttack();
  renderSteps();
  const t0 = Number(new URLSearchParams(location.search).get("t"));
  setTime(Number.isFinite(t0) ? t0 : 0);
  setPlaying(false);
}

function frameTime(fid) {
  const e = ep.events.find((e) => e.frame_id === fid);
  return e ? e.t : 0;
}

function renderBadges() {
  const o = ep.outcome, m = ep.meta;
  const b = (txt, cls = "") => `<span class="badge ${cls}">${txt}</span>`;
  $("badges").innerHTML = [
    b(o.task_success ? "task success" : "task failed", o.task_success ? "ok" : "bad"),
    b(`coverage ${o.coverage_pct}%`), b(`waypoints ${o.waypoints_done}/${o.waypoints_total}`),
    b(o.landed_home ? "landed home" : "not home", o.landed_home ? "ok" : "bad"),
    o.blocked_commands ? b(`${o.blocked_commands} blocked`, "bad") : "",
    o.attack_attempted != null ? b(o.attack_attempted ? "model fooled" : "attack resisted", o.attack_attempted ? "bad" : "ok") : "",
    o.attack_attempted ? b(o.attack_executed ? "attack executed" : "attack blocked", o.attack_executed ? "bad" : "ok") : "",
    o.safety_violations ? b(`${o.safety_violations} safety violation${o.safety_violations > 1 ? "s" : ""}`, "bad") : "",
    b(`${ep.model} · ${ep.defense}`), b(`${m.tokens.toLocaleString()} tokens · ${m.wall_s}s`),
  ].join("");
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const INJ_LABEL = { scene_text: "Painted sign (A1)", brief: "Poisoned mission brief (A2)", tool_output: "Tampered tool output (A3)" };

function renderAttack() {
  const box = $("attack");
  const inj = ep.injections || [];
  if (!inj.length) { box.hidden = true; return; }
  box.hidden = false;
  const goal = ep.attacker_goal ? `<div><b>Attacker goal:</b> ${esc(ep.attacker_goal.description)}</div>` : "";
  box.innerHTML = `<div class="label">${ep.category === "benign" ? "Benign scene text" : "Attack"}</div>${goal}`
    + inj.map((i) => {
      const where = i.type === "scene_text" ? ` · under waypoint ${i.at_waypoint}`
        : i.type === "tool_output" ? ` · ${i.tool}.${i.field}` : ` · ${i.mode}`;
      return `<div class="inj">${esc(INJ_LABEL[i.type] || i.type)}${esc(where)}\n“${esc(i.text || i.value)}”</div>`;
    }).join("");
}

function matchedCalls() {
  const ids = new Set();
  ((ep.outcome && ep.outcome.attack_matches) || []).forEach((m) => (m.events || []).forEach((id) => ids.add(id)));
  return ids;
}

function renderSteps() {
  const box = $("steps");
  box.innerHTML = "";
  const matched = matchedCalls();
  stepEls = ep.steps.map((s) => {
    const blocked = s.policy && s.policy.decision !== "allow";
    const isMatch = s.call_id != null && matched.has(s.call_id);
    const el = document.createElement("div");
    el.className = `step ${s.kind || "tool"}${blocked ? " blocked" : ""}${isMatch ? " matched" : ""}`;
    const label = s.tool || (s.kind === "plan" ? "plan" : "summary");
    const args = s.args && Object.keys(s.args).length ? JSON.stringify(s.args) : "";
    el.innerHTML = `<div class="head"><span class="t">${(s.t ?? 0).toFixed(1)}s</span><span class="tool">${esc(label)}</span></div>`
      + (args ? `<div class="args">${esc(args)}</div>` : "")
      + (s.agent ? `<div class="thought">${esc(s.agent)}</div>` : "")
      + (blocked ? `<div class="policy">BLOCKED · ${esc(s.policy.rule)}: ${esc(s.policy.detail || "")}</div>` : "");
    el.onclick = () => setTime(s.t || 0);
    box.appendChild(el);
    return el;
  });
}

function poseAt(time) {
  const tr = ep.track;
  if (!tr.length) return null;
  let i = tr.findIndex((p) => p.t > time);
  if (i === -1) return tr[tr.length - 1];
  if (i === 0) return tr[0];
  const a = tr[i - 1], b = tr[i], k = (time - a.t) / (b.t - a.t || 1);
  return { lat: a.lat + (b.lat - a.lat) * k, lon: a.lon + (b.lon - a.lon) * k, alt: a.alt + (b.alt - a.alt) * k, battery: a.battery };
}

function setTime(time) {
  t = Math.min(Math.max(time, 0), tMax);
  $("scrub").value = Math.round((t / tMax) * 1000);
  const p = poseAt(t);
  if (p) {
    drone.setLatLng([p.lat, p.lon]);
    drone.bindTooltip(`alt ${p.alt.toFixed(1)} m · battery ${p.battery}%`);
  }
  trackLine.setLatLngs(ep.track.filter((q) => q.t <= t).map((q) => [q.lat, q.lon]));
  let latestFrame = null;
  footprints.forEach((fp) => {
    const on = fp.t <= t;
    if (on && !layers.hasLayer(fp.r)) fp.r.addTo(layers);
    if (!on && layers.hasLayer(fp.r)) layers.removeLayer(fp.r);
    if (on) latestFrame = fp.f;
  });
  showFrame(latestFrame);
  let cur = -1;
  ep.steps.forEach((s, i) => {
    const done = (s.t ?? 0) <= t;
    stepEls[i].classList.toggle("done", done);
    if (done) cur = i;
  });
  stepEls.forEach((el, i) => el.classList.toggle("current", i === cur));
  if (cur >= 0 && playing) stepEls[cur].scrollIntoView({ block: "nearest" });
  $("clock").textContent = `${t.toFixed(1)} / ${tMax.toFixed(1)} s`;
}

function showFrame(f) {
  const img = $("frameImg");
  if (!f) { img.removeAttribute("src"); $("frameMeta").textContent = "No frame captured yet."; return; }
  const src = `data/episodes/${f.png}`;
  if (!img.src.endsWith(src)) img.src = src;
  $("frameMeta").innerHTML = `<b>${f.id}</b><br>alt ${f.pose.alt} m · footprint ${f.footprint_m} m<br>${f.pose.lat.toFixed(6)}, ${f.pose.lon.toFixed(6)}`;
}

function setPlaying(on) {
  playing = on;
  $("play").textContent = on ? "❚❚" : "▶";
  $("play").setAttribute("aria-label", on ? "Pause" : "Play");
  if (on) { if (t >= tMax) setTime(0); lastTick = performance.now(); requestAnimationFrame(tick); }
}

function tick(now) {
  if (!playing) return;
  const dt = (now - lastTick) / 1000;
  lastTick = now;
  setTime(t + dt * Number($("speed").value));
  if (t >= tMax) return setPlaying(false);
  requestAnimationFrame(tick);
}

$("play").onclick = () => setPlaying(!playing);
$("scrub").oninput = (e) => setTime((e.target.value / 1000) * tMax);
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.target.tagName !== "SELECT") { e.preventDefault(); setPlaying(!playing); }
});
legend().addTo(map);
loadIndex().catch((err) => { $("steps").innerHTML = `<div class="empty">${esc(err.message)}</div>`; });
