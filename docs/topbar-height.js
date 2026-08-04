// Publishes the topbar's measured height as a --topbar-h custom property on
// :root, for anything that has to come to rest below it.
//
// Needed because at <=800px the document (not .mainpane) scrolls and .topbar is
// pinned over it with position:sticky, so a second sticky element at top:0 slides
// underneath. A constant won't do: about.html's topbar wraps to two rows at
// <=1100px and its height varies with font metrics and the scheme <select>, so the
// value is measured live and re-measured on resize.
//
// Consumers: the sticky section-header rule in styles.css (@media max-width:800px)
// and scrollIntoViewBelowTopbar() in anchor-details.js.

let topbar = null;

function measure() {
  if (!topbar) topbar = document.querySelector(".topbar");
  if (!topbar) return 0;
  // Only the *pinned* topbar needs clearing. Above the breakpoint it scrolls away
  // with the page (.mainpane is the scroll container), so the offset is 0 and
  // sticky headers rest at the very top of the pane.
  const pinned = getComputedStyle(topbar).position === "sticky";
  return pinned ? Math.round(topbar.getBoundingClientRect().height) : 0;
}

function publish() {
  document.documentElement.style.setProperty("--topbar-h", `${measure()}px`);
}

export function initTopbarHeight() {
  publish();
  // Height changes when the topbar rewraps (viewport resize / orientation) and
  // once webfonts settle; ResizeObserver catches both without polling.
  if (window.ResizeObserver && document.querySelector(".topbar")) {
    new ResizeObserver(publish).observe(document.querySelector(".topbar"));
  }
  window.addEventListener("resize", publish);
}

// Current offset in px, for JS-driven scrolling that must clear the same bar.
export function topbarOffset() {
  return measure();
}
