let selectedFiles = [];
let currentJobId = null;
let currentOutputPath = null;
let liveJobId = null;

const $ = (id) => document.getElementById(id);

function log(message, obj) {
  const box = $('logBox');
  const line = `[${new Date().toLocaleTimeString()}] ${message}` + (obj ? ` ${JSON.stringify(obj)}` : '');
  box.textContent = line + '\n' + box.textContent;
}

function setProgress(percent, statusText, stepText) {
  $('progressBar').style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  if (statusText) $('jobStatus').textContent = statusText;
  if (stepText) $('currentStep').textContent = `Current step: ${stepText}`;
}

function showTab(tabId) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('button.tab').forEach(b => b.classList.remove('active'));
  $(tabId).classList.add('active');
  document.querySelector(`button[data-tab="${tabId}"]`).classList.add('active');
}

document.querySelectorAll('button.tab').forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));

async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    $('serverStatus').textContent = `backend online — workspace: ${data.workspace}`;
    $('serverStatus').className = 'status ok';
  } catch (e) {
    $('serverStatus').textContent = 'backend offline';
    $('serverStatus').className = 'status failed';
  }
}

function refreshFileList() {
  const ul = $('fileList');
  ul.innerHTML = '';
  selectedFiles.forEach(file => {
    const li = document.createElement('li');
    li.textContent = `${file.name} — ${(file.size / (1024 * 1024)).toFixed(2)} MB`;
    ul.appendChild(li);
  });
}

function collectOptions() {
  const val = (id) => $(id).value.trim();
  const numOrNull = (id) => val(id) ? Number(val(id)) : null;
  return {
    output_dir: val('outputDir') || null,
    sampling_rate: numOrNull('samplingRate'),
    signal_path: val('signalPath') || null,
    orientation: val('orientation'),
    include_aux: $('includeAux').checked,
    original_units: val('originalUnits') || null,
    target_units: val('targetUnits') || null,
    scale_factor: numOrNull('scaleFactor'),
    offset: numOrNull('offset'),
    export_format: val('exportFormat') || 'npy',
    save_signal_csv: $('saveSignalCsv').checked,
    csv_max_mb: 250,
    preprocess: $('preprocess').checked,
    demean: $('demean').checked,
    detrend: $('detrend').checked,
    normalization: val('normalization') || null,
    make_windows: $('makeWindows').checked,
    window_seconds: numOrNull('windowSeconds') || 2,
    step_seconds: numOrNull('stepSeconds') || 1,
  };
}

function connectJobEvents(jobId) {
  currentJobId = jobId;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/jobs/${jobId}/events`);
  showTab('resultsTab');
  setProgress(0, 'Job started', 'Connecting to heartbeat...');
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === 'heartbeat') return;
    log(data.message || data.step || data.type, data);
    if (data.percent !== undefined) setProgress(data.percent, data.status || 'running', data.step || data.message);
    if (data.status === 'complete') {
      setProgress(100, 'Conversion complete', data.step || 'Complete');
      $('completedStep').textContent = `Completed step: ${data.step || 'Complete'}`;
      currentOutputPath = data.output_dir || (data.result && data.result.output_dir) || currentOutputPath;
      $('outputPath').textContent = currentOutputPath || '';
      $('resultJson').textContent = JSON.stringify(data.result || data, null, 2);
      ws.close();
    }
    if (data.status === 'failed') {
      setProgress(100, 'Job failed', data.step || 'Failed');
      $('jobStatus').className = 'status failed big-status';
      $('resultJson').textContent = JSON.stringify(data, null, 2);
      ws.close();
    }
    if (data.status === 'running') {
      $('jobStatus').className = 'status big-status';
    }
  };
  ws.onerror = () => log('WebSocket error');
}

async function startConvert(fullAnalyze = false) {
  if (!selectedFiles.length) { alert('Choose or drop at least one file.'); return; }
  const form = new FormData();
  selectedFiles.forEach(f => form.append('files', f));
  const options = collectOptions();
  options.make_windows = fullAnalyze ? true : options.make_windows;
  form.append('options_json', JSON.stringify(options));
  if (options.output_dir) form.append('output_dir', options.output_dir);
  setProgress(1, 'Uploading files', 'Uploading files to local backend...');
  showTab('resultsTab');
  const res = await fetch('/api/jobs/convert-upload', { method: 'POST', body: form });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function inspectUploads() {
  if (!selectedFiles.length) { alert('Choose or drop at least one file.'); return; }
  const form = new FormData();
  selectedFiles.forEach(f => form.append('files', f));
  const res = await fetch('/api/jobs/inspect-upload', { method: 'POST', body: form });
  const data = await res.json();
  showTab('resultsTab');
  $('resultJson').textContent = JSON.stringify(data, null, 2);
  log('Inspection complete', data);
}

async function startCompare() {
  const groupA = $('groupA').value.split('\n').map(x => x.trim()).filter(Boolean);
  const groupB = $('groupB').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (!groupA.length || !groupB.length) { alert('Provide at least one converted folder in Group A and Group B.'); return; }
  const payload = {
    group_a: groupA,
    group_b: groupB,
    output_dir: $('compareOutput').value.trim() || null,
    options: { comparison_name: $('comparisonName').value.trim() || 'comparison' }
  };
  const res = await fetch('/api/jobs/compare', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function startLive() {
  const payload = {
    source: $('liveSignal').value.trim(),
    channels_csv: $('liveChannels').value.trim() || null,
    metadata_json: $('liveMetadata').value.trim() || null,
    fs: $('liveFs').value.trim() || null,
    channel_profile: $('liveProfile').value || 'auto'
  };
  if (!payload.source) { alert('Provide a signal.npy path.'); return; }
  const res = await fetch('/api/jobs/live', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  liveJobId = data.job_id;
  connectJobEvents(data.job_id);
}

async function stopLive() {
  if (!liveJobId) { alert('No live job id recorded.'); return; }
  await fetch(`/api/jobs/${liveJobId}/stop-live`, { method: 'POST' });
}

$('chooseFilesBtn').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', (e) => { selectedFiles = Array.from(e.target.files); refreshFileList(); });
$('clearFilesBtn').addEventListener('click', () => { selectedFiles = []; $('fileInput').value = ''; refreshFileList(); });
$('inspectBtn').addEventListener('click', inspectUploads);
$('convertBtn').addEventListener('click', () => startConvert(false));
$('fullAnalyzeBtn').addEventListener('click', () => startConvert(true));
$('compareBtn').addEventListener('click', startCompare);
$('startLiveBtn').addEventListener('click', startLive);
$('stopLiveBtn').addEventListener('click', stopLive);
$('openOutputBtn').addEventListener('click', async () => { if (currentOutputPath) await fetch(`/api/open-output?path=${encodeURIComponent(currentOutputPath)}`); });
$('copyOutputBtn').addEventListener('click', () => { if (currentOutputPath) navigator.clipboard.writeText(currentOutputPath); });

const dropZone = $('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  selectedFiles = Array.from(e.dataTransfer.files || []);
  refreshFileList();
});

checkHealth();

// ---- v0.8 SpeedMouse integration ----
function openUrl(url) { window.open(url, '_blank'); }

async function analyzeUploadsInSpeedMouse() {
  if (!selectedFiles.length) { alert('Choose or drop at least one file first.'); return; }
  const form = new FormData();
  selectedFiles.forEach(f => form.append('files', f));
  const options = collectOptions();
  options.speedmouse_max_analysis_samples = 240000;
  options.speedmouse_max_windows = 600;
  form.append('options_json', JSON.stringify(options));
  if (options.output_dir) form.append('output_dir', options.output_dir);
  setProgress(1, 'SpeedMouse analysis started', 'Uploading and converting files...');
  showTab('resultsTab');
  const res = await fetch('/api/jobs/analyze-speedmouse-upload', { method: 'POST', body: form });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function buildSpeedMouseFromConverted() {
  const dirs = $('speedmouseConvertedDirs').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (!dirs.length) { alert('Provide at least one converted recording folder.'); return; }
  const payload = {
    recording_dirs: dirs,
    output_dir: $('speedmouseOutput').value.trim() || null,
    options: { sampling_rate: $('samplingRate').value.trim() || null }
  };
  const res = await fetch('/api/jobs/speedmouse-from-converted', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function compareGroupsInSpeedMouse() {
  const groupA = $('groupA').value.split('\n').map(x => x.trim()).filter(Boolean);
  const groupB = $('groupB').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (!groupA.length || !groupB.length) { alert('Provide at least one converted folder in Group A and Group B.'); return; }
  const payload = {
    group_a: groupA,
    group_b: groupB,
    output_dir: $('compareOutput').value.trim() || null,
    options: { comparison_name: $('comparisonName').value.trim() || 'speedmouse_comparison', sampling_rate: $('samplingRate').value.trim() || null }
  };
  const res = await fetch('/api/jobs/compare-speedmouse', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

function openSpeedMouseLiveReplay() {
  const source = $('speedmouseLiveSignal').value.trim() || $('liveSignal').value.trim();
  if (!source) { alert('Provide a signal.npy path.'); return; }
  const params = new URLSearchParams();
  params.set('source', source);
  const channels = $('speedmouseLiveChannels').value.trim() || $('liveChannels').value.trim();
  const metadata = $('speedmouseLiveMetadata').value.trim() || $('liveMetadata').value.trim();
  const fs = $('speedmouseLiveFs').value.trim() || $('liveFs').value.trim();
  const speed = $('speedmouseLiveSpeed').value.trim() || '1';
  if (channels) params.set('channels_csv', channels);
  if (metadata) params.set('metadata_json', metadata);
  if (fs) params.set('fs', fs);
  params.set('speed', speed);
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${wsProto}://${location.host}/ws/speedmouse/live?${params.toString()}`;
  openUrl(`/speedmouse/?live_ws=${encodeURIComponent(wsUrl)}`);
}

// Extend connectJobEvents to auto-open SpeedMouse links when present.
const _oldConnectJobEvents = connectJobEvents;
connectJobEvents = function(jobId) {
  currentJobId = jobId;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/jobs/${jobId}/events`);
  showTab('resultsTab');
  setProgress(0, 'Job started', 'Connecting to heartbeat...');
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === 'heartbeat') return;
    log(data.message || data.step || data.type, data);
    if (data.percent !== undefined) setProgress(data.percent, data.status || 'running', data.step || data.message);
    if (data.status === 'complete') {
      setProgress(100, data.step || 'Job complete', data.step || 'Complete');
      $('completedStep').textContent = `Completed step: ${data.step || 'Complete'}`;
      currentOutputPath = data.output_dir || (data.result && data.result.output_dir) || currentOutputPath;
      $('outputPath').textContent = currentOutputPath || '';
      $('resultJson').textContent = JSON.stringify(data.result || data, null, 2);
      const r = data.result || {};
      if (r.primary_speedmouse_url) openUrl(r.primary_speedmouse_url);
      if (r.speedmouse_comparison_url) openUrl(r.speedmouse_comparison_url);
      ws.close();
    }
    if (data.status === 'failed') {
      setProgress(100, 'Job failed', data.step || 'Failed');
      $('jobStatus').className = 'status failed big-status';
      $('resultJson').textContent = JSON.stringify(data, null, 2);
      ws.close();
    }
    if (data.status === 'running') $('jobStatus').className = 'status big-status';
  };
  ws.onerror = () => log('WebSocket error');
};

if ($('openSpeedMouseBtn')) $('openSpeedMouseBtn').addEventListener('click', () => openUrl('/speedmouse/'));
if ($('speedmouseAnalyzeBtn')) $('speedmouseAnalyzeBtn').addEventListener('click', analyzeUploadsInSpeedMouse);
if ($('analyzeSpeedMouseBtn2')) $('analyzeSpeedMouseBtn2').addEventListener('click', analyzeUploadsInSpeedMouse);
if ($('speedmouseFromConvertedBtn')) $('speedmouseFromConvertedBtn').addEventListener('click', buildSpeedMouseFromConverted);
if ($('speedmouseCompareBtn')) $('speedmouseCompareBtn').addEventListener('click', compareGroupsInSpeedMouse);
if ($('speedmouseCompareBtn2')) $('speedmouseCompareBtn2').addEventListener('click', compareGroupsInSpeedMouse);
if ($('openSpeedMouseLiveBtn')) $('openSpeedMouseLiveBtn').addEventListener('click', openSpeedMouseLiveReplay);

// ---- v0.9 complete SpeedMouse workbench + quick visualization layer ----
let latestSpeedMouseUrl = null;
let latestSpeedMouseComparisonUrl = null;
let latestSpeedMouseData = null;
let latestComparisonManifest = null;

function speedmouseOptions() {
  const maxSamples = Number(($('speedmouseMaxSamples')?.value || '').trim()) || 240000;
  const maxWindows = Number(($('speedmouseMaxWindows')?.value || '').trim()) || 600;
  return {
    speedmouse_max_analysis_samples: maxSamples,
    speedmouse_max_windows: maxWindows,
  };
}

async function analyzeUploadsInSpeedMouse() {
  if (!selectedFiles.length) { alert('Choose or drop at least one file first.'); return; }
  const form = new FormData();
  selectedFiles.forEach(f => form.append('files', f));
  const options = { ...collectOptions(), ...speedmouseOptions() };
  form.append('options_json', JSON.stringify(options));
  if (options.output_dir) form.append('output_dir', options.output_dir);
  setProgress(1, 'SpeedMouse analysis started', 'Uploading and converting files...');
  showTab('resultsTab');
  const res = await fetch('/api/jobs/analyze-speedmouse-upload', { method: 'POST', body: form });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function buildSpeedMouseFromConverted() {
  const dirs = $('speedmouseConvertedDirs').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (!dirs.length) { alert('Provide at least one converted recording folder.'); return; }
  const payload = {
    recording_dirs: dirs,
    output_dir: $('speedmouseOutput').value.trim() || null,
    options: { sampling_rate: $('samplingRate').value.trim() || null, ...speedmouseOptions() }
  };
  const res = await fetch('/api/jobs/speedmouse-from-converted', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

async function compareGroupsInSpeedMouse() {
  const groupA = $('groupA').value.split('\n').map(x => x.trim()).filter(Boolean);
  const groupB = $('groupB').value.split('\n').map(x => x.trim()).filter(Boolean);
  if (!groupA.length || !groupB.length) { alert('Provide at least one converted folder in Group A and Group B.'); return; }
  const payload = {
    group_a: groupA,
    group_b: groupB,
    output_dir: $('compareOutput').value.trim() || null,
    options: { comparison_name: $('comparisonName').value.trim() || 'speedmouse_comparison', sampling_rate: $('samplingRate').value.trim() || null, ...speedmouseOptions() }
  };
  const res = await fetch('/api/jobs/compare-speedmouse', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  connectJobEvents(data.job_id);
}

function datasetFetchUrlFromSpeedMouseUrl(speedmouseUrl) {
  if (!speedmouseUrl) return null;
  try {
    const u = new URL(speedmouseUrl, location.origin);
    return u.searchParams.get('dataset') || u.searchParams.get('data_json');
  } catch { return null; }
}

function comparisonFetchUrlFromSpeedMouseUrl(speedmouseUrl) {
  if (!speedmouseUrl) return null;
  try {
    const u = new URL(speedmouseUrl, location.origin);
    return u.searchParams.get('comparison');
  } catch { return null; }
}

async function fetchJsonMaybe(url) {
  if (!url) return null;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url}: HTTP ${res.status}`);
  return await res.json();
}

function meanRows(matrix) {
  if (!Array.isArray(matrix) || !matrix.length) return [];
  const n = Math.max(...matrix.map(row => Array.isArray(row) ? row.length : 0));
  const out = new Array(n).fill(0);
  const count = new Array(n).fill(0);
  matrix.forEach(row => {
    if (!Array.isArray(row)) return;
    row.forEach((v, i) => {
      const x = Number(v);
      if (Number.isFinite(x)) { out[i] += x; count[i] += 1; }
    });
  });
  return out.map((v, i) => count[i] ? v / count[i] : NaN);
}

function drawLineChart(canvas, xValues, yValues, title, yLabel) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#010505'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(0,229,229,0.35)'; ctx.strokeRect(0.5, 0.5, w-1, h-1);
  ctx.fillStyle = '#dfffff'; ctx.font = '14px monospace'; ctx.fillText(title, 16, 24);
  if (!yValues || yValues.length < 2) { ctx.fillText('No preview data available', 16, 52); return; }
  const vals = yValues.map(Number).filter(Number.isFinite);
  if (!vals.length) { ctx.fillText('No finite values', 16, 52); return; }
  const minY = Math.min(...vals), maxY = Math.max(...vals);
  const pad = Math.max((maxY - minY) * 0.08, 1e-9);
  const yMin = minY - pad, yMax = maxY + pad;
  const left = 54, right = 14, top = 38, bottom = 32;
  const plotW = w - left - right, plotH = h - top - bottom;
  ctx.strokeStyle = 'rgba(127,255,255,0.18)';
  for (let i=0; i<5; i++) {
    const y = top + (plotH * i / 4);
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(w-right, y); ctx.stroke();
  }
  ctx.fillStyle = '#7bdada'; ctx.font = '11px monospace';
  ctx.fillText(`${yMax.toFixed(3)} ${yLabel || ''}`, 8, top + 4);
  ctx.fillText(`${yMin.toFixed(3)} ${yLabel || ''}`, 8, top + plotH);
  ctx.strokeStyle = '#00ff99'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  yValues.forEach((v, i) => {
    const x = left + plotW * (i / Math.max(1, yValues.length - 1));
    const y = top + plotH * (1 - ((Number(v) - yMin) / (yMax - yMin || 1)));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function renderSpeedMouseDatasetPreview(speedmouseUrl) {
  const fetchUrl = datasetFetchUrlFromSpeedMouseUrl(speedmouseUrl);
  if (!fetchUrl) return;
  try {
    const data = await fetchJsonMaybe(fetchUrl);
    latestSpeedMouseData = data;
    const nChannels = data?.meta?.channels?.length || data?.meta?.n_channels || 0;
    const nFrames = data?.geometry?.time?.length || data?.centroid?.time_relative?.length || 0;
    const text = `Loaded SpeedMouse dataset: ${data?.meta?.dataset_id || 'dataset'} · ${nChannels} channels · ${nFrames} frames`;
    if ($('previewMeta')) $('previewMeta').textContent = text;
    if ($('resultsPreviewMeta')) $('resultsPreviewMeta').textContent = text;
    drawLineChart($('centroidPreview'), data?.geometry?.time || data?.centroid?.time_relative || [], meanRows(data?.centroid?.values || data?.geometry?.centroid || []), 'Mean spectral centroid over time', 'Hz');
    const avgPsd = meanRows(data?.welch_psd?.psd || []);
    drawLineChart($('psdPreview'), data?.welch_psd?.frequencies || [], avgPsd.map(v => Math.log10(Math.max(Number(v), 1e-18))), 'Mean log10 Welch PSD across channels', 'log');
  } catch (err) {
    if ($('previewMeta')) $('previewMeta').textContent = `Preview failed: ${err.message}`;
  }
}

async function renderSpeedMouseComparisonPreview(speedmouseUrl) {
  const fetchUrl = comparisonFetchUrlFromSpeedMouseUrl(speedmouseUrl);
  if (!fetchUrl) return;
  try {
    const manifest = await fetchJsonMaybe(fetchUrl);
    latestComparisonManifest = manifest;
    const rows = manifest.datasets || [];
    const container = $('comparisonPreview');
    if (!container) return;
    let html = `<strong>${manifest.comparison_name || 'SpeedMouse comparison'}</strong><br><small>${manifest.compatibility_note || ''}</small>`;
    html += '<table><thead><tr><th>Group</th><th>Dataset</th><th>Channels</th><th>Open</th></tr></thead><tbody>';
    rows.forEach(row => {
      const url = row.data_json_url ? `/speedmouse/?dataset=${encodeURIComponent(row.data_json_url)}` : '';
      html += `<tr><td>${row.group || ''}</td><td>${row.dataset_id || ''}</td><td>variable</td><td>${url ? `<a href="${url}" target="_blank">open</a>` : ''}</td></tr>`;
    });
    html += '</tbody></table>';
    const report = manifest?.comparison_result?.report_html;
    if (report) html += `<p><a href="/api/file?path=${encodeURIComponent(report)}" target="_blank">Open backend comparison report</a></p>`;
    container.innerHTML = html;
    if ($('resultsPreviewMeta')) $('resultsPreviewMeta').textContent = `Loaded comparison: ${rows.length} SpeedMouse datasets`;
  } catch (err) {
    if ($('comparisonPreview')) $('comparisonPreview').textContent = `Comparison preview failed: ${err.message}`;
  }
}

async function handleCompletedSpeedMouseResult(result) {
  const r = result || {};
  if (r.primary_speedmouse_url) {
    latestSpeedMouseUrl = r.primary_speedmouse_url;
    await renderSpeedMouseDatasetPreview(latestSpeedMouseUrl);
  }
  if (r.speedmouse_comparison_url) {
    latestSpeedMouseComparisonUrl = r.speedmouse_comparison_url;
    await renderSpeedMouseComparisonPreview(latestSpeedMouseComparisonUrl);
  }
}

// Final override: job stream + SpeedMouse auto-open + quick preview render.
connectJobEvents = function(jobId) {
  currentJobId = jobId;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/jobs/${jobId}/events`);
  showTab('resultsTab');
  setProgress(0, 'Job started', 'Connecting to heartbeat...');
  ws.onmessage = async (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === 'heartbeat') return;
    log(data.message || data.step || data.type, data);
    if (data.percent !== undefined) setProgress(data.percent, data.status || 'running', data.step || data.message);
    if (data.status === 'complete') {
      setProgress(100, data.step || 'Job complete', 'Complete');
      $('completedStep').textContent = `Completed step: ${data.step || 'Complete'}`;
      currentOutputPath = data.output_dir || (data.result && data.result.output_dir) || currentOutputPath;
      $('outputPath').textContent = currentOutputPath || '';
      $('resultJson').textContent = JSON.stringify(data.result || data, null, 2);
      const r = data.result || {};
      await handleCompletedSpeedMouseResult(r);
      if (r.primary_speedmouse_url) openUrl(r.primary_speedmouse_url);
      if (r.speedmouse_comparison_url) openUrl(r.speedmouse_comparison_url);
      ws.close();
    }
    if (data.status === 'failed') {
      setProgress(100, 'Job failed', data.step || 'Failed');
      $('jobStatus').className = 'status failed big-status';
      $('resultJson').textContent = JSON.stringify(data, null, 2);
      ws.close();
    }
    if (data.status === 'running') $('jobStatus').className = 'status big-status';
  };
  ws.onerror = () => log('WebSocket error');
};

function openLatestSpeedMouse() { if (latestSpeedMouseUrl) openUrl(latestSpeedMouseUrl); else openUrl('/speedmouse/'); }
function openLatestSpeedMouseComparison() { if (latestSpeedMouseComparisonUrl) openUrl(latestSpeedMouseComparisonUrl); else alert('No SpeedMouse comparison has been generated yet.'); }

if ($('openLatestSpeedMouseBtn')) $('openLatestSpeedMouseBtn').addEventListener('click', openLatestSpeedMouse);
if ($('openLatestSpeedMouseComparisonBtn')) $('openLatestSpeedMouseComparisonBtn').addEventListener('click', openLatestSpeedMouseComparison);
if ($('resultsOpenLatestSpeedMouseBtn')) $('resultsOpenLatestSpeedMouseBtn').addEventListener('click', openLatestSpeedMouse);
if ($('resultsOpenLatestSpeedMouseComparisonBtn')) $('resultsOpenLatestSpeedMouseComparisonBtn').addEventListener('click', openLatestSpeedMouseComparison);
