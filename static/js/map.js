import { FUEL_META, STATE, escapeHtml, showToast } from './state.js';

let map = null;
let streetLayer = null;
let satelliteLayer = null;
let activeLayers = [];

export function initMap(onMapClickInEditMode) {
  map = L.map('map', { zoomControl: false }).setView([40.8028, 73.0009], 13);
  L.control.zoom({ position: 'bottomright' }).addTo(map);

  streetLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 19,
  }).addTo(map);

  satelliteLayer = L.layerGroup([
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: '&copy; Esri', maxZoom: 19 }
    ),
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19 }
    ),
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19 }
    ),
  ]);

  map.on('click', e => {
    if (STATE.editMode && onMapClickInEditMode) {
      onMapClickInEditMode(e.latlng);
    }
  });
}

export function toggleMapLayer() {
  STATE.isSatellite = !STATE.isSatellite;
  const btn = document.getElementById('btn-layer');
  if (STATE.isSatellite) {
    map.removeLayer(streetLayer);
    satelliteLayer.addTo(map);
    btn.textContent = '🗺️';
  } else {
    map.removeLayer(satelliteLayer);
    streetLayer.addTo(map);
    btn.textContent = '🛰️';
  }
}

export function flyToSettlement(settlementId) {
  if (!map || !STATE.config) return;

  if (settlementId === 'all') {
    const pts = [
      ...STATE.config.mahallas.map(m => m.center),
      ...STATE.config.stations.map(s => s.center),
    ].filter(Boolean);
    if (pts.length) {
      map.fitBounds(pts, { padding: [55, 55], maxZoom: 13 });
    }
    return;
  }

  const target = (STATE.config.settlements || []).find(s => s.id === settlementId);
  if (target?.center) {
    map.flyTo(target.center, target.zoom || 13, { duration: 0.65 });
  }
}

export function panToCoords(coords) {
  if (map && coords) map.panTo(coords);
}

/**
 * Рисует все маркеры и возвращает статистику сводки по выбранному населённому пункту.
 */
export function renderMarkers({ onMahallaClick, onStationClick }) {
  activeLayers.forEach(l => map.removeLayer(l));
  activeLayers = [];
  if (!STATE.config) return { summaryHtml: 'Yuklanmoqda...' };

  if (STATE.mode === 'utilities') {
    const relevant = STATE.incidents.filter(i => i.utility_type === STATE.utility);
    const incsByMahalla = {};
    for (const inc of relevant) {
      if (!incsByMahalla[inc.mahalla_id]) incsByMahalla[inc.mahalla_id] = [];
      incsByMahalla[inc.mahalla_id].push(inc);
    }

    let totalReports = 0;
    let confirmedCnt = 0;
    let plannedCnt = 0;

    for (const mh of STATE.config.mahallas) {
      if (!mh.center) continue;
      const inScope = STATE.settlement === 'all' || mh.settlement === STATE.settlement;

      const allForMh = incsByMahalla[mh.id] || [];
      const visibleList = allForMh.filter(inc => {
        if (inc.status === 'planned') return STATE.showPlanned;
        return STATE.showCrowd;
      });

      const crowdInc = visibleList.find(i => i.status !== 'planned') || null;
      const plannedInc = visibleList.find(i => i.status === 'planned') || null;

      if (inScope) {
        if (crowdInc) {
          totalReports += crowdInc.reports_count || 1;
          if (crowdInc.status === 'confirmed') confirmedCnt++;
        }
        if (plannedInc) plannedCnt++;
      }

      const primary = crowdInc || plannedInc;

      let color = '#10b981';
      let ringColor = null;
      let pulse = '';
      let size = 11;
      let innerTxt = '';
      let labelExtra = '';

      if (primary) {
        if (primary.status === 'confirmed') {
          color = '#ef4444';
          ringColor = '#ef4444';
          pulse = 'marker-pulse';
          size = 24;
          innerTxt = String(primary.reports_count || 4);
          labelExtra = 'mahalla-label-alert';
        } else if (primary.status === 'unconfirmed') {
          color = '#f59e0b';
          ringColor = '#f59e0b';
          size = 20;
          innerTxt = String(primary.reports_count || 2);
          labelExtra = 'mahalla-label-warn';
        } else if (primary.status === 'planned') {
          color = '#8b5cf6';
          ringColor = '#8b5cf6';
          size = 20;
          innerTxt = 'R';
          labelExtra = 'mahalla-label-plan';
        }
      }

      if (ringColor && !STATE.editMode) {
        const ring = L.circle(mh.center, {
          radius: 360,
          color: ringColor,
          weight: 1.5,
          opacity: 0.5,
          fillColor: ringColor,
          fillOpacity: 0.13,
          interactive: false,
        }).addTo(map);
        activeLayers.push(ring);
      }

      const dualBadge = crowdInc && plannedInc
        ? '<span style="position:absolute;top:-3px;right:-3px;width:9px;height:9px;border-radius:9999px;background:#8b5cf6;border:1.5px solid #fff;"></span>'
        : '';

      const icon = L.divIcon({
        className: '',
        html: `
          <div class="marker-wrap" style="display:flex;flex-direction:column;align-items:center;transform:translate(-50%,-50%);cursor:pointer;">
            <div class="${pulse}" style="position:relative;width:${size}px;height:${size}px;border-radius:9999px;background:${color};border:2px solid #fff;box-shadow:0 2px 6px rgba(15,23,42,0.28);color:#fff;font-weight:700;font-size:10.5px;display:flex;align-items:center;justify-content:center;">${innerTxt}${dualBadge}</div>
            <div class="mahalla-label ${labelExtra}" style="margin-top:2px;">${escapeHtml(mh.name_uz)}</div>
          </div>
        `,
        iconSize: [0, 0],
      });

      const marker = L.marker(mh.center, {
        icon,
        draggable: STATE.editMode,
        zIndexOffset: primary ? 600 : 0,
      }).addTo(map);

      if (STATE.editMode) {
        marker.on('dragend', e => {
          const p = e.target.getLatLng();
          mh.center = [Number(p.lat.toFixed(6)), Number(p.lng.toFixed(6))];
          showToast(`${mh.name_uz}: ${mh.center[0]}, ${mh.center[1]}`);
        });
      } else {
        marker.on('click', () => onMahallaClick(mh, allForMh));
      }
      activeLayers.push(marker);
    }

    const parts = [];
    if (totalReports > 0) parts.push(`<b>${totalReports}</b> xabar`);
    if (confirmedCnt > 0) parts.push(`<b>${confirmedCnt}</b> tasdiqlangan`);
    if (plannedCnt > 0) parts.push(`<b class="text-violet-600">+${plannedCnt}</b> rejali`);

    return {
      summaryHtml: parts.length
        ? parts.join(' · ')
        : '<span class="text-emerald-600 font-medium">🟢 Hamma mahallada hammasi joyida</span>',
    };
  }

  // Режим заправок (Metan / Propan / Benzin)
  let openCnt = 0;
  let queueCnt = 0;
  let longQueueCnt = 0;
  let closedCnt = 0;

  for (const st of STATE.config.stations) {
    if (!st.center) continue;
    if (STATE.fuelFilter !== 'all' && st.fuel !== STATE.fuelFilter) continue;

    const inScope = STATE.settlement === 'all' || st.settlement === STATE.settlement;
    const info = STATE.stationsState[st.id];

    let color = '#94a3b8';
    if (info) {
      if (info.status === 'open') { color = '#10b981'; if (inScope) openCnt++; }
      else if (info.status === 'queue') { color = '#eab308'; if (inScope) queueCnt++; }
      else if (info.status === 'long_queue') { color = '#f97316'; if (inScope) longQueueCnt++; }
      else if (info.status === 'closed') { color = '#ef4444'; if (inScope) closedCnt++; }
    }

    const meta = FUEL_META[st.fuel] || { badge: '?', short: st.fuel };
    const camBadge = info?.photo_url
      ? '<span style="position:absolute;top:-5px;right:-6px;font-size:11px;line-height:1;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.4));">📷</span>'
      : '';

    const icon = L.divIcon({
      className: '',
      html: `
        <div class="marker-wrap" style="display:flex;flex-direction:column;align-items:center;transform:translate(-50%,-50%);cursor:pointer;">
          <div style="position:relative;width:27px;height:27px;border-radius:9999px;background:${color};border:2px solid #fff;box-shadow:0 2px 8px rgba(15,23,42,0.3);color:#fff;font-weight:800;font-size:11.5px;display:flex;align-items:center;justify-content:center;">${meta.badge}${camBadge}</div>
          <div class="mahalla-label" style="margin-top:2px;">${escapeHtml(st.name_uz)}</div>
        </div>
      `,
      iconSize: [0, 0],
    });

    const marker = L.marker(st.center, {
      icon,
      draggable: STATE.editMode,
      zIndexOffset: info ? 300 : 0,
    }).addTo(map);

    if (STATE.editMode) {
      marker.on('dragend', e => {
        const p = e.target.getLatLng();
        st.center = [Number(p.lat.toFixed(6)), Number(p.lng.toFixed(6))];
        showToast(`${st.name_uz}: ${st.center[0]}, ${st.center[1]}`);
      });
    } else {
      marker.on('click', () => onStationClick(st, info));
    }
    activeLayers.push(marker);
  }

  return {
    summaryHtml: `⛽ <b>${openCnt}</b> bo‘sh · <b>${queueCnt}</b> o‘rtacha · <b>${longQueueCnt}</b> uzun · <b>${closedCnt}</b> yopiq`,
  };
}

/**
 * Конвертирует текущую конфигурацию обратно в строгий формат data/mahallas.json (совместимый с seed.py).
 */
export function buildSeedCompatibleJson() {
  const cfg = STATE.config;
  return JSON.stringify(
    {
      map_config: cfg.map_config,
      settlements: (cfg.settlements || []).map(s => ({
        id: s.id,
        name_uz: s.name_uz,
        center_lat: s.center[0],
        center_lon: s.center[1],
        default_zoom: s.zoom || 13,
      })),
      mahallas: cfg.mahallas,
      stations: cfg.stations,
    },
    null,
    2
  );
}