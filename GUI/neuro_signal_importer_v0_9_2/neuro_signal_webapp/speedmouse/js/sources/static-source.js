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

  const requestedDatasetUrl = datasetUrlFromQuery();
  const datasetUrl = requestedDatasetUrl ?? new URL("../../data/data.json", import.meta.url);
  loadedFromBackendDataset = Boolean(requestedDatasetUrl && isBackendDatasetUrl(String(requestedDatasetUrl)));
  loadedFromDemoDataset = !requestedDatasetUrl;
  dataCache = await loadDataFromUrl(datasetUrl, {
    fromBackend: loadedFromBackendDataset,
    fromDemo: loadedFromDemoDataset,
  });
  loadedDatasetUrl = String(datasetUrl);
  return dataCache;
}

export async function loadDataFromUrl(datasetUrl, opts = {}) {
  const response = await fetch(datasetUrl);
  if (!response.ok) {
    throw new Error(`Failed to load data.json: HTTP ${response.status}`);
  }

  const data = await response.json();
  validateData(data);
  try {
    data.meta = data.meta || {};
    data.meta.loaded_dataset_url = String(datasetUrl);
    data.meta.loaded_from_demo = Boolean(opts.fromDemo);
    data.meta.loaded_from_backend = Boolean(opts.fromBackend);
    const params = new URLSearchParams(window.location.search);
    if (opts.fromBackend && params.get("backend_job")) data.meta.backend_job_id = params.get("backend_job");
    if (opts.fromBackend && !data.meta.backend_job_id) data.meta.backend_job_id = inferJobIdFromDatasetUrl(String(datasetUrl));
  } catch {}
  return data;
}

function isBackendDatasetUrl(url) {
  return url.includes("/api/jobs/") || url.includes("backend=1") || url.includes("/api/file");
}

function inferJobIdFromDatasetUrl(url) {
  const m = url.match(/\/api\/jobs\/([^/]+)\/speedmouse\/data\.json/);
  return m ? m[1] : null;
}

function datasetUrlFromQuery() {
  try {
    const params = new URLSearchParams(window.location.search);
    const dataset = params.get("dataset") || params.get("data_json");
    if (!dataset) return null;
    return dataset;
  } catch {
    return null;
  }
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
