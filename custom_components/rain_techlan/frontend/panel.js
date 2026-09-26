/* Панель «Полив и осадки» (rain_techlan): сетка камер 1/2/4, зоны, оценка дождя, полив, запреты. */
const API = "rain_techlan";

class RainTechlanPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._state = null;
    this._drawing = false;
    this._drag = null;
    this._gesture = false;
    this._busy = false;
    this._lastLoad = 0;
    this._lastTs = null;
    this._layout = Number(localStorage.getItem("rain_layout") || 2);
    this._oneCam = localStorage.getItem("rain_one_cam") || "";
    this._editing = localStorage.getItem("rain_zone_edit") === "1";
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._build(); this._lastLoad = 0; }
    if (this._gesture) return;                        // не мешаем правке зон
    const now = Date.now();
    if (now - (this._lastLoad || 0) > 5000) {          // опрос не чаще 1 раза в 5 с
      this._lastLoad = now;
      this._load();
    }
  }
  set panel(p) { this._panel = p; }

  /* --------------------------------------------------------------- разметка */
  _build() {
    this.innerHTML = `
      <style>
        :host { display:block; padding:12px 16px 32px; color: var(--primary-text-color);
                background: var(--primary-background-color); font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        h1 { font-size:20px; margin:0 0 4px; }
        .muted { color: var(--secondary-text-color); font-size:13px; }
        .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
        .card { background: var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; padding:12px 14px; margin:10px 0; }
        .grid { display:grid; gap:12px; grid-template-columns: minmax(0,2fr) minmax(0,1fr); }
        @media (max-width: 900px){ .grid{ grid-template-columns: 1fr; } }
        @media (max-width: 700px){ .frames.n2, .frames.n4 { grid-template-columns: 1fr; } button { padding:8px 12px; } }
        .badge { display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:13px; }
        .dry { background:#15803d22; color:#15803d; border:1px solid #15803d55; }
        .maybe { background:#b4530922; color:#b45309; border:1px solid #b4530955; }
        .wet { background:#b91c1c22; color:#b91c1c; border:1px solid #b91c1c55; }
        .mutedbadge { background:#64748b22; color:#64748b; border:1px solid #64748b55; }
        .frames { display:grid; gap:8px; margin-top:10px; }
        .frames.n1 { grid-template-columns: 1fr; }
        .frames.n2 { grid-template-columns: 1fr 1fr; }
        .frames.n4 { grid-template-columns: 1fr 1fr; }
        .cell { position:relative; }
        .cell img { width:100%; display:block; border-radius:10px; background:#111; min-height:120px; }
        .cell .ovl { position:absolute; inset:0; }
        .cell .cap { position:absolute; left:8px; top:6px; font-size:12px; color:#fff; text-shadow:0 1px 2px #000; }
        .zone { position:absolute; border:2px solid #40c878; border-radius:4px; pointer-events:none; }
        .frames.editing .zone { pointer-events:auto; cursor:move; }
        .frames:not(.editing) .zone .rz { display:none; }
        .frames:not(.editing) .zone { opacity:.9; }
        .zone.wet { border-color:#ff4040; }
        .zone .rz { position:absolute; right:-7px; bottom:-7px; width:14px; height:14px; background:#14b8a6;
                    border:1px solid #fff; border-radius:3px; cursor:nwse-resize; }
        .zone span { position:absolute; top:2px; left:4px; font-size:11px; color:#fff; text-shadow:0 1px 2px #000; }
        button { cursor:pointer; border-radius:8px; border:1px solid var(--divider-color); background: var(--secondary-background-color);
                 color: var(--primary-text-color); padding:6px 12px; font-size:13px; }
        button.primary { background:#0f766e; border-color:#0f766e; color:#fff; }
        button.danger { background:#b91c1c; border-color:#b91c1c; color:#fff; }
        input, select { padding:5px 8px; border-radius:8px; border:1px solid var(--divider-color);
                        background: var(--secondary-background-color); color: var(--primary-text-color); }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        td, th { padding:4px 6px; border-bottom:1px solid var(--divider-color); text-align:left; }
        .jrow { padding:4px 0; border-bottom:1px dashed var(--divider-color); font-size:13px; }
        .small { font-size:12px; }
      </style>
      <h1>Полив и осадки</h1>
      <div class="muted" id="sub">Загрузка…</div>

      <div class="card row" style="justify-content:space-between">
        <div class="row">
          <span id="verdict" class="badge mutedbadge">нет данных</span>
          <span class="muted" id="score">оценка —</span>
          <span class="muted" id="mode"></span>
        </div>
        <div class="row">
          <button id="scan" class="primary">Проверить сейчас</button>
          <button id="learn">Пометить сухо (24 ч)</button>
          <button id="menuBtn">⋯ Меню</button>
          <div id="menuBox" style="display:none; position:absolute; right:16px; margin-top:34px; z-index:20;
               background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:10px; padding:6px; min-width:200px">
            <button id="mRefresh" style="display:block;width:100%;text-align:left;border:none;background:none">⟳ Обновить страницу</button>
            <button id="mHome" style="display:block;width:100%;text-align:left;border:none;background:none">🏠 На главную HA</button>
            <button id="mLogout" style="display:block;width:100%;text-align:left;border:none;background:none;color:#b91c1c">⎋ Выход (выйти из HA)</button>
          </div>
        </div>
      </div>

      <div class="card" id="nowcard">
        <div class="row" style="justify-content:space-between; align-items:center">
          <div class="row" style="gap:14px">
            <span id="nowVerdict" class="badge mutedbadge">нет данных</span>
            <span class="muted small">источники: <b id="srcCam">камера —</b> · <b id="srcTruth">датчик —</b></span>
            <span class="muted small" id="nowIrrigation"></span>
            <span class="muted small" id="nowDelay"></span>
            <span class="muted small" id="nowFresh"></span>
          </div>
          <div class="row">
            <button id="p1">Пауза 1 дн</button><button id="p2">2 дн</button><button id="p3">3 дн</button><button id="p7">7 дн</button>
            <button id="p0" class="danger">Снять паузу</button>
          </div>
        </div>
        <div id="spark" style="margin-top:8px"></div>
        <div id="errBanner" style="display:none; margin-top:8px; padding:6px 10px; border-radius:8px; background:#b91c1c22; border:1px solid #b91c1c66; color:#b91c1c; font-size:13px"></div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="row" style="justify-content:space-between">
            <div class="row">
              <label class="small">Раскладка:</label>
              <select id="layout">
                <option value="1">1 камера</option>
                <option value="2">2 камеры</option>
                <option value="4">4 камеры</option>
              </select>
              <button id="draw">＋ Добавить зону</button>
              <button id="zExport">⭳ Зоны</button>
              <button id="zImport">⭱ Импорт зон</button>
              <button id="heatBtn">🌡 Тепловая карта</button>
              <input id="zFile" type="file" accept=".json" style="display:none">
              <button id="edit">✎ Редактировать зоны</button>
              <label class="small" id="camselLabel">Камера:</label>
              <select id="camsel"></select>
            </div>
            <div class="muted small" id="frameinfo"></div>
          </div>
          <div id="frames" class="frames n2"></div>
          <div class="muted small" id="hint" style="margin-top:6px">Зоны: перетаскивайте мышью, за уголок — размер; «Добавить зону» — рисуете новую (на выбранной камере).</div>
          <div class="muted small" id="errs" style="margin-top:4px"></div>
        </div>

        <div>
          <div class="card">
            <b>Зоны</b>
            <table id="zones"><thead><tr><th>Имя</th><th>Камера</th><th>Оценка</th><th></th></tr></thead><tbody></tbody></table>
          </div>
          <div class="card">
            <b>Полив</b>
            <div class="row" style="margin:6px 0">
              <button id="stopall" class="danger">Стоп всё</button>
              <label class="small">Задержка дождя, дн:</label>
              <input id="rd" type="number" min="0" max="14" step="1" style="width:64px">
              <button id="rdset">Применить</button>
            </div>
            <div class="muted small" id="irr"></div>
          </div>
          <div class="card">
            <b>Порог и «истина»</b>
            <div class="row" style="margin:6px 0">
              <label class="small">Порог:</label>
              <input id="thr" type="range" min="0.1" max="0.9" step="0.05" style="width:140px">
              <span id="thrv" class="small"></span>
            </div>
            <div class="row">
              <label class="small">Датчик дождя:</label>
              <input id="truth" placeholder="binary_sensor.…" style="min-width:210px">
              <button id="truthset">ОК</button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="row" style="justify-content:space-between">
            <b>Журнал</b>
            <div class="row">
              <select id="jf"><option value="">все</option><option value="rain">дождь</option>
                <option value="action">действия</option><option value="error">ошибки</option></select>
              <button id="jCsv">⭳ CSV</button><button id="jJson">⭳ JSON</button>
            </div>
          </div>
          <div id="journal" style="max-height:260px; overflow:auto"></div>
        </div>
        <div class="card">
          <b>Запреты полива</b>
          <div class="muted small">Правило: сенсор → условие → блокировка запуска.</div>
          <div id="ilist"></div>
          <div class="row" style="margin-top:8px">
            <input id="ient" placeholder="sensor.…" style="min-width:150px">
            <select id="iop"><option value="is_on">включён</option><option value="is_off">выключен</option>
              <option value="above">&gt;</option><option value="below">&lt;</option></select>
            <input id="ival" value="0" style="width:70px">
            <select id="iact"><option value="all">весь полив</option><option value="start_zone">запуск зоны</option>
              <option value="start_program">запуск программы</option></select>
            <select id="imode"><option value="block">блокировать</option><option value="warn">предупредить</option></select>
            <button id="iadd">＋ Запрет</button>
          </div>
        </div>
      </div>
    `;
    this.$ = (id) => this.querySelector("#" + id);
    this.$("layout").value = String(this._layout);
    this.$("layout").onchange = (e) => {
      this._layout = Number(e.target.value);
      localStorage.setItem("rain_layout", String(this._layout));
      // карточка «Сейчас»: вердикт, источники, полив, задержка, свежесть кадра
    this._renderFrames();
    };
    for (const [id, days] of [["p1",1],["p2",2],["p3",3],["p7",7],["p0",0]]) {
      const btn = this.$(`${id}`);
      if (btn) btn.onclick = () => this._svc("rain_techlan", "set_rain_delay", { days });
    }
    this.$("edit").onclick = () => {
      this._editing = !this._editing;
      localStorage.setItem("rain_zone_edit", this._editing ? "1" : "0");
      this.$("edit").classList.toggle("primary", this._editing);
      this.$("frames").classList.toggle("editing", this._editing);
    };
    this.$("camsel").onchange = (e) => {
      this._oneCam = e.target.value;
      localStorage.setItem("rain_one_cam", this._oneCam);
      // карточка «Сейчас»: вердикт, источники, полив, задержка, свежесть кадра
    this._renderFrames();
    };
    this.$("edit").classList.toggle("primary", this._editing);
    this.$("frames").classList.toggle("editing", this._editing);
    this.$("scan").onclick = () => this._scan();
    this.$("learn").onclick = () => this._learn();
    this.$("draw").onclick = () => {
      this._drawing = !this._drawing;
      this.$("draw").classList.toggle("primary", this._drawing);
      this.$("draw").textContent = this._drawing ? "✕ Отменить рисование" : "＋ Добавить зону";
    };
    this.$("stopall").onclick = () => this._svc("rain_techlan", "stop_all_zones", {});
    this.$("rdset").onclick = () => this._svc("rain_techlan", "set_rain_delay", { days: Number(this.$("rd").value || 0) });
    this.$("thr").oninput = (e) => { this.$("thrv").textContent = e.target.value; };
    this.$("thr").onchange = (e) => this._save({ rain_threshold: Number(e.target.value) });
    this.$("truthset").onclick = () => this._save({ truth_entity: this.$("truth").value.trim() });
    this.$("heatBtn").onclick = () => {
      this._heat = !this._heat;
      this.$("heatBtn").classList.toggle("primary", this._heat);
      this._renderFrames();
    };
    this.$("iadd").onclick = () => this._addInterlock();
    this.$("jf").onchange = () => this._renderJournal();
    this.$("jCsv").onclick = () => this._exportJournal("csv");
    this.$("jJson").onclick = () => this._exportJournal("json");
    this.$("zExport").onclick = () => {
      const zones = ((this._state || {}).settings || {}).zones || [];
      const blob = new Blob([JSON.stringify(zones, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "rain_techlan-zones.json"; a.click();
    };
    this.$("zImport").onclick = () => this.$("zFile").click();
    this.$("zFile").onchange = (ev) => {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      const rd = new FileReader();
      rd.onload = async () => {
        try {
          const zones = JSON.parse(rd.result);
          if (!Array.isArray(zones)) throw new Error("ожидается массив зон");
          await this._save({ zones });
        } catch (e) { this.$("errs").textContent = "Импорт зон: " + e; }
      };
      rd.readAsText(f);
    };
    this.$("menuBtn").onclick = (ev) => {
      ev.stopPropagation();
      const b = this.$("menuBox");
      b.style.display = b.style.display === "none" ? "block" : "none";
    };
    this.addEventListener("click", (ev) => {
      if (!ev.target.closest("#menuBox") && ev.target.id !== "menuBtn") this.$("menuBox").style.display = "none";
    });
    this.$("mRefresh").onclick = () => window.location.reload();
    this.$("mHome").onclick = () => { this.$("menuBox").style.display = "none"; window.location.href = "/lovelace/0"; };
    this.$("mLogout").onclick = async () => {
      this.$("menuBox").style.display = "none";
      if (!confirm("Выйти из Home Assistant?")) return;
      try { if (this._hass.auth && this._hass.auth.logout) { await this._hass.auth.logout(); return; } } catch (e) { /* фолбэк */ }
      window.location.href = "/logout";
    };
    this._installGestures();
  }

  /* --------------------------------------------------------------- данные */
  async _load() {
    if (this._busy) return;
    this._busy = true;
    try {
      this._state = await this._hass.callApi("GET", `${API}/state`);
      this._render();
    } catch (e) {
      this.$("sub").textContent = "Ошибка загрузки: " + e;
    }
    this._busy = false;
  }
  async _scan() {
    this.$("scan").textContent = "Сканирую…";
    try { await this._hass.callApi("POST", `${API}/scan`, {}); }
    catch (e) { this.$("errs").textContent = "Ошибка скана: " + e; }
    this.$("scan").textContent = "Проверить сейчас";
    await this._load();
  }
  async _learn() {
    try {
      const r = await this._hass.callApi("POST", `${API}/learn`, { hours: 24 });
      this.$("errs").textContent = `Помечено «сухо»: ${r.marked}, групп эталона: ${r.groups}`;
    } catch (e) { this.$("errs").textContent = "Ошибка: " + e; }
    await this._load();
  }
  async _save(patch) {
    try { await this._hass.callApi("POST", `${API}/settings`, patch); }
    catch (e) { this.$("errs").textContent = "Настройки сохранены, ответ: " + e; }
    await this._load();
  }
  async _svc(domain, service, data) { return this._hass.callService(domain, service, data); }

  /* --------------------------------------------------------------- отрисовка */
  _render() {
    const st = this._state || {};
    const det = st.detector || {}, set = st.settings || {};
    const sum = det.summary || {};
    const v = this.$("verdict");
    v.className = "badge " + (sum.verdict === "дождь" ? "wet" : sum.verdict === "возможно" ? "maybe" : sum.verdict === "сухо" ? "dry" : "mutedbadge");
    v.textContent = sum.verdict || "нет данных";
    this.$("score").textContent = `оценка ${sum.score ?? "—"}`;
    this.$("mode").textContent = det.bucket ? `режим ${det.bucket}` : "";
    this.$("sub").textContent = [
      det.ts ? `проход ${String(det.ts).slice(11, 19)}` : "прохода ещё не было",
      `камер: ${(det.cameras || []).length}`, `зон: ${(det.zones || []).length}`,
      `замеров: ${det.samples ?? 0}`, `эталон: ${det.reference_groups ?? 0} гр.`,
      det.truth_wet ? "истина: дождь" : "",
    ].filter(Boolean).join(" • ");
    this.$("errs").textContent = (det.errors || []).join("; ");
    this.$("thr").value = set.threshold ?? 0.5;
    this.$("thrv").textContent = set.threshold ?? 0.5;
    if (document.activeElement !== this.$("truth")) this.$("truth").value = set.truth_entity || "";
    this.$("rd").value = (this._hass.states["number.poliv_rain_bird_zaderzhka_dozhdia"] || {}).state || 0;
    this._renderSpark(det);
    // карточка «Сейчас»: вердикт, источники, полив, задержка, свежесть кадра
    const sources = det.sources || {};
    const srcCam = this.$("srcCam"), srcTruth = this.$("srcTruth");
    if (srcCam) {
      const camOk = det.camera_ok !== false;
      srcCam.textContent = "камера " + (camOk ? (sources.camera ? "мокро ✓" : "сухо") : "нет данных");
      srcCam.style.color = !camOk ? "#b45309" : sources.camera ? "#b91c1c" : "#15803d";
      srcTruth.textContent = "датчик " + (sources.truth ? "дождь ✓" : "сухо");
      srcTruth.style.color = sources.truth ? "#b91c1c" : "#15803d";
      this.$("nowVerdict").className = "badge " + (det.wet ? "wet" : "mutedbadge");
      this.$("nowVerdict").textContent = det.wet ? "ДОЖДЬ" : "сухо";
      const irrS = this._hass.states["binary_sensor.oroshenie_any_zone_running"];
      this.$("nowIrrigation").textContent = irrS && irrS.state === "on" ? "полив идёт" : "полив не идёт";
      const dl = this._hass.states["sensor.oroshenie_rain_delay"];
      this.$("nowDelay").textContent = "задержка: " + (dl ? dl.state : "—") + " дн";
      const age = det.ts ? Math.round((Date.now() - Date.parse(det.ts)) / 1000) : null;
      this.$("nowFresh").textContent = age !== null ? `кадр ${age} с назад` : "";
      const errs = det.errors || [];
      const banner = this.$("errBanner");
      if (banner) {
        banner.style.display = errs.length ? "" : "none";
        banner.textContent = errs.length ? "⚠ Камеры: " + errs.join("; ") : "";
      }
    }
    this._renderFrames();
    this._renderZonesTable();
    const irr = [];
    for (const [k, val] of Object.entries(this._hass.states)) {
      if (/^sensor\.oroshenie_(station_\d+|rain_delay|controller_mode)/.test(k)) irr.push(`${k.split(".")[1]}=${val.state}`);
    }
    this.$("irr").textContent = irr.join(" · ");
    if (!this._journal) { this._loadJournal(); } else { this._renderJournal(); }
    const rules = set.interlocks || [];
    this.$("ilist").innerHTML = rules.map((r, i) =>
      `<div class="row" style="justify-content:space-between; border-bottom:1px dashed var(--divider-color); padding:4px 0">
        <span class="small">${r.name}: <code>${r.entity_id}</code> ${r.op} ${r.value} → ${r.mode === "block" ? "блок" : "предупр."}</span>
        <button data-il="${i}" class="danger">✕</button></div>`).join("") || `<div class="muted small">правил нет</div>`;
    this.$("ilist").querySelectorAll("[data-il]").forEach((b) => b.onclick = () => {
      const rules2 = rules.filter((_, i) => i !== Number(b.dataset.il));
      this._hass.callApi("POST", `${API}/interlocks`, { rules: rules2 }).then(() => this._load());
    });
  }

  _renderSpark(det) {
    // мини-график 24 ч: оценка камеры (линия) + метки «истины» (точки)
    const series = (det && det.series) || [];
    const sp = this.$("spark");
    if (!sp) return;
    if (sp) {
      if (series.length < 2) {
        sp.innerHTML = `<span class="muted small">график появится после нескольких проходов</span>`;
      } else {
        const W = 640, H = 60, thr = det.effective_threshold ?? det.threshold ?? 0.5;
        const pts = series.map((r, i) => {
          const x = (i / (series.length - 1)) * W;
          const y = H - Math.max(0, Math.min(1, Number(r.score) || 0)) * H;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(" ");
        const truth = series.map((r, i) => r.truth
          ? `<circle cx="${((i / (series.length - 1)) * W).toFixed(1)}" cy="6" r="2.5" fill="#b91c1c"/>` : "").join("");
        const ty = (H - thr * H).toFixed(1);
        sp.innerHTML = `<svg viewBox="0 0 ${W} ${H + 10}" style="width:100%;height:80px">
            <line x1="0" y1="${ty}" x2="${W}" y2="${ty}" stroke="#64748b" stroke-dasharray="4 4"/>
            <polyline fill="none" stroke="#0f766e" stroke-width="2" points="${pts}"/>
            ${truth}
          </svg>
          <div class="muted small">24 ч: оценка камеры (линия), пунктир — порог ${thr}, красные точки — «истина» (датчик)</div>`;
      }
    }
  }

  /* ------------------------------------------------- кадры: сетка 1 / 2 / 4 */
  _renderFrames() {
    if (this._gesture || !this._state) return;         // во время правки не пересобираем
    const det = this._state.detector || {}, set = this._state.settings || {};
    const cams = (det.cameras || set.cameras || []);
    const box = this.$("frames");
    box.className = "frames n" + (this._layout === 1 ? 1 : this._layout === 4 ? 4 : 2)
                    + (this._editing ? " editing" : "");
    if (!cams.length) { box.innerHTML = `<div class="muted">камеры не заданы — добавьте в Настройках интеграции</div>`; return; }
    const single = this._layout === 1;
    const chosen = cams.find((c) => c.id === this._oneCam) || cams[0];
    const visible = single ? [chosen] : cams.slice(0, Math.min(this._layout, cams.length));
    this.$("camsel").style.display = single ? "" : "none";
    this.$("camselLabel").style.display = single ? "" : "none";
    this.$("camsel").innerHTML = cams.map((c) =>
      `<option value="${c.id}" ${c.id === chosen.id ? "selected" : ""}>${c.name || c.id}</option>`).join("");
    const ts = det.ts || "";
    box.innerHTML = visible.map((c) => `
      <div class="cell" data-cam="${c.id}">
        <img src="/rain_techlan/${this._heat ? "heat_" : "raw_"}${encodeURIComponent(c.id)}.jpg?t=${encodeURIComponent(ts)}" alt="${c.name || c.id}">
        <div class="cap">${c.name || c.id}</div>
        <div class="ovl" data-cam="${c.id}">${this._zonesHtml(c.id)}</div>
      </div>`).join("");
    this.$("frameinfo").textContent = single
      ? `камера «${chosen.name || chosen.id}»`
      : `${visible.length} из ${cams.length} камер`;
  }

  _zonesHtml(camId) {
    const set = (this._state || {}).settings || {};
    const det = (this._state || {}).detector || {};
    const thr = set.threshold ?? 0.5;
    return (set.zones || []).filter((z) => !z.camera || z.camera === camId).map((z) => {
      const r = (det.results || []).find((x) => x.camera === camId && x.zone === z.id);
      const wet = r && r.score >= thr;
      return `<div class="zone ${wet ? "wet" : ""}" data-z="${z.id}" data-cam="${camId}"
        style="left:${z.x * 100}%; top:${z.y * 100}%; width:${z.w * 100}%; height:${z.h * 100}%">
        <span>${z.name}${r ? " • " + r.score : ""}</span><i class="rz" title="Размер"></i></div>`;
    }).join("");
  }

  _renderZonesTable() {
    const set = (this._state || {}).settings || {}, det = (this._state || {}).detector || {};
    const tb = this.$("zones").querySelector("tbody");
    tb.innerHTML = (set.zones || []).map((z) => {
      const r = (det.results || []).find((x) => x.zone === z.id);
      return `<tr><td>${z.name}</td><td class="small">${z.camera || "все"}</td><td>${r ? r.score : "—"}<span class="muted small">${r && r.dev && r.dev.mean_down ? " ↓" + r.dev.mean_down : ""}</span></td>
        <td class="row"><button data-edit="${z.id}">✎</button><button data-del="${z.id}" class="danger">✕</button></td></tr>`;
    }).join("") || `<tr><td colspan="4" class="muted">зон нет — нарисуйте на кадре</td></tr>`;
    tb.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
      if (!confirm("Удалить зону?")) return;
      this._save({ zones: (set.zones || []).filter((z) => z.id !== b.dataset.del) });
    });
    tb.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => {
      const z = (set.zones || []).find((x) => x.id === b.dataset.edit);
      if (!z) return;
      const name = prompt("Имя зоны:", z.name);
      if (name === null) return;
      const vals = prompt("Положение и размер в % (x,y,w,h):",
        [z.x, z.y, z.w, z.h].map((v) => Math.round(v * 100)).join(","));
      let upd = { ...z, name };
      if (vals) {
        const parts = vals.split(",").map((v) => Number(String(v).replace(",", ".")) / 100);
        if (parts.length === 4 && parts.every((v) => !isNaN(v))) {
          upd.x = Math.min(0.99, Math.max(0, parts[0])); upd.y = Math.min(0.99, Math.max(0, parts[1]));
          upd.w = Math.min(1 - upd.x, Math.max(0.03, parts[2])); upd.h = Math.min(1 - upd.y, Math.max(0.03, parts[3]));
        }
      }
      this._save({ zones: (set.zones || []).map((x) => (x.id === z.id ? upd : x)) });
    });
  }

  _renderJournal() {
    const j = this._journal || [];
    const f = this.$("jf") ? this.$("jf").value : "";
    const rows = (f ? j.filter((x) => (x.kind || "") === f) : j).slice(-60).reverse();
    this.$("journal").innerHTML = rows.map((r) =>
      `<div class="jrow"><span class="muted small">${String(r.ts).slice(11, 19)}</span> [${r.kind}] ${r.text}</div>`).join("") || `<div class="muted">пусто</div>`;
  }

  _exportJournal(kind) {
    const j = this._journal || [];
    let data, mime, name;
    if (kind === "csv") {
      data = "ts;kind;text\n" + j.map((r) => `${r.ts};${r.kind};${String(r.text).replace(/;/g, ",")}`).join("\n");
      mime = "text/csv"; name = "rain_techlan-journal.csv";
    } else {
      data = JSON.stringify(j, null, 2); mime = "application/json"; name = "rain_techlan-journal.json";
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([data], { type: mime })); a.download = name; a.click();
  }

  async _loadJournal() {
    try {
      const r = await this._hass.callApi("GET", `${API}/events`);
      this._journal = r.journal || [];
      const j = this._journal;
      this.$("journal").innerHTML = j.slice(-40).reverse().map((x) =>
        `<div class="jrow"><span class="muted small">${String(x.ts).slice(11, 19)}</span> ${x.text}</div>`).join("") || `<div class="muted">пусто</div>`;
    } catch (e) { /* ignore */ }
  }

  async _addInterlock() {
    const ent = this.$("ient").value.trim();
    if (!ent) return;
    const rules = ((this._state || {}).settings?.interlocks || []).concat([{
      name: ent.split(".")[1] || "правило", entity_id: ent, op: this.$("iop").value,
      value: this.$("ival").value, mode: this.$("imode").value, actions: [this.$("iact").value], enabled: true,
    }]);
    await this._hass.callApi("POST", `${API}/interlocks`, { rules });
    this.$("ient").value = "";
    await this._load();
  }

  /* ------------------------------------------------- жесты: рисование/движение/размер */
  _installGestures() {
    const box = this.$("frames");
    const pos = (ev, ovl) => {
      const r = ovl.getBoundingClientRect();
      return { x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
               y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)) };
    };
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

    box.addEventListener("pointerdown", (ev) => {
      const ovl = ev.target.closest(".ovl");
      if (!ovl) return;
      const camId = ovl.dataset.cam;
      if (this._drawing) {
        this._gesture = true;
        this._drag = { mode: "new", cam: camId, ovl, start: pos(ev, ovl) };
        ovl.setPointerCapture(ev.pointerId);
        return;
      }
      if (!this._editing) return;                     // правка зон только по кнопке
      const el = ev.target.closest(".zone");
      if (!el || el.dataset.cam !== camId) return;
      const z = ((this._state || {}).settings?.zones || []).find((x) => x.id === el.dataset.z);
      if (!z) return;
      this._gesture = true;
      this._drag = { mode: ev.target.classList.contains("rz") ? "resize" : "move",
                     cam: camId, ovl, orig: { ...z }, start: pos(ev, ovl), el };
      ovl.setPointerCapture(ev.pointerId);
    });

    box.addEventListener("pointermove", (ev) => {
      const d = this._drag;
      if (!d) return;
      const p = pos(ev, d.ovl), s0 = d.start;
      if (d.mode === "new") {
        const rect = { x: Math.min(s0.x, p.x), y: Math.min(s0.y, p.y), w: Math.abs(p.x - s0.x), h: Math.abs(p.y - s0.y) };
        if (rect.w < 0.01 || rect.h < 0.01) return;
        d.last = rect;
        d.ovl.querySelectorAll(".zone.new").forEach((n) => n.remove());
        const el = document.createElement("div");
        el.className = "zone new";
        el.style.cssText = `left:${rect.x * 100}%;top:${rect.y * 100}%;width:${rect.w * 100}%;height:${rect.h * 100}%;border-style:dashed`;
        d.ovl.appendChild(el);
        return;
      }
      const o = d.orig;
      const z = { ...o };
      if (d.mode === "move") {
        z.x = clamp(o.x + (p.x - s0.x), 0, 1 - o.w);
        z.y = clamp(o.y + (p.y - s0.y), 0, 1 - o.h);
      } else {
        z.w = clamp(o.w + (p.x - s0.x), 0.03, 1 - o.x);
        z.h = clamp(o.h + (p.y - s0.y), 0.03, 1 - o.y);
      }
      d.el.style.left = z.x * 100 + "%"; d.el.style.top = z.y * 100 + "%";
      d.el.style.width = z.w * 100 + "%"; d.el.style.height = z.h * 100 + "%";
      d.last = z;
    });

    const finish = async () => {
      const d = this._drag;
      this._drag = null;
      if (!d) return;
      if (d.mode === "new") {
        d.ovl.querySelectorAll(".zone.new").forEach((n) => n.remove());
        this._gesture = false;
        if (!d.last) return;
        const name = prompt("Имя зоны:", "Зона");
        if (name === null) return;
        const zones = ((this._state || {}).settings?.zones || []).concat([{
          id: "z" + Date.now().toString(36), name, camera: d.cam, ...d.last,
        }]);
        this._drawing = false;
        this.$("draw").classList.remove("primary");
        this.$("draw").textContent = "＋ Добавить зону";
        await this._save({ zones });
        return;
      }
      this._gesture = false;
      if (!d.last) return;
      const zones = ((this._state || {}).settings?.zones || []).map((x) => (x.id === d.last.id ? d.last : x));
      await this._save({ zones });
    };
    box.addEventListener("pointerup", finish);
    box.addEventListener("pointercancel", finish);
  }
}

customElements.define("rain-techlan-panel", RainTechlanPanel);
