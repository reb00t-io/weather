(function() {
  'use strict';

  const API_KEY = window.__API_KEY || '';
  const AUTH_HEADERS = API_KEY ? { 'Authorization': 'Bearer ' + API_KEY } : {};

  // ── Icons ─────────────────────────────────────────────
  const ICONS = {
    'clear':            ['\u2600\uFE0F', '\uD83C\uDF19'],
    'mostly-clear':     ['\uD83C\uDF24\uFE0F', '\uD83C\uDF19'],
    'partly-cloudy':    ['\u26C5', '\u2601\uFE0F'],
    'overcast':         ['\u2601\uFE0F', '\u2601\uFE0F'],
    'fog':              ['\uD83C\uDF2B\uFE0F', '\uD83C\uDF2B\uFE0F'],
    'drizzle-light':    ['\uD83C\uDF26\uFE0F', '\uD83C\uDF26\uFE0F'],
    'drizzle':          ['\uD83C\uDF26\uFE0F', '\uD83C\uDF26\uFE0F'],
    'drizzle-heavy':    ['\uD83C\uDF26\uFE0F', '\uD83C\uDF26\uFE0F'],
    'freezing-drizzle': ['\u2744\uFE0F', '\u2744\uFE0F'],
    'rain-light':       ['\uD83C\uDF26\uFE0F', '\uD83C\uDF26\uFE0F'],
    'rain':             ['\uD83C\uDF27\uFE0F', '\uD83C\uDF27\uFE0F'],
    'rain-heavy':       ['\uD83C\uDF27\uFE0F', '\uD83C\uDF27\uFE0F'],
    'freezing-rain':    ['\u2744\uFE0F', '\u2744\uFE0F'],
    'snow-light':       ['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'snow':             ['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'snow-heavy':       ['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'snow-grains':      ['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'showers-light':    ['\uD83C\uDF26\uFE0F', '\uD83C\uDF26\uFE0F'],
    'showers':          ['\uD83C\uDF27\uFE0F', '\uD83C\uDF27\uFE0F'],
    'showers-heavy':    ['\uD83C\uDF27\uFE0F', '\uD83C\uDF27\uFE0F'],
    'snow-showers':     ['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'snow-showers-heavy':['\uD83C\uDF28\uFE0F', '\uD83C\uDF28\uFE0F'],
    'thunderstorm':     ['\u26C8\uFE0F', '\u26C8\uFE0F'],
    'thunderstorm-hail':['\u26C8\uFE0F', '\u26C8\uFE0F'],
    'unknown':          ['\u2753', '\u2753'],
  };

  function icon(name, isDay) {
    const p = ICONS[name] || ICONS['unknown'];
    return p[isDay ? 0 : 1];
  }

  const DAYS = ['So','Mo','Di','Mi','Do','Fr','Sa'];
  const DAYS_F = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'];
  const MON = ['Jan','Feb','M\u00E4r','Apr','Mai','Jun','Jul','Aug','Sep','Okt','Nov','Dez'];

  function windDir(d) {
    if (d == null) return '';
    const dirs = ['N','NNO','NO','ONO','O','OSO','SO','SSO','S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return dirs[Math.round(d / 22.5) % 16];
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  // ── Condition -> card class ───────────────────────────
  function condClass(iconName, isDay) {
    if (!isDay) {
      if (iconName === 'clear' || iconName === 'mostly-clear') return 'night-clear';
      return 'night';
    }
    if (iconName === 'clear' || iconName === 'mostly-clear') return 'clear';
    if (iconName.includes('thunder')) return 'thunder';
    if (iconName.includes('snow') || iconName.includes('freezing')) return 'snow';
    if (iconName.includes('rain') || iconName.includes('drizzle') || iconName.includes('shower')) return 'rain';
    if (iconName === 'fog') return 'fog';
    return 'cloudy';
  }

  // ── DOM ────────────────────────────────────────────────
  const $ = (s) => document.getElementById(s);
  const searchInput = $('search');
  const searchResults = $('search-results');
  const loading = $('loading');
  const placeholder = $('placeholder');
  const weatherContent = $('weather-content');

  let searchTimeout = null;
  let currentCity = null;

  // ── Tabs ──────────────────────────────────────────────
  const tabBtns = document.querySelectorAll('.tab-btn');
  let radarInitialized = false;

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      $('panel-' + btn.dataset.tab).classList.add('active');

      if (btn.dataset.tab === 'radar' && !radarInitialized) {
        initRadar();
        radarInitialized = true;
      } else if (btn.dataset.tab === 'radar' && window._radarMap) {
        window._radarMap.invalidateSize();
      }
    });
  });

  // ── Recent cities ─────────────────────────────────────
  const MAX_RECENT = 5;
  const searchClear = $('search-clear');

  function getRecentCities() {
    try {
      return JSON.parse(localStorage.getItem('weather_recent') || '[]');
    } catch(e) { return []; }
  }

  function addRecentCity(city) {
    let recents = getRecentCities();
    // Remove duplicate (same lat/lon)
    recents = recents.filter(r => !(r.lat === city.lat && r.lon === city.lon));
    recents.unshift(city);
    if (recents.length > MAX_RECENT) recents = recents.slice(0, MAX_RECENT);
    localStorage.setItem('weather_recent', JSON.stringify(recents));
  }

  function updateClearBtn() {
    if (searchInput.value.length > 0) {
      searchClear.classList.add('visible');
    } else {
      searchClear.classList.remove('visible');
    }
  }

  // ── Search ────────────────────────────────────────────
  searchInput.addEventListener('input', function() {
    clearTimeout(searchTimeout);
    updateClearBtn();
    const q = this.value.trim();
    if (q.length === 0) {
      showRecentCities();
      return;
    }
    // Show matching recents immediately, fetch geocode after debounce
    showMatchingRecents(q);
    searchTimeout = setTimeout(() => fetchGeocode(q), 250);
  });

  function showMatchingRecents(q) {
    const recents = getRecentCities();
    const qLower = q.toLowerCase();
    const matching = recents.filter(r => {
      const haystack = [r.name, r.admin1, r.country].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(qLower);
    });
    if (matching.length) {
      searchResults.innerHTML = '';
      const label = document.createElement('div');
      label.className = 'search-section-label';
      label.textContent = 'Zuletzt gesucht';
      searchResults.appendChild(label);
      for (const r of matching) searchResults.appendChild(createRecentItem(r));
      searchResults.classList.add('open');
    } else {
      searchResults.innerHTML = '';
      searchResults.classList.remove('open');
    }
  }

  searchInput.addEventListener('focus', function() {
    const q = this.value.trim();
    if (q.length === 0) {
      showRecentCities();
    } else {
      showMatchingRecents(q);
    }
  });

  searchClear.addEventListener('click', function() {
    searchInput.value = '';
    updateClearBtn();
    searchResults.classList.remove('open');
    searchInput.focus();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-wrap')) searchResults.classList.remove('open');
  });

  function showRecentCities() {
    const recents = getRecentCities();
    searchResults.innerHTML = '';
    if (!recents.length) { searchResults.classList.remove('open'); return; }
    const label = document.createElement('div');
    label.className = 'search-section-label';
    label.textContent = 'Zuletzt gesucht';
    searchResults.appendChild(label);
    for (const r of recents) {
      searchResults.appendChild(createRecentItem(r));
    }
    searchResults.classList.add('open');
  }

  function createRecentItem(city) {
    const div = document.createElement('div');
    div.className = 'search-result-item recent';
    div.innerHTML =
      '<svg class="recent-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>' +
      '<div class="recent-info"><div class="search-result-name">' + esc(city.name) + '</div>' +
      '<div class="search-result-region">' + esc([city.admin1 !== city.name ? city.admin1 : null, city.country].filter(Boolean).join(', ')) + '</div></div>' +
      '<button class="recent-delete" aria-label="Entfernen"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';
    div.querySelector('.recent-info').addEventListener('click', () => selectCity(city));
    div.querySelector('.recent-icon').addEventListener('click', () => selectCity(city));
    div.querySelector('.recent-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      removeRecentCity(city);
      div.remove();
      if (!searchResults.querySelector('.search-result-item')) searchResults.classList.remove('open');
    });
    return div;
  }

  function removeRecentCity(city) {
    let recents = getRecentCities();
    recents = recents.filter(r => !(r.lat === city.lat && r.lon === city.lon));
    localStorage.setItem('weather_recent', JSON.stringify(recents));
  }

  async function fetchGeocode(q) {
    try {
      const r = await fetch('/api/geocode?q=' + encodeURIComponent(q), { headers: AUTH_HEADERS });
      const data = await r.json();
      renderSearch(data, q);
    } catch(e) { console.error(e); }
  }

  function renderSearch(results, query) {
    searchResults.innerHTML = '';

    // Filter recent cities matching the query and show on top
    const qLower = (query || '').toLowerCase();
    const recents = getRecentCities();
    const matchingRecents = qLower
      ? recents.filter(r => {
          const haystack = [r.name, r.admin1, r.country].filter(Boolean).join(' ').toLowerCase();
          return haystack.includes(qLower);
        })
      : [];

    // Deduplicate: remove API results that match a recent city (same lat/lon)
    const filteredResults = results.filter(res =>
      !matchingRecents.some(rc => rc.lat === res.lat && rc.lon === res.lon)
    );

    if (!matchingRecents.length && !filteredResults.length) {
      searchResults.classList.remove('open');
      return;
    }

    if (matchingRecents.length) {
      const label = document.createElement('div');
      label.className = 'search-section-label';
      label.textContent = 'Zuletzt gesucht';
      searchResults.appendChild(label);
      for (const r of matchingRecents) {
        searchResults.appendChild(createRecentItem(r));
      }
    }

    if (filteredResults.length) {
      if (matchingRecents.length) {
        const label = document.createElement('div');
        label.className = 'search-section-label';
        label.textContent = 'Suchergebnisse';
        searchResults.appendChild(label);
      }
      for (const r of filteredResults) {
        const div = document.createElement('div');
        div.className = 'search-result-item';
        div.innerHTML = '<div class="search-result-name">' + esc(r.name) + '</div>' +
          '<div class="search-result-region">' + esc([r.admin1 !== r.name ? r.admin1 : null, r.country].filter(Boolean).join(', ')) + '</div>';
        div.addEventListener('click', () => selectCity(r));
        searchResults.appendChild(div);
      }
    }

    searchResults.classList.add('open');
  }

  function selectCity(city) {
    currentCity = city;
    searchInput.value = '';
    updateClearBtn();
    searchResults.classList.remove('open');
    searchInput.blur();
    addRecentCity(city);
    localStorage.setItem('weather_city', JSON.stringify(city));
    fetchWeather(city);

    // Center radar on city if initialized
    if (window._radarMap) {
      window._radarMap.setView([city.lat, city.lon], 8);
    }

    fetchEvents(city);
  }

  // ── Fetch Weather ─────────────────────────────────────
  async function fetchWeather(city) {
    loading.classList.add('visible');
    placeholder.style.display = 'none';
    weatherContent.style.display = 'none';

    try {
      const r = await fetch('/api/weather?lat=' + city.lat + '&lon=' + city.lon, { headers: AUTH_HEADERS });
      const data = await r.json();
      if (data.error) throw new Error(data.error);
      renderWeather(city, data);
    } catch(e) {
      console.error(e);
      placeholder.style.display = 'block';
      placeholder.querySelector('p').textContent = 'Fehler beim Laden.';
    } finally {
      loading.classList.remove('visible');
    }
  }

  // ── Render Weather ────────────────────────────────────
  function renderWeather(city, data) {
    const c = data.current;
    const now = new Date();
    const card = $('current-card');

    // Dynamic gradient
    card.className = 'current-card fade-in ' + condClass(c.icon, c.is_day);

    const adminSuffix = city.admin1 && city.admin1 !== city.name ? ', ' + city.admin1 : '';
    $('current-location').textContent = city.name + adminSuffix;
    $('current-date').textContent = DAYS_F[now.getDay()] + ', ' + now.getDate() + '. ' + MON[now.getMonth()];
    $('current-icon').textContent = icon(c.icon, c.is_day);

    // Match the current hourly entry (used below for detail tiles).
    const ch = data.hourly.find(h => {
      const hd = new Date(h.time);
      return hd.getHours() === now.getHours() && hd.getDate() === now.getDate();
    }) || {};

    // DWD station source
    const srcEl = $('current-source');
    if (c.source) {
      srcEl.textContent = 'DWD ' + c.source;
      srcEl.style.display = '';
    } else {
      srcEl.style.display = 'none';
    }

    $('current-details').innerHTML =
      tempTile(Math.round(c.temp)) +
      detailCard('\uD83D\uDCA8', Math.round(c.wind || 0) + ' km/h', 'Wind') +
      detailCard('\uD83D\uDCA7', (ch.precip_prob ?? '--') + '%', 'Regen') +
      detailCard('\uD83D\uDCA6', (c.humidity ?? ch.humidity ?? '--') + '%', 'Feuchte');

    // City background image (weather-aware)
    loadCityImage(city, c.icon, c.is_day);

    // Warnings
    renderWarnings(data.warnings || []);

    // Air quality
    renderAqi(data.aqi);

    renderHourly(data.hourly, now);
    renderTempChart(data.daily);
    renderDaily(data.daily, data.hourly);
    renderPollen(data.pollen);
    weatherContent.style.display = 'block';
  }

  // ── Warnings ──────────────────────────────────────────
  function renderWarnings(warnings) {
    const wrap = $('warnings-wrap');
    wrap.innerHTML = '';
    if (!warnings.length) return;

    for (const w of warnings) {
      const sev = (w.severity || 'minor').toLowerCase();
      const cls = ['extreme','severe','moderate'].includes(sev) ? sev : 'minor';
      const div = document.createElement('div');
      div.className = 'warning-banner severity-' + cls;
      let exp = '';
      if (w.expires) {
        const d = new Date(w.expires);
        exp = '<div class="warning-expires">bis ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ' Uhr, ' + d.getDate() + '. ' + MON[d.getMonth()] + '</div>';
      }
      div.innerHTML = '<div class="warning-headline">\u26A0\uFE0F ' + esc(w.headline || w.event) + '</div>' + exp +
        '<div class="warning-desc">' + esc(w.description) + '</div>';
      div.addEventListener('click', () => div.classList.toggle('expanded'));
      wrap.appendChild(div);
    }
  }

  // ── Air Quality ───────────────────────────────────────
  function renderAqi(aqi) {
    const el = $('aqi-card');
    if (!aqi) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const details = [
      aqi.pm2_5 != null ? 'PM2.5: ' + Math.round(aqi.pm2_5) : '',
      aqi.pm10 != null ? 'PM10: ' + Math.round(aqi.pm10) : '',
      aqi.no2 != null ? 'NO\u2082: ' + Math.round(aqi.no2) : '',
      aqi.o3 != null ? 'O\u2083: ' + Math.round(aqi.o3) : '',
    ].filter(Boolean).join(' \u00B7 ');
    el.innerHTML =
      '<div class="aqi-gauge ' + aqi.color + '">' + Math.round(aqi.eaqi) + '</div>' +
      '<div class="aqi-info">' +
        '<div class="aqi-title">Luftqualit\u00E4t</div>' +
        '<div class="aqi-level">' + esc(aqi.level) + '</div>' +
        '<div class="aqi-details">' + details + '</div>' +
      '</div>';
  }

  // ── Pollen ────────────────────────────────────────────
  function renderPollen(pollen) {
    const sec = $('pollen-section');
    const el = $('pollen-card');
    if (!pollen) {
      sec.style.display = 'none';
      el.style.display = 'none';
      return;
    }
    sec.style.display = 'block';
    el.style.display = 'flex';
    const species = pollen.species || [];
    if (!species.length) {
      el.innerHTML = '<div class="pollen-empty">Keine Pollen aktiv</div>';
      return;
    }
    el.innerHTML = species.map(s =>
      '<div class="pollen-row">' +
        '<div class="pollen-emoji">' + s.emoji + '</div>' +
        '<div class="pollen-name">' + esc(s.name) + '</div>' +
        '<div class="pollen-value">' + s.value + ' /m³</div>' +
        '<div class="pollen-level ' + s.color + '">' + esc(s.level_label) + '</div>' +
      '</div>'
    ).join('');
  }

  // ── City Background Image ─────────────────────────────
  let imageCache = {};

  function weatherCategory(iconName) {
    if (!iconName) return '';
    if (iconName === 'clear' || iconName === 'mostly-clear') return 'sun';
    if (iconName.includes('snow') || iconName.includes('freezing')) return 'snow';
    if (iconName.includes('rain') || iconName.includes('drizzle') || iconName.includes('shower')) return 'rain';
    return 'cloud';
  }

  function loadCityImage(city, weatherIcon, isDay) {
    const bgEl = $('current-card-bg');
    bgEl.classList.remove('loaded');

    const baseName = city.name.split(',')[0].trim();
    const weather = weatherCategory(weatherIcon);
    const isNight = isDay === 0 || isDay === false ? '1' : '0';
    const cacheKey = baseName + ':' + weather + ':' + isNight;

    if (imageCache[cacheKey]) {
      bgEl.style.backgroundImage = 'url(' + imageCache[cacheKey].url + ')';
      bgEl.classList.add('loaded');
      renderAttribution(imageCache[cacheKey].attribution);
      return;
    }

    const params = 'city=' + encodeURIComponent(baseName)
      + (weather ? '&weather=' + weather : '')
      + '&is_night=' + isNight;

    fetch('/api/city-image?' + params, { headers: AUTH_HEADERS })
      .then(r => r.json())
      .then(data => {
        if (!data.url) return;
        const img = new Image();
        img.onload = () => {
          imageCache[cacheKey] = { url: img.src, attribution: data.attribution };
          bgEl.style.backgroundImage = 'url(' + img.src + ')';
          bgEl.classList.add('loaded');
          renderAttribution(data.attribution);
        };
        img.onerror = () => {};
        img.src = data.url;
      })
      .catch(() => {});
  }

  function renderAttribution(attr) {
    let el = $('image-attribution');
    if (!el) return;
    if (attr && attr.name) {
      el.innerHTML = 'Foto: <a href="' + esc(attr.link) + '" target="_blank" rel="noopener">' + esc(attr.name) + '</a> / Unsplash';
      el.style.display = '';
    } else {
      el.style.display = 'none';
    }
  }

  function detailCard(ic, val, label) {
    return '<div class="current-detail">' +
      '<div class="current-detail-icon">' + ic + '</div>' +
      '<div class="current-detail-val">' + esc(val) + '</div>' +
      '<div class="current-detail-label">' + label + '</div></div>';
  }

  function tempTile(val) {
    return '<div class="current-detail current-detail-temp">' +
      '<div class="current-detail-temp-val">' + val +
        '<span class="current-detail-temp-unit">°</span></div></div>';
  }

  // ── Hourly (chart + items) ─────────────────────────────
  let hourlyItems = []; // store for click handler

  function renderHourly(hourly, now) {
    const outer = $('hourly-outer');
    outer.innerHTML = '';
    const curHour = now.getHours();

    let startIdx = 0;
    for (let i = 0; i < hourly.length; i++) {
      const hd = new Date(hourly[i].time);
      if (hd >= new Date(now.getFullYear(), now.getMonth(), now.getDate(), curHour)) {
        startIdx = i; break;
      }
    }

    hourlyItems = hourly.slice(startIdx, startIdx + 25);
    const n = hourlyItems.length;
    if (!n) return;

    const colW = 29;
    const padL = 11, padR = 11;
    const totalW = padL + n * colW + padR;

    const topArea = 18;
    const chartH = 130;
    const tempPad = 16;
    const botGap = 3;
    const botRowH = 14;
    const iconArea = 30;
    const barPad = 3;
    const H = topArea + chartH + botGap + botRowH + botGap + botRowH + botGap + iconArea + 4;

    let tMin = Infinity, tMax = -Infinity;
    for (const h of hourlyItems) {
      if (h.temp < tMin) tMin = h.temp;
      if (h.temp > tMax) tMax = h.temp;
    }
    const tRange = (tMax - tMin) || 1;

    const curveTop = topArea;
    function yPos(temp) {
      const ratio = (temp - tMin) / tRange;
      return curveTop + chartH - tempPad - ratio * (chartH - 2 * tempPad);
    }

    let svg = '<svg class="hourly-chart-svg" width="' + totalW + '" height="' + H + '" viewBox="0 0 ' + totalW + ' ' + H + '">';

    svg += '<defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">';
    svg += '<stop offset="0%" stop-color="rgba(59,130,246,0.25)"/>';
    svg += '<stop offset="100%" stop-color="rgba(59,130,246,0)"/>';
    svg += '</linearGradient></defs>';

    // Selection highlight (behind everything)
    svg += '<rect id="hourly-sel-bg" x="' + padL + '" y="0" width="' + colW + '" height="' + H + '" fill="rgba(59,130,246,0.12)" rx="4"/>';

    // Hour labels
    hourlyItems.forEach((h, i) => {
      const d = new Date(h.time);
      const cx = padL + i * colW + colW / 2;
      const label = i === 0 ? 'Jetzt' : pad(d.getHours()) + 'h';
      svg += '<text x="' + cx + '" y="13" text-anchor="middle" fill="#94a3b8" font-size="9" font-weight="600" font-family="inherit">' + label + '</text>';
    });

    // Temperature curve
    const pts = hourlyItems.map((h, i) => ({
      x: padL + i * colW + colW / 2,
      y: yPos(h.temp),
      val: Math.round(h.temp)
    }));

    if (pts.length >= 2) {
      let areaPath = 'M' + pts[0].x + ',' + pts[0].y;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(pts.length - 1, i + 2)];
        const t = 0.3;
        areaPath += ' C' + (p1.x + (p2.x - p0.x) * t) + ',' + (p1.y + (p2.y - p0.y) * t) +
          ' ' + (p2.x - (p3.x - p1.x) * t) + ',' + (p2.y - (p3.y - p1.y) * t) +
          ' ' + p2.x + ',' + p2.y;
      }
      svg += '<path d="' + areaPath + ' V' + (curveTop + chartH) + ' H' + pts[0].x + ' Z" fill="url(#hg)"/>';
      svg += '<path d="' + areaPath + '" fill="none" stroke="#60a5fa" stroke-width="1.5" stroke-linecap="round"/>';
    }

    pts.forEach(p => {
      svg += '<circle cx="' + p.x + '" cy="' + p.y + '" r="2.5" fill="#60a5fa"/>';
      svg += '<text x="' + p.x + '" y="' + (p.y - 7) + '" text-anchor="middle" fill="#f1f5f9" font-size="9" font-weight="700" font-family="inherit">' + p.val + '°</text>';
    });

    // Sun bars
    const sunY = curveTop + chartH + botGap;
    hourlyItems.forEach((h, i) => {
      const x = padL + i * colW + barPad;
      const w = colW - barPad * 2;
      if (h.is_day) {
        const sl = sunLevelFromCloud(h.cloud);
        svg += '<rect x="' + x + '" y="' + sunY + '" width="' + w + '" height="' + botRowH + '" rx="3" fill="' + sunBarColor(sl) + '"/>';
      } else {
        svg += '<rect x="' + x + '" y="' + sunY + '" width="' + w + '" height="' + botRowH + '" rx="3" fill="rgb(55,65,81)"/>';
      }
    });

    // Rain bars
    const rainY = sunY + botRowH + botGap;
    hourlyItems.forEach((h, i) => {
      const x = padL + i * colW + barPad;
      const w = colW - barPad * 2;
      const prob = h.precip_prob || 0;
      const hasRain = prob > 20;
      const rainAlpha = hasRain ? (0.15 + (prob / 100) * 0.5) : 0.06;
      svg += '<rect x="' + x + '" y="' + rainY + '" width="' + w + '" height="' + botRowH + '" rx="3" fill="rgba(96,165,250,' + rainAlpha.toFixed(2) + ')"/>';
      if (hasRain) {
        svg += '<text x="' + (x + w / 2) + '" y="' + (rainY + botRowH / 2 + 3) + '" text-anchor="middle" font-size="9">💧</text>';
      }
    });

    // Weather icons (bottom row) — sized to nearly fill column width.
    // The crescent-moon glyph fills its em-box more aggressively than
    // sun/cloud emojis, so render it slightly smaller for visual parity.
    const iconY = rainY + botRowH + botGap + 23;
    hourlyItems.forEach((h, i) => {
      const cx = padL + i * colW + colW / 2;
      const g = icon(h.icon, h.is_day);
      const fs = g === '🌙' ? 16 : 22;
      svg += '<text x="' + cx + '" y="' + iconY + '" text-anchor="middle" font-size="' + fs + '">' + g + '</text>';
    });

    // Invisible click targets (on top of everything)
    hourlyItems.forEach((h, i) => {
      const x = padL + i * colW;
      svg += '<rect x="' + x + '" y="0" width="' + colW + '" height="' + H + '" fill="transparent" data-idx="' + i + '" style="cursor:pointer" class="h-col"/>';
    });

    svg += '</svg>';
    outer.innerHTML = svg;

    // Click handling via SVG rects
    const selBg = outer.querySelector('#hourly-sel-bg');
    outer.querySelectorAll('.h-col').forEach(rect => {
      rect.addEventListener('click', () => {
        const idx = parseInt(rect.dataset.idx);
        selBg.setAttribute('x', padL + idx * colW);
        selectHourDetails(hourlyItems[idx]);
      });
    });

    selectHourDetails(hourlyItems[0]);
  }


  function selectHourDetails(h) {
    $('current-icon').textContent = icon(h.icon, h.is_day);

    $('current-details').innerHTML =
      tempTile(Math.round(h.temp)) +
      detailCard('\uD83D\uDCA8', Math.round(h.wind || 0) + ' km/h ' + windDir(h.wind_dir), 'Wind') +
      detailCard('\uD83D\uDCA7', (h.precip_prob ?? '--') + '%', 'Regen') +
      detailCard('\uD83D\uDCA6', (h.humidity ?? '--') + '%', 'Feuchte');
  }

  // ── Sun bar: smooth interpolation through 3 stops ──
  const SUN_C = [255, 210, 60];
  const MIXED_C = [255, 230, 110];
  const CLOUD_C = [200, 200, 200];

  function lerpColor(a, b, t) {
    return [
      Math.round(a[0] + (b[0] - a[0]) * t),
      Math.round(a[1] + (b[1] - a[1]) * t),
      Math.round(a[2] + (b[2] - a[2]) * t),
    ];
  }

  // sun = 0..1 (fraction of sun). Lerp grey → light-yellow → sun-yellow.
  function sunBarColor(sun) {
    const s = Math.max(0, Math.min(1, sun || 0));
    const c = s <= 0.5
      ? lerpColor(CLOUD_C, MIXED_C, s / 0.5)
      : lerpColor(MIXED_C, SUN_C, (s - 0.5) / 0.5);
    return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
  }

  function sunLevelFromCloud(cloud) {
    // cloud 0-100 -> sun 1-0
    return 1 - Math.min(100, Math.max(0, cloud || 0)) / 100;
  }

  function sunLevelFromIcon(iconName) {
    if (iconName === 'clear' || iconName === 'mostly-clear') return 1.0;
    if (iconName === 'partly-cloudy') return 0.55;
    if (iconName === 'overcast') return 0.1;
    if (iconName === 'fog') return 0.08;
    if (iconName.includes('thunder')) return 0.0;
    if (iconName.includes('heavy') || iconName.includes('snow')) return 0.0;
    if (iconName.includes('rain') || iconName.includes('drizzle') || iconName.includes('shower')) return 0.15;
    return 0.25;
  }

  // ── Temperature Chart (SVG) ────────────────────────────
  function renderTempChart(daily) {
    const el = $('temp-chart-scroll');
    const n = daily.length;
    if (!n) { el.innerHTML = ''; return; }

    const colW = 29;
    const padL = 11, padR = 11;
    const W = padL + n * colW + padR;
    const topArea = 36;   // day labels
    const chartH = 170;   // main chart area
    const botRowH = 14;   // each bottom row
    const botGap = 3;
    const iconArea = 30;  // weather pictogram row
    const H = topArea + chartH + botGap + botRowH + botGap + botRowH + botGap + iconArea + 4;

    // Temp range
    let allMin = Infinity, allMax = -Infinity;
    for (const d of daily) {
      if (d.temp_min < allMin) allMin = d.temp_min;
      if (d.temp_max > allMax) allMax = d.temp_max;
    }
    const tempRange = (allMax - allMin) || 1;
    const tempPad = 16;

    function yPos(temp) {
      const ratio = (temp - allMin) / tempRange;
      return topArea + chartH - tempPad - ratio * (chartH - 2 * tempPad);
    }

    let svg = '<svg class="temp-chart-svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';

    // Gradient fills for area under curves
    svg += '<defs>';
    svg += '<linearGradient id="maxGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(245,158,11,0.2)"/><stop offset="100%" stop-color="rgba(245,158,11,0)"/></linearGradient>';
    svg += '<linearGradient id="minGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(96,165,250,0.15)"/><stop offset="100%" stop-color="rgba(96,165,250,0)"/></linearGradient>';
    svg += '</defs>';

    // Weekend backgrounds
    daily.forEach((d, i) => {
      const date = new Date(d.date + 'T00:00:00');
      const dow = date.getDay();
      const x = padL + i * colW;
      if (dow === 0 || dow === 6) {
        svg += '<rect x="' + x + '" y="0" width="' + colW + '" height="' + (topArea + chartH) + '" fill="rgba(148,163,184,0.06)"/>';
      }
    });

    // Day labels
    daily.forEach((d, i) => {
      const date = new Date(d.date + 'T00:00:00');
      const dow = date.getDay();
      const cx = padL + i * colW + colW / 2;
      const isWe = dow === 0 || dow === 6;
      const label = i === 0 ? 'Heute' : DAYS[dow];
      svg += '<text x="' + cx + '" y="20" text-anchor="middle" fill="' + (isWe ? '#f1f5f9' : '#94a3b8') + '" font-size="9" font-weight="' + (isWe ? '700' : '600') + '" font-family="inherit">' + label + '</text>';
    });

    // Points
    const maxPts = [], minPts = [];
    daily.forEach((d, i) => {
      const cx = padL + i * colW + colW / 2;
      maxPts.push({ x: cx, y: yPos(d.temp_max), val: Math.round(d.temp_max) });
      minPts.push({ x: cx, y: yPos(d.temp_min), val: Math.round(d.temp_min) });
    });

    function smoothPath(pts) {
      if (pts.length < 2) return '';
      let path = 'M' + pts[0].x + ',' + pts[0].y;
      for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1];
        const p3 = pts[Math.min(pts.length - 1, i + 2)];
        const t = 0.3;
        path += ' C' + (p1.x + (p2.x - p0.x) * t) + ',' + (p1.y + (p2.y - p0.y) * t) +
          ' ' + (p2.x - (p3.x - p1.x) * t) + ',' + (p2.y - (p3.y - p1.y) * t) +
          ' ' + p2.x + ',' + p2.y;
      }
      return path;
    }

    // Area fills under curves
    const maxPath = smoothPath(maxPts);
    const minPath = smoothPath(minPts);
    const chartBottom = topArea + chartH;
    svg += '<path d="' + maxPath + ' V' + chartBottom + ' H' + maxPts[0].x + ' Z" fill="url(#maxGrad)"/>';
    svg += '<path d="' + minPath + ' V' + chartBottom + ' H' + minPts[0].x + ' Z" fill="url(#minGrad)"/>';

    // Lines (thinner)
    svg += '<path d="' + maxPath + '" fill="none" stroke="#f59e0b" stroke-width="1.5" stroke-linecap="round"/>';
    svg += '<path d="' + minPath + '" fill="none" stroke="#60a5fa" stroke-width="1.5" stroke-linecap="round"/>';

    // Max dots + labels
    maxPts.forEach(p => {
      svg += '<circle cx="' + p.x + '" cy="' + p.y + '" r="2.5" fill="#f59e0b"/>';
      svg += '<text x="' + p.x + '" y="' + (p.y - 7) + '" text-anchor="middle" fill="#f59e0b" font-size="9" font-weight="700" font-family="inherit">' + p.val + '</text>';
    });

    // Min dots + labels
    minPts.forEach(p => {
      svg += '<circle cx="' + p.x + '" cy="' + p.y + '" r="2.5" fill="#60a5fa"/>';
      svg += '<text x="' + p.x + '" y="' + (p.y + 13) + '" text-anchor="middle" fill="#60a5fa" font-size="9" font-weight="700" font-family="inherit">' + p.val + '</text>';
    });

    // ── Bottom bars: Row 1 = Sunshine, Row 2 = Rain ──
    const sunY = topArea + chartH + botGap;
    const rainY = sunY + botRowH + botGap;
    const barPad = 3;

    daily.forEach((d, i) => {
      const x = padL + i * colW + barPad;
      const w = colW - barPad * 2;

      // Sun bar color: drive from mean cloud cover so it matches the
      // hourly chart. Open-Meteo's sunshine_duration sits at ~daylight
      // on most days, so it can't differentiate cloudy days.
      const sunFrac = d.cloud_mean != null
        ? sunLevelFromCloud(d.cloud_mean)
        : sunLevelFromIcon(d.icon || '');
      const sunH = d.sun_hours || 0;

      // Sunshine bar with hours label
      svg += '<rect x="' + x + '" y="' + sunY + '" width="' + w + '" height="' + botRowH + '" rx="3" fill="' + sunBarColor(sunFrac) + '"/>';
      const sunLabel = sunH >= 1 ? Math.round(sunH) + 'h' : (sunH > 0 ? '<1h' : '');
      if (sunLabel) {
        svg += '<text x="' + (x + w / 2) + '" y="' + (sunY + botRowH / 2 + 3) + '" text-anchor="middle" fill="#555" font-size="7" font-weight="600" font-family="inherit">' + sunLabel + '</text>';
      }

      // Rain bar (blue intensity by probability)
      const prob = d.precip_prob || 0;
      const hasRain = prob > 20 || (d.precip_sum && d.precip_sum > 0.5);
      const rainAlpha = hasRain ? (0.15 + (prob / 100) * 0.5) : 0.06;
      svg += '<rect x="' + x + '" y="' + rainY + '" width="' + w + '" height="' + botRowH + '" rx="3" fill="rgba(96,165,250,' + rainAlpha.toFixed(2) + ')"/>';
      if (hasRain) {
        svg += '<text x="' + (x + w / 2) + '" y="' + (rainY + botRowH / 2 + 3) + '" text-anchor="middle" font-size="9">\uD83D\uDCA7</text>';
      }
    });

    // Weather pictograms (bottom row) — sized to nearly fill column width.
    const iconY = rainY + botRowH + botGap + 22;
    daily.forEach((d, i) => {
      const cx = padL + i * colW + colW / 2;
      svg += '<text x="' + cx + '" y="' + iconY + '" text-anchor="middle" font-size="22">' + icon(d.icon, 1) + '</text>';
    });

    svg += '</svg>';
    el.innerHTML = svg;
  }

  // ── Daily ─────────────────────────────────────────────
  function renderDaily(daily, hourly) {
    const el = $('daily-card');
    el.innerHTML = '';

    let gMin = Infinity, gMax = -Infinity;
    for (const d of daily) {
      if (d.temp_min < gMin) gMin = d.temp_min;
      if (d.temp_max > gMax) gMax = d.temp_max;
    }
    const range = gMax - gMin || 1;

    daily.forEach((d, i) => {
      const date = new Date(d.date + 'T00:00:00');
      const isToday = i === 0;
      const dayName = isToday ? 'Heute' : DAYS[date.getDay()];
      const dateStr = date.getDate() + '. ' + MON[date.getMonth()];
      const leftPct = ((d.temp_min - gMin) / range) * 100;
      const widthPct = ((d.temp_max - d.temp_min) / range) * 100;

      const wrapper = document.createElement('div');

      const row = document.createElement('div');
      row.className = 'daily-row';
      row.innerHTML =
        '<div class="daily-day">' + dayName + '<span class="daily-day-sub">' + dateStr + '</span></div>' +
        '<div class="daily-icon">' + icon(d.icon, 1) + '</div>' +
        '<div class="daily-bar-wrap">' +
          '<span class="daily-temp-low">' + Math.round(d.temp_min) + '\u00B0</span>' +
          '<div class="daily-bar"><div class="daily-bar-fill" style="left:' + leftPct + '%;width:' + Math.max(widthPct, 5) + '%"></div></div>' +
          '<span class="daily-temp-high">' + Math.round(d.temp_max) + '\u00B0</span>' +
        '</div>' +
        '<div class="daily-precip">' + (d.precip_prob > 0 ? '\uD83D\uDCA7 ' + d.precip_prob + '%' : '') + '</div>';

      const detail = document.createElement('div');
      detail.className = 'daily-detail';
      detail.id = 'dd-' + i;

      row.addEventListener('click', () => {
        const wasOpen = detail.classList.contains('open');
        document.querySelectorAll('.daily-detail.open').forEach(d => d.classList.remove('open'));
        if (!wasOpen) {
          detail.classList.add('open');
          renderDayDetail(detail, d, hourly);
        }
      });

      wrapper.appendChild(row);
      wrapper.appendChild(detail);
      el.appendChild(wrapper);
    });
  }

  function renderDayDetail(el, day, hourly) {
    const dayH = hourly.filter(h => h.time && h.time.startsWith(day.date));

    const feelsStr = (day.feels_min != null && day.feels_max != null)
      ? Math.round(day.feels_min) + '\u00B0 / ' + Math.round(day.feels_max) + '\u00B0'
      : '--';
    let html = '<div class="daily-detail-grid">' +
      '<div class="daily-detail-item">\uD83C\uDF21\uFE0F Gef\u00FChlt: <strong>' + feelsStr + '</strong></div>' +
      '<div class="daily-detail-item">\uD83D\uDCA8 Wind: <strong>' + Math.round(day.wind_max) + ' km/h ' + windDir(day.wind_dir) + '</strong></div>' +
      '<div class="daily-detail-item">\u2600\uFE0F UV: <strong>' + (day.uv_max != null ? day.uv_max : '--') + '</strong></div>' +
      '<div class="daily-detail-item">\uD83C\uDF05 Aufgang: <strong>' + (day.sunrise ? day.sunrise.slice(11,16) : '--') + '</strong></div>' +
      '<div class="daily-detail-item">\uD83C\uDF07 Untergang: <strong>' + (day.sunset ? day.sunset.slice(11,16) : '--') + '</strong></div>' +
      '<div class="daily-detail-item">\uD83C\uDF27\uFE0F Niederschlag: <strong>' + (day.precip_sum != null ? day.precip_sum.toFixed(1) + ' mm' : '--') + '</strong></div>' +
      '<div class="daily-detail-item">\uD83D\uDCA7 Wahrsch.: <strong>' + (day.precip_prob ?? '--') + '%</strong></div>' +
      '</div>';

    if (dayH.length) {
      html += '<div class="daily-hourly-scroll">';
      for (const h of dayH) {
        const t = new Date(h.time);
        html += '<div class="dh-item">' +
          '<span class="dh-time">' + pad(t.getHours()) + ':00</span>' +
          '<span class="dh-icon">' + icon(h.icon, h.is_day) + '</span>' +
          '<span class="dh-temp">' + Math.round(h.temp) + '\u00B0</span>' +
          (h.precip_prob > 0 ? '<span class="dh-precip">' + h.precip_prob + '%</span>' : '') +
          '</div>';
      }
      html += '</div>';
    }

    el.innerHTML = html;
  }

  // ════════════════════════════════════════════════════════
  // RADAR (RainViewer API + Leaflet)
  // ════════════════════════════════════════════════════════
  let radarMap, radarLayer, radarFrames = [], radarIdx = 0, radarPlaying = false, radarTimer = null;

  function initRadar() {
    const center = currentCity ? [currentCity.lat, currentCity.lon] : [51.1657, 10.4515]; // Germany center
    const zoom = currentCity ? 7 : 5;

    radarMap = L.map('radar-map', {
      center: center,
      zoom: zoom,
      minZoom: 4,
      maxZoom: 12,
      zoomControl: false,
      attributionControl: false,
    });

    window._radarMap = radarMap;

    // Panes: basemap < radar < labels
    radarMap.createPane('basemap');
    radarMap.getPane('basemap').style.zIndex = 200;

    radarMap.createPane('labels');
    radarMap.getPane('labels').style.zIndex = 500;

    radarMap.createPane('radar');
    radarMap.getPane('radar').style.zIndex = 400;

    // CartoDB Voyager base (free, no API key)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png', {
      maxZoom: 18,
      subdomains: 'abcd',
      pane: 'basemap',
    }).addTo(radarMap);

    // Labels on top of radar
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png', {
      maxZoom: 18,
      subdomains: 'abcd',
      pane: 'labels',
    }).addTo(radarMap);

    // Zoom control
    L.control.zoom({ position: 'topright' }).addTo(radarMap);

    // Attribution
    L.control.attribution({ position: 'bottomleft', prefix: false })
      .addAttribution('<a href="https://carto.com/">CARTO</a> | <a href="https://www.openstreetmap.org/copyright">OSM</a> | <a href="https://www.rainviewer.com/">RainViewer</a>')
      .addTo(radarMap);

    // City marker
    if (currentCity) {
      L.circleMarker([currentCity.lat, currentCity.lon], {
        radius: 6,
        fillColor: '#e11d48',
        fillOpacity: 1,
        color: '#fff',
        weight: 2,
      }).addTo(radarMap);
    }

    loadRadarFrames();

    // Controls
    $('radar-play-btn').addEventListener('click', toggleRadarPlay);
    $('radar-slider').addEventListener('input', (e) => {
      radarIdx = parseInt(e.target.value);
      showRadarFrame(radarIdx);
    });
  }

  async function loadRadarFrames() {
    try {
      const r = await fetch('https://api.rainviewer.com/public/weather-maps.json');
      const data = await r.json();
      const host = data.host;
      const past = (data.radar && data.radar.past) || [];
      const nowcast = (data.radar && data.radar.nowcast) || [];

      radarFrames = [];
      for (const f of past) {
        radarFrames.push({ time: f.time, url: host + f.path + '/256/{z}/{x}/{y}/6/1_1.png', type: 'past' });
      }
      for (const f of nowcast) {
        radarFrames.push({ time: f.time, url: host + f.path + '/256/{z}/{x}/{y}/6/1_1.png', type: 'forecast' });
      }

      const pastCount = past.length;

      if (radarFrames.length) {
        const slider = $('radar-slider');
        slider.max = radarFrames.length - 1;
        radarIdx = pastCount > 0 ? pastCount - 1 : 0;
        slider.value = radarIdx;

        // Gradient on slider track: blue = past, amber = forecast
        const pastPct = (pastCount / radarFrames.length * 100).toFixed(1);
        slider.style.background = 'linear-gradient(90deg, rgba(96,165,250,0.35) 0%, rgba(96,165,250,0.35) ' + pastPct + '%, rgba(251,191,36,0.35) ' + pastPct + '%, rgba(251,191,36,0.35) 100%)';

        // Preload ALL layers (hidden) so first playback doesn't flicker
        radarLayers = {};
        radarFrames.forEach((frame, i) => {
          radarLayers[i] = L.tileLayer(frame.url, {
            opacity: 0,
            pane: 'radar',
          });
          radarLayers[i].addTo(radarMap);
        });

        showRadarFrame(radarIdx);
      }
    } catch(e) {
      console.error('Radar load error:', e);
      $('radar-time').textContent = 'Fehler beim Laden';
    }
  }

  // Keep all radar tile layers cached to avoid flicker
  let radarLayers = {};

  function showRadarFrame(idx) {
    const frame = radarFrames[idx];
    if (!frame || !radarLayers[idx]) return;

    // Hide previous, show current
    if (radarLayer && radarLayer !== radarLayers[idx]) {
      radarLayer.setOpacity(0);
    }
    radarLayers[idx].setOpacity(0.85);
    radarLayer = radarLayers[idx];

    // Update time display
    const d = new Date(frame.time * 1000);
    const isForecast = frame.type === 'forecast';
    $('radar-time').textContent = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ' Uhr';
    const label = $('radar-label');
    label.textContent = isForecast ? 'Vorhersage' : 'Niederschlag';
    if (isForecast) { label.classList.add('forecast'); } else { label.classList.remove('forecast'); }
    $('radar-slider').value = idx;
  }

  function stopRadarPlay() {
    radarPlaying = false;
    clearInterval(radarTimer);
    $('play-icon').style.display = 'block';
    $('pause-icon').style.display = 'none';
  }

  function toggleRadarPlay() {
    radarPlaying = !radarPlaying;
    $('play-icon').style.display = radarPlaying ? 'none' : 'block';
    $('pause-icon').style.display = radarPlaying ? 'block' : 'none';

    if (radarPlaying) {
      // If at end, restart from beginning
      if (radarIdx >= radarFrames.length - 1) {
        radarIdx = 0;
        showRadarFrame(radarIdx);
      }
      radarTimer = setInterval(() => {
        radarIdx++;
        if (radarIdx >= radarFrames.length) {
          stopRadarPlay();
          return;
        }
        showRadarFrame(radarIdx);
      }, 500);
    } else {
      clearInterval(radarTimer);
    }
  }

  // ── Geolocation ────────────────────────────────────────
  const geoBtn = $('geo-btn');
  geoBtn.addEventListener('click', () => {
    if (!navigator.geolocation) return;
    geoBtn.classList.add('loading');
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        geoBtn.classList.remove('loading');
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        try {
          const r = await fetch('/api/reverse-geocode?lat=' + lat + '&lon=' + lon, { headers: AUTH_HEADERS });
          const city = await r.json();
          city.lat = lat;
          city.lon = lon;
          selectCity(city);
        } catch(e) {
          selectCity({ name: 'Mein Standort', admin1: '', country: '', lat: lat, lon: lon });
        }
      },
      (err) => {
        geoBtn.classList.remove('loading');
        console.log('Geolocation denied:', err.message);
      },
      { enableHighAccuracy: false, timeout: 10000 }
    );
  });

  // ── Init ──────────────────────────────────────────────
  const saved = localStorage.getItem('weather_city');
  if (saved) {
    try {
      const city = JSON.parse(saved);
      currentCity = city;
      fetchWeather(city);
    } catch(e) {}
  }

  // ── Resume from background ────────────────────────────
  let lastVisible = Date.now();
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && currentCity) {
      const elapsed = Date.now() - lastVisible;
      // Refresh if app was in background for 5+ minutes
      if (elapsed > 5 * 60 * 1000) {
        fetchWeather(currentCity);
        fetchEvents(currentCity);
      }
    }
    if (document.visibilityState === 'hidden') {
      lastVisible = Date.now();
    }
  });

  // ── Events ────────────────────────────────────────────
  const eventsState = { city: null, all: [], cat: 'top', time: 'all', shown: 25 };
  const EVENTS_PAGE = 25;
  const eventsBtn = $('tab-btn-events');
  const eventsList = $('events-list');
  const eventsLoading = $('events-loading');
  const eventsEmpty = $('events-empty');
  const eventsMoreBtn = $('events-more-btn');
  const eventsTitle = $('events-title');
  const eventsCatFilters = $('events-cat-filters');
  const eventsTimeFilters = $('events-time-filters');

  const CAT_EMOJI = {
    music:    '🎵',
    stage:    '🎭',
    art:      '🖼️',
    film:     '🎬',
    family:   '👪',
    market:   '🛒️',
    sports:   '⚽',
    talk:     '📚',
    festival: '🎊',
    civic:    '📋',
    other:    '✨'
  };

  const DAY_HEADERS_F = ['Sonntag','Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag'];
  const MONTH_F = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];

  function isoToday() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function isoOffset(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // Parse 'YYYY-MM-DD' as local-date so day-of-week is correct in any TZ.
  function parseLocalDate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d);
  }

  function formatDayHeader(iso) {
    const d = parseLocalDate(iso);
    const today = isoToday();
    const tomorrow = isoOffset(1);
    let prefix;
    if (iso === today) prefix = 'HEUTE';
    else if (iso === tomorrow) prefix = 'MORGEN';
    else prefix = DAY_HEADERS_F[d.getDay()].toUpperCase();
    return prefix + ' · ' + d.getDate() + '. ' + MONTH_F[d.getMonth()];
  }

  function formatTime(t) {
    return t ? t.slice(0, 5) : null;
  }

  function matchesTime(event, time) {
    if (time === 'all') return true;
    if (time === 'today')    return event.start_date === isoToday();
    if (time === 'tomorrow') return event.start_date === isoOffset(1);
    if (time === 'weekend') {
      const dow = parseLocalDate(event.start_date).getDay();
      return dow === 0 || dow === 6;
    }
    return true;
  }

  function matchesCategory(event, cat) {
    if (cat === 'all') return true;
    if (cat === 'top') {
      // Hide pure civic noise; keep events the classifier rates as
      // generally interesting (score ≥ 2). Falls back to "not civic" for
      // events that haven't been classified yet.
      if (event.is_civic) return false;
      if (event.interest_score == null) return true;
      return event.interest_score >= 2;
    }
    return event.category === cat;
  }

  function applyFilters(events) {
    return events.filter(e => matchesCategory(e, eventsState.cat)
                            && matchesTime(e, eventsState.time));
  }

  function renderEvents() {
    if (eventsState.cat === 'film') {
      renderMovies();
      return;
    }
    const filtered = applyFilters(eventsState.all);
    const visible = filtered.slice(0, eventsState.shown);

    eventsList.innerHTML = '';
    eventsEmpty.style.display = filtered.length ? 'none' : 'block';
    if (!filtered.length) {
      eventsMoreBtn.style.display = 'none';
      return;
    }

    let lastDate = null;
    for (const ev of visible) {
      if (ev.start_date !== lastDate) {
        const h = document.createElement('div');
        h.className = 'events-day-header';
        if (ev.start_date === isoToday()) h.classList.add('today');
        h.textContent = formatDayHeader(ev.start_date);
        eventsList.appendChild(h);
        lastDate = ev.start_date;
      }
      eventsList.appendChild(createEventCard(ev));
    }

    const more = filtered.length - visible.length;
    if (more > 0) {
      eventsMoreBtn.textContent = 'Mehr anzeigen (' + more + ')';
      eventsMoreBtn.style.display = 'block';
    } else {
      eventsMoreBtn.style.display = 'none';
    }
  }

  // ── Movies view (cat === 'film') ──────────────────────
  // Show unique movies (deduped by title across showtimes and cinemas)
  // playing within travel distance of the user. We try a tight 15 km
  // radius first; if that turns up nothing, expand to 30 km. The day
  // filter still applies. Tapping a movie reveals its cinemas, sorted
  // by distance — tapping a cinema opens its program page.
  const FILM_RADIUS_PRIMARY_KM = 15;
  const FILM_RADIUS_FALLBACK_KM = 30;
  // When a movie has more than this many cinemas, show only the closest
  // ones — the rest is noise once the user has reasonable nearby options.
  const FILM_MAX_CINEMAS = 20;

  function buildMovieGroups(radiusKm) {
    const city = eventsState.city;
    const hasCoords = city && city.lat != null && city.lon != null;

    const filtered = eventsState.all.filter(ev => {
      if (ev.category !== 'film') return false;
      if (!matchesTime(ev, eventsState.time)) return false;
      if (!hasCoords) return true;
      if (ev.venue_lat == null || ev.venue_lon == null) return true;
      const d = haversineKm(city.lat, city.lon, ev.venue_lat, ev.venue_lon);
      return d <= radiusKm;
    });

    const byTitle = new Map();
    for (const ev of filtered) {
      const key = (ev.title || '').toLowerCase().trim();
      if (!key) continue;
      let g = byTitle.get(key);
      if (!g) {
        g = {
          title: ev.title,
          events: [],
          venues: new Set(),
          days: new Set(),
          maxScore: -1,
          image_url: null,
          actors: null,
          country: null,
          synopsis: null,
          trailer_url: null,
        };
        byTitle.set(key, g);
      }
      g.events.push(ev);
      // Dedup venues case-insensitively so 'delphi LUX' (Yorck) and
      // 'Delphi LUX' (Kinoheld) don't double-count.
      if (ev.venue) g.venues.add(ev.venue.toLowerCase());
      if (ev.start_date) g.days.add(ev.start_date);
      const score = ev.interest_score == null ? 0 : ev.interest_score;
      if (score > g.maxScore) {
        g.maxScore = score;
        g.title = ev.title;
      }
      if (!g.image_url && ev.image_url) g.image_url = ev.image_url;
      if (!g.actors && ev.actors) g.actors = ev.actors;
      if (!g.country && ev.country) g.country = ev.country;
      // Synopsis: prefer the longest one across sources (Yorck and
      // Kinoheld can both supply it; longer usually = more useful).
      if (ev.synopsis && (!g.synopsis || ev.synopsis.length > g.synopsis.length)) {
        g.synopsis = ev.synopsis;
      }
      if (!g.trailer_url && ev.trailer_url) g.trailer_url = ev.trailer_url;
    }

    for (const movie of byTitle.values()) {
      movie.cinemas = buildCinemaList(movie.events);
      movie.minDistance = movie.cinemas.reduce((min, c) => {
        if (c.distance_km == null) return min;
        return min == null ? c.distance_km : Math.min(min, c.distance_km);
      }, null);
    }

    return [...byTitle.values()].sort((a, b) => {
      const venueDiff = b.venues.size - a.venues.size;
      if (venueDiff !== 0) return venueDiff;
      const ad = a.minDistance == null ? Infinity : a.minDistance;
      const bd = b.minDistance == null ? Infinity : b.minDistance;
      if (ad !== bd) return ad - bd;
      return a.title.localeCompare(b.title, 'de');
    });
  }

  function renderMovies() {
    let radius = FILM_RADIUS_PRIMARY_KM;
    let movies = buildMovieGroups(radius);
    let expanded = false;
    if (!movies.length) {
      radius = FILM_RADIUS_FALLBACK_KM;
      movies = buildMovieGroups(radius);
      expanded = movies.length > 0;
    }

    const visible = movies.slice(0, eventsState.shown);

    eventsList.innerHTML = '';

    if (movies.length) {
      const banner = document.createElement('div');
      banner.className = 'movie-radius-banner';
      banner.textContent = expanded
        ? `Keine Kinos in ${FILM_RADIUS_PRIMARY_KM} km – Radius auf ${radius} km erweitert.`
        : `Filme in ${radius} km Umkreis`;
      eventsList.appendChild(banner);
    }

    eventsEmpty.style.display = movies.length ? 'none' : 'block';
    if (!movies.length) {
      eventsMoreBtn.style.display = 'none';
      return;
    }

    for (const movie of visible) {
      eventsList.appendChild(createMovieCard(movie));
    }

    const more = movies.length - visible.length;
    if (more > 0) {
      eventsMoreBtn.textContent = 'Mehr anzeigen (' + more + ')';
      eventsMoreBtn.style.display = 'block';
    } else {
      eventsMoreBtn.style.display = 'none';
    }
  }

  function createMovieCard(movie) {
    const card = document.createElement('div');
    card.className = 'event-card movie-card';

    if (movie.image_url) {
      const poster = document.createElement('img');
      poster.className = 'movie-poster';
      poster.src = movie.image_url;
      poster.alt = movie.title;
      poster.loading = 'lazy';
      poster.decoding = 'async';
      // Hide the slot if the image fails (404, decode error, etc.) so we
      // don't leave a grey hole in the card.
      poster.addEventListener('error', () => poster.remove());
      card.appendChild(poster);
    }

    const body = document.createElement('div');
    body.className = 'event-body movie-body';

    const title = document.createElement('div');
    title.className = 'event-title';
    const em = document.createElement('span');
    em.className = 'event-cat-emoji';
    em.textContent = '🎬';
    title.appendChild(em);
    title.appendChild(document.createTextNode(movie.title));
    body.appendChild(title);

    if (movie.actors) {
      const cast = document.createElement('div');
      cast.className = 'movie-cast';
      cast.textContent = movie.actors;
      body.appendChild(cast);
    }

    const meta = document.createElement('div');
    meta.className = 'event-meta';
    const parts = [];
    if (movie.country) parts.push(movie.country);
    const cinemaWord = movie.venues.size === 1 ? 'Kino' : 'Kinos';
    parts.push(movie.venues.size + ' ' + cinemaWord);
    const dayWord = movie.days.size === 1 ? 'Tag' : 'Tage';
    parts.push(movie.days.size + ' ' + dayWord);
    if (movie.minDistance != null) {
      parts.push('ab ' + formatKm(movie.minDistance));
    }
    meta.textContent = parts.join(' · ');
    body.appendChild(meta);

    // Trailer button sits under the meta line so it stays close to the
    // movie's headline info — even on a phone the user doesn't have to
    // scroll for it.
    if (movie.trailer_url) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'movie-trailer-btn';
      btn.textContent = '▶ Trailer ansehen';
      btn.addEventListener('click', e => {
        e.stopPropagation();
        openTrailer(movie.trailer_url, movie.title);
      });
      body.appendChild(btn);
    }

    card.appendChild(body);

    // Synopsis and cinema list span the full card width below the
    // poster + body, so long synopses don't stretch a narrow column
    // and push the cinema list far down. Both lazy-populate on first
    // expand.
    const synopsisBlock = document.createElement('div');
    synopsisBlock.className = 'movie-synopsis-block';
    card.appendChild(synopsisBlock);

    const cinemas = document.createElement('div');
    cinemas.className = 'movie-cinemas';
    card.appendChild(cinemas);

    card.classList.add('movie-card-collapsed');
    card.addEventListener('click', () => {
      const expanded = card.classList.toggle('movie-card-expanded');
      card.classList.toggle('movie-card-collapsed', !expanded);
      if (expanded && !card.dataset.populated) {
        if (movie.synopsis) {
          populateSynopsis(synopsisBlock, movie.synopsis);
        }
        const shown = movie.cinemas.slice(0, FILM_MAX_CINEMAS);
        for (const c of shown) cinemas.appendChild(createCinemaItem(c));
        const more = movie.cinemas.length - shown.length;
        if (more > 0) {
          const note = document.createElement('div');
          note.className = 'cinema-overflow';
          note.textContent = '+' + more + ' weitere Kinos';
          cinemas.appendChild(note);
        }
        card.dataset.populated = '1';
      }
    });

    return card;
  }

  // Render synopsis with a 3-line clamp + "weiterlesen" toggle. Hides
  // the toggle entirely when the text already fits without clamping.
  function populateSynopsis(host, text) {
    const p = document.createElement('p');
    p.className = 'movie-synopsis movie-synopsis-clamped';
    p.textContent = text;
    host.appendChild(p);

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'movie-synopsis-toggle';
    toggle.textContent = 'weiterlesen';
    toggle.addEventListener('click', e => {
      e.stopPropagation();
      const clamped = p.classList.toggle('movie-synopsis-clamped');
      toggle.textContent = clamped ? 'weiterlesen' : 'weniger anzeigen';
    });
    host.appendChild(toggle);

    // requestAnimationFrame so layout has happened before we decide
    // whether to keep the toggle.
    requestAnimationFrame(() => {
      if (p.scrollHeight <= p.clientHeight + 1) {
        toggle.remove();
        p.classList.remove('movie-synopsis-clamped');
      }
    });
  }

  // ── Trailer modal (fullscreen iframe) ────────────────
  const YT_RE = /(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{6,})/;
  const VIMEO_RE = /vimeo\.com\/(?:video\/)?(\d+)/;

  function trailerEmbedUrl(url) {
    const yt = url.match(YT_RE);
    if (yt) return 'https://www.youtube.com/embed/' + yt[1] + '?autoplay=1&rel=0&modestbranding=1';
    const v = url.match(VIMEO_RE);
    if (v) return 'https://player.vimeo.com/video/' + v[1] + '?autoplay=1';
    return null;
  }

  function openTrailer(url, title) {
    const embed = trailerEmbedUrl(url);
    if (!embed) {
      // Unknown provider → just open the source URL; user can watch
      // wherever it's hosted.
      window.open(url, '_blank', 'noopener');
      return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'trailer-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-label', 'Trailer: ' + (title || ''));

    const frame = document.createElement('iframe');
    frame.className = 'trailer-frame';
    frame.src = embed;
    frame.allow = 'accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; fullscreen';
    frame.setAttribute('allowfullscreen', '');
    overlay.appendChild(frame);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'trailer-close';
    close.setAttribute('aria-label', 'Trailer schließen');
    close.textContent = '×';
    overlay.appendChild(close);

    function dismiss() {
      if (document.fullscreenElement === overlay) {
        document.exitFullscreen().catch(() => {});
      }
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    }
    function onKey(e) {
      if (e.key === 'Escape') dismiss();
    }
    close.addEventListener('click', e => { e.stopPropagation(); dismiss(); });
    overlay.addEventListener('click', e => {
      if (e.target === overlay) dismiss();
    });
    document.addEventListener('keydown', onKey);

    document.body.appendChild(overlay);
    // Try real fullscreen on platforms that support it; if it fails
    // (iOS Safari, browsers without permission, etc.) the overlay
    // already covers the viewport so playback still feels fullscreen.
    if (overlay.requestFullscreen) {
      overlay.requestFullscreen().catch(() => {});
    }
  }

  function buildCinemaList(events) {
    // Case-insensitive dedup so the same cinema reported by Yorck and
    // Kinoheld with different capitalisation collapses to one row.
    const byVenue = new Map();
    for (const ev of events) {
      const v = ev.venue || '';
      if (!v) continue;
      const key = v.toLowerCase();
      let k = byVenue.get(key);
      if (!k) {
        k = {
          venue: v,
          venue_url: ev.venue_url || null,
          venue_lat: ev.venue_lat,
          venue_lon: ev.venue_lon,
          dates: new Set(),
        };
        byVenue.set(key, k);
      }
      if (ev.start_date) k.dates.add(ev.start_date);
      if (k.venue_url == null && ev.venue_url) k.venue_url = ev.venue_url;
      if (k.venue_lat == null && ev.venue_lat != null) k.venue_lat = ev.venue_lat;
      if (k.venue_lon == null && ev.venue_lon != null) k.venue_lon = ev.venue_lon;
    }
    const cinemas = [...byVenue.values()];
    const city = eventsState.city;
    for (const k of cinemas) {
      if (city && city.lat != null && city.lon != null
          && k.venue_lat != null && k.venue_lon != null) {
        k.distance_km = haversineKm(city.lat, city.lon, k.venue_lat, k.venue_lon);
      } else {
        k.distance_km = null;
      }
    }
    cinemas.sort((a, b) => {
      if (a.distance_km == null && b.distance_km == null) {
        return a.venue.localeCompare(b.venue, 'de');
      }
      if (a.distance_km == null) return 1;
      if (b.distance_km == null) return -1;
      return a.distance_km - b.distance_km;
    });
    return cinemas;
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const toRad = d => d * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  function formatKm(km) {
    if (km < 1) return Math.round(km * 1000) + ' m';
    if (km < 10) return km.toFixed(1) + ' km';
    return Math.round(km) + ' km';
  }

  function createCinemaItem(c) {
    const item = c.venue_url ? document.createElement('a') : document.createElement('div');
    item.className = 'cinema-item';
    if (c.venue_url) {
      item.href = c.venue_url;
      item.target = '_blank';
      item.rel = 'noopener';
    }

    const name = document.createElement('span');
    name.className = 'cinema-name';
    name.textContent = c.venue;
    item.appendChild(name);

    const meta = document.createElement('span');
    meta.className = 'cinema-meta';
    meta.textContent = c.distance_km != null ? formatKm(c.distance_km) : '';
    item.appendChild(meta);

    item.addEventListener('click', e => e.stopPropagation());
    return item;
  }

  function createEventCard(ev) {
    const card = document.createElement('div');
    card.className = 'event-card';

    const timeCol = document.createElement('div');
    timeCol.className = 'event-time';
    const start = formatTime(ev.start_time);
    if (start) {
      const main = document.createElement('div');
      main.className = 'event-time-main';
      main.textContent = start;
      timeCol.appendChild(main);
      const end = formatTime(ev.end_time);
      if (end && end !== start) {
        const e = document.createElement('div');
        e.className = 'event-time-end';
        e.textContent = '– ' + end;
        timeCol.appendChild(e);
      }
    } else {
      const allDay = document.createElement('div');
      allDay.className = 'event-time-allday';
      allDay.textContent = 'Ganz-\ntägig';
      allDay.style.whiteSpace = 'pre-line';
      timeCol.appendChild(allDay);
    }

    const body = document.createElement('div');
    body.className = 'event-body';

    const title = document.createElement('div');
    title.className = 'event-title';
    if (ev.category && CAT_EMOJI[ev.category]) {
      const em = document.createElement('span');
      em.className = 'event-cat-emoji';
      em.textContent = CAT_EMOJI[ev.category];
      title.appendChild(em);
    }
    title.appendChild(document.createTextNode(ev.title));
    body.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'event-meta';
    if (ev.venue) {
      const v = document.createElement('span');
      v.className = 'event-venue';
      v.textContent = ev.venue;
      meta.appendChild(v);
    }
    if (ev.is_free) {
      const b = document.createElement('span');
      b.className = 'event-badge free';
      b.textContent = 'Frei';
      meta.appendChild(b);
    }
    body.appendChild(meta);

    card.appendChild(timeCol);
    card.appendChild(body);
    return card;
  }

  function setEventsTitle(label) {
    eventsTitle.textContent = 'Events in ' + label;
  }

  async function fetchEvents(city) {
    if (!city) return;
    eventsState.city = city;
    eventsState.shown = EVENTS_PAGE;
    // Optimistic title from the city name; the API response will replace
    // it with the resolved region (e.g. 'Hansestadt Salzwedel' →
    // 'Salzwedel') once it lands.
    setEventsTitle(city.name.split(',')[0].split(/[-–]/)[0].trim());
    eventsLoading.classList.add('visible');
    eventsList.innerHTML = '';
    eventsMoreBtn.style.display = 'none';
    try {
      const r = await fetch('/api/events?city=' + encodeURIComponent(city.name) + '&days=14',
                            { headers: AUTH_HEADERS });
      const data = await r.json();
      eventsState.all = data.events || [];
      if (data.region) setEventsTitle(data.region);
      // Tab visible only when this region actually has events.
      eventsBtn.style.display = eventsState.all.length ? '' : 'none';
      // If the user was on the events tab but the new city has none, fall back.
      if (!eventsState.all.length && eventsBtn.classList.contains('active')) {
        document.querySelector('.tab-btn[data-tab="weather"]').click();
      }
      renderEvents();
    } catch(e) {
      console.error('events fetch failed', e);
      eventsBtn.style.display = 'none';
    } finally {
      eventsLoading.classList.remove('visible');
    }
  }

  eventsCatFilters.querySelectorAll('.events-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      eventsCatFilters.querySelectorAll('.events-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      eventsState.cat = chip.dataset.cat;
      eventsState.shown = EVENTS_PAGE;
      renderEvents();
    });
  });

  eventsTimeFilters.querySelectorAll('.events-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      eventsTimeFilters.querySelectorAll('.events-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      eventsState.time = chip.dataset.time;
      eventsState.shown = EVENTS_PAGE;
      renderEvents();
    });
  });

  eventsMoreBtn.addEventListener('click', () => {
    eventsState.shown += EVENTS_PAGE;
    renderEvents();
  });

  // Initial load: if we restored a saved city, also fetch its events.
  if (currentCity) fetchEvents(currentCity);

  // ── PWA Service Worker ────────────────────────────────
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }

})();
