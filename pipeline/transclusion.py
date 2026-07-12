"""
Build stage, part 3: transclusion detection (Main <-> Index) and
content-to-category membership, including the narrow category-inference rule.

See notes/sawikisource-scraper-spec.md, "Transclusion detection" and
"Content -> category membership" sections.
"""

from __future__ import annotations

import re
from collections import defaultdict

from pipeline.parse_dump import PageRecord, category_links

# Matches a ProofreadPage <pages ... /> transclusion tag and captures its
# index="..." (quoted) or index=... (bare, terminated by whitespace or the
# tag's closing "/>") attribute value. Confirmed against the live dump that
# BOTH forms occur (quoted is the majority, ~1280 of ~1299 occurrences;
# bare is real too, not a parse artifact) -- also tolerates the malformed
# "<pages.index=..." variant (period instead of space after the tag name,
# confirmed present, 4 occurrences) since it's unambiguous which index it
# names despite being invalid ProofreadPage syntax.
_PAGES_TAG_RE = re.compile(
    r"<pages[.\s]index\s*=\s*(?:\"([^\"]*)\"|([^\s/>]+))",
    re.IGNORECASE,
)


def transcluded_index_titles(text: str) -> set[str]:
    """Extract the set of Index-page titles (bare, e.g. 'Work.pdf', no
    namespace prefix) transcluded via <pages index=... /> tags in this
    Main-namespace page's wikitext. A page with no <pages> tags at all, or
    only bare '<pages>'/'<pages/>' with no index attribute, contributes
    nothing.
    """
    titles = set()
    for m in _PAGES_TAG_RE.finditer(text):
        value = m.group(1) if m.group(1) is not None else m.group(2)
        if value:
            titles.add(value.strip())
    return titles


def build_transclusion_map(main_records: list[PageRecord]) -> dict[str, set[str]]:
    """Returns Index-title -> set of Main-namespace titles that transclude it.
    An Index title present as a key here (non-empty set) is transcluded at
    least once; per the spec, ANY transclusion is treated as sufficiently
    complete -- the Index is then dropped from display in favor of the Main
    content. Index titles with zero transclusion are simply absent as keys
    (callers should treat "not in this map" as "untranscluded").
    """
    result: dict[str, set[str]] = defaultdict(set)
    for rec in main_records:
        for index_title in transcluded_index_titles(rec.text):
            result[index_title].add(rec.title)
    return dict(result)


def is_transcluded(index_title: str, transclusion_map: dict[str, set[str]]) -> bool:
    return bool(transclusion_map.get(index_title))


# ---------------------------------------------------------------------------
# Content -> category membership (no bubble-up, narrow inference exception)
# ---------------------------------------------------------------------------

def direct_categories(record: PageRecord, category_ns_name: str) -> set[str]:
    """A single page's own direct category tags -- no ancestor/descendant
    involvement at all. Both Main pages and Index items use this the same
    way; the caller decides which namespace's records to pass in."""
    return set(category_links(record.text, category_ns_name))


def infer_root_categories(
    root_title: str,
    subpage_titles: list[str],
    direct_cats_by_title: dict[str, set[str]],
) -> set[str]:
    """The spec's one narrow bubble-up exception: if EVERY subpage under a
    root shares a category the root itself lacks, infer that category onto
    the root (display-only -- this function returns which categories would
    be inferred; it never mutates wiki content or the dump).

    Deliberately requires ALL subpages to agree, not a majority: this is
    meant to catch a genuine tagging gap (the work as a whole IS that
    category, someone just forgot to tag the index/root page itself), not
    to guess at a root's subject from partial/mixed evidence. A single
    subpage lacking the shared tag disqualifies inference for that category
    entirely, on the theory that partial coverage is at least as consistent
    with "only some subpages relate to this category" as it is with "the
    tag is missing everywhere".

    Returns empty set if there are no subpages (nothing to infer from) or if
    no category is universally shared.
    """
    if not subpage_titles:
        return set()

    root_cats = direct_cats_by_title.get(root_title, set())

    shared: set[str] | None = None
    for title in subpage_titles:
        cats = direct_cats_by_title.get(title, set())
        shared = cats if shared is None else (shared & cats)
        if not shared:
            return set()

    return (shared or set()) - root_cats
