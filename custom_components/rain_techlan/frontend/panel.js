/* Панель «Полив и осадки» (rain_techlan): зоны на кадре, оценка дождя, полив, запреты. */
const API = "rain_techlan";

class RainTechlanPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._state = null;
    this._cam = "";
    this._drawing = false;
    this._drag = null;
    this._zone = null;
    this._busy = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._build();
    this._load();
  }
  set panel(p) { this._panel = p; }

  _build() {
    this.innerHTML = `
      <style>
        :host { display:block; padding:12px 16px 32px; color: var(--primary-text-color);
                background: var(--primary-background-color); font-family: var(--paper-font-body1_-_font-family, sans-serif); }
        h1 { font-size: 20px; margin: 0 0 4px; }
        .muted { color: var(--secondary-text-color); font-size: 13px; }
        .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
        .card { background: var(--card-background-color); border:1px solid var(--divider-color);
                border-radius:12px; padding:12px 14px; margin:10px 0; }
        .grid { display:grid; gap:12px; grid-template-columns: minmax(0,2fr) minmax(0,1fr); }
        @media (max-width: 900px){ .grid{ grid-template-columns: 1fr; } }
        .badge { display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:13px; }
        .dry { background:#15803d22; color:#15803d; border:1px solid #15803d55; }
        .maybe { background:#b4530922; color:#b45309; border:1px solid #b4530955; }
        .wet { background:#b91c1c22; color:#b91c1c; border:1px solid #b91c1c55; }
        .mutedbadge { background:#64748b22; color:#64748b; border:1px solid #64748b55; }
        .wrap { position:relative; display:inline-block; max-width:100%; }
        .wrap img { max-width:100%; border-radius:10px; display:block; }
        .ovl { position:absolute; inset:0; }
        .zone { position:absolute; border:2px solid #40c878; border-radius:4px; }
        .zone.wet { border-color:#ff4040; }
        .zone span { position:absolute; top:2px; left:4px; font-size:11px; color:#fff;
                     text-shadow:0 1px 2px #000; }
        button { cursor:pointer; border-radius:8px; border:1px solid var(--divider-color);
                 background: var(--secondary-background-color); color: var(--primary-text-color);
                 padding:6px 12px; font-size:13px; }
        button.primary { background:#0f766e; border-color:#0f766e; color:#fff; }
        button.danger { background:#b91c1c; border-color:#b91c1c; color:#fff; }
        button:disabled { opacity:.5; cursor:default; }
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
          <button id="menuBtn" title="Меню">⋯ Меню</button>
          <div id="menuBox" style="display:none; position:absolute; right:16px; margin-top:34px; z-index:10;
               background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:10px; padding:6px; min-width:190px">
            <button id="mRefresh" style="display:block; width:100%; text-align:left; border:none; background:none">⟳ Обновить страницу</button>
            <button id="mHome" style="display:block; width:100%; text-align:left; border:none; background:none">🏠 На главную HA</button>
            <button id="mLogout" style="display:block; width:100%; text-align:left; border:none; background:none; color:#b91c1c">⎋ Выход (выйти из HA)</button>
          </div>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="row" style="justify-content:space-between">
            <div class="row">
              <label class="small">Камера:</label>
              <select id="camsel"></select>
              <button id="draw">＋ Добавить зону</button>
            </div>
            <div class="muted small" id="frameinfo"></div>
          </div>
          <div class="wrap" id="wrap" style="margin-top:10px">
            <img id="frame" alt="кадр камеры">
            <div class="ovl" id="ovl"></div>
          </div>
          <div class="muted small" style="margin-top:6px" id="errs"></div>
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
              <input id="truth" placeholder="binary_sensor.…" style="min-width:220px">
              <button id="truthset">ОК</button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <b>Журнал</b>
          <div id="journal" style="max-height:260px; overflow:auto"></div>
        </div>
        <div class="card">
          <b>Запреты полива</b>
          <div class="muted small">Правило: сенсор → условие → блокировка запуска.</div>
          <div id="ilist"></div>
          <div class="row" style="margin-top:8px">
            <input id="ient" placeholder="sensor.…" style="min-width:150px">
            <select id="iop">
              <option value="is_on">включён</option><option value="is_off">выключен</option>
              <option value="above">&gt;</option><option value="below">&lt;</option>
            </select>
            <input id="ival" value="0" style="width:70px">
            <select id="iact"><option value="all">весь полив</option><option value="start_zone">запуск зоны</option><option value="start_program">запуск программы</option></select>
            <select id="imode"><option value="block">блокировать</option><option value="warn">предупредить</option></select>
            <button id="iadd">＋ Запрет</button>
          </div>
        </div>
      </div>
    `;
    this.$ = (id) => this.querySelector("#" + id);
    this.$("scan").onclick = () => this._scan();
    this.$("menuBtn").onclick = (ev) => {
      ev.stopPropagation();
      const box = this.$("menuBox");
      box.style.display = box.style.display === "none" ? "block" : "none";
    };
    this.addEventListener("click", (ev) => {
      if (!ev.target.closest("#menuBox") && ev.target.id !== "menuBtn") this.$("menuBox").style.display = "none";
    });
    this.$("mRefresh").onclick = () => window.location.reload();
    this.$("mHome").onclick = () => { this.$("menuBox").style.display = "none"; window.location.href = "/lovelace/0"; };
    this.$("mLogout").onclick = async () => {
      this.$("menuBox").style.display = "none";
      if (!confirm("Выйти из Home Assistant?")) return;
      try { if (this._hass.auth && this._hass.auth.logout) { await this._hass.auth.logout(); return; } } catch (e) { /* фолбэк ниже */ }
      window.location.href = "/logout";
    };
    this.$("learn").onclick = () => this._learn();
    this.$("draw").onclick = () => { this._drawing = !this._drawing; this.$("draw").classList.toggle("primary", this._drawing);
      this.$("draw").textContent = this._drawing ? "✕ Отменить рисование" : "＋ Добавить зону"; };
    this.$("camsel").onchange = (e) => { this._cam = e.target.value; this._render(); };
    this.$("stopall").onclick = () => this._svc("rain_techlan", "stop_all_zones", {});
    this.$("rdset").onclick = () => this._svc("rainbird_iq4", "set_rain_delay", { days: Number(this.$("rd").value || 0) })
      .catch(() => this._svc("rain_techlan", "set_rain_delay", { days: Number(this.$("rd").value || 0) }));
    this.$("thr").oninput = (e) => { this.$("thrv").textContent = e.target.value; };
    this.$("thr").onchange = (e) => this._save({ rain_threshold: Number(e.target.value) });
    this.$("truthset").onclick = () => this._save({ truth_entity: this.$("truth").value.trim() });
    this.$("iadd").onclick = () => this._addInterlock();
    this._installZoneDrag();
  }

  /* ------------------------------------------------------------- данные */
  async _load() {
    if (this._busy) return;
    try {
      this._state = await this._hass.callApi("GET", `${API}/state`);
      this._render();
    } catch (e) { this.$("sub").textContent = "Ошибка загрузки: " + e; }
  }

  async _scan() {
    this._busy = true; this.$("scan").textContent = "Сканирую…";
    try { await this._hass.callApi("POST", `${API}/scan`, {}); }
    catch (e) { this.$("errs").textContent = "Ошибка скана: " + e; }
    this._busy = false; this.$("scan").textContent = "Проверить сейчас";
    await this._load();
  }
  async _learn() {
    try { const r = await this._hass.callApi("POST", `${API}/learn`, { hours: 24 });
      this.$("errs").textContent = `Помечено «сухо»: ${r.marked}, групп эталона: ${r.groups}`;
    } catch (e) { this.$("errs").textContent = "Ошибка: " + e; }
    await this._load();
  }
  async _save(patch) {
    try { await this._hass.callApi("POST", `${API}/settings`, patch); await this._load(); }
    catch (e) { this.$("errs").textContent = "Ошибка сохранения: " + e; }
  }
  async _svc(domain, service, data) { return this._hass.callService(domain, service, data); }

  /* ------------------------------------------------------------- отрисовка */
  _render() {
    const st = this._state || {};
    const det = st.detector || {}, set = st.settings || {};
    const sum = det.summary || {};
    const v = this.$("verdict");
    const cls = sum.verdict === "дождь" ? "wet" : sum.verdict === "возможно" ? "maybe" : sum.verdict === "сухо" ? "dry" : "mutedbadge";
    v.className = "badge " + cls; v.textContent = sum.verdict || "нет данных";
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

    // камеры
    const cams = det.cameras || set.cameras || [];
    if (!this._cam && cams.length) this._cam = cams[0].id;
    this.$("camsel").innerHTML = cams.map((c) => `<option value="${c.id}" ${c.id === this._cam ? "selected" : ""}>${c.name || c.id}</option>`).join("") || `<option>нет камер</option>`;
    const frame = this.$("frame");
    // 1) статика панели (без авторизации) → 2) image_proxy (сессия) → 3) перезапросить проход
    frame.onerror = () => {
      const step = frame.dataset.step || "0";
      if (step === "0") {
        frame.dataset.step = "1";
        const camName2 = (cams.find((c) => c.id === this._cam) || {}).name || "";
        let imgEnt = null;
        for (const [eid, obj] of Object.entries(this._hass.states)) {
          if (eid.startsWith("image.") && String(obj.attributes?.friendly_name || "").includes(camName2)) { imgEnt = eid; break; }
        }
        if (imgEnt) frame.src = `/api/image_proxy/${imgEnt}?t=${Date.now()}`;
      } else {
        frame.onerror = null;
        this._scan();
      }
    };
    if (frame.dataset.step !== "1") {
      frame.dataset.step = "0";
      frame.src = `/rain_techlan/last_${encodeURIComponent(this._cam)}.jpg?t=${Date.now()}`;
    }
    this.$("frameinfo").textContent = cams.find((c) => c.id === this._cam)?.url ? "" : "";

    // зоны поверх кадра
    const zones = (set.zones || []).filter((z) => !z.camera || z.camera === this._cam);
    const res = det.results || [];
    this.$("ovl").innerHTML = zones.map((z) => {
      const r = res.find((x) => x.camera === this._cam && x.zone === z.id);
      const wet = r && (r.score >= (set.threshold ?? 0.5));
      return `<div class="zone ${wet ? "wet" : ""}" data-z="${z.id}"
        style="left:${z.x * 100}%; top:${z.y * 100}%; width:${z.w * 100}%; height:${z.h * 100}%">
        <span>${z.name}${r ? " • " + r.score : ""}</span></div>`;
    }).join("");

    const tb = this.$("zones").querySelector("tbody");
    tb.innerHTML = (set.zones || []).map((z) => {
      const r = res.find((x) => x.zone === z.id);
      return `<tr><td>${z.name}</td><td class="small">${z.camera || "все"}</td>
        <td>${r ? r.score : "—"}</td>
        <td class="row"><button data-edit="${z.id}">✎</button><button data-del="${z.id}" class="danger">✕</button></td></tr>`;
    }).join("") || `<tr><td colspan="4" class="muted">зон нет — нарисуйте на кадре</td></tr>`;
    tb.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
      const zones2 = (set.zones || []).filter((z) => z.id !== b.dataset.del);
      this._save({ zones: zones2 });
    });
    tb.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => {
      const z = (set.zones || []).find((x) => x.id === b.dataset.edit);
      const name = prompt("Имя зоны:", z?.name || "");
      if (name === null) return;
      const zones2 = (set.zones || []).map((x) => x.id === b.dataset.edit ? { ...x, name } : x);
      this._save({ zones: zones2 });
    });

    // полив
    const irr = [];
    for (const [k, val] of Object.entries(this._hass.states)) {
      if (/^sensor\.oroshenie_(station_\d+|rain_delay|controller_mode)/.test(k)) irr.push(`${k.split(".")[1]}=${val.state}`);
    }
    this.$("irr").textContent = irr.join(" · ");

    // журнал
    const j = (st.detector?.journal) || [];
    this.$("journal").innerHTML = (j.slice(-40).reverse().map((r) =>
      `<div class="jrow"><span class="muted small">${String(r.ts).slice(11, 19)}</span> ${r.text}</div>`).join("")) || `<div class="muted">пусто</div>`;
    if (j.length === 0) this._loadJournal();

    // запреты
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

  async _loadJournal() {
    try {
      const r = await this._hass.callApi("GET", `${API}/events`);
      const j = r.journal || [];
      this.$("journal").innerHTML = j.slice(-40).reverse().map((x) =>
        `<div class="jrow"><span class="muted small">${String(x.ts).slice(11, 19)}</span> ${x.text}</div>`).join("") || `<div class="muted">пусто</div>`;
    } catch (e) { /* ignore */ }
  }

  async _addInterlock() {
    const ent = this.$("ient").value.trim();
    if (!ent) return;
    const rules = (this._state?.settings?.interlocks || []).concat([{
      name: ent.split(".")[1] || "правило", entity_id: ent,
      op: this.$("iop").value, value: this.$("ival").value,
      mode: this.$("imode").value, actions: [this.$("iact").value], enabled: true,
    }]);
    await this._hass.callApi("POST", `${API}/interlocks`, { rules });
    this.$("ient").value = "";
    await this._load();
  }

  /* ------------------------------------------------------------- рисование зон */
  _installZoneDrag() {
    const ovl = this.$("ovl");
    const pos = (ev) => {
      const r = ovl.getBoundingClientRect();
      return { x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
               y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)) };
    };
    ovl.addEventListener("pointerdown", (ev) => {
      if (!this._drawing) return;
      this._drag = { start: pos(ev) };
      ovl.setPointerCapture(ev.pointerId);
    });
    ovl.addEventListener("pointermove", (ev) => {
      if (!this._drag) return;
      const p = pos(ev), s = this._drag.start;
      const rect = { x: Math.min(s.x, p.x), y: Math.min(s.y, p.y),
                     w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) };
      if (rect.w < 0.01 || rect.h < 0.01) return;
      this._drag.last = rect;
      this.$("ovl").querySelectorAll(".zone.new").forEach((n) => n.remove());
      const d = document.createElement("div");
      d.className = "zone new";
      d.style.cssText = `left:${rect.x * 100}%; top:${rect.y * 100}%; width:${rect.w * 100}%; height:${rect.h * 100}%; border-style:dashed`;
      ovl.appendChild(d);
    });
    ovl.addEventListener("pointerup", async () => {
      if (!this._drag) return;
      const rect = this._drag.last;
      this._drag = null;
      this.$("ovl").querySelectorAll(".zone.new").forEach((n) => n.remove());
      if (!rect) return;
      const name = prompt("Имя зоны:", "Зона");
      if (name === null) return;
      const zones = (this._state?.settings?.zones || []).concat([{
        id: "z" + Date.now().toString(36), name, camera: this._cam, ...rect,
      }]);
      this._drawing = false;
      this.$("draw").classList.remove("primary");
      this.$("draw").textContent = "＋ Добавить зону";
      await this._save({ zones });
    });
  }
}

customElements.define("rain-techlan-panel", RainTechlanPanel);
