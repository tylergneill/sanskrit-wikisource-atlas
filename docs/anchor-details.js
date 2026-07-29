// Makes in-page #anchor links work when the target is inside a collapsed
// <details>. Every major section of about.html is a <details class="block
// collapsible"> whose <summary> holds the <h2 id="...">, so a plain
// "#data-quantity" jump would scroll to a section that stays shut -- the browser
// only auto-opens a <details> when the target is *inside* it, and support for
// even that is uneven. Chrome/Safari won't reveal a target that IS the summary's
// own heading.
//
// Opens every <details> ancestor (not just the nearest one), since the audit
// block nests disclosures several deep, then scrolls the summary into view.
// Also runs once on load so a URL pasted with a #hash lands correctly.

import { topbarOffset } from "./topbar-height.js";

function revealHash(hash) {
  if (!hash || hash === "#") return false;
  let target;
  try {
    target = document.querySelector(hash);
  } catch {
    return false; // not a valid selector (e.g. "#2")
  }
  if (!target) return false;

  for (let el = target.parentElement; el; el = el.parentElement) {
    if (el.tagName === "DETAILS") el.open = true;
  }

  // Scroll to the <summary> when the id lives on a heading inside one -- the
  // heading's own box is what the browser would pick, which lands mid-row and
  // clips the disclosure triangle.
  const summary = target.closest("summary") || target;
  scrollIntoViewBelowTopbar(summary);
  return true;
}

// scrollIntoView({block:"start"}) puts the element flush against the top of the
// scroll container, which at <=800px is underneath the pinned topbar (and, once a
// section is open, under its own sticky header). Scroll the container by the delta
// instead, less the topbar's measured height.
//
// Jumps instantly rather than smoothly: a smooth scroll here is animated over
// several frames, during which the browser's own scroll-to-fragment cancels it and
// leaves the pane where it started.
//
// Which element scrolls depends on width: .mainpane above 800px, the document
// below it (see .layout in styles.css). Decide from the *computed overflow*, not
// from scrollHeight > clientHeight -- with every section collapsed the pane may
// not currently overflow, and treating that as "the document scrolls" sends the
// scroll to window, where it silently does nothing.
function scrollIntoViewBelowTopbar(element) {
  const offset = topbarOffset();
  const pane = element.closest(".mainpane");
  const paneScrolls =
    pane && /auto|scroll/.test(getComputedStyle(pane).overflowY);

  if (paneScrolls) {
    const delta =
      element.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    pane.scrollTop = pane.scrollTop + delta - offset;
  } else {
    window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - offset);
  }
}

export function initAnchorDetails() {
  document.addEventListener("click", (ev) => {
    const link = ev.target.closest('a[href*="#"]');
    if (!link) return;

    // Only handle links pointing at this same document. Comparing resolved
    // URLs minus the hash covers "#x", "about.html#x", "/about.html#x", and a
    // full absolute URL alike, while letting genuinely external links through.
    const url = new URL(link.href, location.href);
    const samePage =
      url.origin === location.origin && url.pathname === location.pathname;
    if (!samePage || !url.hash) return;

    if (revealHash(url.hash)) {
      ev.preventDefault();
      // Keep the address bar/back button honest even though we scrolled manually.
      history.pushState(null, "", url.hash);
    }
  });

  window.addEventListener("hashchange", () => revealHash(location.hash));
  if (location.hash) revealHash(location.hash);
}
