const $ = (id) => document.getElementById(id);
let tasks = [];
let catalogue = [];
let currentSource = null;
let activeFilter = 'all';
let generatedResults = new Map();

let isActualSize = false;

function setupActualSizeToggle() {
  const btn = $('toggleActualSizeBtn');
  if (!btn) return;
  btn.onclick = () => {
    isActualSize = !isActualSize;
    $('galleryGrid').classList.toggle('actual-size', isActualSize);
    btn.classList.toggle('active', isActualSize);
    btn.innerHTML = isActualSize ? '⤢ Grid View' : '🔍 Show 1:1 Actual Size';
  };
}

async function init() {
  setupFilterTabs();
  setupModal();
  setupActualSizeToggle();

  try {
    const [tasksRes, catRes] = await Promise.all([
      fetch('/api/tasks'),
      fetch('/api/catalogue')
    ]);

    tasks = await tasksRes.json();
    catalogue = await catRes.json();

    populateTaskSelect();
    renderInitialCards();

    $('generateBtn').onclick = generateAllFormats;
    $('downloadZipBtn').onclick = downloadZip;

    // Automatically trigger initial batch for active sample
    generateAllFormats();
  } catch (err) {
    console.error('Failed to initialize gallery:', err);
  }
}

function populateTaskSelect() {
  const sel = $('taskSelect');
  sel.replaceChildren();
  const labels = ['Warm-up (Square)', 'Core campaign (Square)', 'Advanced campaign (Portrait)'];
  tasks.forEach((t, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${labels[i] || t.task_id} · ${t.source.elements.length} els`;
    sel.append(opt);
  });

  sel.onchange = () => {
    currentSource = tasks[Number(sel.value)].source;
    generatedResults.clear();
    renderInitialCards();
    generateAllFormats();
  };

  currentSource = tasks[0].source;
}

function setupFilterTabs() {
  const tabs = $('filterTabs').querySelectorAll('.filter-btn');
  tabs.forEach(btn => {
    btn.onclick = () => {
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilter = btn.dataset.cat;
      filterCards();
    };
  });
}

function filterCards() {
  const cards = $('galleryGrid').querySelectorAll('.format-card');
  cards.forEach(card => {
    const match = activeFilter === 'all' || card.dataset.category === activeFilter;
    card.style.display = match ? 'flex' : 'none';
  });
}

function renderInitialCards() {
  const grid = $('galleryGrid');
  grid.replaceChildren();

  catalogue.forEach((fmt, index) => {
    const card = document.createElement('div');
    const isTooLarge = fmt.width >= 1920 || fmt.height >= 2000;
    card.className = `format-card ${isTooLarge ? 'too-large-card' : ''}`;
    card.dataset.id = index;
    card.dataset.category = fmt.category;

    card.innerHTML = `
      <div class="card-header">
        <div class="card-title-group">
          <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
            <h3>${fmt.name}</h3>
            ${isTooLarge ? '<span class="too-large-badge" title="This format exceeds desktop bounds and scales to fit in 1:1 mode">Too large to render 1:1</span>' : ''}
          </div>
          <span class="card-dim">${fmt.width} × ${fmt.height} px</span>
        </div>
        <span class="card-badge">${fmt.category}</span>
      </div>
      <div class="card-preview-stage" id="stage-${index}">
        <span style="font-size:11px; color:#a296a8;">Waiting to render…</span>
      </div>
      <div class="card-footer">
        <div class="card-metrics" id="metrics-${index}">
          <span class="metric-chip" style="background:#f4f2f6; color:#95879e;">Pending</span>
        </div>
        <div class="card-actions" id="actions-${index}"></div>
      </div>
    `;

    grid.append(card);
  });

  filterCards();
}

async function generateAllFormats() {
  if (!currentSource) return;

  $('generateBtn').disabled = true;
  $('downloadZipBtn').disabled = true;
  $('progressWrap').style.display = 'block';

  let completed = 0;
  const total = catalogue.length;

  for (let i = 0; i < total; i++) {
    const fmt = catalogue[i];
    const target = { width: fmt.width, height: fmt.height };

    try {
      const res = await fetch('/api/resize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: currentSource,
          target_canvas: target,
          mode: 'auto'
        })
      });

      if (res.ok) {
        const data = await res.json();
        generatedResults.set(i, data);
        updateCardWithResult(i, fmt, data);
      }
    } catch (err) {
      console.error(`Format ${fmt.name} failed:`, err);
    }

    completed++;
    $('progressBar').style.width = `${(completed / total) * 100}%`;
  }

  $('generateBtn').disabled = false;
  $('downloadZipBtn').disabled = false;
  setTimeout(() => {
    $('progressWrap').style.display = 'none';
    $('progressBar').style.width = '0%';
  }, 1000);
}

function updateCardWithResult(index, fmt, data) {
  const stage = $(`stage-${index}`);
  const metrics = $(`metrics-${index}`);
  const actions = $(`actions-${index}`);

  stage.replaceChildren();
  const img = document.createElement('img');
  img.src = data.output_png;
  img.alt = `${fmt.name} preview`;
  img.className = 'card-preview-img';
  img.onclick = () => openModal(data, fmt);
  stage.append(img);

  metrics.replaceChildren();
  const scoreChip = document.createElement('span');
  const score = data.quality?.score ?? 100;
  scoreChip.className = `metric-chip ${score < 90 ? 'warn' : ''}`;
  scoreChip.textContent = `${score}/100`;
  metrics.append(scoreChip);

  if (data.visual_metrics?.balance_score) {
    const balChip = document.createElement('span');
    balChip.className = 'metric-chip';
    balChip.style.borderColor = 'rgba(76, 175, 80, 0.4)';
    balChip.style.color = '#2e7d32';
    balChip.textContent = `Balance: ${data.visual_metrics.balance_score}`;
    metrics.append(balChip);
  }

  if (data.visual_metrics?.contrast) {
    const contrastChip = document.createElement('span');
    contrastChip.className = 'metric-chip';
    contrastChip.style.borderColor = 'rgba(103, 72, 163, 0.3)';
    contrastChip.style.color = '#6748a3';
    contrastChip.textContent = data.visual_metrics.contrast;
    metrics.append(contrastChip);
  }

  if (data.visual_metrics?.ocr_recall) {
    const ocrChip = document.createElement('span');
    ocrChip.className = 'metric-chip';
    ocrChip.textContent = `OCR: ${data.visual_metrics.ocr_recall}`;
    metrics.append(ocrChip);
  }

  actions.replaceChildren();
  const pngBtn = document.createElement('button');
  pngBtn.className = 'file-button';
  pngBtn.style.cssText = 'padding:6px 12px; font-size:12px; width:100%; border-radius:6px;';
  pngBtn.textContent = 'Download PNG ↓';
  pngBtn.onclick = () => downloadBlob(data.output_png, `${fmt.name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_${fmt.width}x${fmt.height}.png`);

  actions.append(pngBtn);
}

async function downloadZip() {
  if (!currentSource) return;
  $('downloadZipBtn').disabled = true;
  $('downloadZipBtn').textContent = 'Zipping 20 Formats…';

  try {
    const targets = catalogue.map(f => ({ width: f.width, height: f.height }));
    const res = await fetch('/api/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: currentSource, targets })
    });

    if (res.ok) {
      const blob = await res.blob();
      downloadBlob(URL.createObjectURL(blob), 'chameleon_20_formats_campaign.zip');
    }
  } catch (err) {
    alert('ZIP generation failed: ' + err.message);
  } finally {
    $('downloadZipBtn').disabled = false;
    $('downloadZipBtn').textContent = 'Download Campaign ZIP ↓';
  }
}

function downloadBlob(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
}

let modalZoom = 1.0;
const MODAL_ZOOM_LEVELS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0];

function applyModalZoom(z) {
  modalZoom = z;
  const img = $('modalImg');
  if (z === 'fit') {
    img.style.transform = 'scale(1)';
    img.style.maxWidth = '88vw';
    img.style.maxHeight = '76vh';
    $('modalZoomLevel').textContent = 'Fit';
  } else {
    img.style.maxWidth = 'none';
    img.style.maxHeight = 'none';
    img.style.transform = `scale(${z})`;
    $('modalZoomLevel').textContent = `${Math.round(z * 100)}%`;
  }
}

function setupModal() {
  $('modalClose').onclick = () => $('imageModal').hidden = true;
  $('imageModal').onclick = (e) => {
    if (e.target === $('imageModal')) $('imageModal').hidden = true;
  };
  $('modalZoomIn').onclick = () => {
    if (modalZoom === 'fit') {
      applyModalZoom(1.0);
    } else {
      const next = MODAL_ZOOM_LEVELS.find(l => l > modalZoom + 0.05) || 3.0;
      applyModalZoom(next);
    }
  };
  $('modalZoomOut').onclick = () => {
    if (modalZoom === 'fit') {
      applyModalZoom(0.75);
    } else {
      const prev = [...MODAL_ZOOM_LEVELS].reverse().find(l => l < modalZoom - 0.05);
      if (prev) applyModalZoom(prev);
      else applyModalZoom('fit');
    }
  };
  $('modalZoomFit').onclick = () => applyModalZoom('fit');
}

let currentModalData = null;
let currentModalFmt = null;

function openModal(data, fmt) {
  currentModalData = data;
  currentModalFmt = fmt;
  const caption = `${fmt.name} (${fmt.width} × ${fmt.height} px)`;
  $('modalImg').src = data.output_png;
  $('modalCaption').textContent = caption;
  applyModalZoom(1.0);

  const mAi = $('modalAiMetrics');
  mAi.replaceChildren();
  if (data.visual_metrics) {
    const chips = [
      `Fit Score: ${data.quality?.score ?? 100}/100`,
      `Visual Balance: ${data.visual_metrics.balance_score || 'Not measured'}`,
      `Image variation: ${data.visual_metrics.contrast || 'Not measured'}`,
      `OCR word recall: ${data.visual_metrics.ocr_recall || 'Not measured'}`,
      `Min Font: ${data.visual_metrics.min_font_px || 'N/A'}`
    ];
    chips.forEach(c => {
      const chip = document.createElement('span');
      chip.className = 'modal-ai-chip';
      chip.textContent = c;
      mAi.append(chip);
    });
  }

  const verdict = $('modalAiVerdict');
  verdict.hidden = true;
  verdict.textContent = '';
  $('modalAiStatus').textContent = 'Ready to scan';

  const scanBtn = $('modalScanVisionBtn');
  scanBtn.disabled = false;
  scanBtn.textContent = 'Scan Format with Moondream VLM 👁️';
  scanBtn.onclick = async () => {
    scanBtn.disabled = true;
    scanBtn.textContent = 'Moondream Ingesting Image… 👁️';
    $('modalAiStatus').textContent = 'Running Vision Model…';
    try {
      const expected_texts = currentSource?.elements?.filter(e => e.type === 'text')?.map(e => e.content) || [];
      const res = await fetch('/api/ai_readability', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          output_png: data.output_png,
          width: fmt.width,
          height: fmt.height,
          expected_texts
        })
      });
      const resData = await res.json();
      verdict.hidden = false;
      if (resData.moondream_vlm && resData.moondream_vlm.available) {
        $('modalAiStatus').textContent = `Assessed in ${resData.moondream_vlm.latency_s}s`;
        verdict.textContent = `Moondream VLM Assessment: ${resData.moondream_vlm.assessment}`;
      } else if (resData.tesseract_ocr?.available) {
        $('modalAiStatus').textContent = 'OCR measured';
        const recall = resData.tesseract_ocr.text_recall_pct;
        verdict.textContent = `Pixel OCR Text Extracted: "${resData.tesseract_ocr.sample_snippet || 'No text detected'}" (${recall == null ? 'recall not measured' : `${recall}% word recall`})`;
      } else {
        $('modalAiStatus').textContent = 'Unavailable';
        verdict.textContent = 'Vision model and OCR are unavailable. Pixel readability has not been verified.';
      }
    } catch (err) {
      verdict.hidden = false;
      verdict.textContent = `Vision scan failed: ${err.message}`;
    } finally {
      scanBtn.disabled = false;
      scanBtn.textContent = 'Scan Format with Moondream VLM 👁️';
    }
  };

  $('imageModal').hidden = false;
}

init();
