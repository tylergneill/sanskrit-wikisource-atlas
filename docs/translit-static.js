// Transliteration for *static* Devanagari written directly into index.html/
// about.html prose -- the hand-written counterpart to app.js/about.js, which only
// ever transliterate titles coming out of tree.json/changelog.json.
//
// Markup convention:
//
//   <span class="sa">देवनागरी</span>              visible text
//   <element data-sa-title="देवनागरी …">          a title="" tooltip
//
// On first run every .sa element's original Devanagari is stashed in
// data-deva (and each data-sa-title's in data-sa-title-deva, which is just the
// authored attribute, left in place). Re-rendering always reads that stash, never
// the element's current text -- Sanscript.t() is not idempotent, so feeding it
// already-transliterated output would corrupt it (e.g. IAST "ā" -> garbage) after
// the second scheme change.
//
// Deliberately NOT transliterated: href/src values (Wikisource URLs must stay
// Devanagari to resolve), and the "अ→a" scheme-toggle labels, which name the
// feature itself and would read "a→a" once transliterated. Neither carries the
// class/attribute, so both are simply skipped.

const SA_SELECTOR = ".sa";
const SA_TITLE_SELECTOR = "[data-sa-title]";

function translit(deva, scheme) {
  if (!deva) return deva;
  if (scheme === "devanagari") return deva;
  try {
    return window.Sanscript.t(deva, "devanagari", scheme);
  } catch {
    return deva;
  }
}

// A data-sa-title may mix Devanagari with surrounding English ("Include
// असम्बद्धवर्गीकृतम् (pages not reachable...)"), so transliterate only the
// Devanagari runs and leave the rest of the sentence alone.
const DEVA_RUN = /[ऀ-ॿ‍]+/g;

function translitMixed(text, scheme) {
  if (!text) return text;
  if (scheme === "devanagari") return text;
  return text.replace(DEVA_RUN, (run) => translit(run, scheme));
}

export function renderStaticTranslit(scheme, root = document) {
  for (const el of root.querySelectorAll(SA_SELECTOR)) {
    if (el.dataset.deva === undefined) el.dataset.deva = el.textContent;
    el.textContent = translit(el.dataset.deva, scheme);
  }
  for (const el of root.querySelectorAll(SA_TITLE_SELECTOR)) {
    el.title = translitMixed(el.dataset.saTitle, scheme);
  }
}

// Keeps static prose in sync with the page's own scheme <select>. Both pages
// render their dynamic content on "change" already; this just piggybacks so the
// two halves of the page never disagree about the active scheme.
export function initStaticTranslit(initialScheme) {
  const select = document.getElementById("schemeSelect");
  const current = () => (select ? select.value : initialScheme);
  renderStaticTranslit(current());
  if (select) {
    select.addEventListener("change", () => renderStaticTranslit(current()));
  }
}
