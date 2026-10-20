"use strict";

// localStorage только для удобств (язык, кэш справочников); может быть недоступен
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* приватный режим */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// ---------- Состояние ----------
const state = {
  lang: store.get("kh.lang") || "ru",
  me: store.get("kh.me"),
  vehicles: [],
  fields: [],
  serverTrips: [],      // рейсы за сегодня с сервера
  outbox: [],           // рейсы, ещё не принятые сервером
  online: navigator.onLine,
  tareManual: false,
  editing: null,
  tab: "entry",
};

// ---------- Утилиты ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtKg = (n) => Number(n).toLocaleString("ru-RU");
const fmtT = (kg) => (Number(kg) / 1000).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const pad = (n) => String(n).padStart(2, "0");
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const localDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() :
  "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));

function t(key, ...args) {
  const v = (I18N[state.lang] || I18N.ru)[key] ?? I18N.ru[key] ?? key;
  return typeof v === "function" ? v(...args) : v;
}

let toastTimer;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

// ---------- API ----------
class NetworkError extends Error {}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    setOnline(false);
    throw new NetworkError("network");
  }
  setOnline(true);
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (res.status === 401 && path !== "/api/auth/login") onUnauthorized();
  return { ok: res.ok, status: res.status, data };
}

function setOnline(v) {
  state.online = v;
  renderPill();
}

// ---------- Язык (НФТ-08) ----------
function applyI18n() {
  document.documentElement.lang = state.lang === "kk" ? "kk" : "ru";
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll(".lang-toggle").forEach((b) => {
    b.textContent = state.lang === "ru" ? "ҚАЗ" : "РУС";
    b.setAttribute("aria-label", state.lang === "ru" ? "Қазақ тілі" : "Русский язык");
  });
  document.title = `${t("tab_entry")} · ${t("brand")}`;
  renderAll();
}

function toggleLang() {
  state.lang = state.lang === "ru" ? "kk" : "ru";
  store.set("kh.lang", state.lang);
  applyI18n();
}

// ---------- Вход ----------
function showLogin(message) {
  $("appView").hidden = true;
  $("loginView").hidden = false;
  $("loginError").textContent = message || "";
  $("loginInput").focus();
}

function showApp() {
  $("loginView").hidden = true;
  $("appView").hidden = false;
  $("userName").textContent = state.me ? state.me.name : "";
  document.querySelectorAll("[data-admin]").forEach((el) => { el.hidden = !(state.me && state.me.role === "admin"); });
}

function onUnauthorized() {
  state.me = null;
  store.del("kh.me");
  showLogin(t("unauthorized"));
}

async function doLogin(e) {
  e.preventDefault();
  const login = $("loginInput").value.trim();
  const password = $("passwordInput").value;
  if (!login || !password) { $("loginError").textContent = t("required"); return; }
  try {
    const r = await api("POST", "/api/auth/login", { login, password });
    if (!r.ok) { $("loginError").textContent = t(r.data?.code || "server"); return; }
    state.me = r.data;
    store.set("kh.me", r.data);
    $("passwordInput").value = "";
    await startApp();
  } catch {
    $("loginError").textContent = t("network");
  }
}

async function doLogout() {
  try { await api("POST", "/api/auth/logout"); } catch { /* офлайн — всё равно выходим локально */ }
  state.me = null;
  store.del("kh.me");
  showLogin();
}

// ---------- Справочники ----------
async function loadCatalog() {
  try {
    const [v, f] = await Promise.all([api("GET", "/api/vehicles"), api("GET", "/api/fields")]);
    if (v.ok && f.ok) {
      state.vehicles = v.data;
      state.fields = f.data;
      store.set("kh.catalog", { vehicles: v.data, fields: f.data });
      return;
    }
  } catch { /* офлайн — берём из кэша */ }
  const cached = store.get("kh.catalog");
  if (cached) { state.vehicles = cached.vehicles; state.fields = cached.fields; }
}

// ---------- Форма рейса (ZER-1..7) ----------
function renderVehicleButtons() {
  const selected = selectedVehicleId();
  $("vehicleButtons").innerHTML = state.vehicles.map((v) => `
    <input type="radio" name="vehicle" id="veh-${v.id}" value="${v.id}" ${v.id === selected ? "checked" : ""}>
    <label for="veh-${v.id}"><b>${esc(v.name)}</b><small>${v.tare_kg ? `${t("tare")} ${fmtKg(v.tare_kg)} ${t("kg")}` : "—"}</small></label>
  `).join("");
}

function renderFieldSelect() {
  const sel = $("fieldSelect");
  const current = sel.value;
  sel.innerHTML = `<option value="">${esc(t("choose_field"))}</option>` +
    state.fields.map((f) => `<option value="${f.id}">${esc(f.name)}${f.crop ? ` · ${esc(f.crop)}` : ""}</option>`).join("");
  sel.value = current;
}

function selectedVehicleId() {
  const r = document.querySelector('input[name="vehicle"]:checked');
  return r ? Number(r.value) : null;
}

function syncTareFromVehicle() {
  const v = state.vehicles.find((x) => x.id === selectedVehicleId());
  const input = $("tareInput");
  if (!state.tareManual) {
    input.value = v && v.tare_kg ? v.tare_kg : "";
  }
  const noTare = v && !v.tare_kg;
  input.readOnly = !(state.tareManual || noTare);
  $("tareEditBtn").hidden = state.tareManual || noTare;
  $("tareHint").textContent = noTare ? t("tare_not_set") : state.tareManual ? t("tare_manual") : t("tare_from_vehicle");
  recalcNetto();
}

const parseKg = (s) => (/^\d+$/.test(String(s).trim()) ? Number(s) : NaN);

function recalcNetto() {
  const g = parseKg($("grossInput").value);
  const ta = parseKg($("tareInput").value);
  const out = $("nettoOut");
  out.classList.remove("bad");
  if (Number.isFinite(g) && Number.isFinite(ta)) {
    if (g > ta) out.textContent = `${fmtKg(g - ta)} ${t("kg")}`;
    else { out.textContent = t("tare_ge_gross"); out.classList.add("bad"); }
  } else {
    out.textContent = `— ${t("kg")}`;
  }
}

function clearErrors(form) {
  form.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
  form.querySelectorAll("[data-error-for]").forEach((el) => { el.textContent = ""; });
  $("formError").textContent = "";
}

function showFieldError(field, msg) {
  const map = { vehicle_id: "vehicleButtons", field_id: "fieldSelect", gross_kg: "grossInput", tare_kg: "tareInput" };
  const el = $(map[field]);
  if (el) el.classList.add("invalid");
  const p = document.querySelector(`[data-error-for="${field}"]`);
  if (p) p.textContent = msg;
}

function clearFieldError(field) {
  const map = { vehicle_id: "vehicleButtons", field_id: "fieldSelect", gross_kg: "grossInput", tare_kg: "tareInput" };
  $(map[field])?.classList.remove("invalid");
  const p = document.querySelector(`[data-error-for="${field}"]`);
  if (p) p.textContent = "";
}

function validateTrip() {
  const errors = {};
  const vehicle_id = selectedVehicleId();
  const field_id = Number($("fieldSelect").value) || null;
  const gross = parseKg($("grossInput").value);
  const tare = parseKg($("tareInput").value);
  if (!vehicle_id) errors.vehicle_id = t("unknown_vehicle");
  if (!field_id) errors.field_id = t("unknown_field");
  if ($("grossInput").value.trim() === "") errors.gross_kg = t("gross_required");
  else if (!(gross > 0 && gross <= 100000)) errors.gross_kg = t("positive_number");
  if (!(tare > 0 && tare <= 100000)) errors.tare_kg = vehicle_id ? t("tare_missing") : t("required");
  else if (gross > 0 && tare >= gross) errors.gross_kg = t("tare_ge_gross");
  return { errors, body: { vehicle_id, field_id, gross_kg: gross, tare_kg: tare } };
}

async function saveTrip(e) {
  e.preventDefault();
  const form = $("tripForm");
  clearErrors(form);
  const { errors, body } = validateTrip();
  const keys = Object.keys(errors);
  if (keys.length) {
    keys.forEach((k) => showFieldError(k, errors[k]));   // данные в форме сохраняются (ZER-1)
    const first = { vehicle_id: "vehicleButtons", field_id: "fieldSelect", gross_kg: "grossInput", tare_kg: "tareInput" }[keys[0]];
    (first === "vehicleButtons" ? document.querySelector('input[name="vehicle"]') : $(first))?.focus();
    return;
  }

  const item = {
    id: uuid(),
    body: { ...body, weighed_at: new Date().toISOString() },   // время фиксируется автоматически (ZER-4)
    vehicle_name: state.vehicles.find((v) => v.id === body.vehicle_id)?.name,
    field_name: state.fields.find((f) => f.id === body.field_id)?.name,
    status: "pending",
  };
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await Outbox.put(item);   // сначала на телефон — запись не теряется при пропадании связи
    // Сбрасываем брутто и машину; поле оставляем — машины обычно возят с одного поля подряд
    $("grossInput").value = "";
    document.querySelectorAll('input[name="vehicle"]').forEach((r) => { r.checked = false; });
    state.tareManual = false;
    syncTareFromVehicle();
    await refreshOutbox();
    await flush();
    const stillPending = state.outbox.find((x) => x.id === item.id);
    if (!stillPending) toast(t("trip_saved"));
    else if (stillPending.status === "rejected") toast(t(stillPending.error || "validation_error"));
    else toast(t("trip_saved_offline"));
  } finally {
    btn.disabled = false;
  }
}

// ---------- Синхронизация ----------
let flushing = false;
async function flush() {
  if (flushing) return;
  flushing = true;
  let sent = 0;
  try {
    const items = (await Outbox.all()).filter((x) => x.status === "pending");
    for (const item of items) {
      let r;
      try { r = await api("PUT", `/api/trips/${item.id}`, item.body); } catch { break; }  // нет связи — ждём
      if (r.ok) { await Outbox.remove(item.id); sent++; continue; }
      if (r.status === 401 || r.status >= 500) break;
      // 4xx: сервер не принял данные — помечаем, чтобы не отправлять бесконечно
      await Outbox.put({ ...item, status: "rejected", error: r.data?.code || "validation_error" });
    }
  } finally {
    flushing = false;
  }
  await refreshOutbox();
  if (sent) await refreshToday();
}

async function refreshOutbox() {
  try { state.outbox = await Outbox.all(); } catch { state.outbox = []; }
  renderPill();
  renderTripLog();
}

function renderPill() {
  const pill = $("syncPill");
  if (!pill) return;
  const pending = state.outbox.filter((x) => x.status === "pending").length;
  pill.className = "pill " + (state.online ? "online" : "offline");
  pill.textContent = pending ? t("pending_count", pending) : state.online ? t("online") : t("offline");
}

// ---------- Рейсы сегодня (ZER-8, ZER-10) ----------
async function refreshToday() {
  try {
    const r = await api("GET", "/api/trips");
    if (r.ok) state.serverTrips = r.data.trips;
  } catch { /* офлайн — показываем что есть */ }
  renderTripLog();
}

function renderTripLog() {
  const today = localDate(new Date());
  const serverIds = new Set(state.serverTrips.map((x) => x.id));
  const rows = [
    ...state.serverTrips.map((x) => ({ ...x, kind: "server", sortKey: x.weighed_at })),
    ...state.outbox
      .filter((x) => !serverIds.has(x.id) && localDate(new Date(x.body.weighed_at)) === today)
      .map((x) => ({
        id: x.id, kind: x.status, sortKey: x.body.weighed_at,
        weighed_local: hhmm(new Date(x.body.weighed_at)),
        vehicle_name: x.vehicle_name, field_name: x.field_name,
        net_kg: x.body.gross_kg - x.body.tare_kg,
      })),
  ].sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  $("tripLog").innerHTML = rows.map((r, i) => {
    const badge = r.kind === "pending" ? `<span class="badge pending">${esc(t("pending"))}</span>`
      : r.kind === "rejected" ? `<span class="badge rejected">${esc(t("rejected"))}</span>`
      : r.is_corrected ? `<span class="badge corrected" title="${esc(t("corrected"))}">${esc(t("corrected"))}</span>` : "";
    return `<tr data-id="${r.id}" data-kind="${r.kind}" class="${r.kind}" tabindex="0">
      <td>${i + 1}</td><td>${esc(r.weighed_local)}</td><td>${esc(r.vehicle_name)}</td>
      <td>${esc(r.field_name)}</td><td class="num">${fmtKg(r.net_kg)}${badge}</td></tr>`;
  }).join("");
  $("tripLogEmpty").hidden = rows.length > 0;
  $("todayCount").textContent = rows.length ? `· ${t("trips_count", rows.length)}` : "";
}

async function onTripRowActivate(row) {
  const id = row.dataset.id;
  const kind = row.dataset.kind;
  if (kind === "pending") { toast(t("pending_cannot_edit")); return; }
  if (kind === "rejected") {
    const item = state.outbox.find((x) => x.id === id);
    if (confirm(`${t("rejected_delete")}\n\n${t(item?.error || "validation_error")}`)) {
      await Outbox.remove(id);
      await refreshOutbox();
    }
    return;
  }
  if (!state.online) { toast(t("edit_needs_online")); return; }
  openEdit(state.serverTrips.find((x) => x.id === id));
}

// ---------- Исправление (ZER-9) ----------
function openEdit(trip) {
  if (!trip) return;
  state.editing = trip;
  $("editVehicle").innerHTML = state.vehicles.map((v) => `<option value="${v.id}">${esc(v.name)}</option>`).join("");
  $("editField").innerHTML = state.fields.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join("");
  $("editVehicle").value = trip.vehicle_id;
  $("editField").value = trip.field_id;
  $("editGross").value = trip.gross_kg;
  $("editTare").value = trip.tare_kg;
  $("editMeta").textContent = `${trip.weighed_local} · ${trip.vehicle_name} · ${trip.field_name}`;
  $("editError").textContent = "";
  recalcEdit();
  $("editDialog").showModal();
}

function recalcEdit() {
  const g = parseKg($("editGross").value), ta = parseKg($("editTare").value);
  $("editNetto").textContent = Number.isFinite(g) && Number.isFinite(ta) && g > ta ? `${fmtKg(g - ta)} ${t("kg")}` : "—";
}

async function saveEdit(e) {
  e.preventDefault();
  const trip = state.editing;
  const g = parseKg($("editGross").value), ta = parseKg($("editTare").value);
  if (!(g > 0) || !(ta > 0)) { $("editError").textContent = t("positive_number"); return; }
  if (ta >= g) { $("editError").textContent = t("tare_ge_gross"); return; }
  const next = { vehicle_id: Number($("editVehicle").value), field_id: Number($("editField").value), gross_kg: g, tare_kg: ta };
  const patch = Object.fromEntries(Object.entries(next).filter(([k, v]) => trip[k] !== v));
  if (!Object.keys(patch).length) { $("editDialog").close(); return; }
  try {
    const r = await api("PATCH", `/api/trips/${trip.id}`, patch);
    if (!r.ok) { $("editError").textContent = t(r.data?.code || "server"); return; }
    $("editDialog").close();
    toast(t("trip_corrected"));
    await refreshToday();
  } catch {
    $("editError").textContent = t("edit_needs_online");
  }
}

// ---------- Итог дня (ZER-11..13) ----------
function barRows(items, kind) {
  const max = Math.max(1, ...items.map((x) => x.net_kg));
  return items.map((x) => `
    <div class="bar-row ${x.trips ? "" : "zero"}">
      <span class="name">${esc(x.name)}</span>
      <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${(x.net_kg / max * 100).toFixed(0)}%"></div></div>
      <span class="val num">${x.trips ? `${fmtKg(x.net_kg)} ${t("kg")}<small>${esc(t("trips_count", x.trips))}</small>`
        : (kind === "field" ? esc(t("no_trips_short")) : `0 ${t("kg")}<small>${esc(t("trips_count", 0))}</small>`)}</span>
    </div>`).join("");
}

async function loadDay() {
  const date = $("dayDate").value || localDate(new Date());
  try {
    const r = await api("GET", `/api/summary/day?date=${date}`);
    if (!r.ok) return;
    const s = r.data;
    $("dayTotal").textContent = fmtT(s.net_kg);
    $("dayTrips").textContent = `${t("trips_count", s.trips)} · ${fmtKg(s.net_kg)} ${t("kg")}`;
    $("byVehicle").innerHTML = barRows(s.by_vehicle, "vehicle");
    $("byField").innerHTML = barRows(s.by_field, "field");
  } catch {
    $("dayTrips").textContent = t("network");
  }
  const pending = state.outbox.filter((x) => localDate(new Date(x.body.weighed_at)) === date).length;
  $("dayPending").hidden = !pending;
  $("dayPending").textContent = pending ? t("pending_note", pending) : "";
}

// ---------- Сезон ----------
function seasonRows(items) {
  return items.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${x.trips}</td><td class="num">${fmtT(x.net_kg)}</td></tr>`).join("");
}

async function loadSeason() {
  const year = Number($("seasonYear").value) || new Date().getFullYear();
  try {
    const r = await api("GET", `/api/summary/season?year=${year}`);
    if (!r.ok) return;
    $("seasonTotal").textContent = fmtT(r.data.net_kg);
    $("seasonFields").innerHTML = seasonRows(r.data.by_field);
    $("seasonVehicles").innerHTML = seasonRows(r.data.by_vehicle);
  } catch { /* офлайн */ }
}

// ---------- Машины: тара (администратор) ----------
function renderVehicleTare() {
  $("vehicleTareList").innerHTML = state.vehicles.map((v) => `
    <form class="tare-item" data-vehicle="${v.id}">
      <label for="tare-${v.id}"><b>${esc(v.name)}</b></label>
      <input id="tare-${v.id}" type="number" inputmode="numeric" min="1" step="1" value="${v.tare_kg ?? ""}" aria-label="${esc(t("tare"))}, ${esc(t("kg"))}">
      <button class="link-btn" type="submit">${esc(t("save"))}</button>
    </form>`).join("");
}

async function saveVehicleTare(form) {
  const id = Number(form.dataset.vehicle);
  const value = parseKg(form.querySelector("input").value);
  if (!(value > 0)) { toast(t("positive_number")); return; }
  try {
    const r = await api("PATCH", `/api/vehicles/${id}`, { tare_kg: value });
    if (!r.ok) { toast(t(r.data?.code || "server")); return; }
    await loadCatalog();
    renderAll();
    toast(`${r.data.name}: ${t("saved")}`);
  } catch { toast(t("network")); }
}

// ---------- Вкладки ----------
function switchTab(name) {
  state.tab = name;
  document.querySelectorAll("nav button").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on);
  });
  document.querySelectorAll("main section").forEach((s) => s.classList.toggle("active", s.id === name));
  if (name === "day") loadDay();
  if (name === "season") loadSeason();
  if (name === "entry") refreshToday();
  if (name === "vehicles") renderVehicleTare();
}

function renderAll() {
  if ($("appView").hidden) return;
  renderVehicleButtons();
  renderFieldSelect();
  syncTareFromVehicle();
  renderTripLog();
  renderPill();
  if (state.tab === "day") loadDay();
  if (state.tab === "season") loadSeason();
  if (state.tab === "vehicles") renderVehicleTare();
}

// ---------- Запуск ----------
async function startApp() {
  showApp();
  await loadCatalog();
  await refreshOutbox();
  applyI18n();
  await refreshToday();
  flush();
}

async function boot() {
  applyI18n();
  $("dayDate").value = localDate(new Date());
  $("seasonYear").value = new Date().getFullYear();
  const tick = () => { $("nowTime").textContent = hhmm(new Date()); };
  tick(); setInterval(tick, 15000);

  try {
    const r = await api("GET", "/api/auth/me");
    if (r.ok) { state.me = r.data; store.set("kh.me", r.data); await startApp(); }
    else showLogin();
  } catch {
    // Нет связи: если раньше уже входили — работаем офлайн
    if (state.me) await startApp();
    else showLogin(t("network"));
  }
}

// ---------- События ----------
document.addEventListener("DOMContentLoaded", () => {
  $("loginForm").addEventListener("submit", doLogin);
  $("logoutBtn").addEventListener("click", doLogout);
  document.querySelectorAll(".lang-toggle").forEach((b) => b.addEventListener("click", toggleLang));
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

  $("tripForm").addEventListener("submit", saveTrip);
  $("vehicleButtons").addEventListener("change", () => {
    clearFieldError("vehicle_id");
    clearFieldError("tare_kg");
    syncTareFromVehicle();
  });
  $("fieldSelect").addEventListener("change", () => clearFieldError("field_id"));
  $("grossInput").addEventListener("input", () => { clearFieldError("gross_kg"); recalcNetto(); });
  $("tareInput").addEventListener("input", () => { clearFieldError("tare_kg"); clearFieldError("gross_kg"); recalcNetto(); });
  $("tareEditBtn").addEventListener("click", () => {
    state.tareManual = true;
    syncTareFromVehicle();
    $("tareInput").focus();
    $("tareInput").select();
  });

  $("tripLog").addEventListener("click", (e) => { const row = e.target.closest("tr[data-id]"); if (row) onTripRowActivate(row); });
  $("tripLog").addEventListener("keydown", (e) => {
    const row = e.target.closest("tr[data-id]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onTripRowActivate(row); }
  });

  $("editForm").addEventListener("submit", saveEdit);
  $("editCancel").addEventListener("click", () => $("editDialog").close());
  ["editGross", "editTare"].forEach((id) => $(id).addEventListener("input", recalcEdit));

  $("dayDate").addEventListener("change", loadDay);
  $("seasonYear").addEventListener("change", loadSeason);
  $("vehicleTareList").addEventListener("submit", (e) => { e.preventDefault(); saveVehicleTare(e.target); });

  window.addEventListener("online", () => { setOnline(true); flush(); });
  window.addEventListener("offline", () => setOnline(false));
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.me) { flush(); refreshToday(); } });
  setInterval(() => { if (state.me) flush(); }, 20000);

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  boot();
});
