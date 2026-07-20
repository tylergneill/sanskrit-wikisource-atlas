const CHANGELOG_URL = "./data/changelog2.json";

function fmtBytes(n) {
  if (n === null || n === undefined) return "n/a";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

function fmtPct(n) {
  if (n === null || n === undefined) return "n/a";
  return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
}

function fmtDelta(n) {
  if (n === null || n === undefined) return "n/a";
  return (n >= 0 ? "+" : "") + n.toLocaleString();
}

function renderEntry(entry) {
  const div = document.createElement("div");
  div.className = "block";

  const date = (entry.date || "").slice(0, 10);
  const idLabel = entry.id !== undefined ? `#${entry.id} — ` : "";

  const header = document.createElement("div");
  header.innerHTML = `<strong>${idLabel}${date}</strong>` + (entry.label ? ` <span style="color:var(--muted); font-size:0.9em;">(${entry.label})</span>` : "");
  div.appendChild(header);

  if (entry.notes) {
    const notes = document.createElement("p");
    notes.style.color = "var(--muted)";
    notes.textContent = entry.notes;
    div.appendChild(notes);
  }

  // content_bytes is the headline size figure -- raw_bytes includes
  // markup/template overhead the pipeline already strips out (see about.html's
  // "How byte counts are calculated"); transliterated_bytes is shown as a
  // secondary figure alongside it.
  const sizes = entry.sizes || {};
  const contentSize = sizes.content_bytes || {};
  const translitSize = sizes.transliterated_bytes || {};
  const d = entry.delta || {};

  const stats = document.createElement("p");
  stats.innerHTML = `
    <strong>${fmtBytes(contentSize.old)}</strong> / ${entry.old?.count ?? "n/a"} items
    &rarr; <strong>${fmtBytes(contentSize.new)}</strong> / ${entry.new?.count ?? "n/a"} items
    (${fmtDelta(contentSize.delta)} bytes, ${fmtPct(contentSize.delta_pct)}; ${fmtDelta(d.count)} items, ${fmtPct(d.count_pct)})
  `;
  div.appendChild(stats);

  const translitLine = document.createElement("p");
  translitLine.style.color = "var(--muted)";
  translitLine.style.fontSize = "0.9em";
  translitLine.textContent = `transliterated size: ${fmtBytes(translitSize.old)} → ${fmtBytes(translitSize.new)} (${fmtDelta(translitSize.delta)} bytes, ${fmtPct(translitSize.delta_pct)})`;
  div.appendChild(translitLine);

  const counts = document.createElement("p");
  counts.style.color = "var(--muted)";
  counts.style.fontSize = "0.9em";
  const added = entry.items_added_count ?? 0;
  const removed = entry.items_removed_count ?? 0;
  const changed = entry.items_changed_count ?? 0;
  counts.textContent = `${added} items added (${fmtPct(entry.items_added_pct)}), ${removed} items removed (${fmtPct(entry.items_removed_pct)}), ${changed} items with a changed last-edited date`;
  div.appendChild(counts);

  if (added || removed || changed) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.style.cursor = "pointer";
    summary.style.color = "var(--accent)";
    summary.textContent = "Show item-level detail";
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

    const stripPrefix = (id) => id.replace(/^(page|index-item):/, "");
    buildList("Added", entry.items_added, stripPrefix);
    buildList("Removed", entry.items_removed, stripPrefix);
    buildList("Changed last-edited date", entry.items_with_changed_timestamp, (c) => `${stripPrefix(c.id)}: ${c.old} → ${c.new}`);

    div.appendChild(details);
  }

  return div;
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
    container.textContent = "";
    // Newest first
    for (const entry of [...log].reverse()) {
      container.appendChild(renderEntry(entry));
    }
  } catch (e) {
    container.textContent = "Could not load changelog.";
    console.log("Could not load changelog:", e);
  }
}

main();
