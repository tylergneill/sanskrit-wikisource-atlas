const CHANGELOG_URL = "./data/changelog.json";

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

  const d = entry.delta || {};
  // iast_bytes_est is the only size figure displayed (see app.js's
  // contentSizeBytes for the full raw -> content -> IAST rationale); falls
  // back through content_bytes_est to raw bytes for entries predating one or
  // both fields.
  const oldSize = entry.old?.iast_bytes_est ?? entry.old?.content_bytes_est ?? entry.old?.bytes;
  const newSize = entry.new?.iast_bytes_est ?? entry.new?.content_bytes_est ?? entry.new?.bytes;
  const sizeDelta = d.iast_bytes_est ?? d.content_bytes_est ?? d.bytes;
  const sizeDeltaPct = d.iast_bytes_est_pct ?? d.content_bytes_est_pct ?? d.bytes_pct;
  const stats = document.createElement("p");
  stats.innerHTML = `
    <strong>${fmtBytes(oldSize)}</strong> / ${entry.old?.count ?? "n/a"} pages
    &rarr; <strong>${fmtBytes(newSize)}</strong> / ${entry.new?.count ?? "n/a"} pages
    (${fmtDelta(sizeDelta)} bytes, ${fmtPct(sizeDeltaPct)}; ${fmtDelta(d.count)} pages, ${fmtPct(d.count_pct)})
  `;
  div.appendChild(stats);

  const counts = document.createElement("p");
  counts.style.color = "var(--muted)";
  counts.style.fontSize = "0.9em";
  const added = entry.pages_added?.length ?? 0;
  const removed = entry.pages_removed?.length ?? 0;
  const retimed = entry.pages_with_changed_timestamp?.length ?? 0;
  counts.textContent = `${added} pages added, ${removed} pages removed, ${retimed} pages with a changed last-edited date`;
  div.appendChild(counts);

  if (added || removed || retimed) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.style.cursor = "pointer";
    summary.style.color = "var(--accent)";
    summary.textContent = "Show page-level detail";
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

    buildList("Added", entry.pages_added, (id) => id.replace(/^page:/, ""));
    buildList("Removed", entry.pages_removed, (id) => id.replace(/^page:/, ""));
    buildList("Changed last-edited date", entry.pages_with_changed_timestamp, (c) => `${c.id.replace(/^page:/, "")}: ${c.old} → ${c.new}`);

    div.appendChild(details);
  }

  return div;
}

async function main() {
  const container = document.getElementById("changelog");
  try {
    const r = await fetch(CHANGELOG_URL, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status}`);
    const log = await r.json();
    if (!Array.isArray(log) || log.length === 0) {
      container.textContent = "No changelog entries yet.";
      return;
    }
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
