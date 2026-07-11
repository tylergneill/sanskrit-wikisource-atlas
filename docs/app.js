const DATA_URL = "./data/tree.json";

const state = {
  data: null,
  byId: new Map(),          // id -> node, built at load time (needed to resolve category-pointer nodes)
  siblingIds: new Map(),    // id -> [other occurrence ids of the same shared category] (both directions)
  parentPath: new Map(),    // id -> array of ancestor titles (root excluded), for "see also X > Y" hints
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

const CAT_TYPES = new Set(["category", "collection", "category-pointer"]);

function walkCategories(node, fn) {
  if (node.id) fn(node);
  for (const ch of (node.children || [])) walkCategories(ch, fn);
}

// Find category node by id (matches category-pointer occurrences too -- each is
// independently selectable, distinct from the occurrence that holds real content).
function findCatById(node, id) {
  if (!node) return null;
  if (CAT_TYPES.has(node.type) && node.id === id) return node;
  for (const ch of (node.children || [])) {
    const hit = findCatById(ch, id);
    if (hit) return hit;
  }
  return null;
}

// Resolve a category-pointer occurrence to the occurrence holding its real content
// (children/pages/stats). Non-pointer nodes resolve to themselves.
function resolveContent(node) {
  if (!node) return node;
  if (node.type === "category-pointer") return state.byId.get(node.points_to) || node;
  return node;
}

// Path of ancestor category nodes from (but not including) root down to (but not including) id.
function findAncestorPath(node, id, path = []) {
  if (!node) return null;
  if (CAT_TYPES.has(node.type) && node.id === id) return path;
  for (const ch of (node.children || [])) {
    const hit = findAncestorPath(ch, id, [...path, node]);
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
    else if (k.startsWith("on") && k.length > 2 && typeof v === "function") n.addEventListener(k.slice(2), v);
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

  for (const ch of (root.children || [])) {
    host.appendChild(renderSidebarNode(ch, 0));
  }

  const allStatsEl = document.getElementById("allStats");
  if (allStatsEl) {
    allStatsEl.textContent = formatStats(root.stats);
  }

  const allRowEl = document.getElementById("allRow");
  if (allRowEl) {
    allRowEl.classList.toggle("selected", state.selectedCatId == null || state.selectedCatId === root.id);
    allRowEl.onclick = (ev) => {
      if (isExpandAllGesture(ev)) {
        setExpandedDeep(root, true);
      }
      state.selectedCatId = null;
      renderSidebarTree();
      renderMain();
    };
  }
}

function renderSidebarNode(catNode, depth) {
  const isPointer = catNode.type === "category-pointer";
  // A pointer occurrence has no children/pages/stats of its own -- expanding it
  // browses into the occurrence that actually holds the content.
  const content = resolveContent(catNode);

  const isExpanded = state.expanded.has(catNode.id);
  const hasKids = (content.children || []).length > 0;

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

  // Every occurrence of a shared category is equally real and shows its own real
  // stats -- no occurrence is privileged over another for display purposes.
  const statsText = formatStats(catNode.stats);

  // Shared-category bookkeeping: siblings are the other occurrence(s) of this same
  // category elsewhere in the tree. Non-shared categories have none.
  const siblings = state.siblingIds.get(catNode.id) || [];
  const isShared = siblings.length > 0;
  // Group key for hover highlighting -- same value for every occurrence in the group.
  const groupKey = isPointer ? catNode.points_to : (isShared ? catNode.id : null);
  const siblingLocations = siblings
    .map((sid) => (state.parentPath.get(sid) || []).map(displayTitle).join(" > "))
    .filter(Boolean);
  const rowTitle = siblingLocations.length
    ? `Also filed under: ${siblingLocations.join("; ")}`
    : catNode.title;

  const row = el("div", {
    class: "row" + (state.selectedCatId === catNode.id ? " selected" : ""),
    dataset: groupKey ? { sharedGroup: groupKey } : {},
    title: rowTitle,
    onclick: () => {
      state.selectedCatId = catNode.id;
      renderSidebarTree();
      renderMain();
    },
    onmouseenter: () => setSharedGroupHighlight(groupKey, true),
    onmouseleave: () => setSharedGroupHighlight(groupKey, false),
  },
    toggleArrow,
    el("span", { class: depth === 0 ? "title topLevel" : "title" }, displayTitle(catNode.title)),
    statsText ? el("span", { class: depth === 0 ? "small topLevel" : "small", style: "margin-left:auto; padding-left:10px; opacity:0.7;" }, statsText) : null
  );

  const wrap = el("div", { class: depth ? "indent" : "" }, row);

  if (hasKids && isExpanded) {
    for (const ch of content.children) {
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
    // focused: render the selected node's own subtree in full, but wrapped in sticky
    // "breadcrumb" headers for its ancestors, so super-category context (e.g. दर्शनानि >
    // आस्तिकदर्शनानि > उत्तरमीमांसादर्शनम्) stays visible/stuck while scrolling, instead of
    // being discarded just because a deeper category was picked in the sidebar.
    const selected = findCatById(root, state.selectedCatId) || root;
    const ancestors = findAncestorPath(root, state.selectedCatId) || [];
    // ancestors[0] is the true root (no header of its own); skip it, keep the rest.
    const breadcrumb = ancestors.slice(1);

    const selectedBlock = renderCategoryBlock(selected, { includePages: true, depth: breadcrumb.length + 1, isRoot: false });

    let inner = selectedBlock;
    for (let i = breadcrumb.length - 1; i >= 0; i--) {
      const anc = breadcrumb[i];
      const ancDepth = i + 1;
      const header = el("div", {
        class: "panelTitle sticky-header",
        dataset: { stickyDepth: String(ancDepth) },
        style: `z-index:${1000 - ancDepth};`
      },
        displayTitle(anc.title),
        (() => {
          const s = formatStats(anc.stats, { includeDate: true });
          return s ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, s) : null;
        })()
      );
      inner = el("div", { class: "block" }, header, el("div", { style: "margin-top:10px" }, inner));
    }

    host.appendChild(inner);
  }

  positionStickyHeaders(host);
}

// Each sticky header's `top` must equal the total height of all its ancestor
// sticky headers, so nested headers stack below one another instead of overlapping.
// Computed from actual measured heights (rather than a fixed constant) since
// header height varies with title length/wrapping and stats text.
function positionStickyHeaders(host) {
  const headers = host.querySelectorAll(".sticky-header");
  for (const header of headers) {
    let offset = 0;
    let node = header.parentElement;
    while (node && node !== host) {
      const ancestorHeader = node.querySelector(":scope > .sticky-header");
      if (ancestorHeader && ancestorHeader !== header) {
        offset += ancestorHeader.getBoundingClientRect().height;
      }
      node = node.parentElement;
    }
    header.style.top = `${offset}px`;
  }
}

function renderCategoryBlock(catNode, { includePages, depth, isRoot, isSearch }) {
  const isActualRoot = catNode.id === state.data.root.id;

  // Resolve to the occurrence that actually holds children/pages (a category-pointer
  // occurrence carries none of its own -- see scrape.py's skeleton_to_json). Every
  // occurrence renders its full content here; nothing is collapsed.
  const content = resolveContent(catNode);

  // In search mode: show stats ONLY if it's a direct match or a leaf page match?
  // Prompt: "along with their parent categories (whose size and count labels can be suppressed in this context)"
  // So: if `isSearch` is true, we ONLY show stats if `catNode.__isMatch` is true.
  // If `catNode` is just a parent container (not a match itself), stats are hidden.
  let showStats = true;
  if (isSearch && !content.__isMatch) {
    showStats = false;
  }

  const statsText = showStats ? formatStats(catNode.stats, { includeDate: true }) : "";

  // "See also" hint: this category is filed under more than one parent -- name the
  // other occurrence(s) and link to jump there instead of duplicating full content.
  const siblings = state.siblingIds.get(catNode.id) || [];
  const seeAlso = siblings.length
    ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px; opacity:0.75;" },
        "see also: ",
        ...siblings.flatMap((sid, i) => {
          const loc = (state.parentPath.get(sid) || []).map(displayTitle).join(" > ") || displayTitle(catNode.title);
          const link = el("a", {
            href: "#",
            onclick: (ev) => {
              ev.preventDefault();
              state.selectedCatId = sid;
              renderSidebarTree();
              renderMain();
            },
          }, loc);
          return i === 0 ? [link] : [", ", link];
        })
      )
    : null;

  // depth (1-indexed among non-root headers) determines stacking order/offset of sticky headers.
  // Shallower headers must paint OVER deeper ones (so descendants scroll underneath their
  // ancestors' sticky headers, not on top of them) -- hence z-index decreases with depth.
  const header = isActualRoot ? null : el("div", {
    class: "panelTitle sticky-header",
    dataset: { stickyDepth: String(depth) },
    style: `z-index:${1000 - depth};`
  },
    displayTitle(catNode.title),
    statsText ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, statsText) : null,
    seeAlso
  );

  const block = el("div", { class: isActualRoot ? "" : "block" }, header);

  // child categories
  for (const ch of (content.children || [])) {
    block.appendChild(el("div", { style: "margin-top:10px" },
      renderCategoryBlock(ch, { includePages, depth: depth + 1, isRoot: false, isSearch })
    ));
  }

  // pages
  if (includePages && (content.pages || []).length) {
    const ul = el("ul", {});
    for (const p of content.pages) {
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

function indexById(node, byId, parentPath, ancestorTitles = []) {
  if (node.id) {
    byId.set(node.id, node);
    parentPath.set(node.id, ancestorTitles);
  }
  const isActualRoot = node.id === "root";
  const childAncestors = isActualRoot ? ancestorTitles : [...ancestorTitles, node.title];
  for (const ch of (node.children || [])) indexById(ch, byId, parentPath, childAncestors);
}

async function loadData() {
  const r = await fetch(DATA_URL, { cache: "no-store" });
  if (!r.ok) throw new Error(`Failed to load ${DATA_URL}: ${r.status}`);
  state.data = await r.json();

  // Safety: ensure root has an ID and Title
  if (!state.data.root.id) state.data.root.id = "root";
  if (!state.data.root.title) state.data.root.title = "ग्रन्थाः (धर्मशास्त्राणि च)";

  state.byId = new Map();
  state.parentPath = new Map();
  indexById(state.data.root, state.byId, state.parentPath);

  // Group every occurrence of a shared category (content-holder + all its
  // category-pointers) so each can look up its sibling(s), in either direction.
  const groups = new Map(); // content-holder id -> [all occurrence ids in that group]
  for (const node of state.byId.values()) {
    if (node.type === "category-pointer") {
      if (!groups.has(node.points_to)) groups.set(node.points_to, [node.points_to]);
      groups.get(node.points_to).push(node.id);
    }
  }
  state.siblingIds = new Map();
  for (const ids of groups.values()) {
    for (const id of ids) {
      state.siblingIds.set(id, ids.filter((x) => x !== id));
    }
  }

  state.selectedCatId = state.data.root.id;
  state.expanded.add(state.data.root.id);
}

// Hover highlight for occurrences of a shared category: toggles a CSS class on
// every sidebar row (content-holder + all pointer occurrences) sharing groupKey.
function setSharedGroupHighlight(groupKey, on) {
  if (!groupKey) return;
  const rows = document.querySelectorAll(`[data-shared-group="${groupKey}"]`);
  for (const row of rows) row.classList.toggle("shared-highlight", on);
}

function updateThemeToggleLabel() {
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  btn.textContent = theme === "dark" ? "☀ Light" : "☾ Dark";
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

  updateThemeToggleLabel();
  document.getElementById("themeToggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    updateThemeToggleLabel();
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
    const lines = text.split("\n");
    for (const line of lines) {
      if (!line.includes("=")) continue;
      const [key, rawValue] = line.split("=");
      const value = rawValue.trim().replace(/['"]/g, "");
      if (key.trim() === "__version__") {
        const el = document.getElementById("appVersion");
        if (el) el.textContent = "v" + value;
      } else if (key.trim() === "__data_version__") {
        const el = document.getElementById("dataUpdated");
        if (el) {
          el.textContent = `data updated ${value}`;
          el.title = "Date the scraping pipeline was last run (see About for full changelog)";
        }
      }
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