# Silent subpage category divergence

Open pipeline bug, found 2026-07-22 while investigating why Tantrāloka's
chapter pages showed up disconnected from their TOC page, in the orphan
bucket (असम्बद्धवर्गीकृतम्).

## The bug

Even where `/` subpage syntax IS used correctly, `build_page_node()` in
`pipeline/process.py` rolls up every real breadcrumb-subpage into its
top-level ancestor's category filing **unconditionally** — a subpage's own
direct category tags, if any, are completely ignored for filing purposes.
The subpage's stats fold into wherever its ancestor is filed; its own tag
is silently dropped from the output entirely (no pointer, no second
filing, nothing).

**Measured scope** (live dump, 2026-07-01): of 22,625 breadcrumb-subpages
under the 419 top-level pages that have real subpages —
- **3,100** carry at least one direct category tag of their own
- **1,823** of those differ from the parent's tags
- **1,700** carry a category not present on the parent at all

This is not noise — the divergences are frequently meaningful:
- `महाभारतम्/शांतिपर्व` tagged with its own parvan-specific category,
  distinct from parent's generic `महाभारतम्`
- `कामसूत्रम्` chapters tagged `कामसूत्रम्` itself, while the parent page is
  tagged the broader genre `कामशास्त्रम्`
- `मेघदूतम्` cantos tagged general `काव्यम्` (poetry), while the parent is
  tagged the specific genre `सन्देशकाव्यानि` (messenger-poems)

## Fix direction

This is a pipeline/frontend fix, not a wiki-editing task — no wiki edits
are needed to expose data that's already present in the dump. The mirror
needs to stop silently dropping subpage-level category tags and instead
surface multiple categories per page/subpage, the same way it already
handles multi-parented categories and multi-tagged top-level pages
(`page-pointer` / `category-pointer` nodes).

Not yet implemented.
