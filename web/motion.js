"use strict";

// Height stays continuous when an opening translation is closed midway (and vice versa).
window.ReaderMotion = (() => {
  const states = new WeakMap();
  const active = new Set();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let releaseAnchor = () => {};

  function settle(node, state) {
    state.height?.cancel();
    state.fade?.cancel();
    node.hidden = !state.open;
    node.inert = !state.open;
    node.setAttribute("aria-hidden", String(!state.open));
    active.delete(node);
  }

  function finishAll() {
    for (const node of active) settle(node, states.get(node));
    releaseAnchor();
  }

  function setOpen(node, open, animate = false) {
    const previous = states.get(node);
    if (previous?.open === open && animate) return;
    const inner = node.firstElementChild;
    const from = node.hidden ? 0 : node.getBoundingClientRect().height;
    const opacity = node.hidden ? .45 : Number(getComputedStyle(inner).opacity);
    previous?.height?.cancel();
    previous?.fade?.cancel();
    const state = {open};
    states.set(node, state);
    node.inert = !open;
    node.setAttribute("aria-hidden", String(!open));
    if (!animate || reduced.matches || typeof node.animate !== "function") {
      settle(node, state);
      return;
    }
    node.hidden = false;
    const target = open ? inner.getBoundingClientRect().height : 0;
    if (Math.abs(from - target) < .5) { settle(node, state); return; }
    const duration = open ? 260 : 200;
    active.add(node);
    state.height = node.animate([{height: `${from}px`}, {height: `${target}px`}],
      {duration, easing: "cubic-bezier(.16, 1, .3, 1)", fill: "both"});
    state.fade = inner.animate([{opacity}, {opacity: open ? 1 : .35}],
      {duration: open ? 200 : 160, easing: "ease-out", fill: "both"});
    state.height.onfinish = () => {
      if (states.get(node) !== state) return;
      settle(node, state);
      if (!active.size) window.dispatchEvent(new Event("reader:motion-end"));
    };
  }

  function holdPosition(scroller, anchor, top) {
    releaseAnchor();
    if (!anchor || !Number.isFinite(top)) return;
    let frame;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      cancelAnimationFrame(frame);
      scroller.removeEventListener("wheel", release);
      scroller.removeEventListener("touchstart", release);
      scroller.removeEventListener("pointerdown", release);
      document.removeEventListener("keydown", scrollKey);
    };
    const scrollKey = (event) => {
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) release();
    };
    const tick = () => {
      if (released || !anchor.isConnected) { release(); return; }
      scroller.scrollTop += anchor.getBoundingClientRect().top - top;
      if (active.size) frame = requestAnimationFrame(tick);
      else release();
    };
    releaseAnchor = release;
    scroller.addEventListener("wheel", release, {passive: true});
    scroller.addEventListener("touchstart", release, {passive: true});
    scroller.addEventListener("pointerdown", release, {passive: true});
    document.addEventListener("keydown", scrollKey);
    tick();
  }

  reduced.addEventListener("change", () => {
    if (reduced.matches) {
      // Let the current anchor receive the final reflow before releasing it.
      for (const node of active) settle(node, states.get(node));
      window.dispatchEvent(new Event("reader:motion-end"));
    }
  });
  return {setOpen, holdPosition, finishAll};
})();

// Only the body fades. Install content and restore its scroll while fully invisible.
window.ReaderViewMotion = (() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  let current = null;
  function finish() { current?.finish(); }
  function swap(frame, from, to, prepare = () => {}) {
    const opacity = Number(getComputedStyle(frame).opacity);
    current?.cancel();
    return new Promise((resolve) => {
      const animations = [];
      let installed = false;
      let frameId;
      function install() {
        if (installed) return;
        installed = true;
        from.hidden = true;
        from.inert = true;
        from.setAttribute("aria-hidden", "true");
        to.hidden = false;
        to.inert = false;
        to.setAttribute("aria-hidden", "false");
        prepare();
      }
      const state = {
        cancel() {
          cancelAnimationFrame(frameId);
          animations.forEach((animation) => animation.cancel());
          resolve(false);
        },
        finish() {
          if (current !== state) return;
          cancelAnimationFrame(frameId);
          install();
          animations.forEach((animation) => animation.cancel());
          current = null;
          resolve(true);
        }
      };
      current = state;
      if (reduced.matches || typeof frame.animate !== "function") { state.finish(); return; }
      const options = {easing: "cubic-bezier(.16, 1, .3, 1)", fill: "both"};
      const exit = frame.animate([{opacity}, {opacity: 0}], {...options, duration: 120});
      animations.push(exit);
      exit.onfinish = () => {
        if (current !== state) return;
        install();
        frameId = requestAnimationFrame(() => {
          if (current !== state) return;
          frameId = requestAnimationFrame(() => {
            if (current !== state) return;
            const enter = frame.animate([{opacity: 0}, {opacity: 1}], {...options, duration: 180});
            animations.push(enter);
            exit.cancel();
            enter.onfinish = state.finish;
          });
        });
      };
    });
  }
  reduced.addEventListener("change", () => { if (reduced.matches) finish(); });
  return {swap, finish};
})();
