"use strict";
const $ = id => document.getElementById(id);
const state = {
  recordings: [], recording: null, session: null, result: null, selected: null,
  busy: false, mode: "raw", tool: "select", interval: null, frame: 0, hoverFrame: null,
  image: null, reference: true, zoom: 1, cx: 0, cy: 0, drag: null, ghost: null,
  generation: 0, imageRequest: 0, playing: false, pollTimer: null, playTimer: null, savedAnalyses: new Map(),
};
const fmt = (n, decimals = 1) => n == null || !Number.isFinite(n) ? "—" : Number(n).toLocaleString(undefined, {maximumFractionDigits: decimals});
const regionName = id => `Region ${String(id).padStart(3, "0")}`;
const escapeHTML = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!response.ok) {
    let message;
    try { const error = await response.json(); message = Array.isArray(error.detail) ? error.detail.map(x => x.msg).join("; ") : error.detail; } catch { message = "The app could not complete this request."; }
    throw new Error(message || `Request failed (${response.status})`);
  }
  return response.json();
}

function notify(message) { $("toastText").textContent = message; $("toast").hidden = false; }
function selectedROI() { return state.result?.rois.find(r => r.id === state.selected); }
function updateSensitivityLabel() {
  const value = Number($("sensitivity").value);
  $("sensitivityValue").textContent = value === 50 ? "Standard" : `${value} / 100`;
}
function parameters() {
  return {interval: state.interval, baseline_frames: Number($("baseline").value), prominence_percent: Number($("prominence").value), separation_frames: Number($("separation").value)};
}

function setBusy(busy) {
  state.busy = busy;
  $("analyzeButton").disabled = busy || !state.recording;
  $("analyzeButton").textContent = busy ? "Analyzing…" : "↻  Analyze recording";
  $("applySignal").disabled = busy || !state.result;
  $("exportButton").disabled = busy || !state.result;
  $("includeSelected").disabled = busy || !selectedROI();
  $("deleteSelected").disabled = busy || !selectedROI();
  $("openButton").disabled = busy;
  for (const id of ["radius", "sensitivity", "baseline", "prominence", "separation"]) $(id).disabled = busy;
  document.querySelectorAll(".recording-card, .timing-actions button, .timing-actions input, .region-row input, [data-tool]").forEach(el => el.disabled = busy);
  document.querySelectorAll('[data-tool="move"], [data-tool="add"]').forEach(el => el.disabled = busy || !state.result);
  $("readyDot").style.background = busy ? "#d4b16d" : state.result ? "#439688" : "#bdc8c7";
  $("progressPanel").hidden = !busy;
  updateRegionControls();
  if (busy) pause();
}

function updateRegionControls() {
  const rois = state.result?.rois ?? [];
  $("checkAllRegions").disabled = state.busy || !rois.some(r => !r.included);
  $("uncheckAllRegions").disabled = state.busy || !rois.some(r => r.included);
  $("regionSort").disabled = state.busy || !rois.length;
}

function renderLibrary() {
  $("recordingCount").textContent = state.recordings.length;
  const root = $("recordingList"); root.replaceChildren();
  if (!state.recordings.length) {
    root.innerHTML = '<p class="help">No recordings found in the dataset folder. Use Open recording to choose a TIFF.</p>';
  }
  for (const r of state.recordings) {
    const button = document.createElement("button");
    button.className = `recording-card${state.recording?.id === r.id ? " active" : ""}`;
    button.disabled = state.busy;
    button.setAttribute("aria-label", `Open ${r.title}, ${r.frames} frames`);
    button.setAttribute("aria-pressed", state.recording?.id === r.id ? "true" : "false");
    button.innerHTML = `<img src="/api/recordings/${r.id}/frame?thumbnail=true" alt="" loading="lazy"><span class="recording-card-info"><span class="recording-card-name">${escapeHTML(r.title)}</span><span class="recording-card-meta"><span>${r.frames} frames · ${fmt(r.bytes / 1e9, 2)} GB</span>${state.recording?.id === r.id ? '<i aria-hidden="true"></i>' : ''}</span></span>`;
    button.addEventListener("click", () => selectRecording(r)); root.append(button);
  }
}

function renderTiming() {
  const banner = $("timingBanner"), r = state.recording;
  if (!r) { banner.hidden = true; return; }
  banner.hidden = false;
  banner.classList.toggle("confirmed", state.interval != null);
  const title = state.interval == null ? (r.timing_conflict ? "Confirm the frame interval" : "Add a frame interval") : `Time axis: ${fmt(state.interval * 1000, 3)} ms per frame`;
  const description = state.interval == null
    ? (r.timing_conflict ? Object.entries(r.intervals).map(([label, seconds]) => `${label}: ${fmt(seconds * 1000)} ms`).join(" · ") + ". Until confirmed, graphs use frame numbers and rates stay blank." : "Graphs use frame numbers until an interval is entered.")
    : `${fmt((r.frames - 1) * state.interval, 2)} seconds from first to last frame. The interval applies to all stored frames.`;
  banner.innerHTML = `<div class="timing-copy"><strong>${escapeHTML(title)}</strong>${escapeHTML(description)}</div><div class="timing-actions"></div>`;
  const actions = banner.querySelector(".timing-actions");
  if (state.interval == null) {
    for (const seconds of [...new Set(Object.values(r.intervals))].sort()) {
      const b = document.createElement("button"); b.textContent = `Use ${fmt(seconds * 1000)} ms`; b.disabled = state.busy;
      b.addEventListener("click", () => setIntervalSeconds(seconds)); actions.append(b);
    }
  }
  const label = document.createElement("label"); label.innerHTML = `<input id="intervalInput" type="number" min="0.001" max="3600000" step="any" value="${state.interval == null ? "" : state.interval * 1000}" placeholder="Custom" aria-label="Frame interval in milliseconds"> ms`;
  label.querySelector("input").disabled = state.busy; actions.append(label);
  const apply = document.createElement("button"); apply.textContent = "Apply"; apply.disabled = state.busy;
  apply.addEventListener("click", () => {
    const ms = Number($("intervalInput").value);
    if (!Number.isFinite(ms) || ms <= 0 || ms > 3600000) return notify("Enter a positive frame interval in milliseconds.");
    setIntervalSeconds(ms / 1000);
  }); actions.append(apply);
  if (state.interval != null) {
    const reset = document.createElement("button"); reset.textContent = "Use frames"; reset.disabled = state.busy;
    reset.addEventListener("click", () => setIntervalSeconds(null)); actions.append(reset);
  }
}

async function setIntervalSeconds(seconds) {
  if (state.busy) return;
  const previous = state.interval; state.interval = seconds;
  setBusy(true);
  try {
    if (state.session && state.result) {
      await api(`/api/sessions/${state.session}/settings`, parameters());
      await fetchResult();
    }
    renderTiming(); drawCharts(); updateFrameLabel();
  } catch (error) { state.interval = previous; notify(error.message); }
  finally {setBusy(false); renderTiming();}
}

async function selectRecording(recording) {
  if (state.busy) return;
  state.generation++; clearTimeout(state.pollTimer); pause();
  Object.assign(state, {recording, result: null, session: null, selected: null, frame: 0, hoverFrame: null,
    interval: recording.interval, reference: true, image: null, zoom: 1, cx: recording.width / 2, cy: recording.height / 2});
  $("referenceMode").checked = true; $("recordingTitle").textContent = recording.title;
  $("recordingMeta").innerHTML = `<span>${recording.width} × ${recording.height}</span><span>${recording.dtype}</span><span>${recording.frames} frames</span><span>1 channel</span>`;
  const notices = recording.warnings.filter(x => !x.includes("interval"));
  $("recordingNotice").textContent = notices.join(" "); $("recordingNotice").hidden = !notices.length;
  $("frameSlider").max = recording.frames - 1; $("frameSlider").value = 0; $("frameSlider").disabled = false;
  $("viewport").style.aspectRatio = `${recording.width} / ${recording.height}`;
  $("playButton").disabled = recording.frames < 2;
  $("viewerPlaceholder").hidden = false; $("viewerBadge").hidden = true;
  renderLibrary(); renderTiming(); renderResults(); loadImage();
  const saved = state.savedAnalyses.get(recording.id);
  if (saved) {
    setBusy(true); $("analysisStatus").textContent = "Restoring previous analysis…";
    state.session = saved.session;
    try {
      await fetchResult();
      const settings = state.result.settings;
      $("radius").value = settings.radius; $("radiusValue").textContent = `${settings.radius} px`;
      $("sensitivity").value = settings.sensitivity * 100; updateSensitivityLabel();
      $("baseline").value = settings.baseline_frames; $("prominence").value = settings.prominence_percent; $("separation").value = settings.separation_frames;
      $("analyzeButton").classList.remove("dirty");
      $("analysisStatus").textContent = `Cellpose · ${state.result.rois.length} regions measured · Previous analysis restored`;
      setBusy(false); renderTiming(); return;
    } catch { state.savedAnalyses.delete(recording.id); state.session = null; setBusy(false); }
  }
  await runAnalysis();
}

async function runAnalysis() {
  if (!state.recording || state.busy) return;
  const generation = ++state.generation;
  state.result = null; state.selected = null;
  renderResults(); setBusy(true); renderTiming();
  $("progressBar").value = 0; $("progressPercent").textContent = "0%";
  $("progressMessage").textContent = "Preparing analysis…"; $("analysisStatus").textContent = "Analyzing recording";
  $("analyzeButton").classList.remove("dirty");
  try {
    const job = await api("/api/analyze", {recording_id: state.recording.id, radius: Number($("radius").value), sensitivity: Number($("sensitivity").value) / 100, ...parameters()});
    if (generation !== state.generation) return;
    state.session = job.id; poll(generation);
  } catch (error) { setBusy(false); renderTiming(); $("analysisStatus").textContent = "Analysis needs attention"; notify(error.message); }
}

async function poll(generation) {
  if (generation !== state.generation) return;
  try {
    const status = await api(`/api/sessions/${state.session}`);
    if (generation !== state.generation) return;
    $("progressBar").value = status.progress; $("progressPercent").textContent = `${Math.round(status.progress * 100)}%`;
    $("progressMessage").textContent = status.message;
    if (status.status === "done") {
      await fetchResult(); setBusy(false); renderTiming();
      $("analysisStatus").textContent = `Cellpose · ${state.result.rois.length} regions measured · ${fmt(status.elapsed, 1)} s`;
      if (!state.result.rois.length) notify("No regions can contain this circle. Try a smaller radius or higher detection sensitivity.");
      if (status.error) notify(status.error);
    } else if (status.status === "error") {
      setBusy(false); renderTiming(); $("analysisStatus").textContent = "Analysis needs attention"; notify(status.error);
    } else state.pollTimer = setTimeout(() => poll(generation), 400);
  } catch (error) { setBusy(false); renderTiming(); notify(error.message); }
}

async function fetchResult() {
  state.result = await api(`/api/sessions/${state.session}/result`);
  state.savedAnalyses.set(state.recording.id, {session:state.session});
  if (!selectedROI()) state.selected = state.result.rois[0]?.id ?? null;
  state.interval = state.result.interval;
  renderResults();
}

function renderResults() {
  const result = state.result, roi = selectedROI();
  $("detectionHint").textContent = result?.detection
    ? `${result.detection.circles_placed} circles initially fit inside ${result.detection.candidate_regions} Cellpose regions. Reanalyzing replaces circle edits.`
    : "Standard sensitivity uses the settings tested on your dataset. Reanalyzing replaces the current circles.";
  $("selectedTitle").textContent = roi ? regionName(roi.id) : "Select a region";
  $("selectedCoordinates").textContent = roi ? `x ${roi.x} · y ${roi.y} · radius ${roi.radius} px${roi.manual ? " · edited" : ""}` : "Fixed circular measurement region";
  $("includeSelected").checked = Boolean(roi?.included);
  $("includeSelected").disabled = state.busy || !roi; $("deleteSelected").disabled = state.busy || !roi;
  $("peakCount").textContent = roi ? roi.peak_count : "—";
  $("peakRate").textContent = fmt(roi?.peak_rate_per_min, 2);
  $("meanIntensity").textContent = fmt(roi?.mean, 1);
  $("roiCount").textContent = result?.rois.length ?? 0;
  const n = result ? (state.mode === "relative" ? result.population.relative_included : result.population.included) : 0;
  $("includedCount").textContent = `${n} region${n === 1 ? "" : "s"}`;
  $("baselineNote").textContent = state.mode === "relative"
    ? `ΔF/F₀ uses each region’s mean over the first ${result?.baseline_frames ?? Number($("baseline").value)} frames as F₀. No background subtraction. Zero baselines are excluded from this view.`
    : "Measurements use original pixel values. Display brightness does not affect results.";
  $("populationSummary").textContent = "Each checked region contributes equally.";
  $("exportButton").disabled = state.busy || !result; $("applySignal").disabled = state.busy || !result;
  updateRegionControls(); renderMetrics(); renderRegions(); drawViewer(); drawCharts();
}

function renderMetrics() {
  const metrics = state.result?.metrics;
  $("checkedRegionCount").textContent = fmt(metrics?.region_count ?? 0, 0);
  $("metricsEmpty").hidden = Boolean(metrics);
  $("metricsContent").hidden = !metrics;
  if (!metrics) return;
  $("checkedMeanPeaks").textContent = fmt(metrics.mean_peak_count, 2);
  $("checkedPeakInterval").textContent = metrics.median_interval_seconds == null ? "—" : `${fmt(metrics.median_interval_seconds, 2)} s`;
  $("noPeaksCount").textContent = fmt(metrics.no_peaks_count, 0);
  $("singlePeakCount").textContent = fmt(metrics.single_peak_count, 0);
  $("multiplePeaksCount").textContent = fmt(metrics.multiple_peaks_count, 0);
  $("metricsTiming").textContent = !metrics.region_count
    ? "Check regions to calculate these metrics."
    : state.result.interval == null
      ? "Confirm the frame interval to calculate time between peaks."
      : !metrics.interval_region_count
        ? "Time between peaks needs a region with at least two detected peaks."
        : `Time between peaks uses ${fmt(metrics.interval_region_count, 0)} checked region${metrics.interval_region_count === 1 ? "" : "s"} with at least two peaks.`;
}

function setupPopulationTabs() {
  const tabs = [$("metricsTab"), $("populationTraceTab")];
  const activate = tab => {
    for (const button of tabs) {
      const active = button === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      $(button.getAttribute("aria-controls")).hidden = !active;
    }
    drawCharts();
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", event => {
      let next;
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) next = tabs[1 - index];
      else if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs[1];
      if (next) {event.preventDefault(); activate(next); next.focus();}
    });
  });
}

function renderRegions() {
  const root = $("regionList"); root.replaceChildren();
  const query = $("regionSearch").value.trim().replace(/^#/, "");
  const rois = state.result?.rois.filter(r => !query || String(r.id).includes(query)) ?? [];
  rois.sort($("regionSort").value === "peaks"
    ? (a, b) => b.peak_count - a.peak_count || a.id - b.id
    : (a, b) => a.id - b.id);
  if (!rois.length) { root.innerHTML = `<p class="empty-list">${query ? "No matching regions." : state.result ? "No regions detected. Adjust detection settings and analyze again." : "Detected regions will appear here."}</p>`; return; }
  const fragment = document.createDocumentFragment();
  for (const roi of rois) {
    const row = document.createElement("div"); row.className = `region-row${roi.id === state.selected ? " active" : ""}${roi.included ? "" : " excluded"}`;
    row.innerHTML = `<label><input type="checkbox" ${roi.included ? "checked" : ""} aria-label="Include ${regionName(roi.id)} in checked regions"></label><button>${regionName(roi.id)}</button><span>${roi.peak_count}</span><span>${fmt(roi.mean, 0)}</span>`;
    row.querySelector("input").disabled = state.busy;
    row.querySelector("input").addEventListener("change", e => editRegion({action: "include", roi_id: roi.id, included: e.target.checked}));
    row.querySelector("button").addEventListener("click", () => selectROI(roi.id));
    fragment.append(row);
  }
  root.append(fragment);
}

function selectROI(id) { state.selected = id; renderResults(); }
async function editRegion(body) {
  if (state.busy || !state.session) return;
  setBusy(true);
  $("progressMessage").textContent = body.action === "include_all" ? "Updating all regions…" : "Updating region…"; $("progressBar").value = 0;
  try {
    const job = await api(`/api/sessions/${state.session}/edit`, body);
    if (job.status === "running") poll(state.generation);
    else { await fetchResult(); setBusy(false); }
  } catch (error) { setBusy(false); renderResults(); notify(error.message); }
}

async function loadImage() {
  if (!state.recording) return;
  const request = ++state.imageRequest;
  const url = `/api/recordings/${state.recording.id}/frame?index=${state.reference ? -1 : state.frame}&gain=${$("brightness").value}`;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error("Unable to read this image frame.");
    const blob = await response.blob();
    if (request !== state.imageRequest) return;
    const image = await createImageBitmap(blob);
    if (request !== state.imageRequest) { image.close(); return; }
    state.image?.close(); state.image = image;
    $("viewerPlaceholder").hidden = true; $("viewerBadge").hidden = false;
    $("viewerBadge").textContent = state.reference ? `Mean reference · ${Math.min(12, state.recording.frames)} sampled frames` : `Frame ${state.frame + 1}${state.interval ? ` · ${fmt(state.frame * state.interval, 2)} s` : ""}`;
    drawViewer(); updateFrameLabel();
  } catch (error) { if (request === state.imageRequest) { pause(); notify(error.message); } }
}

function updateFrameLabel() {
  $("frameLabel").textContent = state.recording ? `${state.frame + 1} / ${state.recording.frames}` : "— / —";
}
async function setFrame(frame) {
  if (!state.recording) return;
  state.frame = Math.max(0, Math.min(state.recording.frames - 1, Math.round(frame)));
  state.reference = false; $("referenceMode").checked = false; $("frameSlider").value = state.frame;
  updateFrameLabel(); drawCharts(); await loadImage();
}
function pause() { state.playing = false; clearTimeout(state.playTimer); $("playButton").textContent = "▶"; $("playButton").setAttribute("aria-label", "Play recording"); }
async function playStep() {
  if (!state.playing) return;
  await setFrame(state.frame >= state.recording.frames - 1 ? 0 : state.frame + 1);
  if (state.playing) state.playTimer = setTimeout(playStep, 1000 / Number($("playSpeed").value));
}

function canvasContext(canvas) {
  const box = canvas.getBoundingClientRect(), ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(box.width)), height = Math.max(1, Math.round(box.height));
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
  return {ctx, width, height};
}
function viewTransform() {
  const box = $("imageCanvas").getBoundingClientRect();
  const scale = box.width / (state.recording?.width || 1920) * state.zoom;
  return {scale, dx: box.width / 2 - state.cx * scale, dy: box.height / 2 - state.cy * scale};
}
function imagePoint(event) {
  const box = $("imageCanvas").getBoundingClientRect(), v = viewTransform();
  return {x: (event.clientX - box.left - v.dx) / v.scale, y: (event.clientY - box.top - v.dy) / v.scale};
}
function hitROI(point) {
  if (!state.result) return null;
  const scale = viewTransform().scale;
  return state.result.rois.find(r => Math.hypot(r.x - point.x, r.y - point.y) <= Math.max(r.radius, 6 / scale));
}

function drawViewer() {
  const {ctx, width, height} = canvasContext($("imageCanvas"));
  ctx.fillStyle = "#11191c"; ctx.fillRect(0, 0, width, height);
  if (!state.image || !state.recording) return;
  const v = viewTransform();
  ctx.save(); ctx.translate(v.dx, v.dy); ctx.scale(v.scale, v.scale);
  ctx.drawImage(state.image, 0, 0, state.recording.width, state.recording.height);
  if ($("outlines").checked && state.result) {
    ctx.strokeStyle = "#6edab557"; ctx.lineWidth = .7 / v.scale;
    for (const contour of state.result.outlines) {
      ctx.beginPath(); contour.forEach(([x,y], i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y)); ctx.closePath(); ctx.stroke();
    }
  }
  if (state.result) for (const roi of state.result.rois) {
    const selected = roi.id === state.selected;
    const position = state.ghost?.id === roi.id ? state.ghost : roi;
    ctx.beginPath(); ctx.arc(position.x, position.y, roi.radius, 0, Math.PI * 2);
    ctx.strokeStyle = selected ? "#ffd576" : roi.included ? "#74d4b5c9" : "#adb3b68c";
    ctx.lineWidth = (selected ? 1.8 : roi.included ? 1.35 : 1) / v.scale;
    ctx.setLineDash(roi.included ? [] : [3 / v.scale, 2 / v.scale]); ctx.stroke(); ctx.setLineDash([]);
    if (selected) { ctx.fillStyle = "#f6c96530"; ctx.fill(); }
    if (selected && Number($("radius").value) !== roi.radius) {
      ctx.beginPath(); ctx.arc(position.x, position.y, Number($("radius").value), 0, Math.PI * 2);
      ctx.setLineDash([3/v.scale, 3/v.scale]); ctx.strokeStyle="#ffd576"; ctx.lineWidth=1/v.scale; ctx.stroke(); ctx.setLineDash([]);
    }
    if (selected || $("labels").checked) {
      ctx.font = `${10 / v.scale}px 'Segoe UI', sans-serif`; ctx.textAlign = "center";
      ctx.lineWidth = 3 / v.scale; ctx.strokeStyle = "#102025db";
      ctx.strokeText(String(roi.id), position.x, position.y - roi.radius - 4 / v.scale);
      ctx.fillStyle = selected ? "#ffdf9a" : "#c2eadc"; ctx.fillText(String(roi.id), position.x, position.y - roi.radius - 4 / v.scale);
    }
  }
  if (state.tool === "add" && state.ghost && !state.busy && state.result) {
    ctx.beginPath(); ctx.arc(state.ghost.x, state.ghost.y, state.result.settings.radius, 0, Math.PI * 2);
    ctx.strokeStyle = "#ffd576"; ctx.lineWidth = 1.5 / v.scale; ctx.setLineDash([4/v.scale, 3/v.scale]); ctx.stroke();
  }
  ctx.restore(); $("zoomBadge").textContent = `${state.zoom.toFixed(1)}×`;
}

function drawChart(canvas, values, options = {}) {
  const {ctx, width, height} = canvasContext(canvas);
  const pad = {left: 49, right: 16, top: 25, bottom: 34}, pw = Math.max(1, width - 65), ph = Math.max(1, height - 59);
  const relative = state.mode === "relative", multiplier = relative ? 100 : 1;
  const sd = options.sd && $("showSpread").checked ? options.sd : null;
  const valid = values?.length > 0;
  let low = valid ? Infinity : 0, high = valid ? -Infinity : 1;
  if (valid) values.forEach((value, i) => { low = Math.min(low, (value - (sd?.[i] || 0)) * multiplier); high = Math.max(high, (value + (sd?.[i] || 0)) * multiplier); });
  const span = Math.max(high - low, Math.abs(high) * .02, .01); low -= span * .12; high += span * .12;
  const count = values?.length || state.recording?.frames || 2;
  const x = i => pad.left + i / Math.max(count - 1, 1) * pw;
  const y = value => pad.top + (high - value * multiplier) / (high - low) * ph;
  ctx.font = "9px 'Segoe UI', sans-serif"; ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const py = pad.top + ph * i / 3, value = high - (high - low) * i / 3;
    ctx.strokeStyle = "#edf1f1"; ctx.beginPath(); ctx.moveTo(pad.left, py); ctx.lineTo(width - pad.right, py); ctx.stroke();
    ctx.fillStyle = "#9ba8aa"; ctx.textAlign = "right";
    if (valid) ctx.fillText(fmt(value, Math.abs(value) < 10 ? 1 : 0), pad.left - 8, py + 3);
  }
  ctx.fillStyle = "#9aa8aa"; ctx.textAlign = "center";
  const ticks = width < 330 ? 3 : 4;
  for (let i = 0; i <= ticks; i++) {
    const frame = Math.round(i * (count - 1) / ticks);
    ctx.fillText(state.interval ? fmt(frame * state.interval, 1) : String(frame + 1), x(frame), height - 18);
  }
  ctx.fillText(state.interval ? "Time (s)" : "Frame", pad.left + pw / 2, height - 3);
  ctx.textAlign = "left"; ctx.fillText(relative ? "ΔF/F₀ (%)" : "Intensity (a.u.)", pad.left, 12);
  if (!valid) return;
  ctx.save(); ctx.beginPath(); ctx.rect(pad.left, pad.top, pw, ph); ctx.clip();
  if (sd) {
    ctx.beginPath(); values.forEach((v, i) => i ? ctx.lineTo(x(i), y(v + sd[i])) : ctx.moveTo(x(i), y(v + sd[i])));
    for (let i = count - 1; i >= 0; i--) ctx.lineTo(x(i), y(values[i] - sd[i]));
    ctx.closePath(); ctx.fillStyle = "#329b8720"; ctx.fill();
  }
  ctx.strokeStyle = options.color || "#318f80"; ctx.lineWidth = 1.6; ctx.lineJoin = "round";
  ctx.beginPath(); values.forEach((value, i) => i ? ctx.lineTo(x(i), y(value)) : ctx.moveTo(x(i), y(value))); ctx.stroke();
  for (const peak of options.peaks || []) {
    ctx.beginPath(); ctx.arc(x(peak), y(values[peak]), 3, 0, Math.PI * 2); ctx.fillStyle = "#dfa351"; ctx.fill();
  }
  const cursor = state.hoverFrame ?? (state.reference ? null : state.frame);
  if (cursor != null && cursor < count) {
    ctx.setLineDash([3,3]); ctx.strokeStyle = "#91ada6"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x(cursor), pad.top); ctx.lineTo(x(cursor), pad.top + ph); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(x(cursor), y(values[cursor]), 3.5, 0, Math.PI * 2); ctx.fillStyle = options.color || "#318f80"; ctx.fill();
  }
  ctx.restore();
}

function drawCharts() {
  const roi = selectedROI(), values = roi?.[state.mode];
  const population = state.result?.population[state.mode];
  $("traceEmpty").hidden = Boolean(values?.length);
  if (roi && !values) $("traceEmpty").textContent = "This region has no positive baseline for ΔF/F₀.";
  else if (!roi) $("traceEmpty").innerHTML = "Select a circle in the recording<br>to explore its intensity.";
  $("populationEmpty").hidden = Boolean(population?.mean.length);
  $("populationEmpty").textContent = !state.result ? "The average of your included regions will appear here."
    : state.result.population.included === 0 ? "No regions included. Check a region to show the average."
    : "No included regions have a positive baseline for ΔF/F₀.";
  drawChart($("traceCanvas"), values, {color: "#548c98", peaks: roi?.peaks});
  if (!$("populationTraceView").hidden) drawChart($("populationCanvas"), population?.mean, {sd: population?.sd});
  const cursor = state.hoverFrame ?? (state.reference ? null : state.frame);
  $("traceReadout").textContent = values && cursor != null ? `Frame ${cursor + 1}${state.interval ? ` · ${fmt(cursor * state.interval, 2)} s` : ""}  /  ${fmt(values[cursor] * (state.mode === "relative" ? 100 : 1), 2)} ${state.mode === "relative" ? "%" : "a.u."}` : "Click a graph to jump to that frame.";
}

function setupViewer() {
  const canvas = $("imageCanvas");
  canvas.addEventListener("pointerdown", e => {
    if (!state.recording || !state.image) return;
    const p = imagePoint(e), roi = hitROI(p);
    if (roi) selectROI(roi.id);
    if (state.tool === "add" && !state.busy && state.result) {
      editRegion({action:"add", x:Math.round(p.x), y:Math.round(p.y)}); return;
    }
    state.drag = {startX: e.clientX, startY: e.clientY, cx: state.cx, cy: state.cy, roi: state.tool === "move" && !state.busy ? roi : null, moved: false};
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", e => {
    if (!state.recording) return;
    const p = imagePoint(e);
    if (state.drag) {
      const d = state.drag, dx = e.clientX - d.startX, dy = e.clientY - d.startY;
      if (Math.hypot(dx,dy) > 3) d.moved = true;
      if (d.roi) state.ghost = {...d.roi, x: Math.round(p.x), y: Math.round(p.y)};
      else if (state.zoom > 1) { const scale = viewTransform().scale; state.cx = d.cx - dx / scale; state.cy = d.cy - dy / scale; }
    } else if (state.tool === "add") state.ghost = p;
    drawViewer();
  });
  const endDrag = async e => {
    const drag = state.drag, ghost = state.ghost; state.drag = null; state.ghost = null;
    if (e && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    if (drag?.roi && ghost && drag.moved) await editRegion({action:"move", roi_id:drag.roi.id, x:ghost.x, y:ghost.y});
    drawViewer();
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", () => {state.drag=null; state.ghost=null; drawViewer();});
  canvas.addEventListener("pointerleave", () => {if (!state.drag) {state.ghost=null; drawViewer();}});
  canvas.addEventListener("wheel", e => {
    if (!state.recording) return; e.preventDefault();
    const p = imagePoint(e), box = canvas.getBoundingClientRect();
    state.zoom = Math.max(1, Math.min(6, state.zoom * Math.exp(-e.deltaY * .0015)));
    const scale = box.width / state.recording.width * state.zoom;
    state.cx = p.x - (e.clientX - box.left - box.width / 2) / scale;
    state.cy = p.y - (e.clientY - box.top - box.height / 2) / scale;
    if (state.zoom === 1) { state.cx = state.recording.width / 2; state.cy = state.recording.height / 2; }
    drawViewer();
  }, {passive:false});
  canvas.addEventListener("keydown", e => {
    if (e.key === "ArrowRight") {e.preventDefault(); pause(); setFrame(state.frame+1);}
    if (e.key === "ArrowLeft") {e.preventDefault(); pause(); setFrame(state.frame-1);}
  });
  $("resetZoom").addEventListener("click", () => {if (state.recording) {state.zoom=1;state.cx=state.recording.width/2;state.cy=state.recording.height/2;drawViewer();}});
}

function setupCharts() {
  for (const id of ["traceCanvas", "populationCanvas"]) {
    const canvas = $(id);
    const frameAt = event => Math.max(0, Math.min((state.recording?.frames || 1) - 1, Math.round((event.clientX - canvas.getBoundingClientRect().left - 49) / Math.max(1, canvas.getBoundingClientRect().width - 65) * ((state.recording?.frames || 1) - 1))));
    canvas.addEventListener("pointermove", event => {state.hoverFrame=frameAt(event); drawCharts();});
    canvas.addEventListener("pointerleave", () => {state.hoverFrame=null; drawCharts();});
    canvas.addEventListener("click", event => {pause(); setFrame(frameAt(event));});
  }
  new ResizeObserver(() => {drawViewer(); drawCharts();}).observe($("analysisStatus").closest("main"));
}

function setMode(mode) {
  state.mode=mode;
  for (const [id, value] of [["rawMode","raw"],["relativeMode","relative"]]) {$(id).classList.toggle("active", mode===value);$(id).setAttribute("aria-pressed", String(mode===value));}
  renderResults();
}

function addRecording(r) {
  if (!state.recordings.some(x=>x.id===r.id)) state.recordings.push(r);
  $("openDialog").close(); selectRecording(r);
}

async function init() {
  setupViewer(); setupCharts(); setupPopulationTabs();
  $("analyzeButton").addEventListener("click", runAnalysis);
  $("radius").addEventListener("input", () => {$("radiusValue").textContent=`${$("radius").value} px`;$("analyzeButton").classList.add("dirty");drawViewer();});
  $("sensitivity").addEventListener("input", () => {updateSensitivityLabel();$("analyzeButton").classList.add("dirty");});
  $("applySignal").addEventListener("click", async () => {
    if (!state.session || state.busy) return;
    setBusy(true);
    try {await api(`/api/sessions/${state.session}/settings`, parameters()); await fetchResult();}
    catch(error){notify(error.message);} finally{setBusy(false);renderTiming();}
  });
  $("rawMode").addEventListener("click", () => setMode("raw")); $("relativeMode").addEventListener("click", () => setMode("relative"));
  $("showSpread").addEventListener("change", drawCharts);
  for (const id of ["outlines","labels"]) $(id).addEventListener("change", drawViewer);
  $("brightness").addEventListener("change", loadImage);
  $("referenceMode").addEventListener("change", () => {pause();state.reference=$("referenceMode").checked;loadImage();drawCharts();});
  $("frameSlider").addEventListener("input", () => {pause();setFrame(Number($("frameSlider").value));});
  $("playButton").addEventListener("click", () => {if(state.playing) pause(); else{state.playing=true;$("playButton").textContent="Ⅱ";$("playButton").setAttribute("aria-label","Pause recording");playStep();}});
  document.querySelectorAll("[data-tool]").forEach(button => button.addEventListener("click", () => {
    state.tool=button.dataset.tool;state.ghost=null;
    document.querySelectorAll("[data-tool]").forEach(b=>{b.classList.toggle("active",b===button);b.setAttribute("aria-pressed",String(b===button));});
    $("viewerInstruction").textContent=state.tool==="move" ? "Drag a circle to a new center. It must remain fully inside a detected region." : state.tool==="add" ? "Click inside a detected region to add a circle. Enable Outlines to see the detected boundaries." : "Click a circle to inspect its trace. Scroll to zoom; drag the background to pan.";
    drawViewer();
  }));
  $("includeSelected").addEventListener("change", e=>editRegion({action:"include",roi_id:state.selected,included:e.target.checked}));
  $("deleteSelected").addEventListener("click", ()=>{if(selectedROI() && confirm(`Remove ${regionName(state.selected)}? You can add a circle again or rerun detection.`))editRegion({action:"delete",roi_id:state.selected});});
  $("regionSearch").addEventListener("input",renderRegions);
  $("regionSort").addEventListener("change",renderRegions);
  $("checkAllRegions").addEventListener("click",()=>editRegion({action:"include_all",included:true}));
  $("uncheckAllRegions").addEventListener("click",()=>editRegion({action:"include_all",included:false}));
  $("dismissToast").addEventListener("click",()=>$("toast").hidden=true);
  $("openButton").addEventListener("click",()=>{$("openError").textContent="";$("openDialog").showModal();});
  document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>$(b.dataset.close).close()));
  $("browseNative").addEventListener("click",async()=>{
    $("browseNative").disabled=true;$("openError").textContent="Choose a file in the system file picker…";
    try{const data=await api("/api/browse",{});if(data.recording)addRecording(data.recording);$("openError").textContent="";}
    catch(error){$("openError").textContent=error.message;}finally{$("browseNative").disabled=false;}
  });
  $("openForm").addEventListener("submit",async event=>{
    event.preventDefault();$("openPathButton").disabled=true;$("openError").textContent="";
    try{const path=$("filePath").value.trim().replace(/^["']|["']$/g,"");addRecording(await api("/api/recordings/open",{path}));}
    catch(error){$("openError").textContent=error.message;}finally{$("openPathButton").disabled=false;}
  });
  $("exportButton").addEventListener("click",()=>{
    for(const [id,kind] of [["exportTraces","traces"],["exportSummary","summary"],["exportMetrics","metrics"],["exportSession","session"]])$(id).href=`/api/sessions/${state.session}/export?kind=${kind}&mode=${state.mode}`;
    $("exportDialog").showModal();
  });
  try{
    const data=await api("/api/recordings");state.recordings=data.recordings;renderLibrary();
    if(data.errors.length)notify(data.errors.join(" "));
    if(state.recordings.length)await selectRecording(state.recordings[0]);
  }catch(error){notify(error.message);$("analysisStatus").textContent="Could not connect to the local app";}
}
init();
