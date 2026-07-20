const CHANGELOG_URL = "./data/changelog2.json";

const state = {
  scheme: "iast", // devanagari | iast | hk | itrans | slp1 | iso
  log: null,
};

function translitText(s) {
  if (!s) return s;
  if (state.scheme === "devanagari") return s;
  try {
    return window.Sanscript.t(s, "devanagari", state.scheme);
  } catch {
    return s;
  }
}

const style = document.createElement("style");
style.textContent = `
  .changelog-summary { list-style: none; justify-content: flex-start; align-items: center; gap: 6px; }
  .changelog-summary::-webkit-details-marker { display: none; }
  .changelog-summary::after {
    content: "";
    display: inline-block;
    width: 0.5em;
    height: 0.5em;
    border-right: 2px solid var(--muted);
    border-bottom: 2px solid var(--muted);
    transform: rotate(-45deg);
    transition: transform 0.15s ease;
  }
  details[open] > .changelog-summary::after { transform: rotate(45deg); }
`;
document.head.appendChild(style);

function fmtChange(pct, oldFormatted) {
  if (pct === null || pct === undefined) return "";
  const arrow = pct >= 0 ? "↑" : "↓";
  return ` (${arrow} ${Math.abs(pct).toFixed(1)}% from ${oldFormatted})`;
}

function fmtDate(s) {
  if (!s) return "n/a";
  return s.slice(0, 10);
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "n/a";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function renderEntry(entry) {
  const div = document.createElement("div");
  div.className = "block";

  const oldDate = fmtDate(entry.old_date);
  const newDate = fmtDate(entry.date);

  const header = document.createElement("div");
  header.innerHTML = `<strong>${newDate}</strong> (since ${oldDate})`;
  div.appendChild(header);

  const stats = document.createElement("p");
  stats.innerHTML = `<strong>${entry.new?.count?.toLocaleString() ?? "n/a"} items</strong>${fmtChange(entry.delta?.count_pct, entry.old?.count?.toLocaleString() ?? "n/a")}`;
  div.appendChild(stats);

  const translitSize = entry.sizes?.transliterated_bytes || {};
  const translitLine = document.createElement("p");
  translitLine.style.color = "var(--muted)";
  translitLine.style.fontSize = "0.9em";
  translitLine.innerHTML = `<strong>${fmtBytes(translitSize.new)}</strong> extracted+transliterated size${fmtChange(translitSize.delta_pct, fmtBytes(translitSize.old))}`;
  div.appendChild(translitLine);

  const added = entry.items_added_count ?? 0;
  const removed = entry.items_removed_count ?? 0;
  const changed = entry.items_changed_count ?? 0;

  if (added || removed || changed) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.style.cursor = "pointer";
    summary.style.color = "var(--muted)";
    summary.style.fontSize = "0.9em";
    summary.className = "changelog-summary";
    summary.style.display = "flex";
    summary.innerHTML = `<span>${added} added, ${removed} removed, ${changed} updated — <span style="color:var(--accent)">show detail</span></span>`;
    details.appendChild(summary);

    const buildList = (title, items, render) => {
      if (!items || items.length === 0) return;
      const h = document.createElement("div");
      h.style.marginTop = "8px";
      h.style.fontWeight = "600";
      h.textContent = `${title} (${items.length})`;
      details.appendChild(h);
      const ul = document.createElement("ul");
      for (const item of items) {
        const li = document.createElement("li");
        li.textContent = render(item);
        ul.appendChild(li);
      }
      details.appendChild(ul);
    };

    const stripPrefix = (id) => translitText(id.replace(/^(page|index-item):/, ""));
    buildList("Added", entry.items_added, (item) => {
      if (typeof item === "string") return stripPrefix(item);
      return `${stripPrefix(item.id)}: ${fmtDate(item.date)}`;
    });
    buildList("Removed", entry.items_removed, stripPrefix);
    buildList("Updated", entry.items_with_changed_timestamp, (c) => `${stripPrefix(c.id)}: ${fmtDate(c.old)} → ${fmtDate(c.new)}`);

    div.appendChild(details);
  } else {
    const counts = document.createElement("p");
    counts.style.color = "var(--muted)";
    counts.style.fontSize = "0.9em";
    counts.textContent = "no items added, removed, or updated";
    div.appendChild(counts);
  }

  return div;
}

function renderChangelog() {
  const container = document.getElementById("changelog2");
  if (!container || !state.log) return;
  container.textContent = "";
  // Newest first
  for (const entry of [...state.log].reverse()) {
    container.appendChild(renderEntry(entry));
  }
}

async function main() {
  const container = document.getElementById("changelog2");
  if (!container) return;
  try {
    const r = await fetch(CHANGELOG_URL, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status}`);
    const log = await r.json();
    if (!Array.isArray(log) || log.length === 0) {
      container.textContent = "No changelog entries yet.";
      return;
    }
    state.log = log;
    renderChangelog();
  } catch (e) {
    container.textContent = "Could not load changelog.";
    console.log("Could not load changelog:", e);
  }
}

const schemeSelect = document.getElementById("schemeSelect");
if (schemeSelect) {
  schemeSelect.value = state.scheme;
  schemeSelect.addEventListener("change", (ev) => {
    state.scheme = ev.target.value;
    renderChangelog();
  });
}

main();
