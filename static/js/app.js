import { apiGet } from './api.js';
import {
  buildSeedCompatibleJson,
  flyToSettlement,
  initMap,
  panToCoords,
  renderMarkers,
  toggleMapLayer,
} from './map.js';
import {
  closeModal,
  openContactModal,
  openFaqModal,
  openMahallaDetail,
  openReportModal,
  openStationDetail,
  openStationModal,
  openStatsModal,
  openTimelineModal,
} from './modals.js';
import { STATE, escapeHtml, formatLocalTime, showToast } from './state.js';

function renderUI() {
  // 1. Погода
  if (STATE.weather && STATE.weather.temp !== null) {
    document.getElementById('weather-icon-desktop').textContent = STATE.weather.icon;
    document.getElementById('weather-temp-desktop').textContent = `${STATE.weather.temp}°`;
    document.getElementById('weather-box-mobile').innerHTML =
      `<span>${STATE.weather.icon}</span><span>${STATE.weather.temp}°</span>`;
  }
  if (STATE.updatedAt) {
    document.getElementById('updated-text').textContent = `Yangilandi: ${formatLocalTime(STATE.updatedAt)}`;
  }

  // 2. Переключатель населённых пунктов
  const sRow = document.getElementById('settlement-row');
  const settlements = STATE.config?.settlements || [];
  if (settlements.length > 1) {
    const items = [{ id: 'all', name_uz: 'Barchasi' }, ...settlements];
    sRow.innerHTML = items
      .map(s => `
        <button data-settlement="${escapeHtml(s.id)}" type="button"
          class="px-2.5 py-1 rounded-full text-[11.5px] border whitespace-nowrap transition ${
            STATE.settlement === s.id
              ? 'border-slate-800 bg-slate-800 text-white font-semibold'
              : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100 font-medium'
          }">${escapeHtml(s.name_uz)}</button>
      `)
      .join('');
    sRow.classList.remove('hidden');
  } else {
    sRow.classList.add('hidden');
  }

  // 3. Кнопка Zapravkalar
  const stBtn = document.getElementById('btn-stations-tab');
  stBtn.className = STATE.mode === 'stations'
    ? 'flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[12px] font-semibold whitespace-nowrap transition border-[#2ca7f2] bg-[#2ca7f2] text-white shadow-sm'
    : 'flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[12px] font-medium whitespace-nowrap transition border-sky-300/90 bg-sky-50/50 text-sky-700 hover:bg-sky-50';

  // 4. Вкладки Chiroq / Suv / Gaz
  const activeTabClass = {
    power: 'border-[#38a3eb] bg-[#eff8ff] text-[#0b6fb5] font-semibold',
    water: 'border-[#3b82f6] bg-[#eff6ff] text-[#1d4ed8] font-semibold',
    gas:   'border-[#f97316] bg-[#fff7ed] text-[#c2410c] font-semibold',
  };
  for (const u of ['power', 'water', 'gas']) {
    const btn = document.getElementById(`tab-${u}`);
    const active = STATE.mode === 'utilities' && STATE.utility === u;
    btn.className =
      'py-1.5 px-2 rounded-xl text-[12.5px] border transition flex items-center justify-center gap-1 whitespace-nowrap ' +
      (active ? activeTabClass[u] : 'border-slate-200/90 bg-white text-slate-600 hover:bg-slate-50 font-medium');
  }

  // 5. Строка фильтров
  const filterRow = document.getElementById('filter-row');
  if (STATE.mode === 'utilities') {
    filterRow.innerHTML = `
      <button data-filter="showCrowd" type="button"
        class="px-3 py-1 rounded-xl text-[12px] border transition flex items-center gap-1.5 whitespace-nowrap ${
          STATE.showCrowd
            ? 'border-[#38a3eb] bg-[#eff8ff] text-[#0b6fb5] font-semibold'
            : 'border-slate-200 bg-white text-slate-400 font-medium'
        }">🤖 Botdan</button>
      <button data-filter="showPlanned" type="button"
        class="px-3 py-1 rounded-xl text-[12px] border transition flex items-center gap-1.5 whitespace-nowrap ${
          STATE.showPlanned
            ? 'border-[#8b5cf6] bg-[#f5f3ff] text-[#6d28d9] font-semibold'
            : 'border-slate-200 bg-white text-slate-400 font-medium'
        }">🟣 Rejali ishlar</button>
    `;
  } else {
    const opts = [
      ['all',    'Barchasi',  'border-[#38a3eb] bg-[#eff8ff] text-[#0b6fb5] font-semibold'],
      ['metan',  '🔵 Metan',  'border-blue-400 bg-blue-50 text-blue-700 font-semibold'],
      ['propan', '🟡 Propan', 'border-amber-400 bg-amber-50 text-amber-700 font-semibold'],
      ['benzin', '⛽ Benzin', 'border-emerald-400 bg-emerald-50 text-emerald-700 font-semibold'],
    ];
    filterRow.innerHTML = opts
      .map(([k, lbl, activeCls]) => `
        <button data-fuel="${k}" type="button"
          class="px-2.5 py-1 rounded-xl text-[12px] border transition whitespace-nowrap ${
            STATE.fuelFilter === k ? activeCls : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50 font-medium'
          }">${lbl}</button>
      `)
      .join('');
  }

  // 6. Нижняя кнопка и легенда
  const cta = document.getElementById('cta-btn');
  const legend = document.getElementById('legend-box');
  if (STATE.mode === 'stations') {
    cta.textContent = '⛽ Zapravka holatini yangilash';
    cta.className = 'pointer-events-auto w-full sm:w-auto sm:min-w-[270px] py-3 px-6 rounded-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] text-white font-semibold text-[14px] shadow-[0_6px_22px_rgba(16,185,129,0.4)] transition flex items-center justify-center gap-2 whitespace-nowrap';
    legend.innerHTML = `
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-emerald-500 shrink-0"></span> Bo‘sh (&lt;15 daq)</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-yellow-500 shrink-0"></span> O‘rtacha (~30 daq)</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-orange-500 shrink-0"></span> Uzun navbat (1 soat+)</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-rose-500 shrink-0"></span> Yopiq / yoqilg‘i yo‘q</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-slate-300 shrink-0"></span> Ma’lumot yo‘q</div>
    `;
  } else {
    const ctaMap = {
      power: ['⚡ Uzilish haqida xabar berish', 'bg-[#2ca7f2] hover:bg-[#1e96e0] shadow-[0_6px_22px_rgba(44,167,242,0.45)]'],
      water: ['💧 Suv uzilishi haqida xabar berish', 'bg-[#2563eb] hover:bg-[#1d4ed8] shadow-[0_6px_22px_rgba(37,99,235,0.4)]'],
      gas:   ['🔥 Gaz muammosi haqida xabar berish', 'bg-[#f97316] hover:bg-[#ea580c] shadow-[0_6px_22px_rgba(249,115,22,0.42)]'],
    };
    const [ctaText, ctaCls] = ctaMap[STATE.utility];
    cta.textContent = ctaText;
    cta.className = `pointer-events-auto w-full sm:w-auto sm:min-w-[270px] py-3 px-6 rounded-full ${ctaCls} active:scale-[0.98] text-white font-semibold text-[14px] transition flex items-center justify-center gap-2 whitespace-nowrap`;
    legend.innerHTML = `
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-[#ef4444] shrink-0"></span> Avariya tasdiqlangan</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-[#f59e0b] shrink-0"></span> Tasdiqlash kutilmoqda</div>
      <div class="flex items-center gap-2.5"><span class="w-3 h-3 rounded-full bg-[#8b5cf6] shrink-0"></span> Rejali o‘chirish</div>
      <div class="flex items-center gap-2.5"><span class="w-2.5 h-2.5 rounded-full bg-[#10b981] shrink-0 ml-[1px]"></span> Hammasi joyida</div>
    `;
  }

  // 7. Маркеры + сводка
  const { summaryHtml } = renderMarkers({
    onMahallaClick: (mh, list) => openMahallaDetail(mh, list, refreshState),
    onStationClick: (st, info) => openStationDetail(st, info, refreshState),
  });
  document.getElementById('summary-text').innerHTML = summaryHtml;
}

async function refreshState() {
  try {
    const data = await apiGet('/api/state');
    STATE.incidents = data.incidents || [];
    STATE.stationsState = data.stations || {};
    STATE.weather = data.weather;
    STATE.updatedAt = data.updated_at;
    renderUI();
  } catch (e) {
    console.error(e);
  }
}

// --- Редактор координат (/?edit=1) ---
function populateEditSelect() {
  const sel = document.getElementById('edit-select');
  if (!STATE.config) return;
  let html = '<optgroup label="Mahallalar">';
  STATE.config.mahallas.forEach((m, idx) => {
    html += `<option value="m:${idx}">📍 ${escapeHtml(m.name_uz)} (${escapeHtml(m.settlement)})</option>`;
  });
  html += '</optgroup><optgroup label="Zapravkalar">';
  STATE.config.stations.forEach((s, idx) => {
    html += `<option value="s:${idx}">⛽ ${escapeHtml(s.name_uz)} (${escapeHtml(s.fuel)})</option>`;
  });
  html += '</optgroup>';
  sel.innerHTML = html;
}

function focusEditTarget() {
  const [kind, idx] = document.getElementById('edit-select').value.split(':');
  STATE.mode = kind === 'm' ? 'utilities' : 'stations';
  renderUI();
  const item = kind === 'm' ? STATE.config.mahallas[idx] : STATE.config.stations[idx];
  if (item?.center) panToCoords(item.center);
}

function toggleEditMode() {
  STATE.editMode = !STATE.editMode;
  document.getElementById('edit-bar').classList.toggle('hidden', !STATE.editMode);
  if (STATE.editMode) focusEditTarget();
  renderUI();
}

function bindEvents() {
  document.getElementById('btn-stations-tab').addEventListener('click', () => {
    STATE.mode = STATE.mode === 'stations' ? 'utilities' : 'stations';
    renderUI();
  });

  document.getElementById('settlement-row').addEventListener('click', e => {
    const btn = e.target.closest('[data-settlement]');
    if (!btn) return;
    STATE.settlement = btn.dataset.settlement;
    flyToSettlement(STATE.settlement);
    renderUI();
  });

  document.querySelectorAll('[data-utility]').forEach(btn => {
    btn.addEventListener('click', () => {
      STATE.mode = 'utilities';
      STATE.utility = btn.dataset.utility;
      renderUI();
    });
  });

  document.getElementById('filter-row').addEventListener('click', e => {
    const filterBtn = e.target.closest('[data-filter]');
    if (filterBtn) {
      const key = filterBtn.dataset.filter;
      STATE[key] = !STATE[key];
      if (!STATE.showCrowd && !STATE.showPlanned) STATE[key] = true;
      renderUI();
      return;
    }
    const fuelBtn = e.target.closest('[data-fuel]');
    if (fuelBtn) {
      STATE.fuelFilter = fuelBtn.dataset.fuel;
      renderUI();
    }
  });

  document.getElementById('btn-layer').addEventListener('click', toggleMapLayer);
  document.getElementById('btn-stats').addEventListener('click', () => openStatsModal());
  document.getElementById('btn-timeline').addEventListener('click', () => openTimelineModal());
  document.getElementById('btn-faq').addEventListener('click', openFaqModal);
  document.getElementById('btn-contact').addEventListener('click', openContactModal);

  document.getElementById('cta-btn').addEventListener('click', () => {
    if (STATE.mode === 'stations') openStationModal(refreshState);
    else openReportModal(refreshState);
  });

  document.getElementById('modal-backdrop').addEventListener('click', closeModal);
  document.getElementById('modal-card').addEventListener('click', e => e.stopPropagation());
  document.getElementById('modal-close').addEventListener('click', closeModal);

  // Редактор координат
  document.getElementById('btn-edit-coords').addEventListener('click', toggleEditMode);
  document.getElementById('btn-edit-close').addEventListener('click', toggleEditMode);
  document.getElementById('edit-select').addEventListener('change', focusEditTarget);
  document.getElementById('btn-edit-prev').addEventListener('click', () => {
    const sel = document.getElementById('edit-select');
    sel.selectedIndex = (sel.selectedIndex - 1 + sel.options.length) % sel.options.length;
    focusEditTarget();
  });
  document.getElementById('btn-edit-next').addEventListener('click', () => {
    const sel = document.getElementById('edit-select');
    sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
    focusEditTarget();
  });
  document.getElementById('btn-edit-copy').addEventListener('click', () => {
    navigator.clipboard.writeText(buildSeedCompatibleJson());
    showToast('JSON nusxalandi!');
  });
  document.getElementById('btn-edit-download').addEventListener('click', () => {
    const blob = new Blob([buildSeedCompatibleJson()], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'mahallas.json';
    a.click();
  });
}

async function initApp() {
  const tgWebApp = window.Telegram?.WebApp;
  if (tgWebApp && tgWebApp.initData) {
    tgWebApp.ready();
    tgWebApp.expand();
    document.getElementById('btn-contact')?.classList.add('hidden');
  }

  initMap(latlng => {
    if (!STATE.config) return;
    const [kind, idx] = document.getElementById('edit-select').value.split(':');
    const item = kind === 'm' ? STATE.config.mahallas[idx] : STATE.config.stations[idx];
    if (!item) return;
    item.center = [Number(latlng.lat.toFixed(6)), Number(latlng.lng.toFixed(6))];
    renderUI();
    showToast(`${item.name_uz} → ${item.center[0]}, ${item.center[1]}`);
  });

  bindEvents();

  if (new URLSearchParams(window.location.search).get('edit') === '1') {
    document.getElementById('btn-edit-coords').classList.remove('hidden');
  }

  try {
    STATE.config = await apiGet('/api/config');
    populateEditSelect();
    flyToSettlement(STATE.settlement);
    await refreshState();
    setInterval(refreshState, 20000);
  } catch {
    document.getElementById('summary-text').textContent = 'Server bilan aloqa yo‘q';
  }
}

initApp();