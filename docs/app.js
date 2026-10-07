/* Painel do Radar Fiscal - lê docs/data.json (gerado pelo GitHub Actions) */

let STATE = { items: [], filterDoc: "all", filterText: "" };

async function loadData() {
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    const data = await res.json();
    STATE.items = data.items || [];
    renderCards(data);
    renderTable();
    renderFooter(data);
  } catch (err) {
    document.getElementById("empty-state").hidden = false;
    document.getElementById("empty-state").textContent =
      "Não foi possível carregar data.json. Verifique se o workflow já rodou ao menos uma vez.";
  }
}

function renderCards(data) {
  const counts = data.counts_by_document || {};
  const cardsEl = document.getElementById("cards");
  const entries = [
    ["Total monitorado", data.total_items ?? 0],
    ["NF-e", counts["NFe"] ?? 0],
    ["CT-e", counts["CTe"] ?? 0],
    ["MDF-e", counts["MDFe"] ?? 0],
    ["Novos na última varredura", data.new_last_run ?? 0],
  ];
  cardsEl.innerHTML = entries
    .map(
      ([label, value]) => `
      <div class="card">
        <div class="value">${value}</div>
        <div class="label">${label}</div>
      </div>`
    )
    .join("");
}

function docBadgeClass(doc) {
  return "doc-" + doc.replace(/\s|-/g, "");
}

/** yyyy-mm-dd -> dd/mm/aaaa (a célula usa white-space: nowrap, sem quebra de linha) */
function formatDateBR(isoDate) {
  if (!isoDate) return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return isoDate;
  const [, year, month, day] = m;
  return `${day}/${month}/${year}`;
}

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Bloco "Resumo das alterações" gerado a partir do documento da NT */
function renderChangeSummary(it) {
  const cs = it.change_summary;
  if (!cs) return "";

  if (cs.status !== "ok") {
    return it.is_new
      ? `<span class="prev-version">Resumo detalhado indisponível: ${escapeHtml(cs.reason || "")}. Consulte o documento original.</span>`
      : "";
  }

  let body = "";
  if (cs.purpose) {
    body += `<p class="nt-purpose"><strong>Objetivo:</strong> ${escapeHtml(cs.purpose)}</p>`;
  }
  for (const sec of cs.sections || []) {
    body += `<div class="nt-sec nt-${sec.key}"><strong>${escapeHtml(sec.title)}</strong><ul>${(sec.items || [])
      .map((t) => `<li>${escapeHtml(t)}</li>`)
      .join("")}</ul></div>`;
  }
  if ((cs.elements || []).length) {
    body += `<p class="nt-line"><strong>Campos/grupos citados:</strong> ${cs.elements
      .map((e) => `<code>${escapeHtml(e)}</code>`)
      .join(" ")}</p>`;
  }
  if ((cs.rules || []).length) {
    body += `<p class="nt-line"><strong>Regras/rejeições citadas:</strong> ${cs.rules
      .map((r) => `<code>${escapeHtml(r)}</code>`)
      .join(" ")}</p>`;
  }
  if ((cs.deadlines || []).length) {
    body += `<p class="nt-line"><strong>Prazos:</strong> ${cs.deadlines.map(escapeHtml).join(" · ")}</p>`;
  }
  body += `<p class="nt-disclaimer">Resumo gerado automaticamente a partir do texto do documento. Confira o PDF original antes de implementar.</p>`;

  return `<details class="nt-summary" ${it.is_new ? "open" : ""}>
    <summary>📝 Resumo das alterações propostas</summary>${body}</details>`;
}

function renderTable() {
  const tbody = document.getElementById("items-body");
  const emptyState = document.getElementById("empty-state");

  let filtered = STATE.items;
  if (STATE.filterDoc !== "all") {
    filtered = filtered.filter((it) => it.document === STATE.filterDoc);
  }
  if (STATE.filterText) {
    const q = STATE.filterText.toLowerCase();
    filtered = filtered.filter(
      (it) =>
        (it.title || "").toLowerCase().includes(q) ||
        (it.summary || "").toLowerCase().includes(q) ||
        (it.code || "").toLowerCase().includes(q)
    );
  }

  if (filtered.length === 0) {
    tbody.innerHTML = "";
    emptyState.hidden = false;
    return;
  }
  emptyState.hidden = true;

  tbody.innerHTML = filtered
    .map((it) => {
      const badge = it.is_new
        ? `<span class="${it.previous_version ? "updated-badge" : "new-badge"}">${
            it.previous_version ? "NOVA VERSÃO" : "NOVO"
          }</span>`
        : "";
      const prevInfo = it.previous_version
        ? `<span class="prev-version">Versão anterior: v${escapeHtml(String(it.previous_version))} — ${escapeHtml(
            it.previous_summary || ""
          )}</span>`
        : "";
      const href = it.doc_url || it.link;
      const linkLabel = it.doc_url ? "Abrir NT ↗" : "Abrir fonte ↗";
      return `
      <tr>
        <td class="col-date">${formatDateBR(it.date)}</td>
        <td><span class="doc-badge ${docBadgeClass(it.document)}">${escapeHtml(it.document)}</span></td>
        <td>${escapeHtml(it.title)} ${badge}</td>
        <td>${escapeHtml(it.summary || "")}${prevInfo}${renderChangeSummary(it)}</td>
        <td class="col-link"><a href="${escapeHtml(href)}" target="_blank" rel="noopener">${linkLabel}</a></td>
      </tr>`;
    })
    .join("");
}

function renderFooter(data) {
  const el = document.getElementById("last-updated");
  if (!data.generated_at) {
    el.textContent = "Ainda sem execução registrada.";
    return;
  }
  const d = new Date(data.generated_at);
  el.textContent = "Última atualização: " + d.toLocaleString("pt-BR");
}

document.getElementById("tabs").addEventListener("click", (e) => {
  if (!e.target.classList.contains("tab")) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  e.target.classList.add("active");
  STATE.filterDoc = e.target.dataset.doc;
  renderTable();
});

document.getElementById("search").addEventListener("input", (e) => {
  STATE.filterText = e.target.value;
  renderTable();
});

loadData();
