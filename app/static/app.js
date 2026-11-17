"use strict";

const state = { config: {}, refs: {}, offersById: new Map(), campaign: null };

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), isError ? 6000 : 2500);
}

function errText(detail) {
  if (Array.isArray(detail)) return detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
  if (detail && typeof detail === "object") return detail.error || detail.message || JSON.stringify(detail);
  return String(detail);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error(errText(data?.detail ?? res.statusText));
  return data;
}

async function busy(btn, fn) {
  const was = btn.disabled;
  btn.disabled = true;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = was; }
}

const offerLabel = (id) => {
  const o = state.offersById.get(id);
  return o ? o.name : `#${id}`;
};
const parseId = (v) => { const m = String(v).trim().match(/^(\d+)/); return m ? Number(m[1]) : null; };

function fillSelect(sel, items, selected) {
  sel.innerHTML = items.map((i) => `<option value="${i.id}" ${i.id === selected ? "selected" : ""}>${esc(i.name)} (${i.id})</option>`).join("");
}

// ---------- init ----------
async function init() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  try {
    const [config, domains, groups, traffic_sources, offers] = await Promise.all([
      api("GET", "/api/config"),
      api("GET", "/api/refs/domains"),
      api("GET", "/api/refs/groups"),
      api("GET", "/api/refs/traffic_sources"),
      api("GET", "/api/refs/offers?active_only=false"),
    ]);
    Object.assign(state, { config });
    state.refs = { domains, groups, traffic_sources, offers };
    offers.forEach((o) => state.offersById.set(o.id, o));
    $("#offers-list").innerHTML = offers
      .filter((o) => o.state === "active")
      .map((o) => `<option value="${o.id} — ${esc(o.name)}">`).join("");
    const defaults = { domains: config.domain_id, groups: config.group_id, traffic_sources: config.traffic_source_id };
    document.querySelectorAll("select[data-ref]").forEach((s) => fillSelect(s, state.refs[s.dataset.ref], defaults[s.dataset.ref]));
    $("#geo-url").textContent = config.geo_redirect_url;
  } catch (e) {
    toast("Не удалось загрузить справочники: " + e.message, true);
  }
  $("#create-form").addEventListener("submit", onCreate);
  $("#campaign-open").addEventListener("click", () => openCampaign(parseId($("#campaign-input").value)));
  $("#campaign-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#campaign-open").click(); });
  $("#only-group").addEventListener("change", loadCampaigns);
  $("#only-mine").addEventListener("change", loadCampaigns);
  loadCampaigns();

  openFromHash();
  window.addEventListener("hashchange", openFromHash);
}

function openFromHash() {
  if (!location.hash.startsWith("#campaign-")) return;
  const id = parseId(location.hash.slice("#campaign-".length));
  if (id && id !== state.campaign?.id) { switchTab("edit"); openCampaign(id); }
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + name));
}

// ---------- creator ----------
async function onCreate(e) {
  e.preventDefault();
  const f = e.target;
  const offer_id = parseId(f.offer.value);
  if (!offer_id) return toast("Укажите ID оффера", true);
  const body = {
    name: f.name.value.trim(),
    country: f.country.value.trim().toUpperCase(),
    offer_id,
    domain_id: Number(f.domain_id.value),
    group_id: Number(f.group_id.value),
    traffic_source_id: Number(f.traffic_source_id.value),
  };
  await busy(f.querySelector("button[type=submit]"), async () => {
    const r = await api("POST", "/api/campaigns", body);
    $("#create-result").innerHTML = `
      <div class="card success">
        <h2>Кампания создана: ${esc(r.name)} <span class="badge">#${r.id}</span></h2>
        <div class="meta">
          <div>Ссылка: <b>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>` : "—"}</b></div>
          <div>Поток 1 (гео ${esc(body.country)} → Google): <b>#${r.geo_stream_id}</b></div>
          <div>Поток 2 (оффер): <b>#${r.offer_stream_id}</b></div>
          <div>Оффер: <b>${esc(offerLabel(offer_id))}</b></div>
        </div>
        <p><button class="primary" data-open="${r.id}">Открыть в редакторе</button></p>
      </div>`;
    $("#create-result [data-open]").addEventListener("click", () => { switchTab("edit"); openCampaign(r.id); });
    f.reset();
    document.querySelectorAll("#create-form select[data-ref]").forEach((s) => {
      const d = { domains: state.config.domain_id, groups: state.config.group_id, traffic_sources: state.config.traffic_source_id }[s.dataset.ref];
      if (d) s.value = d;
    });
    toast("Кампания создана");
    loadCampaigns();
  });
}

// ---------- editor ----------
async function loadCampaigns() {
  const q = new URLSearchParams();
  if ($("#only-group").checked && state.config.group_id) q.set("group_id", state.config.group_id);
  if ($("#only-mine").checked) q.set("mine", "true");
  try {
    const list = await api("GET", "/api/campaigns?" + q);
    list.sort((a, b) => b.id - a.id);
    $("#campaigns-list").innerHTML = list.map((c) => `<option value="${c.id} — ${esc(c.name)}">`).join("");
  } catch (e) { toast(e.message, true); }
}

async function openCampaign(id) {
  if (!id) return toast("Выберите кампанию", true);
  const view = $("#campaign-view");
  view.innerHTML = `<div class="card hint">Загрузка…</div>`;
  try {
    state.campaign = await api("GET", `/api/campaigns/${id}`);
    $("#campaign-input").value = `${id} — ${state.campaign.name}`;
    history.replaceState(null, "", `#campaign-${id}`);
    renderCampaign();
  } catch (e) {
    view.innerHTML = "";
    toast(e.message, true);
  }
}

function renderCampaign() {
  const c = state.campaign;
  const view = $("#campaign-view");
  view.innerHTML = `
    <div class="card" id="camp-card">
      <h2>${esc(c.name)} <span class="badge">#${c.id}</span> <span class="badge">${esc(c.state)}</span></h2>
      <div class="meta" style="margin-bottom:12px">
        <div>Ссылка: <b>${c.domain ? `<a href="${esc(c.domain + c.alias)}" target="_blank" rel="noopener">${esc(c.domain + c.alias)}</a>` : esc(c.alias)}</b></div>
        <div>Создана: <b>${esc(c.created_at)}</b></div>
      </div>
      <div class="row">
        <label class="grow">Название <input name="name" value="${esc(c.name)}"></label>
        <label>Статус <select name="state">
          <option value="active" ${c.state === "active" ? "selected" : ""}>active</option>
          <option value="disabled" ${c.state === "disabled" ? "selected" : ""}>disabled</option></select></label>
      </div>
      <div class="row">
        <label>Домен <select name="domain_id"></select></label>
        <label>Группа <select name="group_id"></select></label>
        <label>Источник <select name="traffic_source_id"></select></label>
      </div>
      <button class="primary" id="camp-save">Сохранить кампанию</button>
    </div>
    <div id="streams"></div>`;
  const card = $("#camp-card");
  fillSelect(card.querySelector("[name=domain_id]"), state.refs.domains || [], c.domain_id);
  fillSelect(card.querySelector("[name=group_id]"), state.refs.groups || [], c.group_id);
  fillSelect(card.querySelector("[name=traffic_source_id]"), state.refs.traffic_sources || [], c.traffic_source_id);
  $("#camp-save").addEventListener("click", (e) => busy(e.target, saveCampaign));

  const wrap = $("#streams");
  [...c.streams].sort((a, b) => a.position - b.position).forEach((s) => {
    const el = document.createElement("div");
    el.className = "card";
    el.dataset.stream = s.id;
    wrap.appendChild(el);
    renderStream(el, s);
  });
}

async function saveCampaign() {
  const card = $("#camp-card");
  const v = (n) => card.querySelector(`[name=${n}]`).value;
  const body = {
    name: v("name").trim(),
    state: v("state"),
    domain_id: Number(v("domain_id")),
    group_id: Number(v("group_id")),
    traffic_source_id: Number(v("traffic_source_id")),
  };
  await api("PUT", `/api/campaigns/${state.campaign.id}`, body);
  toast("Кампания сохранена");
  await openCampaign(state.campaign.id);
}

function streamTitle(s) {
  const kind = { forced: "перехват", regular: "обычный", default: "по умолчанию" }[s.type] || s.type;
  return `#${s.position} ${esc(s.name || "")} <span class="badge">${kind}</span> <span class="badge">${esc(s.schema)}</span>
          ${s.state !== "active" ? `<span class="badge">${esc(s.state)}</span>` : ""}`;
}

function renderStream(el, s) {
  const filters = (s.filters || []).map((f) => `${esc(f.name)} ${f.mode === "reject" ? "≠" : "="} ${esc((f.payload || []).join(", "))}`).join("; ");
  if (s.schema !== "landings") {
    el.innerHTML = `
      <div class="stream-head"><h3>${streamTitle(s)}</h3><span class="hint">поток #${s.id}</span></div>
      <div class="meta">
        <div>Фильтры: <b>${filters || "нет"}</b></div>
        <div>Действие: <b>${esc(s.action_type)}${s.action_payload ? " → " + esc(typeof s.action_payload === "string" ? s.action_payload : JSON.stringify(s.action_payload)) : ""}</b></div>
      </div>`;
    return;
  }
  el.innerHTML = `<div class="stream-head"><h3>${streamTitle(s)}</h3><span class="hint">поток #${s.id}</span></div><p class="hint">Загрузка черновика…</p>`;
  api("GET", `/api/streams/${s.id}/draft`)
    .then((d) => renderDraft(el, s, d, filters))
    .catch((e) => { el.querySelector("p.hint").textContent = e.message; });
}

const STATUS = {
  new: ["новый", "st-new"],
  removed: ["будет удалён", "st-removed"],
  deleted: ["удалён", "st-deleted"],
  changed: ["изменён", "st-changed"],
};

function renderDraft(el, s, d, filters) {
  const base = `/api/streams/${s.id}`;
  const rowHtml = (o) => {
    const [label, cls] = STATUS[o.status] || [];
    const was = o.status === "changed" && o.base_share !== o.share ? `<small class="was">было ${o.base_share}%</small>` : "";
    const locked = o.removed || o.hidden;
    const deleted = o.status === "deleted";
    const acts = deleted
      ? `<button class="link" data-act="restore" title="Вернуть в поток (применится после «Применить»)">Вернуть</button>
         ${o.dismissed
           ? `<button class="link" data-act="undismiss">Показать в списке</button>`
           : `<button class="link" data-act="dismiss" title="Удалён окончательно — убрать из списка (можно показать снова)">Скрыть</button>`}`
      : o.removed
        ? `<button class="link" data-act="restore">Вернуть</button>`
        : `${o.hidden ? `<button class="link" data-act="show" title="Оффер выключен в Keitaro — включить">Включить</button>` : ""}
           <button class="link danger" data-act="remove">Удалить</button>`;
    return `
      <tr data-offer="${o.offer_id}" class="${o.removed ? "row-removed" : ""} ${o.hidden ? "row-hidden" : ""} ${o.dismissed ? "row-dismissed" : ""}">
        <td>${o.offer_id}</td>
        <td class="name" title="${esc(offerLabel(o.offer_id))}">
          ${label ? `<span class="badge first ${cls}" ${deleted && o.deleted_at ? `title="удалён ${esc(o.deleted_at)}"` : ""}>${label}</span>` : ""}
          ${o.hidden && !o.removed ? '<span class="badge first st-hidden">выключен</span>' : ""}
          ${esc(offerLabel(o.offer_id))}
        </td>
        <td class="bar-cell"><span class="bar"><i style="width:${o.share}%"></i></span></td>
        <td class="num">
          ${deleted ? '<span class="hint">—</span>' : `<input type="number" min="0" max="100" step="1" value="${o.share}" ${locked ? "disabled" : ""} title="Изменить = закрепить долю">`}
          ${was}
        </td>
        <td class="pin-cell">${deleted ? "" : `
          <button class="link pin ${o.pinned ? "on" : ""}" data-act="${o.pinned ? "unpin" : "pin"}" ${locked ? "disabled" : ""}
            title="${o.pinned ? "Доля закреплена — нажмите, чтобы открепить" : "Закрепить долю"}">${o.pinned ? "🔒" : "🔓"}</button>`}
        </td>
        <td class="acts">${acts}</td>
      </tr>`;
  };

  const showDismissed = el.dataset.showDismissed === "1";
  const dismissedCount = d.offers.filter((o) => o.dismissed).length;
  const listed = d.offers.filter((o) => showDismissed || !o.dismissed);

  el.innerHTML = `
    <div class="stream-head">
      <h3>${streamTitle(s)} ${d.dirty ? '<span class="badge st-changed">черновик</span>' : ""}</h3>
      <span class="hint">поток #${s.id}${filters ? " · фильтры: " + filters : ""}</span>
    </div>
    ${d.conflict ? `<div class="warn">Поток изменился в Keitaro, пока здесь были неприменённые правки. «Отменить изменения» загрузит актуальное состояние, «Применить» перезапишет его.</div>` : ""}
    <table>
      <thead><tr><th>ID</th><th>Оффер</th><th class="bar-cell">Доля</th><th class="num">%</th><th></th><th></th></tr></thead>
      <tbody>${listed.map(rowHtml).join("") || `<tr><td colspan="6" class="hint">Офферов нет</td></tr>`}</tbody>
      <tfoot><tr><td></td><td>Итого (активные)</td><td class="bar-cell"></td>
        <td class="num total ${d.total === 100 ? "ok" : "bad"}">${d.total}%</td><td></td><td></td></tr></tfoot>
    </table>
    ${dismissedCount ? `<button class="link toggle-dismissed">${showDismissed ? "Спрятать скрытые" : `Показать скрытые удалённые (${dismissedCount})`}</button>` : ""}
    <div class="add-row">
      <input list="offers-list" placeholder="Добавить оффер: ID или название" class="add-input" autocomplete="off">
      <button class="add-btn">Добавить</button>
    </div>
    ${d.problems.length && d.dirty ? `<p class="problems">${d.problems.map(esc).join("<br>")}</p>` : ""}
    <div class="add-row actions">
      <button class="primary" data-act="commit" ${d.can_commit ? "" : "disabled"}>Применить в Keitaro</button>
      <button data-act="discard" ${d.dirty || d.conflict ? "" : "disabled"}>Отменить изменения</button>
      <button data-act="rebalance">Поровну (снять закрепления)</button>
    </div>
    <details class="history"><summary>История применений</summary><div class="history-body hint">Загрузка…</div></details>`;

  const apply = (resp) => renderDraft(el, s, resp, filters);
  const run = (btn, method, path, body, msg) =>
    busy(btn, async () => { apply(await api(method, base + path, body)); if (msg) toast(msg); });

  el.querySelectorAll("tbody tr[data-offer]").forEach((tr) => {
    const id = Number(tr.dataset.offer);
    const input = tr.querySelector("input");
    if (input) input.addEventListener("change", () => {
      const v = Math.round(Number(input.value));
      if (!(v >= 0 && v <= 100)) { input.value = d.offers.find((o) => o.offer_id === id).share; return toast("Доля от 0 до 100", true); }
      run(input, "PUT", `/draft/offers/${id}/pin`, { share: v }, `Оффер ${id}: закреплено ${v}%`);
    });
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
    tr.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "pin") return run(b, "PUT", `/draft/offers/${id}/pin`, { share: Number(input.value) });
      if (act === "unpin") return run(b, "DELETE", `/draft/offers/${id}/pin`);
      if (act === "remove") return run(b, "DELETE", `/draft/offers/${id}`, undefined, `Оффер ${id} убран (до применения)`);
      run(b, "POST", `/draft/offers/${id}/${act}`);
    }));
  });

  const tog = el.querySelector(".toggle-dismissed");
  if (tog) tog.addEventListener("click", () => { el.dataset.showDismissed = showDismissed ? "" : "1"; apply(d); });

  const addInput = el.querySelector(".add-input");
  const addBtn = el.querySelector(".add-btn");
  addInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addBtn.click(); });
  addBtn.addEventListener("click", () => {
    const id = parseId(addInput.value);
    if (!id) return toast("Укажите ID оффера", true);
    run(addBtn, "POST", "/draft/offers", { offer_ids: [id] }, `Оффер ${id} добавлен в черновик`);
  });

  el.querySelector('[data-act="commit"]').addEventListener("click", (e) => {
    if (!confirm("Отправить изменения офферов в Keitaro?")) return;
    run(e.target, "POST", "/draft/commit", undefined, "Изменения применены в Keitaro");
  });
  el.querySelector('[data-act="discard"]').addEventListener("click", (e) => {
    if (!confirm("Отменить все неприменённые изменения?")) return;
    run(e.target, "POST", "/draft/discard", undefined, "Черновик сброшен к состоянию Keitaro");
  });
  el.querySelector('[data-act="rebalance"]').addEventListener("click", (e) => run(e.target, "POST", "/draft/rebalance"));

  const det = el.querySelector("details.history");
  det.addEventListener("toggle", () => { if (det.open) loadHistory(det, s, apply); });
}

const fmtVersion = (offers) =>
  offers.map((o) => `${o.offer_id}: ${o.state === "active" ? o.share + "%" : "скрыт"}`).join(" · ") || "пусто";

async function loadHistory(det, s, apply) {
  const body = det.querySelector(".history-body");
  try {
    const items = await api("GET", `/api/streams/${s.id}/history`);
    if (!items.length) { body.textContent = "Пока ничего не применялось"; return; }
    body.innerHTML = items.map((h) => `
      <div class="ver">
        <div><b>${esc(h.created_at || "")}</b> · изменены: ${h.offer_ids.join(", ") || "—"}</div>
        <div>было: ${esc(fmtVersion(h.before))}</div>
        <div>стало: ${esc(fmtVersion(h.after))}</div>
        <div class="actions">
          <button class="link" data-load="${h.id}" data-which="before">Вернуть как было до</button>
          <button class="link" data-load="${h.id}" data-which="after">Вернуть эту версию</button>
        </div>
      </div>`).join("");
    body.querySelectorAll("[data-load]").forEach((b) => b.addEventListener("click", () =>
      busy(b, async () => {
        apply(await api("POST", `/api/streams/${s.id}/history/${b.dataset.load}/load`, { which: b.dataset.which }));
        toast("Версия загружена в черновик — проверьте и нажмите «Применить»");
      })));
  } catch (e) { body.textContent = e.message; }
}

init();
