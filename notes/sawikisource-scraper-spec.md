# Sanskrit Wikisource Scraper & Browsing Pipeline — System Spec

## Goal
Build an alternate browsing interface over Sanskrit Wikisource (sa.wikisource.org) content, organized as a category tree rooted at *Vargasarvasvam*, showing finished (Main-namespace) works and — where nothing has been published yet — untranscluded raw OCR (Index-namespace) items, clearly marked as such. Ground-up redesign, replacing a previous scraping approach.

## Data acquisition
- **Source**: MediaWiki Content File Exports (`mediawiki_content_current`), *not* the deprecated legacy XML dumps and *not* the live Action API for bulk content. `wiki_id = sawikisource`.
- **Location**: `https://dumps.wikimedia.org/other/mediawiki_content_current/sawikisource/<date>/xml/bzip2/`
- **Discovery**: no API involved. Check for `SHA256SUMS` at candidate dates (generation starts monthly on the 1st); its presence means that run is complete. Download every listed part file, verify against the published hashes.
- **Cadence**: monthly, driven by a GitHub Action (fetch → build → publish, one button/one workflow run). Full-refresh model, not incremental — simpler correctness given the wiki's modest size and the dump's own monthly cadence.
- **Scope of the single XML file**: all namespaces in one export, including Main, Category, Index, Page, Template, and Module — one download covers every input the pipeline needs. Page namespace is used, but narrowly: only to roll up size/date stats onto its owning (untranscluded) Index item, never to enumerate/browse individual scanned leaves — see "Untranscluded Index items" below.

## Namespace roles
| NS | Role |
|---|---|
| Main (0) | Finished, reader-facing works; also the source of transclusion tags |
| Category (14) | Category graph nodes |
| Index (106)\* | Per-work scan metadata; surfaced only when untranscluded |
| Page (104)\* | Per-scanned-leaf OCR/proofread text (`Title/N`); read only to roll up stats onto its owning Index item when untranscluded — never browsed/listed leaf-by-leaf |
| Template / Module | Not part of the content model — used only to support accurate size/text computation (see below) |

\*Confirm actual namespace ID from the dump's own siteinfo rather than hardcoding; IDs are wiki-configurable.

## Relationship structures

**Main-namespace tree (breadcrumb parsing).** Parent/child is determined entirely by splitting titles on the last `/` (e.g. `Work/Part 1` → parent `Work`). Unlike Category, this is a genuine tree — every title has exactly one parent, no multi-parent, no cycles possible by construction. Bottom-up rollups (size, latest-change) need no dedup logic here.

**Category graph.** Edges come from `[[Category:Parent]]` on each Category page's own body — a manually-maintained **directed graph, not guaranteed acyclic**. Traversal/rollup code must guard against cycles explicitly. Root is *Vargasarvasvam*. Two structural facts to design around:
- A category may have multiple parents (multi-filing) — represent with notes/pointers in the display, and dedup via memoized reachable-descendant sets for any size/count rollup, so content reachable via two paths isn't double-counted.
- Not everything reaches the root — disconnected components exist and are listed separately as minor trees or singletons, not forced under Vargasarvasvam.

**Content → category membership.** Both Main pages and Index items may carry zero, one, or several direct category tags (`categorylinks`). **No general bubble-up** — a category on a deep subpage (e.g. *Bhagavad Gītā* tagged on specific Bhīṣmaparvan adhyāyas) must not cause the parent works (Bhīṣmaparvan, Mahābhārata) to inherit it. **One narrow exception**: if *every* subpage under a root shares a category the root itself lacks, that's treated as a probable tagging gap — the category is inferred and displayed on the root, visually marked as inferred (display-only; this never edits the live wiki).

**Insufficiently-categorized content (असम्बद्धवर्गीकृतम्).** Content is unreachable from Vargasarvasvam by category descent for two reasons, treated identically regardless of namespace (Main page or untranscluded Index item): (a) it carries no category tag at all, or (b) every tag it does carry points to a category that is itself never filed under any parent reachable from root (an orphaned category subtree — confirmed real, e.g. the Mahābhārata parvan categories exist as a full hierarchy on the live wiki but are never linked in from anywhere reachable). Such content is swept into a single unified bucket, titled असम्बद्धवर्गीकृतम्, appended as a sibling of the real category tree — not "उncategorized" (अवर्गीकृतम्), since case (b) genuinely does carry a tag, just not one that connects anywhere. Orphaned-category roots are walked in as real subtrees (same tree-building logic as the main walk, so multi-parenting/dedup works identically); zero-tag leftovers are flat pages/index-items directly on the bucket. The central tree's own headline stats (root) deliberately exclude this bucket's totals — it's fully browsable, just not counted as part of the "central," well-organized corpus size.

## Transclusion detection (Main ↔ Index)
Parsed entirely from Main-namespace content — no Page-namespace fetch needed. Extract `<pages index="Work.pdf" from=A to=B />` tags:
- **Any transclusion at all** targeting a given Index → treated as sufficiently complete. The Index is dropped from display; only the Main-namespace content is surfaced.
- **Zero transclusion** → the Index item itself is surfaced in the category tree (using the Index's own category tags), explicitly marked as untranscluded/raw OCR content.
- This is intentionally **not** conflated with proofreading (quality/color) status — a fully-proofread (green) work may still be untranscluded if no one has assembled the Main page yet, and vice versa isn't assumed either.

## Size and "latest change" computation
Rejects the previous approach's two heuristics (regex-based markup exclusion; an estimated Devanagari→IAST expansion factor) in favor of full local computation against the downloaded dump:
1. Parse each relevant page's wikitext with **wikitextprocessor** (and/or `mwparserfromhell` for simpler cases) to strip markup properly.
2. **Expand templates for real**: look up the matching `Template:Name` page from the same dump, substitute parameters, recursively re-parse — plain `mwparserfromhell` only describes a template call, it doesn't evaluate it.
3. Check whether templates actually in use invoke Scribunto (`{{#invoke:Module|...}}`). If so and if it affects visible text materially, evaluate via a Lua bridge (e.g. `lupa`) against the dump's `Module:` pages; if templates in practice are layout/whitespace-only, skip this.
4. Run actual **SLP1/IAST transliteration** via the existing Skrutable hub for the size metric, rather than estimating an expansion factor.

## Display abstraction
Category tree is the primary browsing structure. Top-level (Index/root) nodes show a summary — total size, latest change date — by default; finer subpage-level detail is available but collapsed.

**Untranscluded Index items.** Index is the organizing principle pre-transclusion — there's no need to enumerate individual `Page:Title/N` leaves in the tree or the UI. Instead, all of an Index item's `Page:` children are read once to compute a single rolled-up stat (summed content/raw/transliterated bytes, `max()` last-changed) attached to the Index item itself, the same rollup shape a Main-namespace work gets from its subpages. The Index item still renders as one flat, non-expandable leaf entry (marked as OCR/untranscluded), just with real stats instead of the near-zero figure its own scaffold-only wikitext would otherwise produce.

## Pipeline stages
1. **Fetch** (GitHub Action, monthly) — locate + download + verify the dump.
2. **Build** — parse dump once into per-namespace page/revision records; construct the Main subpage tree; construct the Category digraph; detect transclusion tags and classify Index items transcluded/untranscluded; compute expanded text, size, and transliteration; compute all rollups (tree-based size/date, DAG-based dedup for category rollups, the narrow category-inference rule).
3. **Publish** — materialize the result into whatever store/format the alternate interface reads.

Fetch and Build/Publish are decoupled so tree/dedup/expansion logic can be iterated locally against a cached dump without re-downloading.

## Explicit non-goals (v1)
- Sub-monthly freshness (would require live API + `list=recentchanges` deltas — deferred).
- Full revision history (using `mediawiki_content_current`, not `_history`).
- Partial-transclusion coverage tracking at Page-namespace granularity (binary transcluded/untranscluded chosen deliberately).
