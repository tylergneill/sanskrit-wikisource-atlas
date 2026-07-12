# Wikisource Page & Category Ontology — Reference

## Namespaces (the core content triad + adjuncts)
| NS | Purpose | Categorized in practice? |
|---|---|---|
| **0 (Main)** | Finished, reader-facing works | Yes |
| **106 (Page)**\* | Raw OCR/proofread text, one per scanned leaf, side-by-side with scan image | Rare — would mean categorizing hundreds of near-duplicate scan-pages per book; if seen, usually a bot/status template, not real subject categorization |
| **104 (Index)**\* | Per-work metadata + `<pagelist>`; one per scanned document | Common — subject/language categories, plus maintenance categories often auto-derived from proofreading status |
| **14 (Category)** | Standard MediaWiki namespace, not ProofreadPage-specific | — |
| **Author:** | Wikisource-specific; one page per author, linked from Main works | — |

\*Namespace IDs 104/106 are common but wiki-configurable, not universal (see `$wgProofreadPageNamespaceIds`; there's an ongoing effort to standardize to 250/252).

## Subpages, breadcrumbs, and "hierarchy" (Main namespace only)
- The **`/` in a title** (e.g. `Work/Chapter 1`) is a pure **render-time string-parsing convention** — MediaWiki finds the last `/` and auto-displays a breadcrumb link up (`< Work`). Nothing is stored in the DB as a parent–child edge.
- The breadcrumb is **one-directional** (child → parent, auto-generated). The reverse (parent listing its children) must be built manually — typically a table of contents on the index page with `[[Work/Chapter 1]]` links.
- To discover the full graph via API: enumerate all Main-namespace titles (`list=allpages`), then split each on the last `/` — everything before it is the parent. No stored `parent_id` field exists.
- Nothing stops a child from also linking `[[Parent Title]]` in its own body — that's just an ordinary `pagelinks` edge, not a breadcrumb, and doesn't require the `/` convention.
- **Talk pages** use the same string-parsing trick (split on namespace prefix instead of `/`).

## Proofreading workflow (Index/Page → Main)
1. Upload scan (PDF/DjVu) to Commons → create `Index:File.pdf`. The `Pages` field is auto-populated from PDF page-count metadata, but stored as a **compact `<pagelist>` markup string** (e.g. `1-4=-,5=1,6to200=roman`), not a queryable array — parsed into ranges/labels at render time.
2. Individual `Page:File.pdf/N` pages are **not pre-created**. Clicking an unclicked page number opens a **draft** edit form; nothing exists until **Save** is pressed.
3. OCR is triggered **per page**, on first edit, via a toolbar button (backends: legacy Tesseract gadget, Wikimedia OCR tool, or Google Vision).
4. Whole-item batch OCR/page-creation is **not built into the core UI** but is achievable via bots (e.g. pywikibot loop + OCR call per page).
5. Once Page: items reach a satisfactory proofreading status, some **Main-namespace page** (title need not match the Index title) transcludes the desired range(s):
   `<pages index="Work.pdf" from=1 to=10 />` — multiple `<pages .../>` calls can pull discontinuous ranges, and can span multiple different Main pages if one scan is split into several published works.
6. Alternative to transclusion: **copy-pasting** Page: text directly into Main. Looks identical to readers but creates **no `templatelinks` edge**, no sync on later corrections, and no discoverable link back to the source scan except manual inspection.

## Stored vs. derived relations
| Relation | Storage | How to query |
|---|---|---|
| Subpage parent/child (`/`) | **Not stored** — string-parsed at render | Split title on last `/` |
| Redirects | **Stored** (`redirect` table: `rd_from`/`rd_title`) | `prop=redirects` |
| Page → Category | **Stored** (`categorylinks`) | `prop=categories` |
| Page → Page wikilink | **Stored, directed** (`pagelinks`) | `prop=links` / `list=backlinks` |
| Transclusion (incl. `<pages/>`) | **Stored, directed** (`templatelinks`, marked as transclusion) | From source: `prop=templates`; from target: `prop=transcludedin` / `list=embeddedin` — inverse views of the same table |

## Category graph
- `[[Category:Parent]]` on Category A's own page makes A a child of Parent — just a manual wikilink, same mechanism as any category tag.
- **No guarantee of a single connected graph.** Nothing enforces convergence on one root (e.g. Sanskrit Wikisource's `Vargasarvasvam`):
  - Categories can be orphaned (no parent at all).
  - Small disconnected clusters can exist alongside the main hierarchy.
  - Cycles (A→B→A) are possible — no cycle-checking.
- Verifying real structure requires querying `categorylinks` and checking graph connectivity directly — the "single tree" is an editorial convention, not a technical guarantee.

## Byte-count / content-size gotchas
- `rvprop=size` = **raw stored wikitext bytes** (fast, batchable up to ~500 titles/request) — for a transcluding Main page, this is tiny (just the `<pages/>` tag), not the expanded content.
- `action=parse` / rendered HTML fetch = includes **expanded transcluded content** → double-counts if summed naively against the Page: pages' own sizes.
- Getting a true markup-free content length requires `rvprop=content` (or `rvslots=main`) + a real wikitext parser (e.g. Python's `mwparserfromhell`) to strip templates, refs, comments, tables, etc. — regex on `[[ ]]`/`{{ }}` alone is far from sufficient (headers, bold/italic, lists, `<ref>`, comments, external links, tables, `----`, `<nowiki>`, entities all need handling too).
- Cost tradeoff: content-fetch is roughly two orders of magnitude slower than size-only, driven almost entirely by request-count caps (few hundred vs. ~1 title per batch when content is included), not parse time. Sampling both on a subset and computing a correction ratio is a reasonable shortcut.

## API query pattern: `prop` vs `list`, and the `-prop` suffix convention
- **`prop`** modules take titles you already have and return properties **of** each (`prop=revisions`, `prop=categories`, `prop=transcludedin`).
- **`list`** modules take a criterion and enumerate an open-ended, one-to-many set of **other** pages (`list=allpages`, `list=categorymembers`, `list=backlinks`, `list=embeddedin`).
- Each module has its own sub-field selector, prefixed by its short code + `prop`:
  - `prop=revisions` → `rvprop` (content, size, timestamp, user…)
  - `prop=categories` → `clprop`
  - `prop=imageinfo` → `iiprop`
  - `list=categorymembers` → `cmprop`
  - `list=embeddedin` → `eiprop`
  - `list=backlinks` → `blprop`
- `categorymembers` is a `list`, not a `prop` of Category, because it's a one-to-many enumeration (all pages in a category), not a property lookup on a single title.
