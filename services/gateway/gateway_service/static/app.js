"use strict";

const KEY_STORAGE = "frescoruta.apiKey";
const DEFAULT_KEY = "frescoruta-demo-key";
const $ = (sel) => document.querySelector(sel);
const cop = (n) => "$ " + Math.round(n || 0).toLocaleString("es-CO");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STEP_LABELS = {
  reserve_stock: "Reservar stock",
  process_payment: "Cobrar",
  commit_stock: "Confirmar inventario",
  confirm_order: "Confirmar pedido",
  release_stock: "Liberar stock (compensación)",
  refund_payment: "Reembolsar (compensación)",
  reject_order: "Rechazar pedido",
};

let products = [];

function apiKey() {
  try { return localStorage.getItem(KEY_STORAGE) || DEFAULT_KEY; } catch { return DEFAULT_KEY; }
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey(), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* sin cuerpo */ }
  return { ok: res.ok, status: res.status, data, headers: res.headers };
}

/* ---------- render ---------- */
function renderServices(status) {
  $("#env-badge").textContent = "entorno: " + (status.environment || "?");
  $("#services").innerHTML = status.services.map((s) => `
    <div class="svc ${esc(s.status).toLowerCase()}">
      <strong>${esc(s.service)}</strong>
      <span class="state ${esc(s.status)}" style="align-self:flex-start">${esc(s.status)}</span>
      <span>${esc(s.url)}</span>
      <span>${s.latency_ms ? s.latency_ms + " ms" : ""}</span>
    </div>`).join("");
}

function renderOverview(o) {
  const by = (o.orders && o.orders.by_status) || {};
  $("#kpi-confirmed").textContent = by.CONFIRMED ?? "–";
  $("#kpi-rejected").textContent = by.REJECTED ?? "–";
  $("#kpi-revenue").textContent = o.orders ? cop(o.orders.confirmed_revenue) : "–";
  $("#kpi-notifications").textContent = o.notifications ? o.notifications.total : "–";
  $("#kpi-requests").textContent = o.gateway ? o.gateway.total_requests : "–";

  const chaos = o.chaos || {};
  $("#chaos-state").textContent = chaos.failure_rate !== undefined
    ? `actual: ${Math.round(chaos.failure_rate * 100)} % (${chaos.injected_failures} fallas inyectadas)` : "–";
  document.querySelectorAll("[data-chaos]").forEach((b) =>
    b.classList.toggle("active", Number(b.dataset.chaos) === chaos.failure_rate));

  $("#breakers").innerHTML = (o.circuit_breakers || []).map((b) => `
    <div class="breaker">
      <div><strong>${esc(b.name)}</strong>
        <small>fallos consecutivos ${b.consecutive_failures}/${b.failure_threshold} · rechazadas ${b.total_rejected}
        ${b.state === "OPEN" ? " · reintento en " + b.retry_after_s + " s" : ""}</small></div>
      <span class="state ${esc(b.state)}">${esc(b.state)}</span>
    </div>`).join("");
}

function renderOrders(list) {
  if (!list.length) { $("#orders").innerHTML = '<p class="hint">Aún no hay pedidos.</p>'; return; }
  $("#orders").innerHTML = list.map((o) => `
    <article class="order">
      <header>
        <strong>${esc(o.id)} · ${esc(o.customer_name)}</strong>
        <span class="state ${esc(o.status)}">${esc(o.status)}</span>
      </header>
      <div class="meta">${esc(o.delivery_city)} · ${esc(o.payment_method)} · ${o.items.length} ítems · total ${cop(o.total)} · ${new Date(o.created_at).toLocaleTimeString("es-CO")}</div>
      ${o.rejection_reason ? `<p class="reason">${esc(o.rejection_reason)}</p>` : ""}
      <ol class="timeline">${o.steps.map((s) => `
        <li class="state ${esc(s.status)}" title="${esc(s.detail || "")}">${esc(STEP_LABELS[s.step] || s.step)}</li>`).join("")}
      </ol>
    </article>`).join("");
}

function renderNotifications(list) {
  $("#notifications").innerHTML = list.length ? list.map((n) => `
    <li><strong>${esc(n.channel)}</strong> → ${esc(n.recipient)}<br>${esc(n.subject)}
      <small>${esc(n.event_type)} · ${new Date(n.created_at).toLocaleTimeString("es-CO")}</small></li>`).join("")
    : '<li class="hint">Sin notificaciones todavía.</li>';
}

function renderCatalog(list, cacheHeader) {
  const badge = $("#cache-badge");
  badge.textContent = "cache " + (cacheHeader || "?");
  badge.className = "badge " + (cacheHeader === "HIT" ? "hit" : "miss");
  $("#catalog").innerHTML = list.map((p) => `
    <tr class="${p.is_low_stock ? "low" : ""}">
      <td>${esc(p.id)}</td><td>${esc(p.name)}</td><td>${esc(p.category)}</td><td>${esc(p.unit)}</td>
      <td class="num">${cop(p.price)}</td><td class="num">${p.stock}${p.is_low_stock ? " ⚠" : ""}</td>
    </tr>`).join("");
}

/* ---------- formulario ---------- */
function addLine(productId = "FR-001", qty = 5) {
  const div = document.createElement("div");
  div.className = "line";
  div.innerHTML = `
    <label>Producto<select name="product_id">${products.map((p) =>
      `<option value="${esc(p.id)}" ${p.id === productId ? "selected" : ""}>${esc(p.id)} · ${esc(p.name)}</option>`).join("")}</select></label>
    <label>Cantidad<input name="quantity" type="number" min="1" value="${qty}"></label>
    <button type="button" class="icon" aria-label="Quitar">✕</button>`;
  div.querySelector("button").onclick = () => div.remove();
  $("#lines").appendChild(div);
}

function setLines(lines) {
  $("#lines").innerHTML = "";
  lines.forEach(([id, q]) => addLine(id, q));
}

const SCENARIOS = {
  ok: { method: "CARD", lines: [["FR-001", 10], ["FR-005", 3], ["FR-011", 12]] },
  stock: { method: "PSE", lines: [["FR-009", 500]] },
  declined: { method: "CARD", lines: [["FR-012", 300]] },
};

async function submitOrder(ev) {
  ev?.preventDefault();
  const form = $("#order-form");
  const body = {
    customer_name: form.customer_name.value,
    customer_email: form.customer_email.value,
    delivery_city: form.delivery_city.value,
    payment_method: form.payment_method.value,
    items: [...document.querySelectorAll(".line")].map((l) => ({
      product_id: l.querySelector("select").value,
      quantity: Number(l.querySelector("input").value),
    })),
  };
  const result = $("#order-result");
  result.hidden = false;
  result.className = "result";
  result.textContent = "Ejecutando saga…";
  const key = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  const res = await api("/orders", { method: "POST", body, headers: { "Idempotency-Key": key } });
  if (res.ok && res.data.status === "CONFIRMED") {
    result.className = "result ok";
    result.textContent = `✔ ${res.data.id} confirmado por ${cop(res.data.total)}`;
  } else {
    result.className = "result bad";
    result.textContent = res.data
      ? `✖ ${res.data.id || ""} ${res.data.rejection_reason || res.data.message || JSON.stringify(res.data.detail || res.data)}`
      : `✖ Error HTTP ${res.status}`;
  }
  refresh();
}

/* ---------- ciclo de actualización ---------- */
async function refresh() {
  const [status, overview, orders, notifications, catalog] = await Promise.all([
    api("/status"), api("/overview"), api("/orders?limit=15"), api("/notifications?limit=20"), api("/products"),
  ]);
  if (status.ok) renderServices(status.data);
  if (overview.ok) renderOverview(overview.data);
  if (orders.ok) renderOrders(orders.data);
  if (notifications.ok) renderNotifications(notifications.data);
  if (catalog.ok) {
    renderCatalog(catalog.data, catalog.headers.get("X-Cache"));
    if (!products.length) { products = catalog.data; setLines(SCENARIOS.ok.lines); }
    else products = catalog.data;
  }
  if (overview.status === 401) {
    const result = $("#order-result");
    result.hidden = false;
    result.className = "result bad";
    result.textContent = "API key inválida: revise el campo superior.";
  }
}

function init() {
  const input = $("#api-key");
  input.value = apiKey();
  input.addEventListener("change", () => {
    try { localStorage.setItem(KEY_STORAGE, input.value.trim()); } catch { /* almacenamiento no disponible */ }
    refresh();
  });
  $("#order-form").addEventListener("submit", submitOrder);
  $("#add-line").onclick = () => addLine();
  document.querySelectorAll("[data-scenario]").forEach((b) => b.onclick = () => {
    const s = SCENARIOS[b.dataset.scenario];
    $("#order-form").payment_method.value = s.method;
    setLines(s.lines);
    submitOrder();
  });
  document.querySelectorAll("[data-chaos]").forEach((b) => b.onclick = async () => {
    await api("/resilience/chaos", { method: "PUT", body: { failure_rate: Number(b.dataset.chaos), latency_ms: 0 } });
    refresh();
  });
  $("#reset-breakers").onclick = async () => { await api("/resilience/circuit-breakers/reset", { method: "POST" }); refresh(); };
  refresh();
  setInterval(refresh, 4000);
}

init();
