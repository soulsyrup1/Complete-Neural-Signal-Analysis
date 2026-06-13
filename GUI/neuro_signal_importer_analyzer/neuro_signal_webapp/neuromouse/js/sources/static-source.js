let dataCache = null;
let loadedDatasetUrl = null;
let loadedFromBackendDataset = false;
let loadedFromDemoDataset = false;

export function createStaticSource() {
  return {
    meta() {
      return dataCache?.meta ?? null;
    },
    async start(onFrame, onStatus) {
      onStatus?.("static");
      onFrame?.(await loadStaticData());
    },
    stop() {},
  };
}

export async function loadStaticData() {
  if (dataCache) return dataCache;

  const requestedDatasetUrl = await Promise.resolve(datasetUrlFromQueryOrBackendState());
  const forceBackend = shouldForceBackendDataset();
  if (forceBackend && !requestedDatasetUrl) {
    throw new Error("NeuroMouse was opened in backend-dataset mode, but no backend data.json URL was supplied. Use the generated NeuroMouse link in the launcher Results tab.");
  }

  const datasetUrl = requestedDatasetUrl ?? new URL("../../data/data.json", import.meta.url);
  loadedFromBackendDataset = Boolean(
    globalThis.NEURO_SIGNAL_BACKEND_DATASET?.backend ||
    forceBackend ||
    (requestedDatasetUrl && isBackendDatasetUrl(String(requestedDatasetUrl)))
  );
  loadedFromDemoDataset = !requestedDatasetUrl && !loadedFromBackendDataset;
  dataCache = await loadDataFromUrl(datasetUrl, {
    fromBackend: loadedFromBackendDataset,
    fromDemo: loadedFromDemoDataset,
  });
  loadedDatasetUrl = String(datasetUrl);
  return dataCache;
}

export async function loadDataFromUrl(datasetUrl, opts = {}) {
  const response = await fetch(datasetUrl, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Failed to load data.json: HTTP ${response.status} from ${datasetUrl}`);
  }

  const data = await response.json();
  validateData(data);
  try {
    data.meta = data.meta || {};
    data.meta.loaded_dataset_url = String(datasetUrl);
    data.meta.loaded_from_demo = Boolean(opts.fromDemo);
    data.meta.loaded_from_backend = Boolean(opts.fromBackend);
    const params = new URLSearchParams(window.location.search);
    const injectedJob = globalThis.NEURO_SIGNAL_BACKEND_DATASET?.jobId;
    if (opts.fromBackend && injectedJob) data.meta.backend_job_id = injectedJob;
    if (opts.fromBackend && params.get("backend_job")) data.meta.backend_job_id = params.get("backend_job");
    if (opts.fromBackend && !data.meta.backend_job_id) data.meta.backend_job_id = inferJobIdFromDatasetUrl(String(datasetUrl));
    if (opts.fromBackend && !data.meta.source) data.meta.source = "Neuro Signal backend generated data.json";
  } catch {}
  return data;
}

function shouldForceBackendDataset() {
  try {
    const params = new URLSearchParams(window.location.search);
    return Boolean(
      globalThis.NEURO_SIGNAL_BACKEND_DATASET?.forceBackend ||
      globalThis.NEURO_SIGNAL_BACKEND_DATASET?.disableDemoFallback ||
      params.get("force_backend") === "1" ||
      params.get("backend") === "1"
    );
  } catch {
    return Boolean(globalThis.NEURO_SIGNAL_BACKEND_DATASET?.forceBackend);
  }
}

function isBackendDatasetUrl(url) {
  return url.includes("/api/jobs/") ||
    url.includes("/api/neuromouse/") ||
    url.includes("backend=1") ||
    url.includes("/api/file");
}

function inferJobIdFromDatasetUrl(url) {
  const m = url.match(/\/api\/jobs\/([^/]+)\/neuromouse\/data\.json/);
  return m ? m[1] : null;
}

function datasetUrlFromQueryOrBackendState() {
  try {
    const params = new URLSearchParams(window.location.search);
    // Explicit demo path remains /neuromouse/?demo=1
    if (params.get("demo") === "1") return null;

    const injected = globalThis.NEURO_SIGNAL_BACKEND_DATASET?.datasetUrl;
    if (injected) return injected;

    const dataset = params.get("dataset") || params.get("data_json");
    if (dataset) return dataset;

    // Plain /neuromouse/ should ask the backend for the newest generated
    // dataset first. Older versions trusted a URL saved in localStorage, which
    // could point at a deleted/restarted job and cause a startup 404.
    const latest = awaitLatestBackendDatasetUrl();
    if (latest) return latest;

    // Only use the stored URL as a final fallback. This keeps old workflows
    // working but prevents stale browser state from outranking fresh backend
    // outputs.
    const stored = window.localStorage?.getItem("NEURO_SIGNAL_LAST_BACKEND_DATASET_URL");
    if (stored && isBackendDatasetUrl(stored)) return stored;
    return null;
  } catch {
    return null;
  }
}

async function latestBackendDatasetUrl() {
  try {
    const response = await fetch("/api/neuromouse/latest?t=" + Date.now(), { cache: "no-store" });
    if (!response.ok) return null;
    const info = await response.json();
    const datasetUrl = info?.dataset_url;
    if (datasetUrl && isBackendDatasetUrl(datasetUrl)) {
      try {
        window.localStorage?.setItem("NEURO_SIGNAL_LAST_BACKEND_DATASET_URL", datasetUrl);
        if (info.neuromouse_url) window.localStorage?.setItem("NEURO_SIGNAL_LAST_NEUROMOUSE_URL", info.neuromouse_url);
      } catch {}
      return datasetUrl;
    }
  } catch {}
  return null;
}

function awaitLatestBackendDatasetUrl() {
  // datasetUrlFromQueryOrBackendState is used inside async loadStaticData(), so
  // return a Promise and let loadStaticData await it via Promise.resolve below.
  return latestBackendDatasetUrl();
}

export function validateData(data) {
  const channels = data?.meta?.channels;
  if (!Array.isArray(channels) || channels.length < 1) {
    throw new Error("data.json must contain one or more meta.channels entries");
  }
  const nChannels = Number(data?.meta?.n_channels ?? channels.length);
  if (!Number.isFinite(nChannels) || nChannels !== channels.length) {
    throw new Error("data.json meta.n_channels must match meta.channels.length");
  }
  if (!Array.isArray(data?.welch_psd?.frequencies) || !Array.isArray(data?.welch_psd?.psd)) {
    throw new Error("data.json is missing welch_psd arrays");
  }
  if (data.welch_psd.psd.length !== channels.length) {
    throw new Error("data.json welch_psd.psd must be channel-major and match meta.channels.length");
  }
  if (!Array.isArray(data?.centroid?.time_relative) || !Array.isArray(data?.centroid?.values)) {
    throw new Error("data.json is missing centroid arrays");
  }
  if (data.centroid.values.length !== channels.length) {
    throw new Error("data.json centroid.values must be channel-major and match meta.channels.length");
  }
  if (!Array.isArray(data?.geometry?.time)) {
    throw new Error("data.json is missing geometry.time");
  }
  if (!Array.isArray(data?.channel_summary) || data.channel_summary.length !== channels.length) {
    throw new Error("data.json channel_summary must match meta.channels.length");
  }
}


export function getLoadedDatasetUrl() { return loadedDatasetUrl; }
export function isLoadedFromBackendDataset() { return loadedFromBackendDataset; }
export function isLoadedFromDemoDataset() { return loadedFromDemoDataset; }
