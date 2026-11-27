export const BOT_USERNAME = 'xonobodbugun_bot';

export const UTILITY_NAMES = {
  power: '⚡ Chiroq',
  water: '💧 Suv',
  gas: '🔥 Gaz',
};

export const FUEL_META = {
  metan:  { label: '🔵 Metan',  short: 'Metan',  badge: 'M' },
  propan: { label: '🟡 Propan', short: 'Propan', badge: 'P' },
  benzin: { label: '⛽ Benzin', short: 'Benzin', badge: 'B' },
};

export const STATION_STATUS_LABELS = {
  open:       '🟢 Bo‘sh (<15 daq)',
  queue:      '🟡 O‘rtacha (~30 daq)',
  long_queue: '🟠 Uzun navbat (1 soat+)',
  closed:     '🔴 Yopiq / yoqilg‘i yo‘q',
};

export function formatTimeAgo(isoStr) {
  if (!isoStr) return '';
  const diffMin = Math.max(0, Math.floor((Date.now() - new Date(isoStr).getTime()) / 60000));
  if (diffMin < 1) return 'Hozirgina';
  if (diffMin < 60) return `${diffMin} daqiqa oldin`;
  const hours = Math.floor(diffMin / 60);
  const mins = diffMin % 60;
  return mins > 0 ? `${hours} soat ${mins} daqiqa oldin` : `${hours} soat oldin`;
}

export const STATE = {
  config: null,
  incidents: [],
  stationsState: {},
  weather: null,
  updatedAt: null,
  settlement: 'xonobod',   // 'all' | 'xonobod' | 'sultonobod'
  mode: 'utilities',       // 'utilities' | 'stations'
  utility: 'power',        // 'power' | 'water' | 'gas'
  showCrowd: true,
  showPlanned: true,
  fuelFilter: 'all',       // 'all' | 'metan' | 'propan' | 'benzin'
  editMode: false,
  isSatellite: false,
};

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function formatLocalTime(isoStr) {
  if (!isoStr) return '';
  return new Date(isoStr).toLocaleTimeString('uz-UZ', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

let toastTimer = null;
export function showToast(text) {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3200);
}

export function getSettlementName(settlementId) {
  if (settlementId === 'all') return 'Barcha hududlar';
  const found = (STATE.config?.settlements || []).find(s => s.id === settlementId);
  return found ? found.name_uz : settlementId;
}

export function openBotDeepLink(payload = '') {
  const url = payload
    ? `https://t.me/${BOT_USERNAME}?start=${encodeURIComponent(payload)}`
    : `https://t.me/${BOT_USERNAME}`;

  const tgWebApp = window.Telegram?.WebApp;
  if (tgWebApp && tgWebApp.initData && typeof tgWebApp.openTelegramLink === 'function') {
    tgWebApp.openTelegramLink(url);
    return;
  }
  window.open(url, '_blank', 'noopener');
}