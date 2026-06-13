import {
  clearLiveHistory,
  configureChannels,
  configurePlayback,
  getChannel,
  getLiveState,
  liveMetric,
  onChannelChange,
  onLiveChange,
  pushLiveFrame,
  setChannelFilter,
  setChannelSort,
  setPsdScale,
  updateLiveStatus,
} from "./state.js";
import { createDisposables } from "./disposables.js";
import { createLiveSource, createStaticSource, loadData, loadDatasetFiles, loadDatasetUrl, setSource } from "./loader.js";
import {
  MAX_SESSIONS,
  addSession,
  getBaselineId,
  getBaselineSession,
  getSessions,
  getViewMode,
  onSessionsChange,
  removeSession,
  setBaseline,
  setViewMode,
  toggleSession,
} from "./sessions.js";
import { initPsdView } from "./views/psd-view.js";
import { initCentroidView } from "./views/centroid-view.js";
import { initGeometryView } from "./views/geometry-view.js";
import { initChannelGrid } from "./views/channel-grid.js";
import { initPlaybackBar } from "./views/playback-bar.js";
import { initPhaseSpace } from "./views/phase-space.js";
import { initMonitorView } from "./views/monitor-view.js";
import { initPolarChronomap } from "./views/polar-chronomap.js";
import { initKuramotoView } from "./views/kuramoto.js";
import { initChannelNetwork } from "./views/channel-network.js";
import { initTdaView } from "./views/tda-view.js";
import {
  buildWorkbenchState,
  formatSignedNumber,
  formatSignedPercent,
  generateWorkbenchReport,
} from "./workbench.js";

const dashboard = document.querySelector("#dashboard");
const loadStatus = document.querySelector("#load-status");
const selectedChannel = document.querySelector("#selected-channel");
const tooltip = createTooltip(document.querySelector("#tooltip"));
const liveUrl = document.querySelector("#live-url");
const liveConnect = document.querySelector("#live-connect");
const liveDisconnect = document.querySelector("#live-disconnect");
const liveStatus = document.querySelector("#live-status");
const liveFrames = document.querySelector("#live-frames");
const liveTime = document.querySelector("#live-time");
const liveCompute = document.querySelector("#live-compute");
const liveAlpha = document.querySelector("#live-alpha");
const sessionDropZone = document.querySelector("#session-drop-zone");
const sessionFileInput = document.querySelector("#session-file-input");
const sessionList = document.querySelector("#session-list");
const sessionMessage = document.querySelector("#session-message");
const sessionCount = document.querySelector("#session-count");
const baselineSelect = document.querySelector("#baseline-select");
const workbenchImport = document.querySelector("#workbench-import");
const workbenchReport = document.querySelector("#workbench-report");
const workbenchDropZone = document.querySelector("#workbench-drop-zone");
const workbenchScenario = document.querySelector("#workbench-scenario");
const workbenchStatus = document.querySelector("#workbench-status");
const workbenchComparisons = document.querySelector("#workbench-comparisons");
const workbenchMetrics = document.querySelector("#workbench-metrics");
const workbenchOpenComparison = document.querySelector("#workbench-open-comparison");
const headerStatus = document.querySelector(".header-status");
let backendDatasetBanner = null;
let liveConnection = null;
let activeData = null;
let monitorView = null;
let activeScenarioId = workbenchScenario?.value ?? "trained-vs-naive";
const appDisposables = createDisposables();

init();

async function init() {
  try {
    const data = await loadData();
    activeData = data;
    configureChannels(data.meta.channels);
    configurePlayback(data.geometry.time.length);
    updateHeaderSummary(data);
    updateSelectedChannelLabel(getChannel());
    await waitForFonts();
    if (loadStatus) loadStatus.textContent = datasetReadyLabel(data);
    ensureBackendDatasetBanner(data);
    dashboard.setAttribute("aria-busy", "false");

    // Bind controls before initializing plot modules. In previous builds, a
    // single plotting exception could stop the whole workbench before buttons
    // were registered, which made the NeuroMouse page appear dead/blank.
    bindSessionControls();
    bindControls();
    renderSessionSidebar();
    renderWorkbench();

    safeInitView("PSD heatmap", "#psd-heatmap", () => initPsdView(data, tooltip));
    safeInitView("Centroid over time", "#centroid-chart", () => initCentroidView(data, tooltip));
    safeInitView("Playback", "#playback-bar", () => initPlaybackBar(document.querySelector("#playback-bar"), data));
    safeInitView("Monitor", "#monitor-panel", () => {
      monitorView = initMonitorView(document.querySelector("#monitor-panel"), data);
      return monitorView?.dispose;
    });
    safeInitView("Geometry stack", "#geometry-chart", () => initGeometryView(data, tooltip));
    safeInitView("Channel grid", "#channel-grid", () => initChannelGrid(data, tooltip));
    safeInitView("Phase space", "#phase-space", () => initPhaseSpace(document.querySelector("#phase-space"), data));
    safeInitView("Polar alpha chronomap", "#polar-chronomap", () => initPolarChronomap(document.querySelector("#polar-chronomap"), data, tooltip));
    safeInitView("Kuramoto animation", "#kuramoto-view", () => initKuramotoView(document.querySelector("#kuramoto-view"), data));
    safeInitView("Channel network", "#channel-network", () => initChannelNetwork(document.querySelector("#channel-network"), data, tooltip));
    safeInitView("TDA view", "#tda-view", () => initTdaView(document.querySelector("#tda-view"), data, tooltip));

    appDisposables.add(onChannelChange((channel) => {
      updateSelectedChannelLabel(channel);
      updateLiveMetrics(getLiveState());
    }));
    applyQueryStartupOptions();
    appDisposables.add(onLiveChange((state) => {
      updateLiveMetrics(state);
      monitorView?.setLiveState(state);
    }));
    appDisposables.add(onSessionsChange(() => {
      syncSessionState();
      renderSessionSidebar();
      renderWorkbench();
      updateLiveMetrics(getLiveState());
    }));
    appDisposables.listen(window, "pagehide", () => appDisposables.dispose(), { once: true });
  } catch (error) {
    dashboard.setAttribute("aria-busy", "false");
    if (loadStatus) {
      loadStatus.textContent = error.message;
      loadStatus.style.color = "#ff786d";
    }
    renderPageError(error);
    console.error("NeuroMouse startup failed", error);
  }
}

function safeInitView(label, selector, initializer) {
  try {
    const disposer = initializer();
    appDisposables.add(disposer);
    return true;
  } catch (error) {
    console.error(`NeuroMouse view failed: ${label}`, error);
    renderViewError(label, selector, error);
    return false;
  }
}

function renderPageError(error) {
  const target = dashboard || document.body;
  const node = element("div", { className: "view-error page-error", role: "alert" },
    element("strong", {}, "NeuroMouse startup error"),
    element("span", {}, String(error?.message || error || "Unknown error")),
  );
  target.prepend(node);
}

function renderViewError(label, selector, error) {
  try {
    renderCoreFallback(label, selector);
  } catch (fallbackError) {
    console.error(`NeuroMouse fallback failed: ${label}`, fallbackError);
  }
  const anchor = selector ? document.querySelector(selector) : null;
  const panel = anchor?.closest?.(".panel") || anchor || dashboard || document.body;
  const body = panel.querySelector?.(".panel-body") || panel;
  const node = element("div", { className: "view-error", role: "alert" },
    element("strong", {}, `${label} recovered with fallback renderer`),
    element("span", {}, String(error?.message || error || "Unknown plotting error")),
  );
  body.append(node);
}

function renderCoreFallback(label, selector) {
  if (!activeData) return;
  if (selector === "#psd-heatmap") {
    drawFallbackPsdHeatmap();
    drawFallbackPsdOverlay();
  } else if (selector === "#centroid-chart") {
    drawFallbackLine("#centroid-chart", activeData?.centroid?.time_relative, firstChannelSeries(activeData?.centroid?.values), "Fallback centroid");
  } else if (selector === "#geometry-chart") {
    drawFallbackLine("#geometry-chart", activeData?.geometry?.time, firstChannelSeries(activeData?.geometry?.centroid), "Fallback geometry centroid");
  } else if (selector === "#channel-grid") {
    renderFallbackChannelGrid();
  }
}

function firstChannelSeries(matrix) {
  return Array.isArray(matrix) && Array.isArray(matrix[0]) ? matrix[0] : [];
}

function drawFallbackPsdHeatmap() {
  const canvas = document.querySelector("#psd-heatmap");
  const ctx = canvas?.getContext?.("2d");
  if (!ctx) return;
  const data = activeData;
  const channels = data?.meta?.channels || [];
  const freqs = data?.welch_psd?.frequencies || [];
  const psd = data?.welch_psd?.psd || [];
  const width = canvas.width || 760;
  const height = canvas.height || 420;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#061318";
  ctx.fillRect(0, 0, width, height);
  const left = 64, top = 18, right = 14, bottom = 38;
  const plotW = Math.max(1, width - left - right);
  const plotH = Math.max(1, height - top - bottom);
  const vals = psd.flat().map((v) => Math.log10(Math.max(Number(v) || 0, 1e-12)));
  const min = Math.min(...vals, -12);
  const max = Math.max(...vals, min + 1);
  channels.forEach((ch, ci) => {
    const row = Array.isArray(psd[ci]) ? psd[ci] : [];
    const y = top + ci * plotH / Math.max(1, channels.length);
    const h = Math.max(1, plotH / Math.max(1, channels.length));
    row.forEach((v, fi) => {
      const x = left + fi * plotW / Math.max(1, row.length);
      const w = Math.max(1, plotW / Math.max(1, row.length));
      const t = Math.max(0, Math.min(1, (Math.log10(Math.max(Number(v) || 0, 1e-12)) - min) / (max - min || 1)));
      const g = Math.round(45 + t * 190);
      const b = Math.round(70 + t * 80);
      ctx.fillStyle = `rgb(0,${g},${b})`;
      ctx.fillRect(x, y, w + 0.5, h + 0.5);
    });
    if (ci % Math.ceil(Math.max(1, channels.length / 24)) === 0) {
      ctx.fillStyle = "#9fb5c1";
      ctx.font = "10px monospace";
      ctx.textAlign = "right";
      ctx.fillText(String(ch), left - 8, y + h * 0.7);
    }
  });
  ctx.strokeStyle = "rgba(255,255,255,0.25)";
  ctx.strokeRect(left, top, plotW, plotH);
  ctx.fillStyle = "#9fb5c1";
  ctx.font = "11px monospace";
  ctx.textAlign = "center";
  ctx.fillText(`${freqs[0] ?? 0}–${freqs.at?.(-1) ?? freqs[freqs.length - 1] ?? "?"} Hz`, left + plotW / 2, height - 12);
}

function drawFallbackPsdOverlay() {
  const canvas = document.querySelector("#psd-overlay");
  const data = activeData;
  drawFallbackLineOnCanvas(canvas, data?.welch_psd?.frequencies, firstChannelSeries(data?.welch_psd?.psd).map((v) => Math.log10(Math.max(Number(v) || 0, 1e-12))), "Fallback PSD");
}

function drawFallbackLine(selector, xValues, yValues, title) {
  drawFallbackLineOnCanvas(document.querySelector(selector), xValues, yValues, title);
}

function drawFallbackLineOnCanvas(canvas, xValues, yValues, title) {
  const ctx = canvas?.getContext?.("2d");
  if (!ctx) return;
  const xs = Array.isArray(xValues) && xValues.length ? xValues.map(Number) : yValues.map((_, i) => i);
  const ys = Array.isArray(yValues) ? yValues.map(Number) : [];
  const width = canvas.width || 640;
  const height = canvas.height || 300;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#061318";
  ctx.fillRect(0, 0, width, height);
  const left = 48, top = 28, right = 16, bottom = 32;
  const plotW = Math.max(1, width - left - right);
  const plotH = Math.max(1, height - top - bottom);
  const xmin = Math.min(...xs, 0), xmax = Math.max(...xs, 1);
  const finiteYs = ys.filter(Number.isFinite);
  const ymin = Math.min(...finiteYs, 0), ymax = Math.max(...finiteYs, 1);
  const sx = (x) => left + ((x - xmin) / (xmax - xmin || 1)) * plotW;
  const sy = (y) => top + plotH - ((y - ymin) / (ymax - ymin || 1)) * plotH;
  ctx.strokeStyle = "rgba(255,255,255,0.25)";
  ctx.strokeRect(left, top, plotW, plotH);
  ctx.strokeStyle = "#00d4a0";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ys.forEach((y, i) => {
    const x = xs[i] ?? i;
    if (!Number.isFinite(y)) return;
    if (i === 0) ctx.moveTo(sx(x), sy(y));
    else ctx.lineTo(sx(x), sy(y));
  });
  ctx.stroke();
  ctx.fillStyle = "#c5d7df";
  ctx.font = "12px monospace";
  ctx.textAlign = "left";
  ctx.fillText(title, left, 16);
}

function renderFallbackChannelGrid() {
  const grid = document.querySelector("#channel-grid");
  if (!grid || !activeData) return;
  const channels = activeData.meta?.channels || [];
  grid.innerHTML = "";
  channels.forEach((channel) => {
    const button = element("button", { type: "button", className: "channel-tile fallback-channel-tile" }, channel);
    button.addEventListener("click", () => {
      try { setChannel(channel); } catch {}
      updateSelectedChannelLabel(channel);
    });
    grid.append(button);
  });
}

function bindControls() {
  document.querySelectorAll("[data-control='filter'] button").forEach((button) => {
    appDisposables.listen(button, "click", () => {
      setActiveButton("[data-control='filter'] button", button);
      setChannelFilter(button.dataset.filter);
    });
  });

  appDisposables.listen(document.querySelector("#channel-sort"), "change", (event) => {
    setChannelSort(event.target.value);
  });

  document.querySelectorAll("[data-control='psd-scale'] button").forEach((button) => {
    appDisposables.listen(button, "click", () => {
      setActiveButton("[data-control='psd-scale'] button", button);
      setPsdScale(button.dataset.scale);
    });
  });

  appDisposables.listen(liveConnect, "click", () => {
    startLive(liveUrl.value.trim() || "ws://127.0.0.1:8766");
  });
  appDisposables.listen(liveDisconnect, "click", stopLive);
}

function startLive(url) {
  if (liveConnection) liveConnection.stop();
  clearLiveHistory();
  updateLiveStatus({ connected: false, status: "connecting", url });
  liveConnect.disabled = true;
  liveDisconnect.disabled = false;
  if (liveStatus) {
    liveStatus.className = "live-status is-connecting";
    liveStatus.textContent = `connecting… ${url}`;
  }

  liveConnection = setSource(createLiveSource(url, { referenceData: activeData }));
  liveConnection.start(
    (frame) => {
      pushLiveFrame(frame);
      monitorView?.handleFrame(frame);
      updateLiveStatus({ connected: true, status: "live", url });
    },
    (status, detail = {}) => {
      const connected = status === "live";
      updateLiveStatus({
        connected,
        status,
        url,
        detail,
      });
      if (status === "error") {
        liveConnection?.stop();
        liveConnection = null;
        setSource(createStaticSource());
        clearLiveHistory();
        liveConnect.disabled = false;
        liveDisconnect.disabled = true;
      } else if (status === "disconnected") {
        liveConnection = null;
        setSource(createStaticSource());
        clearLiveHistory();
        liveConnect.disabled = false;
        liveDisconnect.disabled = true;
      }
    },
  );
}

function stopLive() {
  if (liveConnection) {
    liveConnection.stop();
    liveConnection = null;
  }
  setSource(createStaticSource());
  clearLiveHistory();
  updateLiveStatus({ connected: false, status: "static replay", url: liveUrl.value.trim() });
  liveConnect.disabled = false;
  liveDisconnect.disabled = true;
}

function updateLiveMetrics(state) {
  const frame = state.latestFrame;
  const channel = getChannel();
  if (liveFrames) liveFrames.textContent = String(state.frameCount);
  if (liveTime) liveTime.textContent = frame?.window_start_time_sec == null ? "—" : `${Number(frame.window_start_time_sec).toFixed(2)}s`;
  if (liveCompute) liveCompute.textContent = frame?.compute_ms == null ? "—" : `${Number(frame.compute_ms).toFixed(1)}ms`;
  const alpha = liveMetric(frame, channel, "alpha_relative_power");
  if (liveAlpha) liveAlpha.textContent = alpha == null ? "—" : alpha.toFixed(4);

  if (liveStatus) {
    liveStatus.className = `live-status ${statusClass(state)}`.trim();
    liveStatus.textContent = statusText(state);
  }
  if (loadStatus) {
    loadStatus.textContent = state.connected ? "Live" : "Ready";
  }
  liveConnect.disabled = state.connected;
  liveDisconnect.disabled = !state.connected && !liveConnection;
}

function statusClass(state) {
  if (state.status === "live" || state.connected) return "is-live";
  if (state.status === "connecting") return "is-connecting";
  if (state.status === "error") return "is-error";
  return "";
}

function statusText(state) {
  if (state.status === "live" || state.connected) return `● live · ${state.url || liveUrl.value}`;
  if (state.status === "connecting") return `connecting… ${state.url || liveUrl.value}`;
  if (state.status === "error") {
    return state.detail?.message ? `connection error · ${state.detail.message}` : "connection error";
  }
  if (state.status === "disconnected") return "static replay";
  return "static replay";
}

function setActiveButton(selector, active) {
  document.querySelectorAll(selector).forEach((button) => {
    button.classList.toggle("is-active", button === active);
  });
}

async function waitForFonts() {
  if (!document.fonts?.ready) return;
  await Promise.race([
    document.fonts.ready,
    new Promise((resolve) => setTimeout(resolve, 1500)),
  ]);
}

function bindSessionControls() {
  appDisposables.listen(workbenchImport, "click", () => sessionFileInput?.click());
  appDisposables.listen(workbenchReport, "click", downloadWorkbenchReport);
  appDisposables.listen(workbenchOpenComparison, "click", openComparisonSuite);
  appDisposables.listen(workbenchScenario, "change", () => {
    activeScenarioId = workbenchScenario.value;
    renderWorkbench();
  });
  appDisposables.listen(workbenchDropZone, "click", () => sessionFileInput?.click());
  appDisposables.listen(workbenchDropZone, "dragover", (event) => {
    event.preventDefault();
    workbenchDropZone.classList.add("drag-over");
  });
  appDisposables.listen(workbenchDropZone, "dragleave", () => {
    workbenchDropZone.classList.remove("drag-over");
  });
  appDisposables.listen(workbenchDropZone, "drop", async (event) => {
    event.preventDefault();
    workbenchDropZone.classList.remove("drag-over");
    await handleSessionFiles(Array.from(event.dataTransfer.files));
  });

  appDisposables.listen(sessionDropZone, "click", () => sessionFileInput?.click());
  appDisposables.listen(sessionDropZone, "dragover", (event) => {
    event.preventDefault();
    sessionDropZone.classList.add("drag-over");
  });
  appDisposables.listen(sessionDropZone, "dragleave", () => {
    sessionDropZone.classList.remove("drag-over");
  });
  appDisposables.listen(sessionDropZone, "drop", async (event) => {
    event.preventDefault();
    sessionDropZone.classList.remove("drag-over");
    await handleSessionFiles(Array.from(event.dataTransfer.files));
  });
  appDisposables.listen(sessionFileInput, "change", async () => {
    await handleSessionFiles(Array.from(sessionFileInput.files ?? []));
    sessionFileInput.value = "";
  });
  document.querySelectorAll("[data-control='view-mode'] button").forEach((button) => {
    appDisposables.listen(button, "click", () => {
      setViewMode(button.dataset.viewMode);
    });
  });
  appDisposables.listen(baselineSelect, "change", () => {
    setBaseline(baselineSelect.value);
  });
}

async function handleSessionFiles(files) {
  if (!files.some((file) => /\.(json|zip)$/i.test(file.name))) {
    setSessionMessage("Drop NeuroMouse data.json or ZIP exports", true);
    return;
  }

  setSessionMessage("Loading datasets…");
  const { datasets, errors } = await loadDatasetFiles(files);
  let added = 0;
  for (const dataset of datasets) {
    try {
      addSession(dataset.name, dataset.data);
      added += 1;
    } catch (error) {
      errors.push(error.message);
      break;
    }
  }

  if (added > 0 && errors.length) {
    setSessionMessage(`Added ${added}; ${errors[0]}`, true);
  } else if (added > 0) {
    setSessionMessage(`Added ${added} session${added === 1 ? "" : "s"}`);
  } else {
    setSessionMessage(errors[0] ?? "No datasets found", true);
  }
}

function renderWorkbench() {
  if (!workbenchMetrics || !activeData) return;
  const state = buildWorkbenchState({
    sessions: getSessions(),
    fallbackData: activeData,
    baselineId: getBaselineId(),
    scenarioId: activeScenarioId,
  });

  if (workbenchStatus) {
    workbenchStatus.textContent = state.status;
  }

  workbenchMetrics.innerHTML = "";
  state.metrics.forEach((metric) => {
    workbenchMetrics.append(element("div", { className: "metric-tile" },
      element("span", {}, metric.label),
      element("strong", {}, metric.value),
      element("small", {}, metric.detail),
    ));
  });

  if (workbenchComparisons) {
    workbenchComparisons.innerHTML = "";
    if (!state.comparisons.length) {
      workbenchComparisons.append(element("p", { className: "empty-comparison" },
        `${state.scenario.baselineLabel} -> ${state.scenario.targetLabel}`,
      ));
    } else {
      state.comparisons.slice(0, 3).forEach((row) => {
        workbenchComparisons.append(element("div", { className: "comparison-row" },
          element("span", { className: "comparison-name" }, row.name),
          element("span", {}, `alpha ${formatSignedPercent(row.alphaChange)}`),
          element("span", {}, `centroid ${formatSignedNumber(row.centroidShiftHz, 2)} Hz`),
          element("strong", {}, `${row.separationScore}/100`),
        ));
      });
    }
  }
}

function downloadWorkbenchReport() {
  const report = generateWorkbenchReport({
    sessions: getSessions(),
    fallbackData: activeData,
    baselineId: getBaselineId(),
    scenarioId: activeScenarioId,
    generatedAt: new Date(),
  });
  const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = element("a", {
    href: url,
    download: `neuromouse-analysis-report-${new Date().toISOString().slice(0, 10)}.md`,
  });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function openComparisonSuite() {
  const advancedButton = document.querySelector("#advanced-toggle");
  const advancedViews = document.querySelector("#advanced-views");
  if (advancedButton?.getAttribute("aria-expanded") !== "true") {
    advancedButton?.click();
  } else if (advancedViews) {
    advancedViews.style.display = "grid";
  }
  document.querySelector(".session-sidebar")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function syncSessionState() {
  const primary = getBaselineSession(activeData);
  if (!primary?.data) return;
  configureChannels(primary.data.meta.channels);
  configurePlayback(primary.data.geometry.time.length);
  updateSelectedChannelLabel(getChannel());
}

function updateSelectedChannelLabel(channel) {
  if (selectedChannel) selectedChannel.textContent = `Selected channel: ${channel}`;
}

function renderSessionSidebar() {
  const sessions = getSessions();
  const mode = getViewMode();
  if (sessionCount) sessionCount.textContent = `${sessions.length}/${MAX_SESSIONS}`;

  document.querySelectorAll("[data-control='view-mode'] button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.viewMode === mode);
  });

  if (sessionList) {
    sessionList.innerHTML = "";
    sessions.forEach((session) => {
      const row = element("div", {
        className: `session-item${session.active ? "" : " is-inactive"}`,
        style: `--session-color:${session.color}`,
      });
      const toggle = element("button", {
        type: "button",
        className: "session-toggle",
        title: session.active ? "Hide session" : "Show session",
        "aria-pressed": String(session.active),
      }, element("span", { className: "session-dot" }), element("span", { className: "session-name" }, session.name));
      const remove = element("button", {
        type: "button",
        className: "session-remove",
        "aria-label": `Remove ${session.name}`,
      }, "×");
      toggle.addEventListener("click", () => toggleSession(session.id));
      remove.addEventListener("click", () => removeSession(session.id));
      row.append(toggle, remove);
      sessionList.append(row);
    });
  }

  if (baselineSelect) {
    baselineSelect.innerHTML = "";
    const baselineId = getBaselineId();
    sessions.forEach((session) => {
      baselineSelect.append(element("option", {
        value: session.id,
        selected: session.id === baselineId,
      }, session.name));
    });
    baselineSelect.disabled = sessions.length === 0;
  }

  if (!sessions.length) setSessionMessage("Add sessions to compare");
}

function setSessionMessage(message, isError = false) {
  if (!sessionMessage) return;
  sessionMessage.textContent = message;
  sessionMessage.classList.toggle("is-error", isError);
}

function createTooltip(node) {
  return {
    show(x, y, html) {
      node.replaceChildren(sanitizeTooltipHtml(html));
      node.hidden = false;
      const rect = node.getBoundingClientRect();
      const left = Math.min(window.innerWidth - rect.width - 12, x + 14);
      const top = Math.min(window.innerHeight - rect.height - 12, y + 14);
      node.style.left = `${Math.max(8, left)}px`;
      node.style.top = `${Math.max(8, top)}px`;
    },
    hide() {
      node.hidden = true;
    },
  };
}

function sanitizeTooltipHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const allowedTags = new Set(["BR", "SPAN", "STRONG"]);
  const elements = Array.from(template.content.querySelectorAll("*"));

  elements.forEach((elementNode) => {
    if (!allowedTags.has(elementNode.nodeName)) {
      elementNode.replaceWith(document.createTextNode(elementNode.textContent ?? ""));
      return;
    }

    Array.from(elementNode.attributes).forEach((attribute) => {
      elementNode.removeAttribute(attribute.name);
    });
  });

  return template.content;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch] || ch));
}

function element(name, attrs = {}, ...children) {
  const node = document.createElement(name);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "htmlFor") node.htmlFor = value;
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  });
  children.flat().forEach((child) => {
    if (child == null) return;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}


// ---- Neuro Signal App integration patch ----
// This keeps the original NeuroMouse workbench intact while letting the local
// Neuro Signal backend open it with external data.json files and live replay
// WebSocket URLs.
function applyQueryStartupOptions() {
  try {
    const params = new URLSearchParams(window.location.search);
    const liveWs = params.get("live_ws") || params.get("live");
    const comparisonUrl = params.get("comparison");
    if (liveWs && liveUrl) {
      liveUrl.value = liveWs;
      setTimeout(() => startLive(liveWs), 250);
    }
    if (comparisonUrl) {
      setTimeout(() => loadComparisonManifestFromUrl(comparisonUrl), 300);
    }
  } catch {}
}

async function loadComparisonManifestFromUrl(comparisonUrl) {
  try {
    setSessionMessage("Loading backend comparison manifest…");
    const response = await fetch(comparisonUrl);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const manifest = await response.json();
    const items = Array.isArray(manifest.datasets) ? manifest.datasets : [];
    let added = 0;
    const errors = [];
    for (const item of items) {
      try {
        const url = item.data_json_url || (item.data_json ? `/api/file?path=${encodeURIComponent(item.data_json)}` : null);
        if (!url) throw new Error("missing data_json_url");
        const data = await loadDatasetUrl(url);
        addSession(`${item.group || "dataset"}: ${item.dataset_id || item.recording_dir || added + 1}`, data);
        added += 1;
      } catch (error) {
        errors.push(`${item.dataset_id || item.recording_dir || "dataset"}: ${error.message}`);
      }
    }
    if (added > 0) {
      setSessionMessage(`Loaded ${added} backend comparison dataset${added === 1 ? "" : "s"}${errors.length ? `; ${errors[0]}` : ""}`, Boolean(errors.length));
      renderSessionSidebar();
      renderWorkbench();
      openComparisonSuite();
    } else {
      setSessionMessage(errors[0] || "Comparison manifest did not contain loadable datasets", true);
    }
  } catch (error) {
    setSessionMessage(`Failed to load comparison manifest: ${error.message}`, true);
  }
}

function updateHeaderSummary(data) {
  if (!headerStatus || !data) return;
  const channels = data?.meta?.channels?.length ?? data?.meta?.n_channels ?? 0;
  const frames = data?.geometry?.time?.length ?? data?.centroid?.time_relative?.length ?? 0;
  const params = new URLSearchParams(window.location.search);
  const isBackend = Boolean(data?.meta?.loaded_from_backend || data?.meta?.backend_job_id || globalThis.NEURO_SIGNAL_BACKEND_DATASET?.backend || params.get("backend") === "1");
  const mode = params.get("comparison") ? "backend comparison" : (isBackend ? "backend dataset" : "offline toolbox");
  const datasetName = data?.meta?.dataset_id || data?.meta?.source_file || data?.meta?.backend_job_id || "dataset";
  const sourceUrl = data?.meta?.loaded_dataset_url || "";
  headerStatus.innerHTML = `<span><strong>${channels}</strong> channels</span><span><strong>${frames}</strong> frames</span><span><strong>${mode}</strong></span><span><strong>${escapeHtml(String(datasetName).split(/[\/]/).pop())}</strong></span>${sourceUrl ? `<span title="${escapeHtml(sourceUrl)}"><strong>${isBackend ? "generated" : "demo"}</strong></span>` : ""}`;
}

function datasetReadyLabel(data) {
  if (data?.meta?.loaded_from_backend || data?.meta?.backend_job_id) {
    return `Loaded Neuro backend dataset${data?.meta?.backend_job_id ? ` (${data.meta.backend_job_id})` : ""}`;
  }
  if (data?.meta?.loaded_from_demo) return "Loaded NeuroMouse demo dataset";
  return "Loaded dataset";
}

function ensureBackendDatasetBanner(data) {
  if (!(data?.meta?.loaded_from_backend || data?.meta?.backend_job_id)) return;
  if (!backendDatasetBanner) {
    backendDatasetBanner = document.createElement("div");
    backendDatasetBanner.className = "backend-dataset-banner";
    const target = document.querySelector("main") || dashboard || document.body;
    target.prepend(backendDatasetBanner);
  }
  const meta = data.meta || {};
  backendDatasetBanner.innerHTML = [
    `<strong>Neuro Signal backend dataset loaded</strong>`,
    `Job: ${escapeHtml(meta.backend_job_id || "n/a")}`,
    `Dataset: ${escapeHtml(meta.dataset_id || "dataset")}`,
    `Channels: ${escapeHtml(String(meta.n_channels ?? meta.channels?.length ?? "?"))}`,
    `Source: ${escapeHtml(String(meta.source_signal_path || meta.source_recording_dir || meta.loaded_dataset_url || "backend"))}`,
  ].join(" · ");
}
