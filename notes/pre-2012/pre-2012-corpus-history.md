# The pre-2012 history of sa.wikisource

A throwaway investigation into how the Sanskrit Wikisource corpus grew between
its founding on **2004-07-23** and **2012-02**, the first month this atlas's
changelog can cover.

This is a one-off historical analysis kept for the record, not part of the
maintained pipeline. Nothing here runs routinely, and nothing here writes to
`docs/`.

## Why the main pipeline can't reach this era

The atlas's whole tree model descends from the root category **वर्गसर्वस्वम्**,
whose earliest revision is 2012-01-20. Before that, there is no category
structure to build a tree from at all — the site had exactly **1 category**
from 2006 through early 2011, and **2** as late as 2011-12. `process_dump()`
raises `RootCategoryMissing` for any such dump, and `backfill.py` skips the
month rather than aborting the run. `MATERIALIZED_FLOOR` is set to 2012-01 for
the same reason.

But the three headline numbers — MB, pages, texts — never actually needed the
category graph. They come from the Main-namespace subpage tree, the
transclusion map, and content sizes, all of which are category-free. So the
method here is: run exactly the same pipeline stages as
`backfill.process_dump`, *minus* `build_category_graph`/`build_tree_json`, and
sum the same per-node stats the real tree assembly would have summed.

### Validation: the numbers match the real pipeline exactly

At 2012-02 and 2012-03 this analysis overlaps the committed changelog, and
every metric is byte-for-byte identical to `docs/data/changelog.json`'s first
entry:

| metric | this analysis (2012-02) | changelog `old` | this analysis (2012-03) | changelog `new` |
|---|---|---|---|---|
| `transliterated_bytes` | 45,605,614 | 45,605,614 | 51,952,941 | 51,952,941 |
| `count` | 6,546 | 6,546 | 7,191 | 7,191 |
| `text_count` | 4,256 | 4,256 | 3,951 | 3,951 |

So the pre-2012 figures below are directly comparable to the changelog's own,
on the same definitions. (No orphan-bucket caveat is needed at these dates:
the changelog's headline and `all` stats are identical here, meaning root plus
the orphan bucket accounted for the whole corpus.)

## Yearly growth, 2004 → 2011

Cutoffs are 07-23 of each year, the founding anniversary. "IAST MB" is
`transliterated_bytes`, the frontend's headline effective-size figure.

| date | IAST MB | content MB | pages | texts | categories on site |
|---|---|---|---|---|---|
| **2004-07-24** (day 1) | 0.06 | 0.12 | 9 | 9 | 0 |
| 2005-07-23 | 1.65 | 3.18 | 818 | 810 | 0 |
| 2006-07-23 | 5.12 | 9.87 | 1,576 | 1,493 | 1 |
| 2007-07-23 | 6.09 | 11.67 | 1,592 | 1,509 | 1 |
| 2008-07-23 | 6.42 | 12.35 | 1,625 | 1,542 | 1 |
| 2009-07-23 | 7.53 | 14.52 | 1,808 | 1,701 | 1 |
| 2010-07-23 | 7.54 | 14.53 | 1,810 | 1,703 | 1 |
| 2011-07-23 | 10.14 | 19.60 | 1,995 | 1,887 | 2 |

## Day one: 2004-07-23

The founding date is genuine, and the first surviving revisions are real
content, not site scaffolding. One editor, one evening, nine devotional texts,
115 KB:

```
2004-07-23T18:55:31Z  गणपत्यथर्वशीर्षम्
2004-07-23T18:59:36Z  गणेशस्तोत्रम्
2004-07-23T19:08:19Z  मधुराष्टकम्
2004-07-23T19:29:05Z  गोविन्दाष्टकम्
2004-07-23T19:47:24Z  श्रीमच्छङ्कराचार्यकृतं श्रीकृष्णाष्टकम्
2004-07-23T19:56:26Z  श्रीवल्लभाचार्यकृतं कृष्णाष्टकम्
2004-07-23T20:40:24Z  श्रीजयदेवकृतौ गीतगोविन्दे
2004-07-23T20:55:44Z  वेदसारशिवस्तोत्रम्
2004-07-23T21:02:12Z  ईशोपनिषत्
```

The next edit is five days later (`भगवद्गीता/अर्जुनविषादयोगः`), then one in
August, then nothing until October. A cutoff at 2004-07-23 00:00 UTC
materializes zero pages precisely because all nine came that evening — hence
the day-1 row above is dated 07-24.

## Growth was burst-driven, with multi-year flatlines

Per-month revision counts (`data/revision_histogram.txt`) show the corpus
advancing in short bursts separated by long dormancy, not steadily:

- **2004-10**: 395 Main revisions, 301 new pages — the ऋग्वेद सूक्त series
  begins, one page at a time.
- **2005-07 through 2005-12**: the first sustained push, ~1,160 new pages.
- **2006-01**: 4,322 Main revisions but only **1 new page**. Not growth — see
  the bursts section below.
- **2007 through 2008-11**: near-total dormancy, routinely **1–5 Main
  revisions per month** for two straight years.
- **2008-12 through 2009-02**: a ~2,100-revision burst adding 182 pages, then
  silence again.
- **2009-08 through 2011-02**: eighteen straight months averaging ~4 Main
  revisions/month, several months at literally zero. This is why 2009-07 and
  2010-07 are nearly identical: **+2 pages and +10 KB across a full year**.
- **2011-06 onward**: the real awakening, and it does not stop.

### The two mass-revision bursts were not content growth

Both large pre-ramp revision spikes were mechanical, and characterizing them
matters because raw revision counts would otherwise read as growth
(`data/bursts.txt`):

- **2006-01** — 4,289 of 4,323 revisions by **ThomasBot**, with comments like
  `'Yann : replace'` and `'Yann regex १० : regexp'`, across 1,033 pages
  (mostly ऋग्वेद सूक्त). A bot-driven regex cleanup pass over existing text.
  One new page.
- **2011-03** — 1,877 of 1,924 revisions by **Shijualex**, comments
  `'२ अवतरण: from old wikisource'` and `'६ अवतरण: rigveda and other pages'`,
  across 1,852 pages. A migration/import pass re-touching existing pages
  (the top-touched titles are मुखपुटम्, भगवद् गीता, ऋग्वेदः, महाभारतम् — hub
  pages, plus several `सदस्यः:` user pages). One new page.

## Monthly detail across the 2011–2012 ramp

| date | IAST MB | Δ MB | pages | texts | untranscl. Index | categories |
|---|---|---|---|---|---|---|
| 2011-01-01 | 7.54 | — | 1,810 | 1,703 | 0 | 1 |
| 2011-02-01 | 7.54 | +0.00 | 1,810 | 1,703 | 0 | 1 |
| 2011-03-01 | 7.54 | +0.00 | 1,810 | 1,703 | 0 | 1 |
| 2011-04-01 | 7.54 | +0.00 | 1,811 | 1,704 | 0 | 2 |
| 2011-05-01 | 7.54 | +0.00 | 1,812 | 1,705 | 0 | 2 |
| 2011-06-01 | 7.60 | +0.06 | 1,819 | 1,711 | 0 | 2 |
| 2011-07-01 | 9.82 | **+2.22** | 1,948 | 1,840 | 0 | 2 |
| 2011-08-01 | 10.16 | +0.34 | 1,999 | 1,891 | 0 | 2 |
| 2011-09-01 | 17.94 | **+7.78** | 3,021 | 2,901 | 1 | 2 |
| 2011-10-01 | 29.42 | **+11.48** | 4,370 | 4,158 | 1 | 2 |
| 2011-11-01 | 31.22 | +1.80 | 4,666 | 4,174 | 1 | 2 |
| 2011-12-01 | 35.28 | +4.05 | 5,256 | 4,188 | 2 | 2 |
| 2012-01-01 | 41.86 | +6.58 | 6,067 | 4,235 | 2 | 2 |
| 2012-02-01 | 45.61 | +3.74 | 6,546 | 4,256 | 2 | **29** |
| 2012-03-01 | 51.95 | +6.35 | 7,191 | **3,951** | 6 | 41 |
| 2012-04-01 | 54.34 | +2.38 | 7,532 | 3,983 | 6 | 42 |
| 2012-05-01 | 55.87 | +1.53 | 7,543 | 3,986 | 6 | 42 |
| 2012-06-01 | 55.87 | -0.00 | 7,543 | 3,986 | 6 | 42 |
| 2012-07-01 | 61.97 | +6.10 | 7,925 | 4,142 | 6 | 54 |
| 2012-08-01 | 63.89 | +1.93 | 8,059 | 4,147 | 5 | 57 |

### What the ramp shows

**The corpus more than sextupled in 14 months** — 7.54 MB (2011-01) to 45.61 MB
(2012-02) — after five years of near-stasis. The single biggest month is
2011-10 at **+11.48 MB**, more than the entire corpus held at any point before
2011-09.

**Categorization was a response to growth, not a founding principle.** The
site ran on 1–2 categories for seven years. Categories appear en masse only
*after* the corpus had already quintupled: 2 → 29 in 2012-02, then 41, 54, 57.
वर्गसर्वस्वम् itself is created 2012-01-20, right at the inflection. So the
atlas's 2012-02 floor is not an arbitrary data-availability cutoff — it is
approximately the moment sa.wikisource first became organized enough to have a
browsable structure at all.

**The text_count drop at 2012-03 is real, and it is the category system
working.** Texts fall 4,256 → 3,951 (−305) while pages *rise* 6,546 → 7,191.
This is in the committed changelog too (`text_count_pct: -7.17`). Nothing was
deleted: pages that had been standalone top-level titles were re-titled into
`Parent/Child` breadcrumb form, so they stopped counting as separate texts and
started rolling up under their parents. A drop in "texts" here means
consolidation, not loss — the same effect the atlas's own `text_count`
definition produces by design.

**ProofreadPage/OCR is essentially absent from this era.** The first
untranscluded Index item appears 2011-09, and the count only reaches 6 by
2012-03. The scan-and-proofread workflow that dominates later corpus growth
had barely started.

## Putting the pre-2012 era in perspective

The changelog's first data point is 45.6 MB IAST at 2012-02. Of that, the
2004–2011 period contributed only about **10.1 MB (~22%)**, and essentially all
of it arrived in two short bursts — 2004-2006 and 2011-06 onward — separated by
roughly five largely idle years. The wiki existed for seven years before the
growth that actually built the corpus began.

## Files

Scripts expect the cached meta-history dump at
`dump/_materialize_src/sawikisource-latest-pages-meta-history.xml` (the
decompressed form of what `backfill.py`'s `_ensure_materialize_source`
downloads; ~6.5 GB decompressed, ~533 MB as .bz2). Each full-pass script
streams it once, O(1) memory.

**Source dump vintage**: every figure in this document comes from the
meta-history dump taken **2026-07-01** (Wikimedia runs these on the 1st and
20th; its newest revision is 2026-07-02, since a run cuts at run time rather
than midnight; `<generator>MediaWiki 1.46.0-wmf.26</generator>`). The `latest`
URL alias carries no date of its own, so this was established from the newest
revision timestamp in the file. This vintage is what bounds the
deleted-pages caveat below — re-running these scripts against a newer
meta-history dump can legitimately shift the numbers, since pages deleted in
the meantime drop out of the reconstruction entirely.

This vintage is not local to this investigation: the same cached file is what
`pipeline/backfill.py`'s `_ensure_materialize_source` hands to every
`MATERIALIZED_MONTHS` reconstruction, so all 92 materialized months in
`docs/data/changelog.json` inherit it too, and this document is the only place
it's written down. See CLAUDE.md's "Materialized era" section (the **Dump
vintage** paragraph) for what refreshing the cached dump would invalidate.

| file | what it does |
|---|---|
| `corpus_stats_no_category.py` | The core of this investigation: runs `parse_dump` → `build_main_tree` → `build_transclusion_map` → `compute_all_content_sizes` on a directory of snapshot XMLs and reports MB/pages/texts using `process.py`'s own definitions, skipping the category graph entirely. |
| `earliest_revisions.py` | Finds the earliest surviving revisions to establish the true founding date. |
| `revision_histogram.py` | Per-month all/Main revision counts and new-page counts, used to confirm the flatlines are real rather than materialization artifacts. |
| `characterize_bursts.py` | Identifies who/what drove the 2006-01 and 2011-03 revision spikes. |
| `data/*.json`, `data/*.txt` | Saved outputs of the above. |

Snapshots themselves were materialized with the existing pipeline module and
then discarded (they run 100 KB–25 MB each here, but there is no reason to keep
them — they regenerate from the meta-history dump):

```
python -m pipeline.materialize_snapshots \
    dump/_materialize_src/sawikisource-latest-pages-meta-history.xml \
    --dates 2004-07-24,2005-07-23,2006-07-23,2007-07-23,2008-07-23,2009-07-23,2010-07-23,2011-07-23 \
    --outdir <tmp>/early_snapshots

python -m pipeline.materialize_snapshots \
    dump/_materialize_src/sawikisource-latest-pages-meta-history.xml \
    --start 2011-01 --end 2012-08 --day 1 --outdir <tmp>/ramp_snapshots

python notes/pre-2012/corpus_stats_no_category.py <tmp>/ramp_snapshots out.json
```

## Caveats

- `materialize_snapshots.py`'s known deviations all apply — most relevantly,
  **pages deleted before the meta-history dump was taken (2026-07-01) are
  absent**, so every figure here is a lower bound; twenty-plus years of
  deletions are invisible, and the effect is presumably largest on the earliest
  snapshots. Titles are also as of the dump, so a page
  renamed later appears under its later name (this is exactly what makes the
  2012-03 breadcrumb re-titling visible as a `text_count` drop rather than
  invisible).
- Cutoffs are 00:00 UTC on the given day, whereas real dumps cut at run time.
- The `--day 1` monthly snapshots are labeled by the month they open, so
  `2012-03-01` reflects the state *after* February's edits — matching how
  `changelog.json` keys its entries.
- These are whole-corpus totals. They happen to equal the changelog's headline
  figures at 2012-02/2012-03 (verified above), but for later months the
  headline root stats deliberately exclude the असम्बद्धवर्गीकृतम् orphan bucket,
  so the two would diverge.
