const DATA_URL = "./data/tree.json";

const state = {
  data: null,
  selectedCatId: null,
  scheme: "devanagari",     // devanagari | iast | hk | itrans | slp1
  expanded: new Set(),      // node ids expanded in sidebar
  searchQuery: "",
};

// --- utils

function isExpandAllGesture(ev) {
  // macOS: Option=altKey. Windows/Linux: Alt=altKey.
  // Fallbacks: Shift (browser-safe), Ctrl / Cmd.
  return !!(ev.altKey || ev.shiftKey || ev.ctrlKey || ev.metaKey);
}

function sanscriptMapScheme(s) {
  // sanscript uses "devanagari" as a script name; treat it as "no transliteration"
  // For output schemes, we target roman schemes.
  return s;
}

function translitText(s) {
  if (!s) return s;
  if (state.scheme === "devanagari") return s;

  // Default assumption: source is Devanagari.
  // If later you have mixed scripts, you can add per-node hints and route here.
  try {
    return window.Sanscript.t(s, "devanagari", sanscriptMapScheme(state.scheme));
  } catch {
    return s;
  }
}

// “exceptional one we’ll target for deletion” hook:
// if you want to strip/normalize one weird pattern globally, do it here.
function normalizeTitleForDisplay(s) {
  // placeholder: implement later
  return s;
}

function displayTitle(raw) {
  return translitText(normalizeTitleForDisplay(raw));
}

function walkCategories(node, fn) {
  if (node.id) fn(node);
  for (const ch of (node.children || [])) walkCategories(ch, fn);
}

// Find category node by id
function findCatById(node, id) {
  if (!node) return null;
  if ((node.type === "category" || node.type === "collection") && node.id === id) return node;
  for (const ch of (node.children || [])) {
    const hit = findCatById(ch, id);
    if (hit) return hit;
  }
  return null;
}

function gatherDescendantCatIds(catNode) {
  const ids = [];
  walkCategories(catNode, (c) => ids.push(c.id));
  return ids;
}

function setExpandedDeep(catNode, expand) {
  walkCategories(catNode, (c) => {
    if (expand) state.expanded.add(c.id);
    else state.expanded.delete(c.id);
  });
}

// --- rendering

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.addEventListener("click", v);
    else if (k === "dataset") Object.assign(n.dataset, v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    if (typeof kid === "string") n.appendChild(document.createTextNode(kid));
    else n.appendChild(kid);
  }
  return n;
}

function formatBytes(bytes) {
  if (bytes == null) return "";
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatDate(iso) {
  if (!iso) return "";
  return iso.slice(0, 7);
}

function formatStats(stats, { includeDate } = {}) {
  if (!stats) return "";
  const size = formatBytes(stats.bytes);
  if (!size) return "";
  const parts = [size];
  if (stats.count != null) parts.push(`${stats.count} pages`);
  if (includeDate) {
    const date = formatDate(stats.last_changed);
    if (date) parts.push(date);
  }
  return `(${parts.join(", ")})`;
}

function renderSidebarTree() {
  const root = state.data.root;
  const host = document.getElementById("sidebarTree");
  host.innerHTML = "";

  const tree = renderSidebarNode(root, 0);
  host.appendChild(tree);
}

function renderSidebarNode(catNode, depth) {
  const isExpanded = state.expanded.has(catNode.id);
  const hasKids = (catNode.children || []).length > 0;

  const toggleArrow = el("span", {
    class: "toggleArrow",
    onclick: (ev) => {
      ev.stopPropagation();
      const expandAll = isExpandAllGesture(ev);
      const currentlyExpanded = state.expanded.has(catNode.id);

      if (currentlyExpanded) {
        // Collapsing: always collapse deep so that re-opening shows only immediate children
        setExpandedDeep(catNode, false);
      } else {
        // Expanding
        if (expandAll) {
          setExpandedDeep(catNode, true);
        } else {
          state.expanded.add(catNode.id);
        }
      }
      renderSidebarTree();
      renderMain();
    }
  }, hasKids ? (isExpanded ? "▾" : "▸") : "·");

  // Only show stats for root and top-level categories in the sidebar
  const statsText = (depth <= 1) ? formatStats(catNode.stats) : "";

  const row = el("div", {
    class: "row" + (state.selectedCatId === catNode.id ? " selected" : ""),
    onclick: () => {
      state.selectedCatId = catNode.id;
      renderSidebarTree();
      renderMain();
    }
  },
    toggleArrow,
    el("span", { class: "title", title: catNode.title }, displayTitle(catNode.title)),
    statsText ? el("span", { class: "small", style: "margin-left:auto; padding-left:10px; opacity:0.7;" }, statsText) : null
  );

  const wrap = el("div", { class: depth ? "indent" : "" }, row);

  if (hasKids && isExpanded) {
    for (const ch of catNode.children) {
      wrap.appendChild(renderSidebarNode(ch, depth + 1));
    }
  }
  return wrap;
}

function renderMain() {
  const host = document.getElementById("content");
  host.innerHTML = "";

  const root = state.data.root;

  if (state.searchQuery) {
    const filtered = filterTree(root, state.searchQuery);
    if (filtered) {
      // In search mode, we render the filtered root (or its children if root itself isn't the match?)
      // Actually, root always matches if anything matches (because it contains them).
      // We render it with `isSearch: true`.
      host.appendChild(renderCategoryBlock(filtered, { includePages: true, depth: 0, isRoot: true, isSearch: true }));
    } else {
      host.innerHTML = "<div class='block'>No results found.</div>";
    }
  } else {
    // focused
    const selected = findCatById(root, state.selectedCatId) || root;
    host.appendChild(renderCategoryBlock(selected, { includePages: true, depth: 0, isRoot: true }));
  }
}

function renderCategoryBlock(catNode, { includePages, depth, isRoot, isSearch }) {
  const isExpanded = true; // main pane always renders expanded blocks by default (you can change later)

  // In search mode: show stats ONLY if it's a direct match or a leaf page match?
  // Prompt: "along with their parent categories (whose size and count labels can be suppressed in this context)"
  // So: if `isSearch` is true, we ONLY show stats if `catNode.__isMatch` is true.
  // If `catNode` is just a parent container (not a match itself), stats are hidden.
  let showStats = true;
  if (isSearch && !catNode.__isMatch) {
    showStats = false;
  }

  const statsText = showStats ? formatStats(catNode.stats, { includeDate: true }) : "";
  const header = el("div", { class: "panelTitle" },
    displayTitle(catNode.title),
    statsText ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, statsText) : null
  );

  const block = el("div", { class: "block" }, header);

  if (!isExpanded) return block;

  // child categories
  for (const ch of (catNode.children || [])) {
    block.appendChild(el("div", { style: "margin-top:10px" },
      renderCategoryBlock(ch, { includePages, depth: depth + 1, isRoot: false, isSearch })
    ));
  }

  // pages
  if (includePages && (catNode.pages || []).length) {
    const ul = el("ul", {});
    for (const p of catNode.pages) {
      const a = el("a", { href: p.url, target: "_blank", rel: "noreferrer" }, displayTitle(p.title));
      const metaParts = [];
      if (p.stats?.bytes != null) metaParts.push(formatBytes(p.stats.bytes));
      const date = formatDate(p.stats?.last_changed);
      if (date) metaParts.push(date);
      const meta = metaParts.length ? ` (${metaParts.join(", ")})` : "";
      ul.appendChild(el("li", {}, a, meta ? el("span", { class: "small" }, meta) : null));
    }
    block.appendChild(el("div", { style: "margin-top:10px" },
      ul
    ));
  }

  return block;
}

// --- wiring

async function loadData() {
  const r = await fetch(DATA_URL, { cache: "no-store" });
  if (!r.ok) throw new Error(`Failed to load ${DATA_URL}: ${r.status}`);
  state.data = await r.json();

  // Safety: ensure root has an ID and Title
  if (!state.data.root.id) state.data.root.id = "root";
  if (!state.data.root.title) state.data.root.title = "ग्रन्थाः (धर्मशास्त्राणि च)";

  state.selectedCatId = state.data.root.id;
  state.expanded.add(state.data.root.id);
}

function initUI() {
  document.getElementById("schemeSelect").addEventListener("change", (ev) => {
    state.scheme = ev.target.value;
    renderSidebarTree();
    renderMain();
  });

  document.getElementById("searchInput").addEventListener("input", (ev) => {
    state.searchQuery = ev.target.value.toLowerCase().trim();
    renderMain();
  });
}

function filterTree(node, query) {
  // Check pages
  const matchingPages = (node.pages || []).filter(p => {
    const t = displayTitle(p.title).toLowerCase();
    return t.includes(query);
  });

  // Check children
  const matchingChildren = [];
  for (const ch of (node.children || [])) {
    const filteredCh = filterTree(ch, query);
    if (filteredCh) {
      matchingChildren.push(filteredCh);
    }
  }

  // Check self
  const selfTitle = displayTitle(node.title).toLowerCase();
  const selfMatch = selfTitle.includes(query);

  if (selfMatch || matchingPages.length > 0 || matchingChildren.length > 0) {
    // Return a shallow copy with filtered lists
    return {
      ...node,
      children: matchingChildren,
      pages: matchingPages,
      __isMatch: selfMatch, // Flag to indicate if the node title itself matched
    };
  }

  return null;
}

async function loadVersion() {
  try {
    const r = await fetch("./VERSION");
    if (!r.ok) return;
    const text = await r.text();
    const firstLine = text.split("\n")[0];
    if (firstLine.includes("=")) {
      const version = firstLine.split("=")[1].trim().replace(/['"]/g, "");
      const el = document.getElementById("appVersion");
      if (el) el.textContent = "v" + version;
    }
  } catch (e) {
    console.log("Could not load version:", e);
  }
}

(async function main() {
  initUI();
  loadVersion();
  await loadData();
  renderSidebarTree();
  renderMain();
})();