import { apiGet } from './api.js';
import {
  BOT_USERNAME,
  FUEL_META,
  STATE,
  STATION_STATUS_LABELS,
  UTILITY_NAMES,
  escapeHtml,
  formatLocalTime,
  formatTimeAgo,
  getSettlementName,
  openBotDeepLink,
} from './state.js';

export function openModal(title, html) {
  document.getElementById('modal-title').textContent = title;
  const body = document.getElementById('modal-body');
  body.innerHTML = html;
  document.getElementById('modal-backdrop').classList.remove('hidden');
  return body;
}

export function closeModal() {
  document.getElementById('modal-backdrop').classList.add('hidden');
}

function buildGroupedOptions(items, labelBuilder) {
  const settlements = STATE.config?.settlements || [];
  return settlements
    .map(s => {
      const subset = items.filter(x => x.settlement === s.id);
      if (!subset.length) return '';
      const opts = subset
        .map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(labelBuilder(item))}</option>`)
        .join('');
      return `<optgroup label="${escapeHtml(s.name_uz)}">${opts}</optgroup>`;
    })
    .join('');
}

// --- Карточка махалли (отправка через Deep Link в бота) ---
export function openMahallaDetail(mh, incidentsList) {
  const crowdInc = incidentsList.find(i => i.status !== 'planned') || null;
  const plannedInc = incidentsList.find(i => i.status === 'planned') || null;
  const settlementName = getSettlementName(mh.settlement);

  let html = `<div class="text-xs font-medium text-slate-500 mb-3">${escapeHtml(settlementName)} · ${UTILITY_NAMES[STATE.utility]}</div>`;

  if (plannedInc) {
    html += `
      <div class="p-3.5 rounded-2xl border bg-violet-50 border-violet-200 text-violet-900 mb-3">
        <div class="font-bold text-sm">🟣 Rejali texnik ishlar</div>
        ${plannedInc.note ? `<div class="text-xs mt-1 font-medium">${escapeHtml(plannedInc.note)}</div>` : ''}
        <div class="text-[11px] mt-1.5 opacity-80">Tugash vaqti (taxminan): ${formatLocalTime(plannedInc.expires_at)}</div>
      </div>
    `;
  }

  if (crowdInc) {
    const isConf = crowdInc.status === 'confirmed';
    const cls = isConf ? 'bg-rose-50 border-rose-200 text-rose-800' : 'bg-amber-50 border-amber-200 text-amber-800';
    const lbl = isConf ? '🔴 Avariya tasdiqlangan' : '🟠 Tekshirilmoqda (tasdiqlash uchun 4+ xabar kerak)';
    const sub = crowdInc.issue_subtype === 'low_pressure' ? ' · Bosim past' : '';

    html += `
      <div class="p-3.5 rounded-2xl border ${cls} mb-3">
        <div class="font-bold text-sm">${lbl}${sub}</div>
        <div class="text-xs mt-1.5 opacity-90">
          Tasdiqlaganlar: <b>${crowdInc.reports_count}</b> kishi · Oxirgi xabar: ${formatLocalTime(crowdInc.last_report_at)}
        </div>
        ${crowdInc.street_hint ? `<div class="text-xs mt-1.5 font-medium">📍 Mo‘ljal: ${escapeHtml(crowdInc.street_hint)}</div>` : ''}
      </div>
      <div class="flex gap-2">
        <button data-bot-payload="rep-${STATE.utility}-${crowdInc.issue_subtype}-${mh.id}" type="button"
          class="flex-1 py-3 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-semibold text-xs transition">📍 + Tasdiqlash</button>
        <button data-bot-payload="res-${STATE.utility}-${mh.id}" type="button"
          class="flex-1 py-3 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white font-semibold text-xs transition">✓ Hal bo‘ldi</button>
      </div>
    `;
  } else {
    if (!plannedInc) {
      html += `
        <div class="p-3.5 rounded-2xl bg-emerald-50 border border-emerald-200 text-emerald-800 font-medium mb-3 text-xs">
          🟢 Hozircha bu mahallada uzilish qayd etilmagan.
        </div>
      `;
    }
    html += `
      <button data-bot-payload="rep-${STATE.utility}-outage-${mh.id}" type="button"
        class="w-full py-3 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-semibold mb-2 transition">
        📍 Bot orqali xabar berish (GPS)
      </button>
      ${STATE.utility === 'gas' ? `
        <button data-bot-payload="rep-gas-low_pressure-${mh.id}" type="button"
          class="w-full py-3 rounded-xl bg-amber-500 hover:bg-amber-600 text-white font-semibold transition">
          📉 Gaz bosimi past (botda)
        </button>
      ` : ''}
    `;
  }

  html += `
    <div class="text-[11px] text-slate-400 text-center mt-3">
      🛡 Soxta xabarlardan himoya: GPS tekshiruvi + 2/4 kishi tasdiqlashi
    </div>
  `;

  const body = openModal(`${mh.name_uz} MFY`, html);

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-bot-payload]');
    if (!btn) return;
    openBotDeepLink(btn.dataset.botPayload);
    closeModal();
  });
}

// --- Карточка заправки (4 уровня очереди + свежесть + живое фото) ---
export function openStationDetail(st, info) {
  const meta = FUEL_META[st.fuel] || { label: st.fuel };
  const sName = getSettlementName(st.settlement);

  const statusCard = info
    ? `
      <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-200 mb-3">
        <div class="flex items-center justify-between gap-2 text-[11px] text-slate-500 mb-1">
          <span>Hozirgi holat</span>
          <span class="px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 font-semibold">
            🕒 ${escapeHtml(formatTimeAgo(info.created_at))} yangilandi
          </span>
        </div>
        <div class="text-base font-bold text-slate-900">${STATION_STATUS_LABELS[info.status] || escapeHtml(info.status)}</div>
        <div class="text-[11px] text-slate-500 mt-1">
          Soat ${formatLocalTime(info.created_at)} · ${info.votes_count} ta haydovchi tasdiqlagan
        </div>
      </div>
    `
    : `
      <div class="p-3.5 rounded-2xl bg-slate-50 border border-slate-200 mb-3 text-xs text-slate-600">
        ⚪ So‘nggi 1 soatda ma’lumot yo‘q — birinchi bo‘lib xabar bering!
      </div>
    `;

  const photoBlock = info?.photo_url
    ? `
      <div class="mb-4">
        <div class="text-[11px] font-semibold text-slate-500 mb-1.5">
          📸 Jonli navbat rasmi · ${escapeHtml(formatTimeAgo(info.photo_updated_at))} (${formatLocalTime(info.photo_updated_at)})
        </div>
        <img src="${escapeHtml(info.photo_url)}" alt="${escapeHtml(st.name_uz)}"
          class="w-full max-h-56 object-cover rounded-2xl border border-slate-200 shadow-sm" />
      </div>
    `
    : '';

  const body = openModal(
    st.name_uz,
    `
      <div class="text-xs font-semibold text-slate-500 mb-2">${escapeHtml(sName)} · ${meta.label}</div>
      ${statusCard}
      ${photoBlock}
      <div class="grid grid-cols-1 gap-2">
        <button data-st-status="open" type="button" class="w-full py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white font-semibold text-xs">🟢 Bo‘sh (&lt;15 daq)</button>
        <button data-st-status="queue" type="button" class="w-full py-2.5 rounded-xl bg-yellow-500 hover:bg-yellow-600 text-white font-semibold text-xs">🟡 O‘rtacha (~30 daq)</button>
        <button data-st-status="long_queue" type="button" class="w-full py-2.5 rounded-xl bg-orange-500 hover:bg-orange-600 text-white font-semibold text-xs">🟠 Uzun navbat (1 soat+)</button>
        <button data-st-status="closed" type="button" class="w-full py-2.5 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-semibold text-xs">🔴 Yopiq / yoqilg‘i yo‘q</button>
      </div>
      <div class="text-[11px] text-slate-400 text-center mt-3">
        📍 Holat va navbat rasmi Telegram-botda GPS orqali tasdiqlanadi
      </div>
    `
  );

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-st-status]');
    if (!btn) return;
    openBotDeepLink(`stset-${btn.dataset.stStatus}-${st.id}`);
    closeModal();
  });
}

// --- Нижняя кнопка (коммуналка -> переход в бота) ---
export function openReportModal() {
  if (!STATE.config) return;
  const mhOpts = buildGroupedOptions(STATE.config.mahallas, m => `${m.name_uz} MFY`);

  const body = openModal(
    'Uzilish haqida xabar berish',
    `
      <div class="space-y-3.5">
        <div>
          <label class="block text-xs font-semibold text-slate-600 mb-1">Xizmat turi</label>
          <select id="rep-util" class="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm bg-white">
            <option value="power" ${STATE.utility === 'power' ? 'selected' : ''}>⚡ Chiroq (elektr)</option>
            <option value="water" ${STATE.utility === 'water' ? 'selected' : ''}>💧 Suv</option>
            <option value="gas" ${STATE.utility === 'gas' ? 'selected' : ''}>🔥 Gaz</option>
          </select>
        </div>
        <div id="rep-gas-box" class="${STATE.utility === 'gas' ? '' : 'hidden'}">
          <label class="block text-xs font-semibold text-slate-600 mb-1">Gaz holati</label>
          <select id="rep-subtype" class="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm bg-white">
            <option value="outage">Gaz umuman yo‘q</option>
            <option value="low_pressure">Bosim juda past</option>
          </select>
        </div>
        <div>
          <label class="block text-xs font-semibold text-slate-600 mb-1">Mahallangiz</label>
          <select id="rep-mahalla" class="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm bg-white">${mhOpts}</select>
        </div>
        <button id="rep-submit" type="button"
          class="w-full py-3 rounded-xl bg-[#2ca7f2] hover:bg-[#1e96e0] text-white font-semibold transition">
          📍 Botda GPS bilan tasdiqlash →
        </button>
      </div>
    `
  );

  if (STATE.settlement !== 'all') {
    const first = STATE.config.mahallas.find(m => m.settlement === STATE.settlement);
    if (first) body.querySelector('#rep-mahalla').value = first.id;
  }

  body.querySelector('#rep-util').addEventListener('change', e => {
    body.querySelector('#rep-gas-box').classList.toggle('hidden', e.target.value !== 'gas');
  });

  body.querySelector('#rep-submit').addEventListener('click', () => {
    const util = body.querySelector('#rep-util').value;
    const mahallaId = body.querySelector('#rep-mahalla').value;
    const subtype = util === 'gas' ? body.querySelector('#rep-subtype').value : 'outage';
    openBotDeepLink(`rep-${util}-${subtype}-${mahallaId}`);
    closeModal();
  });
}

// --- Нижняя кнопка (заправки -> переход в бота) ---
export function openStationModal() {
  if (!STATE.config) return;
  const stOpts = buildGroupedOptions(
    STATE.config.stations,
    s => `${s.name_uz} (${(FUEL_META[s.fuel] || {}).short || s.fuel})`
  );

  const body = openModal(
    'Zapravka holatini yangilash',
    `
      <div class="space-y-3">
        <div>
          <label class="block text-xs font-semibold text-slate-600 mb-1">Zapravkani tanlang</label>
          <select id="st-select" class="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm bg-white">${stOpts}</select>
        </div>
        <div class="grid grid-cols-1 gap-2">
          <button data-st-status="open" type="button" class="py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white font-semibold text-xs">🟢 Bo‘sh (&lt;15 daq)</button>
          <button data-st-status="queue" type="button" class="py-2.5 rounded-xl bg-yellow-500 hover:bg-yellow-600 text-white font-semibold text-xs">🟡 O‘rtacha (~30 daq)</button>
          <button data-st-status="long_queue" type="button" class="py-2.5 rounded-xl bg-orange-500 hover:bg-orange-600 text-white font-semibold text-xs">🟠 Uzun navbat (1 soat+)</button>
          <button data-st-status="closed" type="button" class="py-2.5 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-semibold text-xs">🔴 Yopiq / yoqilg‘i yo‘q</button>
        </div>
      </div>
    `
  );

  if (STATE.settlement !== 'all') {
    const first = STATE.config.stations.find(s => s.settlement === STATE.settlement);
    if (first) body.querySelector('#st-select').value = first.id;
  }

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-st-status]');
    if (!btn) return;
    const stationId = body.querySelector('#st-select').value;
    openBotDeepLink(`stset-${btn.dataset.stStatus}-${stationId}`);
    closeModal();
  });
}

// --- Статистика с 24-часовым графиком ---
export async function openStatsModal(scope = STATE.settlement) {
  const query = scope && scope !== 'all' ? `?settlement_id=${encodeURIComponent(scope)}` : '';
  const s = await apiGet(`/api/stats${query}`);

  const tabs = [{ id: 'all', name_uz: 'Barchasi' }, ...(STATE.config?.settlements || [])];
  const scopeTabsHtml = tabs
    .map(t => `
      <button data-stat-scope="${escapeHtml(t.id)}" type="button"
        class="px-2.5 py-1 rounded-xl text-xs border transition ${
          scope === t.id
            ? 'border-[#38a3eb] bg-[#eff8ff] text-[#0b6fb5] font-semibold'
            : 'border-slate-200 bg-white text-slate-500'
        }">${escapeHtml(t.name_uz)}</button>
    `)
    .join('');

  const hourly = s.hourly_24h || [];
  const maxVal = Math.max(1, ...hourly.map(h => h.count));
  const barsHtml = hourly
    .map(h => {
      const pct = h.count > 0 ? Math.max(14, Math.round((h.count / maxVal) * 100)) : 6;
      const title = `${String(h.hour).padStart(2, '0')}:00 — ${h.count} ta`;
      return `<div class="hourly-bar ${h.count > 0 ? 'has-val' : ''}" style="height:${pct}%" title="${title}"></div>`;
    })
    .join('');

  const avgTxt = s.avg_duration_min !== null && s.avg_duration_min !== undefined
    ? `${s.avg_duration_min} daqiqa`
    : 'ma’lumot kam';

  const topRows = (s.top_mahallas_7d || []).length
    ? s.top_mahallas_7d
        .map((m, i) => `
          <div class="flex justify-between py-2 border-b border-slate-100 text-xs">
            <span>${i + 1}. <b>${escapeHtml(m.name_uz)} MFY</b></span>
            <span class="font-semibold text-rose-600">${m.count} marta</span>
          </div>`)
        .join('')
    : '<div class="text-xs text-slate-400">Oxirgi 7 kunda uzilishlar qayd etilmagan.</div>';

  const body = openModal(
    'Statistika',
    `
      <div class="flex items-center gap-1.5 mb-3">${scopeTabsHtml}</div>
      <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="p-3 rounded-2xl bg-sky-50 border border-sky-100"><div class="text-xl font-extrabold text-sky-700">${s.active_by_type.power}</div><div class="text-[11px] text-slate-600 mt-0.5">⚡ Chiroq</div></div>
        <div class="p-3 rounded-2xl bg-blue-50 border border-blue-100"><div class="text-xl font-extrabold text-blue-700">${s.active_by_type.water}</div><div class="text-[11px] text-slate-600 mt-0.5">💧 Suv</div></div>
        <div class="p-3 rounded-2xl bg-orange-50 border border-orange-100"><div class="text-xl font-extrabold text-orange-700">${s.active_by_type.gas}</div><div class="text-[11px] text-slate-600 mt-0.5">🔥 Gaz</div></div>
      </div>
      <div class="grid grid-cols-2 gap-2 mb-4 text-xs">
        <div class="p-3 rounded-2xl bg-slate-50 border border-slate-200">
          <div class="text-slate-500">Oxirgi 24 soat</div>
          <div class="text-base font-bold text-slate-900 mt-0.5">${s.last_24h_total} ta</div>
        </div>
        <div class="p-3 rounded-2xl bg-slate-50 border border-slate-200">
          <div class="text-slate-500">O‘rtacha davomiylik</div>
          <div class="text-base font-bold text-slate-900 mt-0.5">${avgTxt}</div>
        </div>
      </div>
      <div class="p-3 rounded-2xl bg-slate-50 border border-slate-200 mb-4">
        <div class="text-xs font-bold text-slate-700 mb-1">24 soatlik faollik (soatlar bo‘yicha)</div>
        <div class="hourly-chart">${barsHtml}</div>
        <div class="flex justify-between text-[10px] text-slate-400 mt-1">
          <span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span>
        </div>
      </div>
      <div class="text-xs font-bold text-slate-800 mb-1.5">Oxirgi 7 kunda eng ko‘p uzilish bo‘lgan mahallalar:</div>
      ${topRows}
    `
  );

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-stat-scope]');
    if (btn) openStatsModal(btn.dataset.statScope);
  });
}

// --- Таймлайн ---
export async function openTimelineModal(scope = STATE.settlement) {
  const query = scope && scope !== 'all' ? `?settlement_id=${encodeURIComponent(scope)}` : '';
  const data = await apiGet(`/api/timeline${query}`);

  const icons = { power: '⚡', water: '💧', gas: '🔥', station: '⛽' };
  const texts = {
    reported: 'Uzilish xabari keldi',
    confirmed: 'Avariya tasdiqlandi',
    restored: 'Muammo hal bo‘ldi',
    planned: 'Rejali o‘chirish e’lon qilindi',
    station_update: 'Zapravka holati yangilandi',
  };

  const tabs = [{ id: 'all', name_uz: 'Barchasi' }, ...(STATE.config?.settlements || [])];
  const scopeTabsHtml = tabs
    .map(t => `
      <button data-tl-scope="${escapeHtml(t.id)}" type="button"
        class="px-2.5 py-1 rounded-xl text-xs border transition ${
          scope === t.id
            ? 'border-[#38a3eb] bg-[#eff8ff] text-[#0b6fb5] font-semibold'
            : 'border-slate-200 bg-white text-slate-500'
        }">${escapeHtml(t.name_uz)}</button>
    `)
    .join('');

  const rows = (data.events || [])
    .map(ev => `
      <div class="py-2.5 border-b border-slate-100 flex items-start justify-between gap-2 text-xs">
        <div>
          <div class="font-semibold text-slate-800">${icons[ev.category] || '•'} ${escapeHtml(ev.target_name)}</div>
          <div class="text-slate-500 mt-0.5">${texts[ev.event_type] || escapeHtml(ev.event_type)}${ev.details ? ` (${escapeHtml(ev.details)})` : ''}</div>
        </div>
        <span class="text-[11px] text-slate-400 whitespace-nowrap">${formatLocalTime(ev.created_at)}</span>
      </div>
    `)
    .join('') || '<div class="text-xs text-slate-400 py-4 text-center">Hozircha xabarlar yo‘q.</div>';

  const body = openModal(
    'Taymlayn (So‘nggi xabarlar)',
    `<div class="flex items-center gap-1.5 mb-3">${scopeTabsHtml}</div>${rows}`
  );

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-tl-scope]');
    if (btn) openTimelineModal(btn.dataset.tlScope);
  });
}

// --- FAQ и Контакты ---
export function openFaqModal() {
  openModal(
    'Savol-javob',
    `
      <div class="space-y-3 text-xs leading-relaxed">
        <div><b>Soxta xabarlardan qanday himoyalangan?</b><br>Har bir xabar Telegram-botda GPS (2 km radius) orqali tekshiriladi. Joylashuvingiz bazada saqlanmaydi.</div>
        <div><b>Ranglar nimani anglatadi?</b><br>🟢 1 kishi xabar bersa — xaritada hali ko‘rinmaydi (kutiladi).<br>🟠 2–3 kishi xabar bersa — tekshirilmoqda.<br>🔴 4 va undan ko‘p kishi tasdiqlasa — avariya tasdiqlangan.<br>🟣 Rejali texnik ishlar (faqat admin yopadi).</div>
        <div><b>Zapravkalar va rasmlar:</b><br>Haydovchilar zapravka holatini va navbat rasmini yuklashi mumkin. Holat va rasm 1 soatdan keyin avtomatik o‘chadi.</div>
        <div><b>Shaxsiy ma’lumotlar saqlanadimi?</b><br>Yo‘q, telefon raqami ham, aniq GPS koordinata ham saqlanmaydi. Favqulodda holatlarda 1055 ga qo‘ng‘iroq qiling.</div>
      </div>
    `
  );
}

export function openContactModal() {
  openModal(
    'Biz bilan bog‘lanish',
    `
      <div class="space-y-3 text-xs leading-relaxed">
        <p>Eng qulay usul — Telegram-botimiz orqali xabar yuborish va o‘z mahallangizga avtomatik xabarnoma yoqib qo‘yish:</p>
        <a href="https://t.me/${BOT_USERNAME}" target="_blank"
          class="block w-full text-center py-3 rounded-xl bg-[#2ca7f2] hover:bg-[#1e96e0] text-white font-semibold text-sm transition">
          🤖 @${BOT_USERNAME} ga o‘tish
        </a>
        <div class="p-3 rounded-xl bg-slate-50 border border-slate-200 text-slate-600">
          <b>Favqulodda raqam:</b> 1055 (Kommunal xizmatlar dispetcherlik markazi)
        </div>
      </div>
    `
  );
}