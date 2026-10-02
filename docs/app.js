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

/**
 * Converte uma data no formato ISO (yyyy-mm-dd), vinda de data.json, para o
 * formato brasileiro dd/mm/aaaa. Mantida em uma única linha (sem quebra) via
 * CSS (white-space: nowrap) na célula da tabela.
 */
function formatDateBR(isoDate) {
  if (!isoDate) return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!m) return isoDate;
  const [, year, month, day] = m;
  return `${day}/${month}/${year}`;
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
        ? `<span class="prev-version">Versão anterior: v${it.previous_version} — ${escapeHtml(
            it.previous_summary || ""
          )}</span>`
        : "";
      return `
      <tr>
        <td class="col-date">${formatDateBR(it.date)}</td>
        <td><span class="doc-badge ${docBadgeClass(it.document)}">${it.document}</span></td>
        <td>${escapeHtml(it.title)} ${badge}</td>
        <td>${escapeHtml(it.summary || "")}${prevInfo}</td>
        <td><a href="${it.link}" target="_blank" rel="noopener">Abrir fonte ↗</a></td>
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

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
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
