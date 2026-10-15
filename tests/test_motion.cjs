"use strict";
// Exercise interruptions and accessibility state without modifying a browser or article data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../web/motion.js"), "utf8");
function environment() {
  const media = {matches: false, callbacks: [], addEventListener(name, callback) { this.callbacks.push(callback); },
    changed() { this.callbacks.forEach((callback) => callback()); }};
  const events = [];
  const frames = new Map();
  let frameId = 0;
  const window = {dispatchEvent(event) { events.push(event.type); }};
  vm.runInNewContext(source, {window, matchMedia: () => media, getComputedStyle: () => ({opacity: "0.7"}), Event,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); }, document: {addEventListener() {}, removeEventListener() {}}});
  function node() {
    const animations = [];
    const inner = {getBoundingClientRect: () => ({height: 180}), animate: animation};
    const item = {hidden: true, inert: true, attributes: {}, firstElementChild: inner, measured: 70,
      setAttribute(name, value) { this.attributes[name] = value; },
      getBoundingClientRect() { return {height: this.measured}; }, animate: animation};
    function animation(keyframes, options) {
      const result = {keyframes, options, cancelled: false, cancel() { this.cancelled = true; }};
      animations.push(result);
      return result;
    }
    return {item, animations};
  }
  return {motion: window.ReaderMotion, view: window.ReaderViewMotion, media, events, node, frames};
}
const env = environment();
const {item, animations} = env.node();
env.motion.setOpen(item, false);
assert.equal(item.hidden, true);
env.motion.setOpen(item, true, true);
assert.equal(item.hidden, false);
assert.equal(item.inert, false);
assert.equal(animations[0].keyframes[0].height, "0px");
assert.equal(animations[0].options.duration, 260);
env.motion.setOpen(item, false, true); // Reverse at the current, partly expanded height.
assert.equal(animations[0].cancelled, true);
assert.equal(animations[2].keyframes[0].height, "70px");
assert.equal(animations[2].keyframes[1].height, "0px");
assert.equal(animations[2].options.duration, 200);
assert.equal(item.hidden, false); // Visually exits, but immediately leaves the accessibility tree.
assert.equal(item.inert, true);
assert.equal(item.attributes["aria-hidden"], "true");
animations[0].onfinish(); // A stale completion must not reopen/hide a newer animation.
assert.equal(item.hidden, false);
env.motion.setOpen(item, true, true);
animations[2].onfinish();
assert.equal(item.inert, false);
assert.equal(item.hidden, false);
animations[4].onfinish();
assert.equal(item.hidden, false);
assert.equal(animations[4].cancelled, true);
assert.deepEqual(env.events, ["reader:motion-end"]);
const count = animations.length;
env.motion.setOpen(item, true, true);
assert.equal(animations.length, count); // Unchanged segments do not restart.
env.media.matches = true;
env.motion.setOpen(item, false, true);
assert.equal(item.hidden, true);
assert.equal(animations.length, count);
env.media.matches = false;
env.motion.setOpen(item, true, true);
env.media.matches = true;
env.media.changed(); // Changing the system preference during motion must settle now.
assert.equal(item.hidden, false);
assert.equal(animations.at(-2).cancelled, true);
const second = env.node();
second.item.animate = undefined;
env.media.matches = false;
env.motion.setOpen(second.item, true, true);
assert.equal(second.item.hidden, false);
assert.equal(second.animations.length, 0);
env.motion.setOpen(item, false, true);
env.motion.finishAll();
assert.equal(item.hidden, true);
console.log("PASS: continuous reversal, stale completion, no redundant replay, accessibility exit, reduced-motion/live change, fallback, finishAll");

(async () => {
  const scene = environment();
  const frame = scene.node(), from = scene.node(), to = scene.node();
  from.item.hidden = false;
  let restored = 0;
  const result = scene.view.swap(frame.item, from.item, to.item, () => { restored++; });
  assert.equal(restored, 0); // Never reposition visible text at the start of the fade.
  assert.equal(from.item.hidden, false);
  frame.animations[0].onfinish();
  assert.equal(restored, 1);
  assert.equal(from.item.hidden, true);
  assert.equal(to.item.hidden, false);
  assert.equal(frame.animations[0].cancelled, false); // Zero opacity still holds during restoration.
  for (let tick = 0; tick < 2; tick++) {
    const [id, callback] = scene.frames.entries().next().value;
    scene.frames.delete(id);
    callback();
  }
  assert.equal(frame.animations.length, 2);
  assert(frame.animations.every((animation) => animation.keyframes.every((key) => !('height' in key))));
  frame.animations[1].onfinish();
  assert.equal(await result, true);
  assert.equal(restored, 1);
  let staleRestore = false;
  const stale = scene.view.swap(frame.item, to.item, from.item, () => { staleRestore = true; });
  const staleAnimation = frame.animations.at(-1);
  scene.media.matches = true;
  const latest = scene.view.swap(frame.item, from.item, to.item);
  staleAnimation.onfinish();
  assert.equal(await stale, false);
  assert.equal(await latest, true);
  assert.equal(staleRestore, false);
  assert.equal(to.item.hidden, false);
  console.log("PASS: restore only while invisible, no height animation, stale swap cancellation, reduced-motion completion");
})().catch((error) => { console.error(error); process.exitCode = 1; });
