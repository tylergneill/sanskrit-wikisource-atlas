const DATA_URL = "./data/tree2.json";

const state = {
  data: null,
  byId: new Map(),          // id -> node, built at load time (needed to resolve category-pointer nodes)
  siblingIds: new Map(),    // id -> [other occurrence ids of the same shared category] (both directions)
  parentPath: new Map(),    // id -> array of ancestor titles (root excluded), for "see also X > Y" hints
  selectedCatId: null,
  scheme: "iast",           // devanagari | iast | hk | itrans | slp1
  expanded: new Set(),      // node ids expanded in sidebar
  searchQuery: "",
  expandedPageLists: new Set(), // category ids whose page/index-item list has been expanded past the cap
  expandedSubpages: new Set(),  // page ids whose nested subpages sub-list is expanded (collapsed by default)
};

// Rendering every descendant page as a DOM node is what's actually slow (there are
// ~20k pages total, and some single categories like पुराणानि have thousands). Any
// page/index-item list past this length renders capped with a "show all" button.
const PAGE_LIST_CAP = 300;

// --- utils

function isExpandAllGesture(ev) {
  // macOS: Option=altKey. Windows/Linux: Alt=altKey.
  // Fallbacks: Shift (browser-safe), Ctrl / Cmd.
  return !!(ev.altKey || ev.shiftKey || ev.ctrlKey || ev.metaKey);
}

function translitText(s) {
  if (!s) return s;
  if (state.scheme === "devanagari") return s;
  try {
    return window.Sanscript.t(s, "devanagari", state.scheme);
  } catch {
    return s;
  }
}

function displayTitle(raw) {
  return translitText(raw);
}

const CAT_TYPES = new Set(["category", "category-pointer"]);

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
// (children/pages/index_items/stats). Non-pointer nodes resolve to themselves.
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

// transliterated_bytes (IAST) is the primary size figure displayed: raw_bytes is
// Devanagari wikitext including markup/template overhead, not meaningful on its
// own; content_bytes is Devanagari post-strip, still not directly comparable/
// intuitive since Devanagari UTF-8 runs ~1.975x IAST bytes for the same text.
// Falls back to content_bytes (e.g. when transliteration was skipped for a faster
// pipeline run) and then raw_bytes.
function contentSizeBytes(stats) {
  if (!stats) return null;
  if (stats.transliterated_bytes) return stats.transliterated_bytes;
  if (stats.content_bytes != null) return stats.content_bytes;
  return stats.raw_bytes;
}

function formatDate(iso) {
  if (!iso) return "";
  return iso.slice(0, 7);
}

function formatStats(stats, { includeDate } = {}) {
  if (!stats) return "";
  const size = formatBytes(contentSizeBytes(stats));
  if (!size) return "";
  const parts = [size];
  if (stats.count != null) parts.push(`${stats.count} ${stats.count === 1 ? "p" : "pp"}`);
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
      closeSidebarIfMobile();
    };
    // allRowEl is a static element (re-fetched, not re-created) on every render --
    // bind the long-press gesture once rather than accumulating listeners.
    if (!allRowEl.dataset.longPressExpandBound) {
      allRowEl.dataset.longPressExpandBound = "1";
      bindLongPressExpand(allRowEl, () => {
        setExpandedDeep(root, true);
        renderSidebarTree();
        renderMain();
      });
    }
  }
}

function renderSidebarNode(catNode, depth) {
  const isPointer = catNode.type === "category-pointer";
  // A pointer occurrence has no children/pages/index_items of its own -- expanding
  // it browses into the occurrence that actually holds the content.
  const content = resolveContent(catNode);

  const isExpanded = state.expanded.has(catNode.id);
  const hasKids = (content.children || []).length > 0;

  const toggleNode = (expandAll) => {
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
  };

  const toggleArrow = el("span", {
    class: "toggleArrow",
    onclick: (ev) => {
      ev.stopPropagation();
      toggleNode(isExpandAllGesture(ev));
    }
  }, hasKids ? (isExpanded ? "▾" : "▸") : "·");
  // Touch devices have no Option/Shift-click -- long-pressing the disclosure
  // arrow does the same "expand all descendants" gesture instead.
  bindLongPressExpand(toggleArrow, () => toggleNode(true));

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
  const nameAndStats = statsText ? `${displayTitle(catNode.title)} ${statsText}` : displayTitle(catNode.title);
  const rowTitle = siblingLocations.length
    ? `Also filed under: ${siblingLocations.join("; ")}\n${nameAndStats}`
    : nameAndStats;

  const row = el("div", {
    class: "row" + (state.selectedCatId === catNode.id ? " selected" : ""),
    dataset: groupKey ? { sharedGroup: groupKey } : {},
    title: rowTitle,
    onclick: () => {
      state.selectedCatId = catNode.id;
      renderSidebarTree();
      renderMain();
      closeSidebarIfMobile();
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
      host.appendChild(renderCategoryBlock(filtered, { includeLeaves: true, depth: 0, isRoot: true, isSearch: true }));
    } else {
      host.innerHTML = "<div class='block'>No results found.</div>";
    }
  } else {
    // focused: render the selected node's own subtree in full, but wrapped in sticky
    // "breadcrumb" headers for its ancestors, so super-category context stays visible
    // while scrolling, instead of being discarded just because a deeper category was
    // picked in the sidebar.
    const selected = findCatById(root, state.selectedCatId) || root;
    const ancestors = findAncestorPath(root, state.selectedCatId) || [];
    // ancestors[0] is the true root (no header of its own); skip it, keep the rest.
    const breadcrumb = ancestors.slice(1);

    // The actual root has ~20k descendant pages across the whole tree -- fully
    // recursing here (as any other category selection does) would build DOM for
    // all of them on every initial load. Show one level of category summaries
    // instead; drilling into a specific category still renders its subtree in full.
    const isActualRoot = selected.id === root.id;
    const selectedBlock = isActualRoot
      ? renderRootOverview(selected)
      : renderCategoryBlock(selected, { includeLeaves: true, depth: breadcrumb.length + 1, isRoot: false });

    let inner = selectedBlock;
    for (let i = breadcrumb.length - 1; i >= 0; i--) {
      const anc = breadcrumb[i];
      const ancDepth = i + 1;
      const header = el("div", {
        class: "panelTitle sticky-header",
        dataset: { stickyDepth: String(ancDepth) },
        style: `z-index:${1000 - ancDepth};`
      },
        el("span", {
          class: "panelTitleLink",
          onclick: () => {
            state.selectedCatId = anc.id;
            renderSidebarTree();
            renderMain();
          },
        }, displayTitle(anc.title)),
        (() => {
          const s = formatStats(anc.stats, { includeDate: true });
          return s ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, s) : null;
        })(),
        anc.url
          ? el("a", { class: "catLinkArrow", href: anc.url, target: "_blank", rel: "noreferrer", title: "View category on Wikisource" })
          : null,
        renderSeeAlso(anc)
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

// Lightweight initial/root view: one row per top-level category with its stats,
// clickable to drill in via the same selection path as clicking the sidebar.
// See the comment at its call site in renderMain() for why this exists.
function renderRootOverview(root) {
  const block = el("div", {});
  for (const ch of (root.children || [])) {
    const content = resolveContent(ch);
    const statsText = formatStats(ch.stats, { includeDate: true });
    const row = el("div", {
      class: "block panelTitle",
      style: "cursor:pointer;",
      onclick: () => {
        state.selectedCatId = ch.id;
        renderSidebarTree();
        renderMain();
      },
    },
      displayTitle(ch.title),
      statsText ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, statsText) : null,
      (content.children || []).length
        ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px; opacity:0.6;" }, `${content.children.length} subcategories`)
        : null,
    );
    block.appendChild(row);
  }
  return block;
}

// Renders a single Main-namespace page's <li> (link + size/date meta), plus, if
// it has its own MediaWiki subpages (build_tree.MainPageNode nesting), a
// collapsible toggle that reveals a nested indented sub-list. Deliberately NOT
// the same visual treatment as category nesting (renderCategoryBlock): no sticky
// header, no per-node stats block, just a plain indented <ul> -- reads as "parts
// of this page" (structural, from the page-title graph itself) rather than "a
// subcategory" (an editorial grouping).
function renderPageLi(p) {
  const hasSubpages = (p.subpages || []).length > 0;
  // In search mode, a matched nested subpage must stay visible even if the user
  // never manually expanded its parent -- force-expand rather than hide the hit.
  const isExpanded = state.expandedSubpages.has(p.id) || (state.searchQuery && hasSubpages);

  const a = el("a", { href: p.url, target: "_blank", rel: "noreferrer" }, displayTitle(p.title));

  // p.stats is already a full rollup (this page's own size/date plus every
  // descendant subpage's, see publish.py's build_page_node) -- shown as-is,
  // reads like a mini category total whether collapsed or expanded.
  const metaParts = [];
  const pageSize = contentSizeBytes(p.stats);
  if (pageSize != null) metaParts.push(formatBytes(pageSize));
  const date = formatDate(p.stats?.last_changed);
  if (date) metaParts.push(date);
  const meta = metaParts.length ? ` (${metaParts.join(", ")})` : "";

  const toggle = hasSubpages
    ? el("span", {
        class: "toggleArrow subpageToggle",
        onclick: (ev) => {
          ev.preventDefault();
          if (isExpanded) state.expandedSubpages.delete(p.id);
          else state.expandedSubpages.add(p.id);
          renderMain();
        },
      }, isExpanded ? "▾" : "▸")
    : null;

  const row = el("div", { class: "pageRow" },
    el("span", { class: "pageRowMain" },
      a,
      meta ? el("span", { class: "small" }, meta) : null,
      hasSubpages
        ? el("span", { class: "small", style: "margin-left:6px; opacity:0.6;" }, `(+ ${p.subpages.length} pp)`)
        : null,
    ),
    toggle,
  );

  const li = el("li", {}, row);

  if (hasSubpages && isExpanded) {
    const subUl = el("ul", { class: "subpageList" });
    for (const sp of p.subpages) {
      subUl.appendChild(renderPageLi(sp));
    }
    li.appendChild(subUl);
  }

  return li;
}

// Renders a single Index-namespace item (untranscluded scan/OCR-source page --
// see transclusion.is_transcluded and publish.py's build_index_item_node).
// Never expandable into individual पृष्ठम्:Title/N (scanned-leaf) rows --
// Index is the organizing principle pre-transclusion, so leaves are only
// ever summed into one rolled-up stat on the Index item, never listed (see
// notes/sawikisource-scraper-spec.md, "Untranscluded Index items"). This is
// a scanned book with OCR proofing underway on Wikisource but NOT YET
// assembled into a readable mainspace article -- there is nothing to click
// through to but the raw scan. Badge text and tooltip spell that out
// explicitly (a bare "Index" badge reads as a content-type label, not a
// "not real content yet" warning). stats here already include the
// पृष्ठम्:Title/N rollup (see build_index_item_node/compute_page_ns_rollup),
// so the byte size shown is the real scanned/proofread content size, not
// just the Index page's own near-empty proofreading-status scaffolding.
function renderIndexItemLi(item) {
  const a = el("a", { href: item.url, target: "_blank", rel: "noreferrer" }, displayTitle(item.title));

  const metaParts = [];
  const size = contentSizeBytes(item.stats);
  if (size != null) metaParts.push(formatBytes(size));
  const date = formatDate(item.stats?.last_changed);
  if (date) metaParts.push(date);
  const meta = metaParts.length ? ` (${metaParts.join(", ")})` : "";

  return el("li", {},
    el("span", { class: "pageRow" },
      el("span", { class: "indexBadge", title: "Scanned/OCR source, not yet a finished mainspace text" }, "OCR only"),
      a,
      meta ? el("span", { class: "small" }, meta) : null,
    )
  );
}

// "See also" hint: this category is filed under more than one parent -- name the
// other occurrence(s) and link to jump there instead of duplicating full content.
// Shared by renderCategoryBlock (for the selected node and its descendants) and
// the breadcrumb ancestor headers in renderMain (ancestors are shared categories
// just as often as the selected node itself, and previously lost this hint).
function renderSeeAlso(catNode) {
  const siblings = state.siblingIds.get(catNode.id) || [];
  if (!siblings.length) return null;
  return el("span", { class: "small", style: "font-weight:normal; margin-left:8px; opacity:0.75;" },
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
  );
}

function renderCategoryBlock(catNode, { includeLeaves, depth, isRoot, isSearch }) {
  const isActualRoot = catNode.id === state.data.root.id;

  // Resolve to the occurrence that actually holds children/pages/index_items (a
  // category-pointer occurrence carries none of its own). Every occurrence
  // renders its full content here; nothing is collapsed.
  const content = resolveContent(catNode);

  // In search mode: only show stats if this node itself is a direct title match
  // (parent containers pulled in only because a descendant matched stay quiet).
  let showStats = true;
  if (isSearch && !content.__isMatch) {
    showStats = false;
  }

  const statsText = showStats ? formatStats(catNode.stats, { includeDate: true }) : "";

  // Link to the live Wikisource category page itself -- a plain external-link arrow
  // (no visible text) placed right after the stats parenthesis. catNode.url is set by
  // pipeline/process.py's build_category() for both "category" and "category-pointer"
  // nodes; the synthetic spliced root has none (not a real wiki category).
  const catLink = showStats && catNode.url
    ? el("a", { class: "catLinkArrow", href: catNode.url, target: "_blank", rel: "noreferrer", title: "View category on Wikisource" })
    : null;

  const seeAlso = renderSeeAlso(catNode);

  // depth (1-indexed among non-root headers) determines stacking order/offset of sticky headers.
  // Shallower headers must paint OVER deeper ones (so descendants scroll underneath their
  // ancestors' sticky headers, not on top of them) -- hence z-index decreases with depth.
  const header = isActualRoot ? null : el("div", {
    class: "panelTitle sticky-header",
    dataset: { stickyDepth: String(depth) },
    style: `z-index:${1000 - depth};`
  },
    el("span", {
      class: "panelTitleLink",
      onclick: () => {
        state.selectedCatId = catNode.id;
        renderSidebarTree();
        renderMain();
      },
    }, displayTitle(catNode.title)),
    statsText ? el("span", { class: "small", style: "font-weight:normal; margin-left:8px;" }, statsText) : null,
    catLink,
    seeAlso
  );

  const block = el("div", { class: isActualRoot ? "" : "block" }, header);

  // child categories
  for (const ch of (content.children || [])) {
    block.appendChild(el("div", { style: "margin-top:10px" },
      renderCategoryBlock(ch, { includeLeaves, depth: depth + 1, isRoot: false, isSearch })
    ));
  }

  // Main-namespace pages and Index-namespace items are rendered as two separate
  // lists (rather than merged) since they're structurally different: pages nest
  // via subpages, index items never expand into page-namespace detail.
  if (includeLeaves) {
    const leafPages = content.pages || [];
    const indexItems = content.index_items || [];

    if (leafPages.length) {
      const isExpanded = state.expandedPageLists.has(catNode.id + ":pages");
      const capped = !isExpanded && leafPages.length > PAGE_LIST_CAP;
      const shown = capped ? leafPages.slice(0, PAGE_LIST_CAP) : leafPages;

      const ul = el("ul", {});
      for (const p of shown) ul.appendChild(renderPageLi(p));

      const showAllBtn = capped
        ? el("button", {
            class: "theme-toggle",
            type: "button",
            style: "margin-top:8px;",
            onclick: () => {
              state.expandedPageLists.add(catNode.id + ":pages");
              renderMain();
            },
          }, `Show all ${leafPages.length} pages`)
        : null;

      block.appendChild(el("div", { style: "margin-top:10px" }, ul, showAllBtn));
    }

    if (indexItems.length) {
      const isExpanded = state.expandedPageLists.has(catNode.id + ":index");
      const capped = !isExpanded && indexItems.length > PAGE_LIST_CAP;
      const shown = capped ? indexItems.slice(0, PAGE_LIST_CAP) : indexItems;

      const ul = el("ul", {});
      for (const item of shown) ul.appendChild(renderIndexItemLi(item));

      const showAllBtn = capped
        ? el("button", {
            class: "theme-toggle",
            type: "button",
            style: "margin-top:8px;",
            onclick: () => {
              state.expandedPageLists.add(catNode.id + ":index");
              renderMain();
            },
          }, `Show all ${indexItems.length} index items`)
        : null;

      block.appendChild(el("div", { style: "margin-top:10px" }, ul, showAllBtn));
    }
  }

  return block;
}

// --- wiring

function indexById(node, byId, parentPath, ancestorTitles = []) {
  if (node.id) {
    byId.set(node.id, node);
    parentPath.set(node.id, ancestorTitles);
  }
  const isActualRoot = ancestorTitles.length === 0 && node === state.data.root;
  const childAncestors = isActualRoot ? ancestorTitles : [...ancestorTitles, node.title];
  for (const ch of (node.children || [])) indexById(ch, byId, parentPath, childAncestors);
}

async function loadData() {
  const r = await fetch(DATA_URL);
  if (!r.ok) throw new Error(`Failed to load ${DATA_URL}: ${r.status}`);
  state.data = await r.json();

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

const SUN_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 3v2"/><path d="M12 19v2"/><path d="M5 5l1.4 1.4"/><path d="M17.6 17.6L19 19"/><path d="M3 12h2"/><path d="M19 12h2"/><path d="M5 19l1.4-1.4"/><path d="M17.6 6.4L19 5"/></svg>`;
const MOON_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/></svg>`;

function updateThemeToggleLabel() {
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  const icon = theme === "dark" ? SUN_ICON : MOON_ICON;
  const label = theme === "dark" ? "Light" : "Dark";
  btn.innerHTML = `${icon}<span class="toggle-label">${label}</span>`;
}

const MOBILE_BREAKPOINT = 800;

function isMobileLayout() {
  return window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches;
}

function openSidebar() {
  document.getElementById("sidenav").classList.add("open");
  document.getElementById("sidebarBackdrop").classList.add("open");
  document.getElementById("sidebarToggle").setAttribute("aria-expanded", "true");
  // Body itself scrolls at the mobile breakpoint (see .layout in styles.css), so
  // the mainpane's own overflow isn't enough to stop background scroll -- lock body.
  document.body.classList.add("sidebar-open-lock");
}

function closeSidebar() {
  document.getElementById("sidenav").classList.remove("open");
  document.getElementById("sidebarBackdrop").classList.remove("open");
  document.getElementById("sidebarToggle").setAttribute("aria-expanded", "false");
  document.body.classList.remove("sidebar-open-lock");
  hideTooltipPopup();
}

function closeSidebarIfMobile() {
  if (isMobileLayout()) closeSidebar();
}

const SIDEBAR_WIDTH_MIN = 240;
const SIDEBAR_WIDTH_MAX = 800;

function applySidebarWidth(px) {
  document.documentElement.style.setProperty("--sidebar-width", `${px}px`);
}

function initSidebarResizer() {
  const resizer = document.getElementById("sidebarResizer");
  if (!resizer) return;

  const saved = parseFloat(localStorage.getItem("sidebarWidth"));
  if (!Number.isNaN(saved)) applySidebarWidth(saved);

  resizer.addEventListener("pointerdown", (ev) => {
    if (isMobileLayout()) return;
    ev.preventDefault();
    resizer.setPointerCapture(ev.pointerId);
    resizer.classList.add("dragging");
    const startX = ev.clientX;
    const startWidth = document.getElementById("sidenav").getBoundingClientRect().width;

    const onMove = (moveEv) => {
      const raw = startWidth + (moveEv.clientX - startX);
      const clamped = Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, raw));
      applySidebarWidth(clamped);
    };
    const onUp = () => {
      resizer.classList.remove("dragging");
      resizer.releasePointerCapture(ev.pointerId);
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
      const width = document.getElementById("sidenav").getBoundingClientRect().width;
      localStorage.setItem("sidebarWidth", String(Math.round(width)));
    };
    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
  });

  resizer.addEventListener("dblclick", () => {
    document.documentElement.style.removeProperty("--sidebar-width");
    localStorage.removeItem("sidebarWidth");
  });
}

// Native `title` tooltips never fire on touch devices (no hover concept), so
// on mobile a long-press on any element carrying a `title` attribute (sidebar
// rows, badges, etc.) shows the same text in a small floating popup instead.
// Delegated at the document level rather than wired per-row, so it works for
// every current and future `title`-bearing element without extra plumbing.
const LONG_PRESS_MS = 500;
let longPressTimer = null;
let longPressTarget = null;
// Set true the moment a long-press popup is actually shown (timer fired, not
// just started), so the click that mobile browsers synthesize right after
// the matching touchend can be told apart from a normal tap-to-select and
// suppressed -- otherwise every long-press-to-view-tooltip also navigated/
// selected the row underneath it.
let longPressFired = false;

function showTooltipPopup(text, x, y) {
  hideTooltipPopup();
  const popup = el("div", { class: "touch-tooltip", style: `left:${x}px; top:${y}px;` }, text);
  document.body.appendChild(popup);
  longPressTarget = popup;
}

function hideTooltipPopup() {
  if (longPressTarget) {
    longPressTarget.remove();
    longPressTarget = null;
  }
}

// Touch-device equivalent of isExpandAllGesture's Option/Shift/Ctrl/Cmd-click:
// binds a long-press on `element` to `onExpandAll`, and swallows the
// synthesized click that follows touchend so a long-press doesn't also
// trigger the element's normal (single-level) onclick handler.
function bindLongPressExpand(element, onExpandAll) {
  let timer = null;
  let fired = false;

  element.addEventListener("touchstart", (ev) => {
    fired = false;
    timer = setTimeout(() => {
      fired = true;
      if (navigator.vibrate) navigator.vibrate(15);
      onExpandAll();
    }, LONG_PRESS_MS);
  }, { passive: true });

  const cancel = () => clearTimeout(timer);
  element.addEventListener("touchmove", cancel, { passive: true });
  element.addEventListener("touchend", cancel);
  element.addEventListener("touchcancel", cancel);

  element.addEventListener("click", (ev) => {
    if (fired) {
      fired = false;
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, { capture: true });
}

function initLongPressTooltips() {
  document.addEventListener("touchstart", (ev) => {
    hideTooltipPopup();
    const target = ev.target.closest("[title]");
    if (!target) return;
    const touch = ev.touches[0];
    const x = touch.clientX;
    const y = touch.clientY;
    longPressTimer = setTimeout(() => {
      longPressFired = true;
      showTooltipPopup(target.getAttribute("title"), x, y);
    }, LONG_PRESS_MS);
  }, { passive: true });

  const cancel = () => clearTimeout(longPressTimer);
  document.addEventListener("touchmove", cancel, { passive: true });
  document.addEventListener("touchend", cancel);
  document.addEventListener("touchcancel", cancel);

  // Swallow the click a mobile browser synthesizes right after the touch
  // that triggered our popup, so releasing a long-press doesn't also
  // select/navigate the row underneath. Capture phase so it runs before the
  // row's own onclick handler.
  document.addEventListener("click", (ev) => {
    if (longPressFired) {
      longPressFired = false;
      ev.preventDefault();
      ev.stopPropagation();
    }
  }, { capture: true });

  // iOS/Safari's native long-press (text selection callout, "copy" /
  // "search with Google" context menu) fires independently of our JS timer
  // above -- block it specifically on title-bearing elements so it doesn't
  // show up alongside our popup.
  document.addEventListener("contextmenu", (ev) => {
    if (ev.target.closest("[title]")) ev.preventDefault();
  });
}

function initUI() {
  initSidebarResizer();
  initLongPressTooltips();

  document.getElementById("sidebarToggle").addEventListener("click", () => {
    const sidenav = document.getElementById("sidenav");
    if (sidenav.classList.contains("open")) closeSidebar();
    else openSidebar();
  });
  document.getElementById("sidebarBackdrop").addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeSidebar();
  });

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

// Checks a Main-namespace page (or subpage) against the query, recursively
// including its nested subpages -- a subpage whose own title matches (even if
// its parent's doesn't) still needs to surface in search.
function filterPage(p, query) {
  const matchingSubpages = (p.subpages || [])
    .map(sp => filterPage(sp, query))
    .filter(Boolean);
  const selfMatch = displayTitle(p.title).toLowerCase().includes(query);
  if (selfMatch || matchingSubpages.length > 0) {
    return { ...p, subpages: matchingSubpages };
  }
  return null;
}

function filterIndexItem(item, query) {
  return displayTitle(item.title).toLowerCase().includes(query) ? item : null;
}

function filterTree(node, query) {
  const matchingPages = (node.pages || []).map(p => filterPage(p, query)).filter(Boolean);
  const matchingIndexItems = (node.index_items || []).map(i => filterIndexItem(i, query)).filter(Boolean);

  const matchingChildren = [];
  for (const ch of (node.children || [])) {
    const filteredCh = filterTree(ch, query);
    if (filteredCh) matchingChildren.push(filteredCh);
  }

  const selfTitle = displayTitle(node.title).toLowerCase();
  const selfMatch = selfTitle.includes(query);

  if (selfMatch || matchingPages.length > 0 || matchingIndexItems.length > 0 || matchingChildren.length > 0) {
    return {
      ...node,
      children: matchingChildren,
      pages: matchingPages,
      index_items: matchingIndexItems,
      __isMatch: selfMatch,
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
          el.textContent = `${value}`;
          el.title = "Date the pipeline was last run (see About for full changelog)";
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
