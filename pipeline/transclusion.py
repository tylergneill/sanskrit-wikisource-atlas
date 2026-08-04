"""
Build stage, part 3: transclusion detection (Main <-> Index) and
content-to-category membership, including the narrow category-inference rule.

See notes/sawikisource-scraper-spec.md, "Transclusion detection" and
"Content -> category membership" sections.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

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

# Captures a whole <pages ... /> tag so its from=/to= range attributes can be
# read off individually (the tighter _PAGES_TAG_RE above only grabs the index
# name). Non-greedy up to the first ">" -- ProofreadPage tags are self-closing
# and never contain a literal ">" in an attribute value in practice.
_PAGES_WHOLE_TAG_RE = re.compile(r"<pages[.\s][^>]*>", re.IGNORECASE)
_INDEX_ATTR_RE = re.compile(r"\bindex\s*=\s*(?:\"([^\"]*)\"|([^\s/>]+))", re.IGNORECASE)
_FROM_ATTR_RE = re.compile(r"\bfrom\s*=\s*(?:\"([^\"]*)\"|([^\s/>]+))", re.IGNORECASE)
_TO_ATTR_RE = re.compile(r"\bto\s*=\s*(?:\"([^\"]*)\"|([^\s/>]+))", re.IGNORECASE)
_INCLUDE_ATTR_RE = re.compile(r"\binclude\s*=\s*(?:\"([^\"]*)\"|([^\s/>]+))", re.IGNORECASE)


@dataclass(frozen=True)
class TransclusionRange:
    """One <pages index="X" ... /> transclusion request from a Main page:
    which Index item's scanned leaves it pulls in, and which leaf positions.

    Two mutually-exclusive addressing modes, matching ProofreadPage:
    - from_n/to_n: a contiguous range. None means unbounded on that side
      (from_n=None -> from the Index's first leaf, to_n=None -> through its
      last), so a tag with neither transcludes the whole Index.
    - include_ns: an explicit set of leaf numbers from an include="1,3,5-8"
      attribute (a comma-separated list of single pages and A-B sub-ranges).
      When present, from_n/to_n are ignored -- include is its own selector.
    """
    index_title: str
    from_n: int | None
    to_n: int | None
    include_ns: frozenset[int] | None


def _attr_value(m: re.Match | None) -> str | None:
    if m is None:
        return None
    return (m.group(1) if m.group(1) is not None else m.group(2)) or None


def _parse_int(value: str | None) -> int | None:
    """Parses a from=/to= attribute value to an int. Values are Arabic
    numerals in the live dump (confirmed: <pages ... from=1 to=205/>), even
    though the leaf titles they address use Devanagari numerals -- so range
    endpoints are compared against Devanagari leaf suffixes numerically, not
    by string match (see resolve_transcluded_leaves)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_include(value: str | None) -> frozenset[int] | None:
    """Parses an include="1,3,5-8" attribute (comma-separated single pages
    and A-B sub-ranges, Arabic numerals) into the explicit set of leaf
    numbers it names. Returns None when the attribute is absent; malformed
    fragments are skipped individually rather than failing the whole tag."""
    if value is None:
        return None
    nums: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            lo, hi = _parse_int(lo_s), _parse_int(hi_s)
            if lo is not None and hi is not None and lo <= hi:
                nums.update(range(lo, hi + 1))
        else:
            n = _parse_int(part)
            if n is not None:
                nums.add(n)
    return frozenset(nums)


def transclusion_ranges(text: str) -> list[TransclusionRange]:
    """Parses every <pages index="..." /> tag in a Main page's wikitext into
    structured leaf-selection requests. A tag with no index= attribute is
    skipped (nothing to resolve). A tag with an include= attribute selects
    exactly the listed leaves; otherwise from=/to= define a contiguous range
    (open-ended when either endpoint is absent -- a tag with neither
    transcludes the whole Index)."""
    ranges: list[TransclusionRange] = []
    for m in _PAGES_WHOLE_TAG_RE.finditer(text):
        tag = m.group(0)
        index_title = _attr_value(_INDEX_ATTR_RE.search(tag))
        if not index_title:
            continue
        include_ns = _parse_include(_attr_value(_INCLUDE_ATTR_RE.search(tag)))
        ranges.append(TransclusionRange(
            index_title=index_title.strip(),
            from_n=_parse_int(_attr_value(_FROM_ATTR_RE.search(tag))),
            to_n=_parse_int(_attr_value(_TO_ATTR_RE.search(tag))),
            include_ns=include_ns,
        ))
    return ranges


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


def build_reverse_transclusion_map(main_records: list[PageRecord]) -> dict[str, set[str]]:
    """Returns Main-namespace title -> set of Index titles whose पृष्ठम्:Title/N
    leaf pages it transcludes a range of -- the reverse direction of
    build_transclusion_map, for surfacing a link back to the source scan/
    Index item from the Main page that superseded it in display (see
    docs/about.html, "Transclusion": the atlas drops a transcluded Index
    item from display entirely in favor of the Main page, but a reader may
    still want to jump to the scan). A Main page never transcludes the
    Index item itself -- only individual leaf pages under it, named via the
    Index in its own <pages index="..." /> tag. Main titles with no such
    tags at all are simply absent as keys."""
    result: dict[str, set[str]] = {}
    for rec in main_records:
        titles = transcluded_index_titles(rec.text)
        if titles:
            result[rec.title] = titles
    return result


def is_transcluded(index_title: str, transclusion_map: dict[str, set[str]]) -> bool:
    return bool(transclusion_map.get(index_title))


_DEV_DIGITS = "०१२३४५६७८९"
_DEV_TO_ASCII = {ord(d): str(i) for i, d in enumerate(_DEV_DIGITS)}


def leaf_number(leaf_suffix: str) -> int | None:
    """Parses the trailing "/N" segment of a पृष्ठम्:Index/N leaf title to an
    int. Leaf numbers are Devanagari numerals in the live dump (e.g.
    पृष्ठम्:काठकोपनिषत्.djvu/२०५), so Devanagari digits are normalized to Arabic
    before parsing. Returns None for the rare non-numeric suffix (confirmed
    only 4 of 127,746 leaves site-wide -- e.g. ".../contents"), which simply
    never falls inside any from=/to= range and is skipped."""
    ascii_form = leaf_suffix.translate(_DEV_TO_ASCII).strip()
    if not ascii_form.isdigit():
        return None
    return int(ascii_form)


def resolve_transcluded_leaves(
    ranges: list[TransclusionRange],
    leaves_by_index: dict[str, list[str]],
) -> set[str]:
    """Given a Main page's parsed <pages> ranges and a map of Index bare-title
    -> its leaf full-titles (पृष्ठम्:Index/N), returns the set of leaf
    full-titles this page actually transcludes -- i.e. those whose leaf number
    N falls within the from=/to= range for their Index.

    An open endpoint (from_n/to_n None) is unbounded on that side, so a tag
    with no from=/to= at all (e.g. <pages index="X" include=1/>) resolves to
    every leaf under that Index. Multiple sibling Main pages transcluding
    different sub-ranges of the same Index each get only their own slice --
    the range is applied per Main page, not per Index (see
    notes/proofreadpage-transclusion-undercount-fix.md, gotcha 4)."""
    resolved: set[str] = set()
    for r in ranges:
        leaves = leaves_by_index.get(r.index_title)
        if not leaves:
            continue
        for leaf_full_title in leaves:
            suffix = leaf_full_title.rsplit("/", 1)[1] if "/" in leaf_full_title else ""
            n = leaf_number(suffix)
            if n is None:
                continue
            if r.include_ns is not None:
                if n in r.include_ns:
                    resolved.add(leaf_full_title)
                continue
            if r.from_n is not None and n < r.from_n:
                continue
            if r.to_n is not None and n > r.to_n:
                continue
            resolved.add(leaf_full_title)
    return resolved


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
