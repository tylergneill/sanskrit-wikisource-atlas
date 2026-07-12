"""
Build stage, part 4: size and content computation.

Per notes/sawikisource-scraper-spec.md, "Size and latest change computation" --
rejects the previous approach's two heuristics (regex-based markup exclusion;
an estimated Devanagari->IAST expansion factor) in favor of full local
computation against the downloaded dump:
  1. Parse wikitext with mwparserfromhell, strip markup properly.
  2. Expand templates for real: look up the matching Template page from the
     same dump, substitute parameters, recursively re-parse.
  3. Scribunto (#invoke:) evaluation -- SKIPPED. Confirmed against the live
     2026-07 dump: 0 of 32,428 Main-namespace pages invoke a module at all
     (the Module/पटलम् namespace exists, 46 pages, but nothing in real
     content calls it -- likely vestigial imports never wired up). Revisit
     only if a future dump shows real #invoke: usage in Main content.
  4. Run actual SLP1/IAST transliteration via skrutable for the size metric,
     rather than estimating an expansion factor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import mwparserfromhell
from skrutable.transliteration import Transliterator

from pipeline.parse_dump import PageRecord

MAX_TEMPLATE_EXPANSION_DEPTH = 20  # guards against template recursion cycles

_transliterator = Transliterator(from_scheme="DEV", to_scheme="IAST")


@dataclass
class ContentSizeResult:
    raw_wikitext_bytes: int
    stripped_text: str  # markup-free text, after template expansion
    content_bytes: int  # len(stripped_text.encode("utf-8"))
    transliterated_text: str
    transliterated_bytes: int


def build_template_index(template_records: list[PageRecord], template_ns_name: str) -> dict[str, str]:
    """Maps a template's bare name (as it would appear in a {{Name}} call,
    i.e. without the namespace prefix) to its raw wikitext body. MediaWiki
    template-name matching is first-letter-case-insensitive and normalizes
    underscores to spaces; both are applied to lookup keys here so a
    {{some_template}} call matches a "Some template" page.
    """
    index: dict[str, str] = {}
    prefix = template_ns_name + ":"
    for rec in template_records:
        if not rec.title.startswith(prefix):
            continue
        bare = rec.title[len(prefix):]
        index[_normalize_template_name(bare)] = rec.text
    return index


def _normalize_template_name(name: str) -> str:
    name = name.strip().replace("_", " ")
    if name:
        name = name[0].upper() + name[1:]
    return name


def _substitute_params(template_text: str, params: dict[str, str]) -> str:
    """Replaces {{{param}}} and {{{param|default}}} placeholders in a
    template body with the caller-supplied argument, or the default if the
    caller didn't supply that param, or "" if neither exists. Positional
    params (1, 2, ...) and named params are both looked up in the same
    `params` dict, matching how mwparserfromhell exposes template call params.
    """
    def replace(m: re.Match) -> str:
        inner = m.group(1)
        name, _, default = inner.partition("|")
        name = name.strip()
        if name in params:
            return params[name]
        return default
    pattern = re.compile(r"\{\{\{([^{}]*)\}\}\}")
    prev = None
    while prev != template_text:
        prev = template_text
        template_text = pattern.sub(replace, template_text)
    return template_text


def _split_pf_args(inner: str) -> list[str]:
    """Splits a parser function's argument string on top-level "|" only,
    respecting nested {{ }} / [[ ]] so a nested template/link's own "|"
    doesn't split early. inner is everything after the function name and
    its ":", e.g. for {{#if:A|B|C}} this receives "A|B|C".
    """
    args = []
    depth = 0
    current = []
    i = 0
    while i < len(inner):
        two = inner[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            current.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            current.append(two)
            i += 2
            continue
        if inner[i] == "|" and depth == 0:
            args.append("".join(current))
            current = []
            i += 1
            continue
        current.append(inner[i])
        i += 1
    args.append("".join(current))
    return args


def _is_blank(s: str) -> bool:
    return s.strip() == ""


def evaluate_parser_functions(wikitext: str, page_title: str, known_titles: set[str] | None = None) -> str:
    """Evaluates the parser functions that actually occur at meaningful
    volume in this wiki's templates (per a live survey of the 2026-07 dump:
    #if 1029x, #ifeq 353x, #switch 203x, #ifexist 53x across Main+Template
    content -- dominated by the header/footer/verse nav-chrome templates
    that wrap most real content pages). Functions not implemented here
    (#ifexpr, #time, #titleparts, #expr, #lst, #invoke, ...) are left
    unevaluated in the output, same tradeoff as an unresolved template call
    in expand_templates -- rare enough in practice (confirmed: none of the
    high-frequency real-content templates use them) not to block on.

    known_titles is used for #ifexist: evaluation -- a page "exists" if its
    title is in this set. Pass None to always evaluate #ifexist as false
    (conservative default when the caller hasn't built a title index).
    """
    known_titles = known_titles or set()

    # Repeated passes handle nesting (innermost functions resolve first as
    # mwparserfromhell's own template-call regex won't see un-{{}}-wrapped
    # #if bodies, so this operates as a straight string scan+replace instead).
    pattern = re.compile(r"\{\{\s*#(if|ifeq|switch|ifexist)\s*:", re.IGNORECASE)

    def find_matching_close(text: str, open_pos: int) -> int:
        depth = 1
        i = open_pos
        while i < len(text) and depth > 0:
            two = text[i:i + 2]
            if two == "{{":
                depth += 1
                i += 2
                continue
            if two == "}}":
                depth -= 1
                if depth == 0:
                    return i
                i += 2
                continue
            i += 1
        return -1

    prev = None
    while prev != wikitext:
        prev = wikitext
        m = pattern.search(wikitext)
        if not m:
            break
        func = m.group(1).lower()
        inner_start = m.end()
        close = find_matching_close(wikitext, inner_start)
        if close == -1:
            break  # malformed/unterminated -- leave as-is
        inner = wikitext[inner_start:close]
        args = _split_pf_args(inner)

        replacement = _eval_one_pf(func, args, page_title, known_titles)
        wikitext = wikitext[:m.start()] + replacement + wikitext[close + 2:]

    return wikitext


def _eval_one_pf(func: str, args: list[str], page_title: str, known_titles: set[str]) -> str:
    if func == "if":
        cond, then, *rest = args + ["", ""]
        return then if not _is_blank(cond) else (rest[0] if rest else "")
    if func == "ifeq":
        a, b, then, *rest = args + ["", "", ""]
        return then if a.strip() == b.strip() else (rest[0] if rest else "")
    if func == "ifexist":
        title, then, *rest = args + ["", ""]
        return then if title.strip() in known_titles else (rest[0] if rest else "")
    if func == "switch":
        if not args:
            return ""
        subject = args[0].strip()
        default = ""
        for arg in args[1:]:
            if "=" in arg:
                key, _, val = arg.partition("=")
                key = key.strip()
                if key == subject or key == "#default":
                    if key == "#default":
                        default = val
                    else:
                        return val
                else:
                    default = val if key == "#default" else default
            else:
                default = arg  # trailing valueless arg is switch's fallthrough default
        return default
    return ""


def expand_magic_words(wikitext: str, page_title: str) -> str:
    """Substitutes the handful of PAGENAME-family magic words that occur at
    real volume (883x {{PAGENAME}} alone in the live survey). page_title is
    the bare Main-namespace title (no namespace prefix, since these are
    Main-page-only in practice here).
    """
    base = page_title.rsplit("/", 1)[0] if "/" in page_title else page_title
    sub = page_title.rsplit("/", 1)[1] if "/" in page_title else page_title
    replacements = {
        "PAGENAME": page_title,
        "FULLPAGENAME": page_title,
        "BASEPAGENAME": base,
        "SUBPAGENAME": sub,
        "PAGENAMEE": page_title.replace(" ", "_"),
        "FULLPAGENAMEE": page_title.replace(" ", "_"),
        "NAMESPACE": "",
        "SUBJECTSPACE": "",
        "TALKSPACE": "Talk",
    }
    for name, value in replacements.items():
        wikitext = re.sub(r"\{\{\s*" + name + r"\s*\}\}", value, wikitext)
    return wikitext


def expand_templates(
    wikitext: str,
    template_index: dict[str, str],
    page_title: str = "",
    known_titles: set[str] | None = None,
    _depth: int = 0,
) -> str:
    """Recursively expands {{Template|args}} calls found via mwparserfromhell
    against template_index, substituting parameters into the template body
    and re-parsing the result for further nested template calls. Also
    evaluates parser functions (#if/#ifeq/#switch/#ifexist) and PAGENAME-
    family magic words at each pass, since real templates here (header,
    footer) embed them directly and they must resolve before/alongside
    template substitution, not as an afterthought -- otherwise their raw
    {{#if:...}} syntax leaks into the final stripped text (confirmed live:
    this was previously inflating content_bytes on pages using these
    templates with un-evaluated parser-function noise).

    Templates not found in template_index (genuinely missing, or a parser
    function/magic word not covered by evaluate_parser_functions /
    expand_magic_words -- e.g. #ifexpr, #time, #invoke) are left as-is in
    the output, not deleted, so their absence doesn't silently understate
    size. Confirmed via a live survey that the functions covered here
    (#if 1029x, #ifeq 353x, #switch 203x, #ifexist 53x) account for the
    large majority of real usage; the ones left unevaluated are individually
    rare and none appear in the header/footer/verse templates that wrap
    most real content pages.

    Depth-limited to guard against template self-reference / mutual
    recursion cycles, not because deep legitimate nesting is expected.
    """
    if _depth >= MAX_TEMPLATE_EXPANSION_DEPTH:
        return wikitext

    wikitext = evaluate_parser_functions(wikitext, page_title, known_titles)
    wikitext = expand_magic_words(wikitext, page_title)

    wikicode = mwparserfromhell.parse(wikitext)
    changed = False
    for template in wikicode.filter_templates(recursive=False):
        name = _normalize_template_name(str(template.name))
        body = template_index.get(name)
        if body is None:
            continue  # unresolved: parser function or genuinely missing template

        params = {}
        for i, param in enumerate(template.params, start=1):
            if param.showkey:
                params[str(param.name).strip()] = str(param.value)
            else:
                params[str(i)] = str(param.value)

        substituted = _substitute_params(body, params)
        wikicode.replace(template, substituted)
        changed = True

    result = str(wikicode)
    if changed:
        return expand_templates(result, template_index, page_title, known_titles, _depth + 1)
    return result


def strip_markup(expanded_wikitext: str, category_ns_name: str = "वर्गः") -> str:
    """Strips wikitext markup from already-template-expanded text, returning
    plain reader-facing text. mwparserfromhell's strip_code() handles
    headers, bold/italic, lists, tables, comments, external links, and
    <ref>/<nowiki> the same way MediaWiki's own renderer treats them for
    text-extraction purposes -- EXCEPT category links: confirmed live that
    strip_code() treats [[वर्गः:Foo]] like an ordinary wikilink and keeps
    "वर्गः:Foo" as visible text, whereas MediaWiki's real renderer suppresses
    category links from page output entirely (they're metadata, not
    content). Removed explicitly here before stripping, since every single
    page in the corpus carries at least one category tag and this would
    otherwise inflate content_bytes tree-wide, not just as an edge case.
    """
    wikicode = mwparserfromhell.parse(expanded_wikitext)
    for link in wikicode.filter_wikilinks(recursive=True):
        title = str(link.title).strip()
        if title.startswith(category_ns_name + ":") or title.startswith(category_ns_name.lower() + ":"):
            wikicode.remove(link)
    return wikicode.strip_code()


def compute_content_size(
    record: PageRecord,
    template_index: dict[str, str],
    known_titles: set[str] | None = None,
    category_ns_name: str = "वर्गः",
    transliterate: bool = True,
) -> ContentSizeResult:
    expanded = expand_templates(record.text, template_index, record.title, known_titles)
    stripped = strip_markup(expanded, category_ns_name)
    content_bytes = len(stripped.encode("utf-8"))

    transliterated_text = ""
    transliterated_bytes = 0
    if transliterate and stripped.strip():
        transliterated_text = _transliterator.transliterate(stripped)
        transliterated_bytes = len(transliterated_text.encode("utf-8"))

    return ContentSizeResult(
        raw_wikitext_bytes=len(record.text.encode("utf-8")),
        stripped_text=stripped,
        content_bytes=content_bytes,
        transliterated_text=transliterated_text,
        transliterated_bytes=transliterated_bytes,
    )
