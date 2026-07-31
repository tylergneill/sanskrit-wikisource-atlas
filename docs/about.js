const CHANGELOG_URL = "./data/changelog.json";
const SOURCE_ERAS_URL = "./data/source_eras.json";

const state = {
  scheme: "iast", // devanagari | iast | hk | itrans | slp1 | iso
  log: null,
  granularity: 12, // months per group: 1 = monthly, 3 = quarterly, 12 = yearly
  includeOrphans: false, // when true, trend charts use each entry's "all" total
                          // (central + असम्बद्धवर्गीकृतम्, the orphan bucket) instead
                          // of the central-only old/new/sizes.
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

// Combine N consecutive monthly changelog entries (oldest-first, chained --
// each entry's old_date equals the previous entry's date) into one synthetic
// entry spanning the whole group. Sizes/counts reduce trivially to the
// group's first "old" and last "new". Item-level added/removed/changed lists
// need real net-effect tracking across the group, not concatenation: e.g. an
// item added in month 2 and removed in month 5 nets to neither an add nor a
// remove over the full span, and an item added then later edited should
// still show as "added" (with its final size/date), not also as "changed".
// Replaying each month's added/removed/changed events in order against a
// per-item running state is the only way to get this exactly right from the
// monthly records alone (they don't carry a full item roster per month).
function reduceGroup(entries) {
  if (entries.length === 1) return entries[0];

  const state = new Map(); // id -> {status: 'added'|'removed'|'present', bytes, date}

  for (const entry of entries) {
    for (const item of entry.items_added || []) {
      state.set(item.id, { status: "added", bytes: item.new_bytes, date: item.date });
    }
    for (const item of entry.items_removed || []) {
      if (state.has(item.id) && state.get(item.id).status === "added") {
        // Added then removed within the same group: nets to no-op.
        state.delete(item.id);
      } else {
        state.set(item.id, { status: "removed", bytes: item.old_bytes });
      }
    }
    for (const item of entry.items_with_changed_timestamp || []) {
      const prev = state.get(item.id);
      if (prev && prev.status === "added") {
        // Still a net-new item over the whole group -- keep it "added" but
        // roll its bytes/date forward to this later edit.
        state.set(item.id, { status: "added", bytes: item.new_bytes, date: item.new });
      } else {
        state.set(item.id, {
          status: "changed",
          old: prev && prev.status === "changed" ? prev.old : item.old,
          new: item.new,
          old_bytes: prev && prev.status === "changed" ? prev.old_bytes : item.old_bytes,
          new_bytes: item.new_bytes,
        });
      }
    }
  }

  const items_added = [];
  const items_removed = [];
  const items_with_changed_timestamp = [];
  for (const [id, v] of state) {
    if (v.status === "added") items_added.push({ id, date: v.date, new_bytes: v.bytes });
    else if (v.status === "removed") items_removed.push({ id, old_bytes: v.bytes });
    else if (v.status === "changed") {
      items_with_changed_timestamp.push({ id, old: v.old, new: v.new, old_bytes: v.old_bytes, new_bytes: v.new_bytes });
    }
  }
  items_added.sort((a, b) => a.id.localeCompare(b.id));
  items_removed.sort((a, b) => a.id.localeCompare(b.id));
  items_with_changed_timestamp.sort((a, b) => a.id.localeCompare(b.id));

  const first = entries[0];
  const last = entries[entries.length - 1];
  const oldCount = first.old?.count ?? 0;
  const newCount = last.new?.count ?? 0;
  const deltaCount = newCount - oldCount;

  // text_count is missing entirely on older changelog entries (added after
  // the fact) -- track presence with null rather than defaulting to 0, so
  // a group spanning the gap reports "n/a" instead of a misleading delta.
  const oldTextCount = first.old?.text_count;
  const newTextCount = last.new?.text_count;
  const hasTextCount = oldTextCount != null && newTextCount != null;
  const deltaTextCount = hasTextCount ? newTextCount - oldTextCount : null;

  const sizes = {};
  for (const key of ["raw_bytes", "content_bytes", "transliterated_bytes"]) {
    const oldV = first.sizes?.[key]?.old ?? 0;
    const newV = last.sizes?.[key]?.new ?? 0;
    const deltaV = newV - oldV;
    sizes[key] = { old: oldV, new: newV, delta: deltaV, delta_pct: oldV === 0 ? null : (100 * deltaV) / oldV };
  }

  // entry.all (true total, including असम्बद्धवर्गीकृतम्) is only present on
  // changelog entries generated after that field was introduced -- older
  // entries fall back to their own central-only old/new/sizes, same as
  // compare.py's build_report does for individual snapshots that predate it.
  const firstAll = first.all || { old: first.old, sizes: first.sizes };
  const lastAll = last.all || { new: last.new, sizes: last.sizes };
  const oldCountAll = firstAll.old?.count ?? 0;
  const newCountAll = lastAll.new?.count ?? 0;
  const oldTextCountAll = firstAll.old?.text_count;
  const newTextCountAll = lastAll.new?.text_count;
  const hasTextCountAll = oldTextCountAll != null && newTextCountAll != null;
  const deltaTextCountAll = hasTextCountAll ? newTextCountAll - oldTextCountAll : null;
  const sizesAll = {};
  for (const key of ["raw_bytes", "content_bytes", "transliterated_bytes"]) {
    const oldV = firstAll.sizes?.[key]?.old ?? 0;
    const newV = lastAll.sizes?.[key]?.new ?? 0;
    const deltaV = newV - oldV;
    sizesAll[key] = { old: oldV, new: newV, delta: deltaV, delta_pct: oldV === 0 ? null : (100 * deltaV) / oldV };
  }

  return {
    id: last.id,
    date: last.date,
    old_date: first.old_date,
    old: first.old,
    new: last.new,
    sizes,
    delta: {
      count: deltaCount,
      count_pct: oldCount === 0 ? null : (100 * deltaCount) / oldCount,
      text_count: deltaTextCount,
      text_count_pct: !hasTextCount || oldTextCount === 0 ? null : (100 * deltaTextCount) / oldTextCount,
    },
    all: {
      old: firstAll.old,
      new: lastAll.new,
      sizes: sizesAll,
      delta: {
        count: newCountAll - oldCountAll,
        count_pct: oldCountAll === 0 ? null : (100 * (newCountAll - oldCountAll)) / oldCountAll,
        text_count: deltaTextCountAll,
        text_count_pct: !hasTextCountAll || oldTextCountAll === 0 ? null : (100 * deltaTextCountAll) / oldTextCountAll,
      },
    },
    items_added,
    items_removed,
    items_with_changed_timestamp,
    items_added_count: items_added.length,
    items_removed_count: items_removed.length,
    items_changed_count: items_with_changed_timestamp.length,
    items_added_pct: oldCount === 0 ? null : (100 * items_added.length) / oldCount,
    items_removed_pct: oldCount === 0 ? null : (100 * items_removed.length) / oldCount,
  };
}

// Group the oldest-first monthly log into chunks of `size` months, most
// recent chunk first (i.e. grouping counts back from "now"), so a leftover
// partial chunk falls at the oldest end where history runs out rather than
// silently merging into the most recent (and most relevant) group.
function groupEntries(log, size) {
  if (size <= 1) return [...log];
  const groups = [];
  for (let end = log.length; end > 0; end -= size) {
    const start = Math.max(0, end - size);
    groups.push(reduceGroup(log.slice(start, end)));
  }
  return groups.reverse(); // oldest-first, matching the ungrouped log's order
}

function renderEntry(entry) {
  const div = document.createElement("div");
  div.className = "block";

  const oldDate = fmtDate(entry.old_date);
  const newDate = fmtDate(entry.date);

  const header = document.createElement("div");
  header.style.margin = "0 0 4px";
  header.innerHTML = `<strong>${newDate}</strong> (since ${oldDate})`;
  div.appendChild(header);

  const translitSize = entry.sizes?.transliterated_bytes || {};
  const translitLine = document.createElement("p");
  translitLine.style.margin = "0 0 4px";
  translitLine.innerHTML = `<strong>${fmtBytes(translitSize.new)}</strong> ${fmtChange(translitSize.delta_pct, fmtBytes(translitSize.old))}`;
  div.appendChild(translitLine);

  const stats = document.createElement("p");
  stats.style.margin = "0 0 4px";
  const textCount = entry.new?.text_count;
  if (textCount != null) {
    stats.innerHTML = `<strong>${textCount.toLocaleString()} texts</strong>${fmtChange(entry.delta?.text_count_pct, entry.old?.text_count?.toLocaleString() ?? "n/a")}`;
  } else {
    stats.innerHTML = `<strong>n/a texts</strong> (not tracked for this period)`;
  }
  div.appendChild(stats);

  const pageStats = document.createElement("p");
  pageStats.style.margin = "0 0 4px";
  pageStats.style.color = "var(--muted)";
  pageStats.style.fontSize = "0.9em";
  pageStats.innerHTML = `<strong>${entry.new?.count?.toLocaleString() ?? "n/a"} pages</strong>${fmtChange(entry.delta?.count_pct, entry.old?.count?.toLocaleString() ?? "n/a")}`;
  div.appendChild(pageStats);

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
    summary.innerHTML = `<span>${added} pages added, ${removed} removed, ${changed} updated — <span style="color:var(--accent)">show detail</span></span>`;
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
    counts.textContent = "no pages added, removed, or updated";
    div.appendChild(counts);
  }

  return div;
}

function renderChangelog() {
  const container = document.getElementById("changelog");
  if (!container || !state.log) return;
  container.textContent = "";
  const grouped = groupEntries(state.log, state.granularity);
  // Newest first
  for (const entry of [...grouped].reverse()) {
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

// Ticks at an exact fixed step (e.g. round 100 MB increments), rather than
// niceTicks' auto-picked 1/2/5x10^n step -- lets a chart's gridlines land on
// fixed, predictable, cross-chart-comparable values instead of whatever
// number happens to divide the current data's range evenly.
function fixedStepTicks(min, max, step) {
  const start = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.001; v += step) ticks.push(v);
  return ticks.length > 0 ? ticks : [min];
}

function fmtAxisBytes(n) {
  const mb = n / 1024 / 1024;
  if (mb >= 1000) return (mb / 1024).toFixed(1) + " GB";
  return Math.round(mb) + " MB";
}

function fmtAxisCount(n) {
  return n.toLocaleString();
}

function renderTrendChart(container, points, { title, getValue, fmtValue, fmtAxis, tickStep }) {
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

  // Gridlines + y-axis labels (nice round values, or a fixed step if given).
  const yTicks = tickStep ? fixedStepTicks(yMin, yMax, tickStep) : niceTicks(yMin, yMax, 4);
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
  // Fewer date ticks on a phone: the axis text is counter-scaled up there (see
  // styles.css) so six "YYYY-MM" labels crowd into each other, and the two leftmost
  // end up nearly touching.
  const narrow =
    typeof window !== "undefined" &&
    window.matchMedia("(max-width: 800px)").matches;
  const xTickCount = Math.min(narrow ? 4 : 6, points.length);
  const xTickIdxs = new Set();
  for (let i = 0; i < xTickCount; i++) {
    xTickIdxs.add(Math.round((i / (xTickCount - 1 || 1)) * (points.length - 1)));
  }
  // Anchor the end labels inward rather than centering them: a centered label at
  // the first/last tick hangs half its width past the plot area, which at phone
  // widths (where the axis text is counter-scaled up, see styles.css) runs visibly
  // outside the chart box.
  const sortedTickIdxs = [...xTickIdxs].sort((a, b) => a - b);
  const firstIdx = sortedTickIdxs[0];
  const lastIdx = sortedTickIdxs[sortedTickIdxs.length - 1];
  for (const idx of sortedTickIdxs) {
    const x = xScale(dates[idx].getTime());
    const anchor =
      idx === firstIdx ? "start" : idx === lastIdx ? "end" : "middle";
    const label = svgEl("text", {
      class: "chart-axis-text", x, y: height - 6, "text-anchor": anchor,
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

  // One point per group's "new" state, in chronological order (grouped
  // entries come back oldest-first, matching the ungrouped log), plus the
  // first group's "old" state so the earliest data point isn't dropped from
  // the trend. Same grouping as the deltas list below, so the chart's
  // resolution matches whatever granularity is currently selected.
  const sorted = groupEntries(state.log, state.granularity);
  if (sorted.length === 0) {
    container.textContent = "No data yet.";
    return;
  }
  // includeOrphans switches every point to each entry's "all" total (central
  // + असम्बद्धवर्गीकृतम्, the orphan bucket) instead of the central-only figures --
  // falls back to the central old/new/sizes on entries that predate "all".
  const first0 = state.includeOrphans ? (sorted[0].all || sorted[0]) : sorted[0];
  const points = [
    {
      date: sorted[0].old_date,
      count: first0.old?.count ?? 0,
      textCount: first0.old?.text_count ?? null,
      bytes: first0.sizes?.transliterated_bytes?.old ?? 0,
    },
    ...sorted.map((e) => {
      const src = state.includeOrphans ? (e.all || e) : e;
      return {
        date: e.date,
        count: src.new?.count ?? 0,
        textCount: src.new?.text_count ?? null,
        bytes: src.sizes?.transliterated_bytes?.new ?? 0,
      };
    }),
  ];

  renderTrendChart(container, points, {
    title: "Effective Size (transliterated content bytes)",
    getValue: (p) => p.bytes,
    fmtValue: fmtBytes,
    fmtAxis: fmtAxisBytes,
    tickStep: 100 * 1024 * 1024,
  });

  // text_count is absent on snapshots from before that stat existed --
  // only chart the trailing run of points that actually have it, rather
  // than plotting a misleading 0 for the untracked era.
  const textPoints = points.filter((p) => p.textCount != null);
  if (textPoints.length > 1) {
    renderTrendChart(container, textPoints, {
      title: "Text Count",
      getValue: (p) => p.textCount,
      fmtValue: (n) => `${n.toLocaleString()} texts`,
      fmtAxis: fmtAxisCount,
    });
  }

  renderTrendChart(container, points, {
    title: "Page Count",
    getValue: (p) => p.count,
    fmtValue: (n) => `${n.toLocaleString()} pages`,
    fmtAxis: fmtAxisCount,
  });
}

// "YYYY-MM-01" -> "YYYY-MM-01" for the previous calendar month -- the
// legacy-format live window ends the month before the current-format live
// window's rolling start takes over, never the same month (the two never
// overlap).
function monthBefore(yyyyMmDd) {
  const [y, m] = yyyyMmDd.split("-").map(Number);
  const prevM = m === 1 ? 12 : m - 1;
  const prevY = m === 1 ? y - 1 : y;
  return `${prevY}-${String(prevM).padStart(2, "0")}-01`;
}

function fmtYearMonth(yyyyMmDd) {
  return yyyyMmDd.slice(0, 7);
}

function fmtDateRanges(ranges) {
  return ranges
    .map(([start, end]) => (start === end ? fmtYearMonth(start) : `${fmtYearMonth(start)} to ${fmtYearMonth(end)}`))
    .join(", ");
}

// "YYYY-MM-01" -> integer month index (months since 0000-01), for subtracting/
// comparing dates and computing proportional segment widths along the timeline.
function monthIndex(yyyyMmDd) {
  const [y, m] = yyyyMmDd.split("-").map(Number);
  return y * 12 + (m - 1);
}

function monthIndexToDate(idx) {
  const y = Math.floor(idx / 12);
  const m = (idx % 12) + 1;
  return `${y}-${String(m).padStart(2, "0")}-01`;
}

// Subtracts a sorted, non-overlapping list of [start, end] ranges (inclusive,
// month-granularity) from a [start, end] span, returning the leftover pieces
// in chronological order. Used to derive the Internet Archive's own real
// coverage (its span minus its interior gaps) without hand-listing it
// separately from archive_gap_ranges.
function subtractRanges(spanStart, spanEnd, ranges) {
  let cursor = monthIndex(spanStart);
  const spanEndIdx = monthIndex(spanEnd);
  const pieces = [];
  for (const [gapStart, gapEnd] of ranges) {
    const gapStartIdx = monthIndex(gapStart);
    const gapEndIdx = monthIndex(gapEnd);
    if (gapStartIdx > cursor) pieces.push([monthIndexToDate(cursor), monthIndexToDate(gapStartIdx - 1)]);
    cursor = Math.max(cursor, gapEndIdx + 1);
  }
  if (cursor <= spanEndIdx) pieces.push([monthIndexToDate(cursor), monthIndexToDate(spanEndIdx)]);
  return pieces;
}

// This mirror's tree-building depends on वर्गसर्वस्वम् (created 2012-01-20),
// so no month before this floor could ever produce a usable snapshot --
// regardless of whether a dump file happens to exist for it (Internet
// Archive has 2011-09/2011-10, but both hit RootCategoryMissing and are
// skipped, same as any other pre-floor month -- see pipeline/backfill.py's
// MATERIALIZED_FLOOR and RootCategoryMissing). Everything before this floor
// is folded into the separate #sourceTimelinePre block instead of being
// miscolored as usable coverage in the real bar.
const TIMELINE_FLOOR = "2012-02-01"; // first month with a real changelog snapshot (Jan straddles the category's creation)

// Builds the full chronological list of {start, end, kind} segments covering
// TIMELINE_FLOOR through the present, for the source-type timeline bar. kind
// is one of "current-live" / "legacy-live" / "archive" / "materialized" /
// "uncovered" (a genuine hole -- e.g. 2015-01 -- with no real dump AND no
// materialization, because it isn't reachable through either legacy source
// as an interior gap; see archive_gap_ranges vs materialized_ranges).
function buildTimelineSegments(eras) {
  const { era1_rolling_start, era2_rolling_start, archive_start, archive_end, archive_gap_ranges, materialized_ranges } = eras;
  const floorIdx = monthIndex(TIMELINE_FLOOR);
  const segments = [];

  const clip = (start, end) => (monthIndex(end) < floorIdx ? null : [monthIndex(start) < floorIdx ? TIMELINE_FLOOR : start, end]);

  const archiveCoverage = subtractRanges(archive_start, archive_end, archive_gap_ranges);
  for (const [start, end] of archiveCoverage) {
    const clipped = clip(start, end);
    if (clipped) segments.push({ start: clipped[0], end: clipped[1], kind: "archive" });
  }
  for (const [start, end] of materialized_ranges) {
    const clipped = clip(start, end);
    if (clipped) segments.push({ start: clipped[0], end: clipped[1], kind: "materialized" });
  }

  // Any interior span archive_gap_ranges carries that materialized_ranges
  // doesn't also fill is a real, currently-unfillable hole (not a rendering
  // bug) -- e.g. 2015-01/2015-05 are both, so no "uncovered" segment results
  // for those; a genuinely un-materialized interior gap would show here.
  const materializedSet = new Set();
  for (const [s, e] of materialized_ranges) {
    for (let i = monthIndex(s); i <= monthIndex(e); i++) materializedSet.add(i);
  }
  for (const [gapStart, gapEnd] of archive_gap_ranges) {
    const clipped = clip(gapStart, gapEnd);
    if (!clipped) continue;
    const [cStart, cEnd] = clipped;
    let runStart = null;
    for (let i = monthIndex(cStart); i <= monthIndex(cEnd); i++) {
      const covered = materializedSet.has(i);
      if (!covered && runStart === null) runStart = i;
      if (covered && runStart !== null) {
        segments.push({ start: monthIndexToDate(runStart), end: monthIndexToDate(i - 1), kind: "uncovered" });
        runStart = null;
      }
    }
    if (runStart !== null) segments.push({ start: monthIndexToDate(runStart), end: cEnd, kind: "uncovered" });
  }

  segments.push({ start: era2_rolling_start, end: monthBefore(era1_rolling_start), kind: "legacy-live" });

  const now = new Date();
  const presentDate = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}-01`;
  segments.push({ start: era1_rolling_start, end: presentDate, kind: "current-live" });

  segments.sort((a, b) => monthIndex(a.start) - monthIndex(b.start));
  return segments;
}

const TIMELINE_KIND_INFO = {
  "current-live": { color: "var(--series-1)", label: "Current-format live", desc: "newer Wikimedia “Content File” export format" },
  "legacy-live": { color: "var(--series-2)", label: "Legacy-format live", desc: "legacy Wikimedia export format" },
  "archive": { color: "var(--series-3)", label: "Internet Archive", desc: "legacy Wikimedia export format, stored on Internet Archive" },
  "materialized": { color: "var(--series-4)", label: "Materialized", desc: "synthetic, reconstructed on demand from full Wikimedia revision history" },
  "uncovered": { color: "var(--surface-uncovered)", label: "No coverage", desc: "no dump and no revision history old enough to reconstruct from" },
};

const TIMELINE_PRE_TOOLTIP_HTML =
  `<span class="ttLabel">Before this changelog</span><br>` +
  `sa.wikisource's actual edit history goes back to 2004-07-23, roughly 7.5 years ` +
  `before वर्गसर्वस्वम् (the root category this mirror's tree-building depends on) was ` +
  `created on 2012-01-20. The changelog can't reach earlier than 2012-02, not because ` +
  `the underlying revision data runs out, but because there's no category structure ` +
  `to build a tree from before then.`;

function renderSourceTimeline(eras, idPrefix = "sourceTimeline") {
  const preEl = document.getElementById(`${idPrefix}Pre`);
  const barEl = document.getElementById(`${idPrefix}Bar`);
  const axisEl = document.getElementById(`${idPrefix}Axis`);
  const tooltipEl = document.getElementById(`${idPrefix}Tooltip`);
  if (!barEl) return;

  if (preEl) {
    preEl.addEventListener("mouseenter", () => {
      tooltipEl.innerHTML = TIMELINE_PRE_TOOLTIP_HTML;
      tooltipEl.hidden = false;
    });
    preEl.addEventListener("mousemove", (ev) => positionTimelineTooltip(ev));
    preEl.addEventListener("mouseleave", () => { tooltipEl.hidden = true; });
  }

  const segments = buildTimelineSegments(eras);
  const spanStart = monthIndex(segments[0].start);
  const spanEnd = monthIndex(segments[segments.length - 1].end);
  const totalMonths = spanEnd - spanStart + 1;

  barEl.innerHTML = "";
  for (const seg of segments) {
    const info = TIMELINE_KIND_INFO[seg.kind];
    const months = monthIndex(seg.end) - monthIndex(seg.start) + 1;
    const pct = (months / totalMonths) * 100;
    const el = document.createElement("div");
    el.className = "sourceTimelineSeg";
    el.style.width = `${pct}%`;
    el.style.background = info.color;
    el.addEventListener("mouseenter", () => showTimelineTooltip(el, seg, info));
    el.addEventListener("mousemove", (ev) => positionTimelineTooltip(ev));
    el.addEventListener("mouseleave", () => { tooltipEl.hidden = true; });
    barEl.appendChild(el);
  }

  axisEl.innerHTML = "";
  const startYear = Number(segments[0].start.slice(0, 4));
  const endYear = Number(segments[segments.length - 1].end.slice(0, 4));
  const tickYears = new Set([startYear, endYear]);
  // Skip a generated tick that would land within 2 years of either edge label
  // (e.g. startYear 2011 + the next multiple-of-4 being 2012) -- they'd
  // overlap since both compete for the same left-aligned corner.
  for (let y = Math.ceil(startYear / 4) * 4; y < endYear; y += 4) {
    if (y - startYear < 2 || endYear - y < 2) continue;
    tickYears.add(y);
  }
  for (const y of [...tickYears].sort((a, b) => a - b)) {
    const idx = monthIndex(`${y}-01-01`);
    const leftPct = ((Math.max(idx, spanStart) - spanStart) / totalMonths) * 100;
    const span = document.createElement("span");
    span.style.left = `${leftPct}%`;
    span.textContent = y;
    axisEl.appendChild(span);
  }

  function showTimelineTooltip(el, seg, info) {
    const range = seg.start === seg.end ? fmtYearMonth(seg.start) : `${fmtYearMonth(seg.start)} to ${fmtYearMonth(seg.end)}`;
    tooltipEl.innerHTML = `<span class="ttLabel">${info.label}</span><br>${range}`;
    tooltipEl.hidden = false;
  }
  function positionTimelineTooltip(ev) {
    const rootRect = document.getElementById(idPrefix).getBoundingClientRect();
    tooltipEl.style.left = `${ev.clientX - rootRect.left + 12}px`;
    tooltipEl.style.top = `${ev.clientY - rootRect.top + 16}px`;
  }
}

// Rendered twice with the same data: once under "Snapshots" (where the source
// types are explained) and again under "Data Quantity" (so the trend charts'
// bumps/dips can be visually cross-referenced against which source type
// produced that stretch of the changelog, without scrolling back up).
const SOURCE_TIMELINE_ID_PREFIXES = ["sourceTimeline", "sourceTimelineDataQuantity"];

async function loadSourceEras() {
  const anyContainer = SOURCE_TIMELINE_ID_PREFIXES.some((id) => document.getElementById(id));
  if (!anyContainer) return;
  try {
    const r = await fetch(SOURCE_ERAS_URL, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status}`);
    const eras = await r.json();
    for (const idPrefix of SOURCE_TIMELINE_ID_PREFIXES) {
      if (document.getElementById(idPrefix)) renderSourceTimeline(eras, idPrefix);
    }
  } catch (e) {
    console.log("Could not load source era boundaries:", e);
  }
}

async function main() {
  const container = document.getElementById("changelog");
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

const granularitySelect = document.getElementById("changelogGranularity");
if (granularitySelect) {
  granularitySelect.value = String(state.granularity);
  granularitySelect.addEventListener("change", (ev) => {
    state.granularity = Number(ev.target.value);
    renderChangelog();
    renderChangelogCharts();
  });
}

const includeOrphansCheckbox = document.getElementById("changelogIncludeOrphans");
if (includeOrphansCheckbox) {
  includeOrphansCheckbox.checked = state.includeOrphans;
  includeOrphansCheckbox.addEventListener("change", (ev) => {
    state.includeOrphans = ev.target.checked;
    renderChangelogCharts();
  });
}

// The charts pick their x-tick count from the viewport width (see buildChart), so
// re-render when we cross that breakpoint -- otherwise rotating a phone leaves the
// portrait tick count on a landscape chart. Listening to the media query rather
// than every resize event keeps this to one re-render per actual crossing.
const narrowQuery = window.matchMedia("(max-width: 800px)");
narrowQuery.addEventListener("change", () => {
  if (state.log) renderChangelogCharts();
});

main();
loadSourceEras();
