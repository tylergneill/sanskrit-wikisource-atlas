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

// Compact size formatter for per-item deltas -- picks B/KB/MB by magnitude
// rather than always MB, since a single page's transliterated_bytes is
// usually in the KB range and "0.0 MB" reads as meaningless precision loss.
function fmtBytesCompact(n) {
  if (n === null || n === undefined) return "n/a";
  if (n === 0) return "0";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// "(0→400 KB)" for a new item, "(400 KB → 480 KB, ↑ 20%)" for a size change.
// Omits the trailing percent when the old size is 0 (added items), since a
// percent-of-zero is undefined/misleading.
function fmtSizeDelta(oldBytes, newBytes) {
  oldBytes = oldBytes || 0;
  newBytes = newBytes || 0;
  const oldStr = fmtBytesCompact(oldBytes);
  const newStr = fmtBytesCompact(newBytes);
  if (oldBytes === 0) return ` (${oldStr}→${newStr})`;
  if (newBytes === 0) return ` (${oldStr}→${newStr})`;
  const pct = ((newBytes - oldBytes) / oldBytes) * 100;
  const arrow = pct >= 0 ? "↑" : "↓";
  return ` (${oldStr} → ${newStr}, ${arrow} ${Math.abs(pct).toFixed(0)}%)`;
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
      return `${stripPrefix(item.id)}: ${fmtDate(item.date)}${fmtSizeDelta(0, item.new_bytes)}`;
    });
    buildList("Removed", entry.items_removed, (item) => {
      if (typeof item === "string") return stripPrefix(item);
      return `${stripPrefix(item.id)}${fmtSizeDelta(item.old_bytes, 0)}`;
    });
    buildList("Updated", entry.items_with_changed_timestamp, (c) =>
      `${stripPrefix(c.id)}: ${fmtDate(c.old)} → ${fmtDate(c.new)}${fmtSizeDelta(c.old_bytes, c.new_bytes)}`);

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

// === Trend charts (size, count over time) ===
// Two separate single-series line charts sharing a time x-axis, rather than
// one dual-axis chart -- bytes and item-count have no principled shared
// scale, so overlaying them on one plot with two independently-scaled axes
// would make the visual comparison an artifact of axis choice, not signal.

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

function niceTicks(min, max, count) {
  if (min === max) return [min];
  const span = max - min;
  const rawStep = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.001; v += step) ticks.push(v);
  return ticks;
}

function fmtAxisBytes(n) {
  const mb = n / 1024 / 1024;
  if (mb >= 1000) return (mb / 1024).toFixed(1) + " GB";
  return Math.round(mb) + " MB";
}

function fmtAxisCount(n) {
  return n.toLocaleString();
}

function renderTrendChart(container, points, { title, getValue, fmtValue, fmtAxis }) {
  const width = 720;
  const height = 220;
  const margin = { top: 10, right: 16, bottom: 26, left: 56 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const card = document.createElement("div");
  card.className = "chart-card";
  const h4 = document.createElement("h4");
  h4.textContent = title;
  card.appendChild(h4);

  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  card.appendChild(wrap);

  const values = points.map(getValue);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const pad = (maxV - minV) * 0.08 || Math.abs(maxV) * 0.08 || 1;
  const yMin = Math.max(0, minV - pad);
  const yMax = maxV + pad;

  const dates = points.map((p) => new Date(p.date));
  const xMin = dates[0].getTime();
  const xMax = dates[dates.length - 1].getTime();

  const xScale = (t) => margin.left + (xMax === xMin ? innerW / 2 : ((t - xMin) / (xMax - xMin)) * innerW);
  const yScale = (v) => margin.top + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;

  const svg = svgEl("svg", {
    class: "chart-svg",
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": title,
  });

  // Gridlines + y-axis labels (nice round values).
  const yTicks = niceTicks(yMin, yMax, 4);
  for (const t of yTicks) {
    const y = yScale(t);
    svg.appendChild(svgEl("line", {
      class: "chart-grid", x1: margin.left, x2: width - margin.right, y1: y, y2: y,
    }));
    const label = svgEl("text", {
      class: "chart-axis-text", x: margin.left - 8, y: y + 3, "text-anchor": "end",
    });
    label.textContent = fmtAxis(t);
    svg.appendChild(label);
  }

  // X-axis: label first, last, and a few evenly-spaced dates in between.
  const xTickCount = Math.min(6, points.length);
  const xTickIdxs = new Set();
  for (let i = 0; i < xTickCount; i++) {
    xTickIdxs.add(Math.round((i / (xTickCount - 1 || 1)) * (points.length - 1)));
  }
  for (const idx of xTickIdxs) {
    const x = xScale(dates[idx].getTime());
    const label = svgEl("text", {
      class: "chart-axis-text", x, y: height - 6, "text-anchor": "middle",
    });
    label.textContent = points[idx].date.slice(0, 7);
    svg.appendChild(label);
  }

  // Area wash + line.
  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${xScale(dates[i].getTime())},${yScale(getValue(p))}`).join(" ");
  const areaPath = `${linePath} L${xScale(xMax)},${yScale(yMin)} L${xScale(xMin)},${yScale(yMin)} Z`;
  svg.appendChild(svgEl("path", { class: "chart-area", d: areaPath }));
  svg.appendChild(svgEl("path", { class: "chart-line", d: linePath }));

  // End marker (direct-labeled per marks-and-anatomy: label the endpoint).
  const lastX = xScale(xMax);
  const lastY = yScale(getValue(points[points.length - 1]));
  svg.appendChild(svgEl("circle", { class: "chart-dot", cx: lastX, cy: lastY, r: 4 }));
  const endLabel = svgEl("text", {
    class: "chart-axis-text", x: lastX, y: lastY - 10, "text-anchor": "end",
    style: "font-weight:600;",
  });
  endLabel.textContent = fmtValue(getValue(points[points.length - 1]));
  svg.appendChild(endLabel);

  // Crosshair + hover dot (shared X readout via the tooltip built below).
  const crosshair = svgEl("line", {
    class: "chart-crosshair", x1: 0, x2: 0, y1: margin.top, y2: margin.top + innerH,
  });
  svg.appendChild(crosshair);
  const hoverDot = svgEl("circle", { class: "chart-hover-dot", r: 5 });
  svg.appendChild(hoverDot);

  // Hit layer: one big transparent rect, nearest-point snap on pointermove.
  const hit = svgEl("rect", {
    class: "chart-hit", x: margin.left, y: margin.top, width: innerW, height: innerH,
  });
  svg.appendChild(hit);

  wrap.appendChild(svg);

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  wrap.appendChild(tooltip);

  function showAt(clientX) {
    const rect = svg.getBoundingClientRect();
    const scaleX = width / rect.width;
    const localX = (clientX - rect.left) * scaleX;
    // Nearest point by x-pixel distance.
    let best = 0;
    let bestDist = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(xScale(dates[i].getTime()) - localX);
      if (d < bestDist) { bestDist = d; best = i; }
    }
    const p = points[best];
    const px = xScale(dates[best].getTime());
    const py = yScale(getValue(p));
    crosshair.setAttribute("x1", px);
    crosshair.setAttribute("x2", px);
    crosshair.style.opacity = "1";
    hoverDot.setAttribute("cx", px);
    hoverDot.setAttribute("cy", py);
    hoverDot.style.opacity = "1";

    tooltip.innerHTML = "";
    const dateRow = document.createElement("div");
    dateRow.className = "chart-tooltip-date";
    dateRow.textContent = p.date.slice(0, 10);
    tooltip.appendChild(dateRow);
    const valueRow = document.createElement("div");
    valueRow.className = "chart-tooltip-value";
    valueRow.textContent = fmtValue(getValue(p));
    tooltip.appendChild(valueRow);

    tooltip.style.opacity = "1";
    const tipRect = tooltip.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    let left = (px / width) * wrapRect.width - tipRect.width / 2;
    left = Math.max(0, Math.min(wrapRect.width - tipRect.width, left));
    tooltip.style.left = `${left}px`;
    const top = (py / height) * wrapRect.height - tipRect.height - 12;
    tooltip.style.top = `${Math.max(0, top)}px`;
  }

  function hide() {
    crosshair.style.opacity = "0";
    hoverDot.style.opacity = "0";
    tooltip.style.opacity = "0";
  }

  hit.addEventListener("pointermove", (ev) => showAt(ev.clientX));
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("pointerdown", (ev) => showAt(ev.clientX));

  container.appendChild(card);
}

function renderChangelogCharts() {
  const container = document.getElementById("changelogCharts");
  if (!container || !state.log) return;
  container.textContent = "";

  // One point per changelog entry's "new" state, in chronological order
  // (the log is stored oldest-first already), plus the very first entry's
  // "old" state so the earliest data point isn't dropped from the trend.
  const sorted = [...state.log].sort((a, b) => a.date.localeCompare(b.date));
  if (sorted.length === 0) {
    container.textContent = "No data yet.";
    return;
  }
  const points = [
    { date: sorted[0].old_date, count: sorted[0].old?.count ?? 0, bytes: sorted[0].sizes?.transliterated_bytes?.old ?? 0 },
    ...sorted.map((e) => ({
      date: e.date,
      count: e.new?.count ?? 0,
      bytes: e.sizes?.transliterated_bytes?.new ?? 0,
    })),
  ];

  renderTrendChart(container, points, {
    title: "Effective Size (transliterated content bytes)",
    getValue: (p) => p.bytes,
    fmtValue: fmtBytes,
    fmtAxis: fmtAxisBytes,
  });
  renderTrendChart(container, points, {
    title: "Item Count",
    getValue: (p) => p.count,
    fmtValue: (n) => `${n.toLocaleString()} items`,
    fmtAxis: fmtAxisCount,
  });
}

async function main() {
  const container = document.getElementById("changelog2");
  const chartsContainer = document.getElementById("changelogCharts");
  if (!container) return;
  try {
    const r = await fetch(CHANGELOG_URL, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status}`);
    const log = await r.json();
    if (!Array.isArray(log) || log.length === 0) {
      container.textContent = "No changelog entries yet.";
      if (chartsContainer) chartsContainer.textContent = "No data yet.";
      return;
    }
    state.log = log;
    renderChangelog();
    renderChangelogCharts();
  } catch (e) {
    container.textContent = "Could not load changelog.";
    if (chartsContainer) chartsContainer.textContent = "Could not load data.";
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
