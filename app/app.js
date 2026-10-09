"use strict";

// ---------------------------------------------------------------- helpers

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const enc = encodeURIComponent;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" }); // relative on purpose: served under /app/
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmt(seconds) {
  const t = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

// "42s" or "3m 05s", the way the script says it.
function fmtLost(seconds) {
  const t = Math.max(0, Math.round(Number(seconds) || 0));
  return t < 60 ? `${t}s` : `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, "0")}s`;
}

const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
const videoURL = (source) => `video?source=${enc(source)}`;
const clipOf = (item) => item.clip || String(item.source || "").split("/").pop();
const endOf = (e) => (Number(e.end) > Number(e.start) ? Number(e.end) : (Number(e.start) || 0) + 5);

function clipLabel(clip) {
  if (!clip) return "clip";
  const base = String(clip).split("/").pop().replace(/\.mp4$/i, "").replace(/^\d{8}_\d{6}_/, "");
  const m = /^(.*)_chunk_(\d+)/.exec(base);
  return m ? `${m[1]} · chunk ${parseInt(m[2], 10)}` : base;
}

// Fallback only; the API's "camera" field wins when present.
function cameraOf(item) {
  if (item.camera) return item.camera;
  const c = clipOf(item);
  if (/GOPR|GX\d/.test(c)) return "nyc_bike_gopro-1";
  if (/IMG_\d/.test(c)) return "nyc_streets_cam-2";
  if (/VID_\d/.test(c)) return "nyc_streets_cam-1";
  if (/_sf(\d)_/.test(c)) return `sf_streets_cam-${/_sf(\d)_/.exec(c)[1]}`;
  if (/_set\d\d_/.test(c)) return "pie_cam-3";
  if (/_scene\d/.test(c)) return "i24_cam-1";
  if (/neighborhood/.test(c)) return "neighborhood_cam-1";
  return "camera";
}

const sourceLabel = (item) => `${cameraOf(item)} · ${clipLabel(clipOf(item))} · ${fmt(item.start)}–${fmt(endOf(item))}`;

// Start of a 30-second chunk within its parent video, from "_chunk_0003".
function rideTime(item, t) {
  const m = /_chunk_(\d+)/.exec(clipOf(item));
  return (m ? parseInt(m[1], 10) * 30 : 0) + t;
}

const CATEGORIES = {
  "blocked bike lane": { label: "Blocked bike lane", cls: "cat-blocked", group: "blocked" },
  "double parking": { label: "Double parking", cls: "cat-double", group: "blocked" },
  "street obstruction": { label: "Street obstruction", cls: "cat-obstruction", group: "obstruction" },
  "construction": { label: "Construction", cls: "cat-construction", group: "obstruction" },
  "sanitation": { label: "Sanitation", cls: "cat-sanitation", group: "sanitation" },
  "graffiti": { label: "Graffiti", cls: "cat-graffiti", group: null },
  "none": { label: "No issue", cls: "cat-none", group: null },
};

function categoryOf(event) {
  const key = String(event.category || "none").toLowerCase();
  return CATEGORIES[key] || { label: cap(key), cls: "cat-none", group: null };
}
const reportable = (e) => String(e.category || "none").toLowerCase() !== "none";

const DESTINATIONS = {
  walker_broadway: "Walker St & Broadway",
  "8th_ave_34th": "8th Ave & 34th St",
  nyc_bike_lanes: "Lower Manhattan bike lanes",
  new_york: "New York",
  san_francisco: "San Francisco",
  toronto: "Toronto",
};

// The team's six searches per travel mode; the API's own list wins when it sends one.
const QUERIES = {
  walking: ["pedestrians crossing at a busy crosswalk", "construction barriers or scaffolding on the sidewalk",
    "police vehicle or officer on the street", "people waiting at a bus stop",
    "crowded sidewalk near storefronts", "rain or wet street with umbrellas"],
  cycling: ["truck or van stopped in the bike lane", "cyclist riding in a green bike lane",
    "delivery truck parked with rear doors open", "orange cones narrowing the lane",
    "cyclist stopped behind a parked truck", "car turning across a crosswalk with a cyclist"],
  driving: ["double-parked vehicle blocking a lane", "yellow taxi stopped at the intersection",
    "bus pulling into the intersection", "vehicle entering on a red light",
    "pedestrians stepping into the road between cars", "road work with cones and a lane closure"],
};

const ICON_PLAY = `<svg viewBox="0 0 24 24"><path d="M7 4.5v15l12.5-7.5Z" class="solid"/></svg>`;

// The header taxi drives while something is playing.
const setPlaying = (on) => document.body.classList.toggle("is-playing", on);

const sampleFlags = { ride: false, brief: false, eval: false };
const activeTab = () => $(".tab.active").dataset.tab;
function setSample(tab, isMock) {
  sampleFlags[tab] = Boolean(isMock);
  $("#sample-badge").hidden = !sampleFlags[activeTab()];
}

function setCount(selector, value) {
  const el = $(selector);
  const text = String(value);
  if (el.textContent === text) return;
  el.textContent = text;
  el.classList.remove("bump");
  void el.offsetWidth; // restart the animation
  el.classList.add("bump");
}

// ---------------------------------------------------------------- playback

// One replay, briefing or cold open. stop() cancels everything it started.
class Run {
  constructor(video) {
    this.video = video;
    this.alive = true;
    this.paused = false;
    this.cancels = new Set();
  }
  stop() {
    this.alive = false;
    for (const cancel of [...this.cancels]) cancel();
    this.cancels.clear();
  }
  pause() {
    this.paused = true;
    if (!this.video.hidden) this.video.pause();
  }
  resume() {
    this.paused = false;
    if (!this.video.hidden && this.video.dataset.playing === "1") this.video.play().catch(() => {});
  }
}

function idle(video, placeholder, { title, hand, note, onPlay }) {
  video.pause();
  video.hidden = true;
  placeholder.hidden = false;
  placeholder.innerHTML = `<div>${onPlay ? `<button class="play" aria-label="${esc(title)}">${ICON_PLAY}</button>` : ""}` +
    `<b>${esc(title)}</b>${hand ? `<span>${esc(hand)}</span>` : ""}${note ? `<small>${esc(note)}</small>` : ""}</div>`;
  if (onPlay) placeholder.querySelector(".play").addEventListener("click", onPlay);
}

// Plays [start, end] of one clip. If the video can't load (no source, no relay on
// this machine, a stalled stream) it shows the clip's details and keeps time
// instead, so the feed and the narration still run.
function playSegment(video, placeholder, item, run, { rate = 1, onTime = () => {} } = {}) {
  const start = Number(item.start) || 0;
  const end = endOf(item);

  return new Promise((resolve) => {
    let finished = false;
    let simulated = false;
    let ticker = null;
    let guard = null;

    function cleanup() {
      clearInterval(ticker);
      clearTimeout(guard);
      video.removeEventListener("timeupdate", onUpdate);
      video.removeEventListener("error", simulate);
      video.removeEventListener("loadedmetadata", begin);
      run.cancels.delete(finish);
    }
    function finish() {
      if (finished) return;
      finished = true;
      cleanup();
      video.dataset.playing = "0";
      if (!simulated) video.pause();
      resolve();
    }
    function simulate() {
      if (simulated || finished) return;
      simulated = true;
      video.removeEventListener("timeupdate", onUpdate);
      video.pause();
      video.dataset.playing = "0";
      video.hidden = true;
      placeholder.hidden = false;
      placeholder.innerHTML =
        `<div><b>${esc(clipLabel(clipOf(item)))}</b><span>${fmt(start)}–${fmt(end)}</span>` +
        `<small>${item.source ? "Video unavailable on this machine" : "Video plays on the deployed app"}</small></div>`;
      let elapsed = 0;
      let last = performance.now();
      ticker = setInterval(() => {
        const now = performance.now();
        if (!run.paused) elapsed += ((now - last) / 1000) * rate;
        last = now;
        onTime(start + Math.min(elapsed, end - start));
        if (elapsed >= end - start) finish();
      }, 100);
    }
    function onUpdate() {
      onTime(video.currentTime);
      if (video.currentTime >= end - 0.05) finish();
    }
    function begin() {
      try { video.currentTime = start; } catch (_) { /* seek once metadata exists */ }
      video.playbackRate = rate;
      video.dataset.playing = "1";
      if (!run.paused) video.play().catch(simulate);
    }

    run.cancels.add(finish);
    if (!run.alive) return finish();
    if (!item.source) return simulate();

    video.hidden = false;
    placeholder.hidden = true;
    video.addEventListener("timeupdate", onUpdate);
    video.addEventListener("error", simulate);
    const url = videoURL(item.source);
    if (video.dataset.src === url && video.readyState >= 1) {
      begin();
    } else {
      video.dataset.src = url;
      video.addEventListener("loadedmetadata", begin, { once: true });
      video.src = url;
    }
    guard = setTimeout(() => { if (!finished && video.readyState < 2) simulate(); }, 8000);
  });
}

// ---------------------------------------------------------------- Act 1: the rider

const rideVideo = $("#ride-video");
const ridePlaceholder = $("#ride-placeholder");
let rideRun = null;
let rideData = null;
let tally = null;
let revealed = [];

async function loadRides() {
  const select = $("#ride-select");
  try {
    const data = await getJSON("rides");
    const rides = data.rides || [];
    select.innerHTML = rides.length
      ? rides.map((r) => `<option value="${esc(r.video)}">${esc(r.label || r.video)}` +
          `${r.events != null ? ` · ${r.events} events` : ""}</option>`).join("")
      : `<option value="">No rides yet</option>`;
  } catch (_) {
    select.innerHTML = `<option value="">Rides unavailable</option>`;
  }
}

function stopRide() {
  if (rideRun) rideRun.stop();
  rideRun = null;
  setPlaying(false);
}

function resetRide() {
  tally = { blocked: 0, sanitation: 0, obstruction: 0, reports: 0, filed: 0, lost: 0, causes: {}, lostKeys: new Set() };
  revealed = [];
  $("#feed").innerHTML = "";
  $("#feed-empty").hidden = false;
  $("#feed-empty").textContent = "Press Replay to watch the ride.";
  $("#ride-clock").textContent = "0:00";
  $("#ride-length").textContent = "";
  $("#ride-now").textContent = "";
  idle(rideVideo, ridePlaceholder, { title: "Press Replay to watch the ride.", onPlay: replay });
  renderCounters();
  updateFileAll();
}

function renderCounters() {
  setCount("#c-blocked", tally.blocked);
  setCount("#c-sanitation", tally.sanitation);
  setCount("#c-obstruction", tally.obstruction);
  setCount("#c-reports", tally.reports);
  setCount("#c-filed", tally.filed);
  setCount("#c-lost", fmtLost(tally.lost));
  $("#c-causes").textContent = causesText(tally.causes);
}

// Accepts shares (0.7) or seconds (29); prints the top causes as percentages.
function causesText(causes) {
  const entries = Object.entries(causes || {}).filter(([, v]) => Number(v) > 0);
  const total = entries.reduce((sum, [, v]) => sum + Number(v), 0);
  if (!total) return "";
  return entries.sort((a, b) => b[1] - a[1]).slice(0, 2)
    .map(([k, v]) => `${Math.round((Number(v) / total) * 100)}% ${k}`).join(" · ");
}

const sameClip = (e, item) => (e.source && item.source ? e.source === item.source : clipOf(e) === clipOf(item));
const overlaps = (e, item) => (Number(e.start) || 0) < endOf(item) && endOf(e) > (Number(item.start) || 0);
const popAt = (e) => (Number(e.start) || 0) + Math.min(1, (endOf(e) - (Number(e.start) || 0)) / 3);

// Highlights: each distinct moment with an event, in the order the API returned them.
function highlightPlaylist(events) {
  const seen = new Map();
  for (const e of events) {
    const key = `${e.source || clipOf(e)}|${e.start}`;
    if (!seen.has(key)) {
      seen.set(key, { source: e.source, clip: e.clip, camera: e.camera, start: Number(e.start) || 0, end: endOf(e) });
    }
  }
  return [...seen.values()];
}

async function replay() {
  stopRide();
  const key = $("#ride-select").value;
  if (!key) return;
  const run = (rideRun = new Run(rideVideo));
  resetRide();
  idle(rideVideo, ridePlaceholder, { title: "Loading the ride…" });

  try {
    rideData = await getJSON(`ride?video=${enc(key)}`);
  } catch (_) {
    $("#feed-empty").textContent = "Couldn't load this ride. Try again.";
    return;
  }
  if (!run.alive) return;
  setSample("ride", rideData.mock);
  const duration = Number(rideData.summary && rideData.summary.duration_s);
  $("#ride-length").textContent = duration > 0 ? `/ ${fmt(duration)}` : "";

  const events = (rideData.events || []).map((e, i) => ({ ...e, _i: i }));
  if (!events.length) {
    $("#feed-empty").textContent = "No street issues found on this ride.";
    idle(rideVideo, ridePlaceholder, { title: "A clean ride.", hand: "nothing to file" });
    return;
  }
  $("#feed-empty").hidden = true;

  const segments = Array.isArray(rideData.segments) ? rideData.segments : [];
  const full = $("#ride-mode").value === "full" && segments.length > 0;
  const playlist = full ? segments : highlightPlaylist(events);
  const rate = full ? 4 : 1;
  const pending = new Set(events.map((e) => e._i));
  const reveal = (e) => { if (pending.delete(e._i)) addEvent(e); };
  setPlaying(true);

  for (const item of playlist) {
    if (!run.alive) return;
    const here = events.filter((e) => pending.has(e._i) && sameClip(e, item) && overlaps(e, item));
    $("#ride-now").textContent = `${cameraOf(item)} · ${clipLabel(clipOf(item))}`;
    await playSegment(rideVideo, ridePlaceholder, item, run, {
      rate,
      onTime: (t) => {
        $("#ride-clock").textContent = fmt(rideTime(item, t));
        for (const e of here) if (t >= popAt(e)) reveal(e);
      },
    });
    here.forEach(reveal); // short or skipped segments still surface their events
  }
  if (!run.alive) return;
  events.forEach(reveal);
  applySummary(rideData.summary);
  $("#ride-now").textContent = "Ride complete";
  setPlaying(false);
}

function addEvent(e) {
  const cat = categoryOf(e);
  if (cat.group) tally[cat.group] += 1;
  if (reportable(e)) tally.reports += 1;

  const reaction = String(e.rider_reaction || "").toLowerCase();
  const momentKey = `${e.source || clipOf(e)}|${e.start}`;
  if (/stop|slow|swerv/.test(reaction) && !tally.lostKeys.has(momentKey)) {
    tally.lostKeys.add(momentKey);
    const seconds = endOf(e) - (Number(e.start) || 0);
    const cause = e.cause || (e.company ? "delivery trucks" : cat.label.toLowerCase());
    tally.lost += seconds;
    tally.causes[cause] = (tally.causes[cause] || 0) + seconds;
  }
  renderCounters();

  const vehicle = [e.company, e.fleet_number ? `fleet ${e.fleet_number}` : null].filter(Boolean).join(" · ");
  const merged = Number(e.merged || e.merged_count || (Array.isArray(e.segments) ? e.segments.length : 0));
  const street = e.street && e.street !== "not visible" ? e.street : "street not visible";
  const li = document.createElement("li");
  li.className = `event ${cat.cls}`;
  li.innerHTML =
    `<div class="ev-top"><span class="tag">${esc(cat.label)}</span>` +
    `${merged > 1 ? `<span class="merged">seen in ${merged} segments · merged</span>` : ""}</div>` +
    `<b class="ev-title">${esc(vehicle || cat.label)}</b>` +
    `<p>${esc(e.text || "")}</p>` +
    `<div class="ev-meta">rider ${esc(e.rider_reaction || "—")} · ${esc(street)} · ${esc(clipLabel(clipOf(e)))} ${fmt(e.start)}</div>`;
  li.addEventListener("click", () => openComplaint(e, li));
  $("#feed").prepend(li);
  revealed.push({ e, li });
  updateFileAll();
}

function applySummary(summary) {
  if (!summary) return;
  if (Number.isFinite(Number(summary.time_lost_s))) tally.lost = Number(summary.time_lost_s);
  if (summary.by_cause && Object.keys(summary.by_cause).length) tally.causes = summary.by_cause;
  if (Number.isFinite(Number(summary.reports_ready))) tally.reports = Number(summary.reports_ready);
  renderCounters();
}

function markFiled(item) {
  if (item.e._filed || !reportable(item.e)) return;
  item.e._filed = true;
  item.li.classList.add("is-filed");
  item.li.insertAdjacentHTML("beforeend", `<span class="filed">Filed</span>`);
  tally.filed += 1;
  renderCounters();
  updateFileAll();
}

function updateFileAll() {
  const button = $("#file-all");
  const open = revealed.filter((r) => reportable(r.e) && !r.e._filed).length;
  const filed = revealed.some((r) => r.e._filed);
  button.disabled = open === 0;
  button.textContent = open === 0 && filed ? "All filed ✓" : open > 0 ? `File all (${open})` : "File all";
}

$("#file-all").addEventListener("click", async () => {
  const open = revealed.filter((r) => reportable(r.e) && !r.e._filed).reverse(); // top of the list first
  for (const item of open) {
    markFiled(item);
    await sleep(140);
  }
});

// ---------------------------------------------------------------- the 311 draft

const REACTIONS = { stopped: "had to stop", slowed: "had to slow down", swerved: "had to swerve", riding: "kept riding" };

function complaintFor(e) {
  const list = (rideData && rideData.complaints) || [];
  const match = list.find((c) => c.event_index === e._i) ||
    list.find((c) => c.clip === e.clip && Number(c.start) === Number(e.start) && c.category === e.category);
  const cat = categoryOf(e);
  const street = e.street && e.street !== "not visible" ? e.street : null;
  const vehicle = e.company ? `${e.company}${e.fleet_number ? ` · fleet ${e.fleet_number}` : ""}` : null;
  const reaction = REACTIONS[String(e.rider_reaction || "").toLowerCase()] || e.rider_reaction || "was affected";
  const mentionsVehicle = String(e.text || "").includes(e.fleet_number || e.company || "\u0000");
  const text = (match && (match.text || match.complaint || match.description)) ||
    `${cat.label} observed during a bike ride${street ? ` on ${street}` : ""}. ${e.text || ""} ` +
    `${vehicle && !mentionsVehicle ? `Vehicle: ${vehicle}. ` : ""}The rider ${reaction}. ` +
    `Video evidence: ${clipLabel(clipOf(e))}, ${fmt(e.start)}–${fmt(endOf(e))}.`;
  const clip = esc(sourceLabel(e));
  return {
    cat,
    text,
    fields: [
      ["Category", esc((match && match.category) || cat.label)],
      ["Street", esc(street || "Not visible in video")],
      ["What happened", esc(e.text || "—")],
      ["Company", esc(vehicle || "Private vehicle · no identifier")],
      ["Rider", esc(e.rider_reaction || "—")],
      ["Clip", e.source ? `<a href="${videoURL(e.source)}#t=${Number(e.start) || 0},${endOf(e)}" target="_blank" rel="noopener">${clip}</a>` : clip],
    ],
  };
}

let openItem = null;

function openComplaint(e, li) {
  if (rideRun) rideRun.pause();
  const c = complaintFor(e);
  openItem = revealed.find((r) => r.li === li) || { e, li };
  $("#cp-badge").className = `tag ${c.cat.cls}`;
  $("#cp-badge").textContent = c.cat.label;
  $("#cp-fields").innerHTML = c.fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
  $("#cp-text").textContent = c.text;
  $("#cp-file").textContent = e._filed ? "Filed ✓" : "Ready to file";
  $("#cp-file").disabled = Boolean(e._filed) || !reportable(e);
  $("#complaint").showModal();
}

$("#complaint").addEventListener("close", () => {
  if (openItem && $("#complaint").returnValue === "file") markFiled(openItem);
  openItem = null;
  if (rideRun && rideRun.alive) rideRun.resume();
});

// ---------------------------------------------------------------- Act 3: the newsroom

const briefVideo = $("#brief-video");
const briefPlaceholder = $("#brief-placeholder");
const MODES = ["walking", "cycling", "driving"];
let briefRun = null;
let briefLines = [];
let currentLine = -1;

function stopBrief() {
  if (briefRun) briefRun.stop();
  briefRun = null;
  setPlaying(false);
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

function setBriefMsg(text) {
  $("#brief-msg").textContent = text;
  $("#brief-msg").hidden = !text;
}

function speak(text, run) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    let done = false;
    let waited = 0;
    const limit = 5000 + text.length * 110; // never hang if a voice fails to report "end"
    const timer = setInterval(() => {
      if (!run.paused) waited += 250;
      if (waited > limit) finish();
    }, 250);
    function finish() {
      if (done) return;
      done = true;
      clearInterval(timer);
      run.cancels.delete(cancel);
      resolve();
    }
    function cancel() { speechSynthesis.cancel(); finish(); }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-US";
    utterance.rate = 1;
    utterance.onend = finish;
    utterance.onerror = finish;
    run.cancels.add(cancel);
    speechSynthesis.speak(utterance);
  });
}

function renderSearches(queries) {
  $("#searches").innerHTML = queries.map((q, i) =>
    `<li data-i="${i}" style="animation-delay:${i * 40}ms"><i></i><span>${esc(q)}</span><em></em></li>`).join("");
}

function markSearch(i, hits) {
  const li = $(`#searches li[data-i="${i}"]`);
  if (!li) return;
  li.classList.add("done");
  li.querySelector("em").textContent = hits;
}

function hitsFor(data, i) {
  const q = data && Array.isArray(data.queries) ? data.queries[i] : null;
  const hits = q && typeof q === "object" ? q.hits : null;
  return hits != null ? `${hits} clip${Number(hits) === 1 ? "" : "s"}` : "searched";
}

function renderStats(stats) {
  const plural = { person: "people", bus: "buses" };
  const entries = Object.entries(stats || {}).filter(([, n]) => Number(n) > 0);
  $("#stats").innerHTML = entries.length
    ? `In these clips: ${entries.map(([k, n]) => `<b>${esc(n)}</b> ${esc(Number(n) === 1 ? k : plural[k] || `${k}s`)}`).join(" · ")}`
    : "";
}

function renderScript(lines) {
  $("#script").innerHTML = lines.map((l, i) =>
    `<li data-i="${i}"><p>${esc(l.text)}</p><button class="src" data-i="${i}">${esc(sourceLabel(l))}</button></li>`).join("");
  $$("#script .src").forEach((b) => b.addEventListener("click", () => showSource(Number(b.dataset.i))));
}

function showLine(i) {
  const line = briefLines[i];
  currentLine = i;
  $("#lower").hidden = false;
  $("#bug").hidden = false;
  const caption = $("#caption");
  caption.textContent = line.text;
  caption.style.animation = "none";
  void caption.offsetWidth;
  caption.style.animation = "";
  const chip = $("#chip");
  chip.hidden = false;
  chip.classList.remove("paused");
  chip.textContent = `${i + 1}/${briefLines.length} · ${sourceLabel(line)}`;
  $$("#script li").forEach((li) => {
    const n = Number(li.dataset.i);
    li.classList.toggle("current", n === i);
    li.classList.toggle("spoken", n < i);
  });
}

function togglePause() {
  if (!briefRun || !briefRun.alive) return;
  const chip = $("#chip");
  if (briefRun.paused) {
    briefRun.resume();
    if ("speechSynthesis" in window) speechSynthesis.resume();
    chip.classList.remove("paused");
  } else {
    briefRun.pause();
    if ("speechSynthesis" in window) speechSynthesis.pause();
    chip.classList.add("paused");
  }
}

// "This is the clip the sentence came from, at this second."
function showSource(i) {
  if (briefRun && briefRun.alive && currentLine === i) return togglePause();
  stopBrief();
  const line = briefLines[i];
  if (!line) return;
  showLine(i);
  $("#chip").classList.add("paused");
  const unavailable = (note) => idle(briefVideo, briefPlaceholder,
    { title: clipLabel(clipOf(line)), hand: `${fmt(line.start)}–${fmt(endOf(line))}`, note });
  if (!line.source) return unavailable("Paused on the source clip");
  briefPlaceholder.hidden = true;
  briefVideo.hidden = false;
  const url = videoURL(line.source);
  const seek = () => { try { briefVideo.currentTime = Number(line.start) || 0; } catch (_) { /* not ready */ } briefVideo.pause(); };
  if (briefVideo.dataset.src === url && briefVideo.readyState >= 1) {
    seek();
  } else {
    briefVideo.dataset.src = url;
    briefVideo.addEventListener("loadedmetadata", seek, { once: true });
    briefVideo.addEventListener("error", () => unavailable("Video unavailable on this machine"), { once: true });
    briefVideo.src = url;
  }
}

function renderReady(list) {
  const ready = (list || []).filter((b) => b.destination && b.time_of_day && b.mode);
  $("#ready").hidden = !ready.length;
  $("#ready").innerHTML = ready.length
    ? `<span>Ready to air:</span>` + ready.map((b, i) =>
        `<button class="ready-chip" data-i="${i}">${esc(DESTINATIONS[b.destination] || b.destination.replace(/_/g, " "))} · ${esc(b.time_of_day)} · ${esc(b.mode)}</button>`).join("")
    : "";
  $$(".ready-chip").forEach((btn) => btn.addEventListener("click", () => {
    const b = ready[Number(btn.dataset.i)];
    if (![...$("#b-dest").options].some((o) => o.value === b.destination)) {
      $("#b-dest").insertAdjacentHTML("beforeend", `<option value="${esc(b.destination)}">${esc(b.destination.replace(/_/g, " "))}</option>`);
    }
    $("#b-dest").value = b.destination;
    $("#b-time").value = b.time_of_day;
    $("#b-mode").value = b.mode;
    runBrief();
  }));
}

async function loadReady() {
  try {
    const data = await getJSON("briefs");
    renderReady(data.briefs);
  } catch (_) { /* optional */ }
}

function briefIdle() {
  idle(briefVideo, briefPlaceholder, {
    title: "Pick a block and press Brief me.",
    hand: "a one-minute segment, every line cited",
    onPlay: runBrief,
  });
}

async function runBrief() {
  stopBrief();
  const run = (briefRun = new Run(briefVideo));
  const dest = $("#b-dest").value;
  const time = $("#b-time").value;
  const mode = $("#b-mode").value;
  const place = DESTINATIONS[dest] || dest.replace(/_/g, " ");
  briefLines = [];
  currentLine = -1;
  $("#script").innerHTML = "";
  $("#stats").innerHTML = "";
  $("#lower").hidden = true;
  $("#bug").hidden = true;
  $("#chip").hidden = true;
  setBriefMsg("");
  idle(briefVideo, briefPlaceholder, { title: `Searching ${place}`, hand: `${time} · ${mode}`, note: "Six searches across the city's footage" });

  // The six searches fire on screen, then the montage starts.
  const queries = (QUERIES[mode] || []).map((q) => `${q} · ${time}`);
  renderSearches(queries);
  const fetching = getJSON(`brief?destination=${enc(dest)}&time_of_day=${enc(time)}&mode=${enc(mode)}`).catch(() => null);
  let data = null;
  for (let i = 0; i < queries.length && run.alive; i += 1) {
    await sleep(330);
    if (i === queries.length - 1) data = await fetching;
    markSearch(i, hitsFor(data, i));
  }
  if (!run.alive) return;
  data = data || (await fetching);
  if (!data) {
    setBriefMsg("Couldn't reach the briefing service. Try again in a moment.");
    briefIdle();
    return;
  }
  if (Array.isArray(data.queries) && data.queries.length) {
    renderSearches(data.queries.map((q) => (typeof q === "string" ? q : q.query)));
    data.queries.forEach((_, i) => markSearch(i, hitsFor(data, i)));
  }
  setSample("brief", data.mock);
  $("#rerun").hidden = false;
  renderStats(data.stats);

  briefLines = (data.script || []).filter((l) => l && l.text);
  if (!briefLines.length) {
    if (data.available) renderReady(data.available);
    setBriefMsg(`${data.message || "Nothing filed for this block yet."}` +
      `${data.available && data.available.length ? " Pick one of the ready briefings above." : ""}`);
    idle(briefVideo, briefPlaceholder, { title: "Nothing to air here yet.", hand: "try another block or mode" });
    return;
  }
  renderScript(briefLines);
  $("#lower-place").textContent = `${place} · ${cap(time)}`;
  setPlaying(true);
  for (let i = 0; i < briefLines.length && run.alive; i += 1) {
    showLine(i);
    await Promise.all([speak(briefLines[i].text, run), playSegment(briefVideo, briefPlaceholder, briefLines[i], run)]);
  }
  if (run.alive) {
    setPlaying(false);
    $$("#script li").forEach((li) => { li.classList.remove("current"); li.classList.add("spoken"); });
    $("#caption").textContent = `That's your block: ${place}.`;
    $("#chip").hidden = true;
    currentLine = -1;
  }
}

$("#chip").addEventListener("click", () => {
  if (briefRun && briefRun.alive) togglePause();
  else if (currentLine >= 0) showSource(currentLine);
});

$("#rerun").addEventListener("click", () => {
  const select = $("#b-mode");
  select.value = MODES[(MODES.indexOf(select.value) + 1) % MODES.length];
  runBrief();
});

// ---------------------------------------------------------------- Close: the eval

async function loadEval() {
  try {
    const data = await getJSON("eval");
    setSample("eval", data.mock);
    renderEval(data);
  } catch (_) {
    $("#eval-msg").textContent = "Eval results unavailable.";
  }
}

function renderEval(data) {
  const rows = data.rows || [];
  const body = $("#eval-table tbody");
  const oldFoot = $("#eval-table tfoot");
  if (oldFoot) oldFoot.remove();
  if (!rows.length) {
    body.innerHTML = "";
    ["#e-checked", "#e-claims", "#e-grounded", "#e-rate"].forEach((s) => { $(s).textContent = "–"; });
    $("#eval-msg").textContent = "No eval results yet. Save eval_results.json next to the app.";
    return;
  }
  let claims = 0;
  let grounded = 0;
  body.innerHTML = rows.map((r) => {
    const c = Number(r.claims ?? r.sentences ?? 0);
    const g = Number(r.grounded ?? 0);
    claims += c;
    grounded += g;
    const label = r.label || [DESTINATIONS[r.destination] || r.destination, r.time_of_day, r.mode].filter(Boolean).join(" · ") || r.video || "—";
    const kind = r.kind || (r.video ? "ride" : "briefing");
    return `<tr><td>${esc(label)}</td><td>${esc(kind)}</td><td class="num">${c}</td><td class="num">${g}</td>` +
      `<td class="num">${c ? Math.round((g / c) * 100) : 0}%</td></tr>`;
  }).join("");
  const rate = claims ? Math.round((grounded / claims) * 100) : 0;
  body.insertAdjacentHTML("afterend",
    `<tfoot><tr><td>Total</td><td>${rows.length} checked</td><td class="num">${claims}</td><td class="num">${grounded}</td><td class="num">${rate}%</td></tr></tfoot>`);
  $("#e-checked").textContent = rows.length;
  $("#e-claims").textContent = claims;
  $("#e-grounded").textContent = grounded;
  $("#e-rate").textContent = `${rate}%`;
  const link = $("#weave-link");
  link.hidden = !data.weave_url;
  if (data.weave_url) link.href = data.weave_url;
  $("#eval-msg").textContent = data.note ||
    "Every cited clip opened by a person and marked grounded or not. Logged as a Weave Evaluation.";
}

// ---------------------------------------------------------------- Cold open

let coldRun = null;

async function coldOpen() {
  let data = rideData;
  if (!data) {
    try { data = await getJSON(`ride?video=${enc($("#ride-select").value || "mock")}`); } catch (_) { return; }
  }
  const events = data.events || [];
  const target = data.cold_open || events.find((e) => e.source) || events[0];
  if (!target) return;
  stopRide();
  stopBrief();
  const overlay = $("#cold");
  overlay.hidden = false;
  try { if (overlay.requestFullscreen) await overlay.requestFullscreen(); } catch (_) { /* windowed is fine */ }
  const run = (coldRun = new Run($("#cold-video")));
  while (run.alive) await playSegment($("#cold-video"), $("#cold-placeholder"), target, run);
}

function closeCold() {
  if (coldRun) coldRun.stop();
  coldRun = null;
  $("#cold").hidden = true;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

$("#cold-open").addEventListener("click", coldOpen);
$("#cold-close").addEventListener("click", closeCold);
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && !$("#cold").hidden) closeCold(); });
document.addEventListener("keydown", (ev) => {
  if (ev.target.closest("select, input, textarea") || $("#complaint").open) return;
  if (ev.key === "Escape" && !$("#cold").hidden) closeCold();
  else if ((ev.key === "c" || ev.key === "C") && !ev.metaKey && !ev.ctrlKey && !ev.altKey) coldOpen();
});

// ---------------------------------------------------------------- wiring

$$(".tab").forEach((btn) => btn.addEventListener("click", () => {
  $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
  stopRide();
  stopBrief();
  $("#sample-badge").hidden = !sampleFlags[btn.dataset.tab];
  if (btn.dataset.tab === "eval") loadEval();
}));

$("#replay").addEventListener("click", replay);
$("#ride-select").addEventListener("change", () => { stopRide(); resetRide(); });
$("#brief-me").addEventListener("click", runBrief);

resetRide();
briefIdle();
loadRides();
loadReady();
loadEval();
