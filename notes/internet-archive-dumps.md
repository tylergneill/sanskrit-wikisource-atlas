# Internet Archive dumps: what exists, and how to get it

**Status: not used by this pipeline, deliberately.** The code that fetched
these was removed on 2026-08-01. This note exists so the knowledge survives
the code — if the decision is ever revisited, or if someone needs a genuine
period-accurate snapshot for a different purpose, everything needed is here.

For *why* it isn't used, see `interpretive-decisions.md` §6. Short version: a
real archived dump records the titles pages bore at that date, while this
pipeline's materialized reconstruction records the titles they bear today.
Since `text_count` derives from title breadcrumbs, the two count the same
corpus differently, so mixing them stepped the historical series at every
source switch. One method applied uniformly beats a patchwork of more
authentic individual months.

## Why you might still want these

Everything materialization gets wrong, an archived dump gets right:

- **Period-accurate titles.** The only way to know what a page was actually
  called in 2015. Materialization cannot recover this — a page renamed in
  2020 appears renamed in 2013.
- **Pages later deleted.** A page created in 2015 and deleted in 2017 exists
  in the 2016 archived dump and in *no* materialized month, because it is
  absent from the meta-history dump entirely.
- **Independent verification.** The only external check on whether
  materialization is reconstructing a month correctly. See
  `pipeline/validate_materialization.py`, which was written for exactly this
  and is kept in the repo though not run automatically.

## Coverage

76 monthly snapshots, **2011-09 through 2022-05**. sa.wikisource's volunteer
upload pipeline stalled after 2022-05-01; nothing newer will appear.

Contiguous runs:

| Range | Months |
|---|---|
| 2011-09 .. 2011-10 | 2 |
| 2014-07 .. 2014-12 | 6 |
| 2015-02 .. 2015-04 | 3 |
| 2015-06 .. 2018-03 | 34 |
| 2018-08 .. 2019-03 | 8 |
| 2020-07 .. 2022-05 | 23 |

Interior gaps (no IA dump exists for these):

| Gap | Months |
|---|---|
| 2011-11 .. 2014-06 | 32 |
| 2015-01 | 1 |
| 2015-05 | 1 |
| 2018-04 .. 2018-07 | 4 |
| 2019-04 .. 2020-06 | 15 |

**2011-09 and 2011-10 are unusable for this project** regardless of the
above. They predate `वर्गसर्वस्वम्` (created 2012-01-20) and the
ProofreadPage extension — only 3 categories existed on the whole site, none
of them the root. `process_dump()` raises `RootCategoryMissing` on them. This
is why `MATERIALIZED_FLOOR` is 2012-02.

So the genuinely useful IA coverage is **74 months, 2014-07 .. 2022-05**, with
the four interior gaps above.

## How to fetch one

Each snapshot is one Internet Archive item named `sawikisource-<YYYYMMDD>`,
holding the classic MediaWiki export format (**not** the current "Content File
Export" format the live pipeline uses):

```
https://archive.org/download/sawikisource-<YYYYMMDD>/sawikisource-<YYYYMMDD>-pages-meta-current.xml.bz2
```

The date is the dump run's own date, which is **not** the first of the month —
e.g. 2014-07 is `20140715`, 2011-10 is `20111013`. You cannot construct the URL
from a `YYYY-MM` alone; you need the exact run date. Two ways to get it:

1. **The cached listing**, if still present: `dump/_fetch_legacy_months_cache.json`
   maps `YYYY-MM` → `{date, source, download_url, filename}` for every month
   from both legacy sources. The copy on disk was taken 2026-07-31. This is the
   fastest route and needs no network.
2. **Query archive.org** for items with the `sawikisource-` prefix and read the
   dates off the item names.

`pipeline/fetch_legacy.py` still exists and still knows how to do all of this
(`list_available_months()`, and the fetch/verify/decompress path). It is simply
no longer called by `pipeline/backfill.py`'s routing. If you want a dump, that
module is the shortest path — call it directly rather than rewriting the URL
construction.

`git log -- pipeline/backfill.py` around 2026-08-01 has the removed routing if
the full integration is ever wanted back.

## Format notes, if you do use one

- Classic `pages-meta-current.xml.bz2`: same schema and namespace IDs as
  everything else the pipeline reads. `parse_dump`, `build_tree`,
  `transclusion`, and `content_size` all handle it with **zero** code changes
  (confirmed by a spike run against 2022-01-20).
- One snapshot per item, bzip2-compressed, no SHA256SUMS alongside — unlike the
  current-format export, whose completeness is signalled by that file's
  presence.
- Archive.org rate-limits back-to-back requests; the old listing code paid
  one request per date, dozens per full listing, which is why it was disk-cached
  with a 24h TTL.
