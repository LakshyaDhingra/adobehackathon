import { connectExpress } from './host.js';

const $ = (id) => document.getElementById(id);
const PRESETS = [
  ['Post', 1080, 1080], ['Story', 1080, 1920], ['Portrait', 1080, 1350],
  ['Thumbnail', 1280, 720], ['Banner', 1600, 400], ['Leaderboard', 728, 90]
];
const state = { tasks: [], custom: null, source: null, assets: {}, result: null, version: 0, busy: false, compare: false, host: null };
let service = location.origin;
if (!service || service === 'null' || location.protocol === 'file:') {
  service = 'http://localhost:5050';
}
if ($('serviceUrl')) $('serviceUrl').value = service;
const message = (text, success = false) => {
  $('notice').textContent = text;
  $('notice').classList.toggle('success', success);
  $('notice').hidden = !text;
};
function dimensions() {
  const width = Number($('targetW').value), height = Number($('targetH').value);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1) throw Error('Enter positive whole numbers for width and height.');
  if (width > 8192 || height > 8192 || width * height > 20000000) throw Error('Choose a canvas up to 8192 px per side and 20 megapixels.');
  return { width, height };
}
function updatePresets() {
  for (const button of $('presets').children) {
    button.classList.toggle('active', Number(button.dataset.width) === Number($('targetW').value) && Number(button.dataset.height) === Number($('targetH').value));
  }
}
function setTarget(w, h) { $('targetW').value = w; $('targetH').value = h; updatePresets(); invalidate(); }
function invalidate() {
  state.version++;
  state.result = null;
  if ($('qualityPanel')) $('qualityPanel').hidden = true;
  for (const id of ['pngBtn', 'jsonBtn', 'expressBtn']) { const el = $(id); if (el) el.disabled = true; }
  if ($('batchBtn')) $('batchBtn').disabled = !state.source || state.busy;
  if ($('previewStatus')) $('previewStatus').textContent = 'Settings changed · reflow to update';
}
function setSource(source) {
  if (!source?.canvas || !Array.isArray(source.elements)) throw Error('Choose a Chameleon design JSON with canvas and elements.');
  state.source = structuredClone(source);
  if ($('sourceInfo')) $('sourceInfo').textContent = `${source.canvas.width} × ${source.canvas.height} · ${source.elements.length} elements · ${Object.keys(state.assets).length} added images`;
  if ($('runBtn')) $('runBtn').disabled = false;
  invalidate();
}
function busy(value, label = 'Reflowing…') {
  state.busy = value; document.body.classList.toggle('busy', value);
  if ($('runBtn')) { $('runBtn').disabled = value || !state.source; $('runBtn').textContent = value ? label : 'Reflow design ↗'; }
  for (const id of ['pngBtn', 'jsonBtn']) { const el = $(id); if (el) el.disabled = value || !state.result; }
  if ($('batchBtn')) $('batchBtn').disabled = value || !state.source;
  if ($('expressBtn')) $('expressBtn').disabled = value || !state.result || !state.host;
}
function payload(target) { return { source: state.source, target_canvas: target, mode: $('modeSelect').value, assets: state.assets }; }
async function request(path, body) {
  const response = await fetch(service + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) { let error; try { error = (await response.json()).error; } catch { } throw Error(error || `Service returned ${response.status}`); }
  return response;
}
function setView() {
  $('sourceFrame').hidden = !state.compare || !state.result;
  $('resultTab').setAttribute('aria-selected', String(!state.compare));
  $('compareTab').setAttribute('aria-selected', String(state.compare));
  $('previewStage').classList.toggle('compare', state.compare);
}
const explain = (issue) => {
  const names = issue.ids.join(', ');
  const descriptions = { text_too_small: 'Small text; review at actual size', collision: 'Elements overlap', text_overflow_width: 'Text extends past its box', text_overflow_height: 'Text extends below its box', text_ink_off_canvas: 'Text extends past the canvas', label_off_shape: 'Label extends outside its background', edge_hugging: 'Close to the canvas edge', hierarchy_inverted: 'Text hierarchy changed' };
  return `${names}: ${descriptions[issue.code] || issue.code.replaceAll('_', ' ')}`;
};
function showResult(result) {
  state.result = result;
  const { width, height } = result.output.canvas;
  $('sourceImage').src = result.source_png; $('outputImage').src = result.output_png;
  $('sourceCanvasLabel').textContent = `${state.source.canvas.width} × ${state.source.canvas.height}`;
  $('outputCanvasLabel').textContent = `${width} × ${height}`;
  $('canvasLabel').textContent = `${width} × ${height}`;
  $('outputFrame').hidden = false; $('emptyState').hidden = true;
  $('previewStatus').textContent = `${result.output.elements.length} elements preserved · content unchanged`;
  $('runtime').textContent = `${result.elapsed_ms} ms reflow`;
  $('qualityPanel').hidden = false;
  $('qualityTitle').textContent = result.quality.issues.length ? 'A fit worth reviewing.' : 'Made to fit.';
  $('qualityBadge').textContent = result.quality.issues.length ? `${result.quality.issues.length} things to review` : 'Fit checks passed';
  $('qualityBadge').classList.toggle('warn', Boolean(result.quality.issues.length));
  $('checks').replaceChildren();
  const checkItems = [
    ['Content kept', `${result.output.elements.length} / ${state.source.elements.length}`],
    ['Unwanted overlaps', String(result.quality.collisions.length)],
    ['Local fit score', `${result.quality.score} / 100`]
  ];
    if (result.visual_metrics) {
      if (result.visual_metrics.balance_score) checkItems.push(['Visual balance', result.visual_metrics.balance_score]);
      if (result.visual_metrics.min_font_px) checkItems.push(['Min text size', result.visual_metrics.min_font_px]);
      $('aiOcrRecall').textContent = `${result.visual_metrics.ocr_recall || 'Not measured'} (${result.visual_metrics.legibility || 'Not verified'})`;
      $('aiVisualBalance').textContent = result.visual_metrics.balance_score || 'Not measured';
      $('aiWcagContrast').textContent = result.visual_metrics.contrast || 'Not measured';
    }
  for (const [label, value] of checkItems) {
    const item = document.createElement('div'); item.className = 'check'; item.textContent = label;
    const strong = document.createElement('strong'); strong.textContent = value; item.append(strong); $('checks').append(item);
  }
  $('issues').replaceChildren();
  if (result.quality.issues.length || result.warnings.length) {
    const list = document.createElement('ul');
    const notes = [...result.quality.issues.map(explain), ...result.warnings];
    for (const note of notes) { const li = document.createElement('li'); li.textContent = note; list.append(li); }
    $('issues').append(list);
  } else {
    const p = document.createElement('p'); p.className = 'ok'; p.textContent = 'No overflow detected. Images stay in proportion. Text and brand styles are preserved.'; $('issues').append(p);
  }
  setView();
  applyZoom(currentZoom);
}
async function run() {
  if (state.busy || !state.source) return;
  let target; try { target = dimensions(); } catch (error) { message(error.message); return; }
  const version = ++state.version;
  busy(true); message('');
  try {
    const result = await (await request('/api/resize', payload(target))).json();
    if (version === state.version) showResult(result);
  } catch (error) { if (version === state.version) message(`Couldn’t reflow the design. ${error.message}`); }
  finally { busy(false); }
}
function download(blob, name) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
function stem() { const c = state.result.output.canvas; return `chameleon-${c.width}x${c.height}`; }
if ($('pngBtn')) $('pngBtn').onclick = async () => { if (state.result) download(await (await fetch(state.result.output_png)).blob(), stem() + '.png'); };
if ($('jsonBtn')) $('jsonBtn').onclick = () => { if (state.result) download(new Blob([JSON.stringify(state.result.output, null, 2)], { type: 'application/json' }), stem() + '.json'); };
$('batchBtn').onclick = async () => {
  if (state.busy || !state.source) return;
  busy(true, 'Building campaign…'); message('');
  try {
    const body = { ...payload({ width: 1080, height: 1080 }), mode: 'auto', targets: [{ width: 1080, height: 1080 }, { width: 1080, height: 1920 }, { width: 1280, height: 720 }, { width: 1600, height: 400 }] };
    const response = await request('/api/batch', body); download(await response.blob(), 'chameleon-campaign.zip'); message('Your four-size campaign is ready. Each layout includes PNG, JSON and a shared quality report.', true);
  } catch (error) { message(error.message); } finally { busy(false); }
};
$('expressBtn').onclick = async () => {
  if (!state.host || !state.result || state.busy) return;
  busy(true, 'Adding to Express…');
  try {
    const blob = await (await fetch(state.result.output_png)).blob();
    await state.host.insertPage(blob, state.result.output.canvas);
    message('Added a new Express page at the target size. Your original page is unchanged.', true);
  } catch (error) { message(`Couldn’t add the page. ${error.message}`); } finally { busy(false); }
};
$('runBtn').onclick = run;
$('importBtn').onclick = () => $('jsonFile').click();
$('assetsBtn').onclick = () => $('assetFiles').click();
$('taskSelect').onchange = () => {
  const task = $('taskSelect').value === 'custom' ? state.custom : state.tasks[Number($('taskSelect').value)];
  if (!task) return;
  state.assets = { ...(task.assets || {}) }; setSource(task.source);
  if (task.target_canvas) setTarget(task.target_canvas.width, task.target_canvas.height);
  run();
};
$('modeSelect').onchange = invalidate;
for (const id of ['targetW', 'targetH']) $(id).oninput = () => { updatePresets(); invalidate(); };
let currentZoom = 'fit';
const ZOOM_LEVELS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0];

function applyZoom(zoom) {
  currentZoom = zoom;
  const stage = $('previewStage');
  const outImg = $('outputImage');
  const srcImg = $('sourceImage');
  const label = $('zoomLevel');

  if (zoom === 'fit') {
    stage.classList.remove('custom-zoom', 'actual');
    outImg.style.width = '';
    srcImg.style.width = '';
    if (label) label.textContent = 'Fit';
    return;
  }

  stage.classList.add('custom-zoom');
  stage.classList.remove('actual');

  const targetW = state.result?.output?.canvas?.width || outImg.naturalWidth || 1080;
  const srcW = state.source?.canvas?.width || srcImg.naturalWidth || 1080;

  outImg.style.width = `${Math.round(targetW * zoom)}px`;
  srcImg.style.width = `${Math.round(srcW * zoom)}px`;
  if (label) label.textContent = `${Math.round(zoom * 100)}%`;
}

function zoomIn() {
  if (!state.result) return;
  if (currentZoom === 'fit') {
    applyZoom(1.0);
  } else {
    const next = ZOOM_LEVELS.find(z => z > currentZoom + 0.05) || 4.0;
    applyZoom(next);
  }
}

function zoomOut() {
  if (!state.result) return;
  if (currentZoom === 'fit') {
    applyZoom(0.75);
  } else {
    const prev = [...ZOOM_LEVELS].reverse().find(z => z < currentZoom - 0.05);
    if (prev) applyZoom(prev);
    else applyZoom('fit');
  }
}

$('resultTab').onclick = () => { state.compare = false; setView(); };
$('compareTab').onclick = () => { state.compare = true; setView(); };
$('zoomInBtn').onclick = zoomIn;
$('zoomOutBtn').onclick = zoomOut;
$('zoomBtn').onclick = () => applyZoom('fit');
$('qualityToggle').onclick = () => {
  const body = $('qualityBody');
  const isExpanded = $('qualityToggle').getAttribute('aria-expanded') === 'true';
  $('qualityToggle').setAttribute('aria-expanded', String(!isExpanded));
  body.hidden = isExpanded;
};
$('qualityToggle').onkeydown = (event) => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('qualityToggle').click(); }
};
$('jsonFile').onchange = async (event) => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 2000000) throw Error('Design JSON must be smaller than 2 MB.');
    const task = JSON.parse(await file.text()); setSource(task.source || task);
    state.custom = { source: structuredClone(state.source), target_canvas: task.target_canvas, assets: { ...state.assets } };
    let option = $('taskSelect').querySelector('[data-custom]');
    if (!option) { option = document.createElement('option'); option.dataset.custom = 'true'; $('taskSelect').append(option); }
    option.value = 'custom'; option.textContent = file.name; $('taskSelect').value = 'custom';
    if (task.target_canvas) setTarget(task.target_canvas.width, task.target_canvas.height);
    message('Design imported. Add any referenced images, then reflow.', true);
  } catch (error) { message(error.message); } event.target.value = '';
};
$('assetFiles').onchange = async (event) => {
  try {
    let total = 0; const uploads = {};
    for (const file of event.target.files) {
      total += file.size; if (total > 16000000) throw Error('Keep uploaded images under 16 MB total.');
      const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file); });
      uploads[file.name] = data.split(',')[1];
    }
    state.assets = { ...state.assets, ...uploads };
    if ($('taskSelect').value === 'custom' && state.custom) state.custom.assets = { ...state.assets };
    if (state.source) setSource(state.source); message('Images added. Reflow to preview the updated design.', true);
  } catch (error) { message(error.message); } event.target.value = '';
};
for (const [name, width, height] of PRESETS) {
  const button = document.createElement('button'); button.className = 'preset'; button.dataset.width = width; button.dataset.height = height; button.textContent = name;
  const dimensions = document.createElement('span'); dimensions.textContent = `${width} × ${height}`; button.append(dimensions);
  button.onclick = () => { setTarget(width, height); run(); }; $('presets').append(button);
}
async function loadTasks() {
  try {
    let response;
    try {
      response = await fetch(service + '/api/tasks');
      if (!response.ok && service !== 'http://localhost:5050') throw Error(`HTTP ${response.status}`);
    } catch {
      // Fallback if accessed via Express add-on host or file protocol
      service = 'http://localhost:5050';
      if ($('serviceUrl')) $('serviceUrl').value = service;
      response = await fetch(service + '/api/tasks');
    }
    if (!response || !response.ok) throw Error(`Service returned ${response ? response.status : 'offline'}`);
    state.tasks = await response.json();
    if ($('taskSelect')) {
      $('taskSelect').replaceChildren();
      for (const [i, task] of state.tasks.entries()) {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = `${['Warm-up', 'Core campaign', 'Advanced campaign'][i] || task.task_id} · ${task.source.elements.length} elements`;
        $('taskSelect').append(option);
      }
      $('taskSelect').disabled = false;
      $('taskSelect').value = String(Math.min(1, state.tasks.length - 1));
    }
    const task = state.tasks[Number($('taskSelect').value)];
    if (task) {
      setSource(task.source);
      if (task.target_canvas) setTarget(task.target_canvas.width, task.target_canvas.height);
      await run();
    }
  } catch (error) {
    message(`Start the local studio and check the service URL. ${error.message}`);
  }
}
if ($('connectBtn')) $('connectBtn').onclick = () => { try { const url = new URL($('serviceUrl').value); if (!['http:', 'https:'].includes(url.protocol)) throw Error('Use an HTTP or HTTPS service URL.'); service = url.origin; loadTasks(); } catch (error) { message(error.message); } };
document.addEventListener('keydown', (event) => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); run(); } });
connectExpress().then(host => { if (host) { state.host = host; $('expressBtn').hidden = false; $('hostNote').hidden = false; $('expressBtn').disabled = !state.result; } }).catch(error => message(`Express connection unavailable: ${error.message}`));

const scanBtn = $('scanVisionBtn');
if (scanBtn) {
  scanBtn.onclick = async () => {
    if (!state.result || state.busy) return;
    scanBtn.disabled = true;
    scanBtn.textContent = 'Moondream VLM Ingesting… 👁️';
    $('aiVisionVerdictBox').hidden = true;
    try {
      const expected_texts = state.source.elements.filter(e => e.type === 'text').map(e => e.content);
      const res = await request('/api/ai_readability', {
        output_png: state.result.output_png,
        width: state.result.output.canvas.width,
        height: state.result.output.canvas.height,
        expected_texts
      });
      const data = await res.json();
      if (data.moondream_vlm && data.moondream_vlm.available) {
        $('aiVisionVerdictBox').hidden = false;
        $('aiVisionLatency').textContent = `${data.moondream_vlm.latency_s}s`;
        $('aiVisionAssessmentText').textContent = data.moondream_vlm.assessment;
      } else if (data.tesseract_ocr?.available) {
        $('aiVisionVerdictBox').hidden = false;
        $('aiVisionLatency').textContent = 'OCR Engine';
        $('aiVisionAssessmentText').textContent = `Pixel OCR Text Extracted: "${data.tesseract_ocr.sample_snippet || 'No text detected'}"`;
      } else {
        $('aiVisionVerdictBox').hidden = false;
        $('aiVisionLatency').textContent = 'Unavailable';
        $('aiVisionAssessmentText').textContent = 'Vision model and OCR are unavailable. Pixel readability has not been verified.';
      }
    } catch (err) {
      $('aiVisionVerdictBox').hidden = false;
      $('aiVisionAssessmentText').textContent = `Vision scan failed: ${err.message}`;
    } finally {
      scanBtn.disabled = false;
      scanBtn.textContent = 'Scan with Moondream VLM 👁️';
    }
  };
}

loadTasks();

