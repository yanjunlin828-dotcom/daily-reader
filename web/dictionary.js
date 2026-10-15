"use strict";

window.ReaderDictionary = (() => {
  const reader = document.querySelector("#reader");
  const popup = document.createElement("section");
  popup.className = "dictionary-popup";
  popup.hidden = true;
  popup.setAttribute("role", "dialog");
  popup.setAttribute("aria-modal", "false");
  popup.setAttribute("aria-labelledby", "dictionary-title");
  popup.tabIndex = -1;
  document.body.append(popup);
  const announcer = document.createElement("div");
  announcer.className = "sr-only";
  announcer.setAttribute("role", "status");
  announcer.setAttribute("aria-live", "polite");
  document.body.append(announcer);
  let version = 0, controller, lookupPress = null;
  let current = null, focusBeforePopup = null;
  const cache = new Map();

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function close() {
    lookupPress = null;
    version++;
    controller?.abort();
    controller = null;
    current = null;
    const hadFocus = popup.contains(document.activeElement);
    popup.hidden = true;
    popup.removeAttribute("aria-busy");
    announcer.textContent = "";
    if (hadFocus) {
      const target = focusBeforePopup?.isConnected ? focusBeforePopup : reader;
      target.focus({preventScroll: true});
    }
  }

  function originalAt(target) {
    const original = target.closest?.("#article-view .original");
    if (!original || reader.hidden || document.querySelector("#main").inert ||
        document.querySelector(".reading-view-switching") || original.closest("[hidden], [inert]")) return null;
    return original;
  }

  function isLookupGesture(event) {
    return event.button === 0 && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
  }

  function wordAtPoint(original, x, y) {
    let textNode, offset;
    if (document.caretPositionFromPoint) {
      const caret = document.caretPositionFromPoint(x, y);
      textNode = caret?.offsetNode;
      offset = caret?.offset;
    } else if (document.caretRangeFromPoint) {
      const caret = document.caretRangeFromPoint(x, y);
      textNode = caret?.startContainer;
      offset = caret?.startOffset;
    }
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE || !original.contains(textNode)) return null;
    const text = textNode.textContent;
    const words = /[A-Za-z]+(?:['’‘‐‑-][A-Za-z]+)*/g;
    for (const match of text.matchAll(words)) {
      const start = match.index, end = start + match[0].length;
      if (offset < start || offset > end || match[0].length > 80) continue;
      if (/[A-Za-z0-9]/.test(text[start - 1] || "") || /[A-Za-z0-9]/.test(text[end] || "")) continue;
      // A local Range measures the word; it is never added to window selection.
      const range = document.createRange();
      range.setStart(textNode, start);
      range.setEnd(textNode, end);
      const onWord = [...range.getClientRects()].some(rect =>
        x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom);
      if (onWord) return {word: match[0], range};
    }
    return null;
  }

  function position() {
    if (!current || popup.hidden) return;
    const rects = [...current.range.getClientRects()].filter(rect => rect.width && rect.height);
    if (!rects.length) { close(); return; }
    const anchor = rects.at(-1);
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
    const margin = 12, gap = 9;
    popup.style.maxWidth = `${Math.max(1, width - margin * 2)}px`;
    popup.style.maxHeight = `${Math.max(1, Math.min(460, height - margin * 2))}px`;
    const size = popup.getBoundingClientRect();
    const x = Math.max(left + margin, Math.min(anchor.left, left + width - size.width - margin));
    let y = anchor.bottom + gap;
    if (y + size.height > top + height - margin) y = anchor.top - size.height - gap;
    y = Math.max(top + margin, Math.min(y, top + height - size.height - margin));
    popup.style.left = `${x}px`;
    popup.style.top = `${y}px`;
  }

  function header(word) {
    popup.replaceChildren();
    const heading = node("div", "dictionary-heading");
    const title = node("h2", "dictionary-title", word);
    title.id = "dictionary-title";
    title.lang = "en";
    const button = node("button", "dictionary-close", "×");
    button.type = "button";
    button.setAttribute("aria-label", "关闭单词释义");
    button.addEventListener("click", close);
    heading.append(title, button);
    popup.append(heading);
    popup.hidden = false;
  }

  function showResult(data) {
    header(current.word);
    if (data.status === "not_found") {
      popup.append(node("p", "dictionary-message", "本地词库未收录这个单词。"));
      announcer.textContent = `${current.word}，本地词库未收录。`;
    } else {
      if (data.match_type === "inflection") {
        popup.append(node("p", "dictionary-match", "以下为词库记录的原形释义。"));
      }
      for (const entry of data.entries) {
        const section = node("div", "dictionary-entry");
        if (data.entries.length > 1 || entry.word !== current.word) {
          const headword = node("h3", "dictionary-headword", entry.word);
          headword.lang = "en";
          section.append(headword);
        }
        if (entry.phonetic) {
          const phonetic = node("p", "dictionary-phonetic", entry.phonetic);
          phonetic.lang = "en";
          section.append(phonetic);
        }
        const text = entry.translation || entry.definition;
        const meanings = node("div", "dictionary-meanings", text || "此词条没有释义内容。");
        if (!entry.translation && entry.definition) {
          section.append(node("p", "dictionary-match", "此词条暂无中文释义，以下为英文原文。"));
          meanings.lang = "en";
        }
        section.append(meanings);
        popup.append(section);
        // Collapse the rendered block without cutting or reconstructing the source text.
        if (meanings.scrollHeight > 210) {
          meanings.classList.add("is-collapsed");
          const expand = node("button", "dictionary-expand", "展开全部释义");
          expand.type = "button";
          expand.setAttribute("aria-expanded", "false");
          expand.addEventListener("click", () => {
            const collapsed = meanings.classList.toggle("is-collapsed");
            expand.textContent = collapsed ? "展开全部释义" : "收起释义";
            expand.setAttribute("aria-expanded", String(!collapsed));
            position();
          });
          section.append(expand);
        }
      }
      if (data.truncated) popup.append(node("p", "dictionary-match", "匹配词条较多，当前显示前 8 条。"));
      announcer.textContent = `${current.word} 的词典释义已显示。按 Alt 加向下方向键进入释义浮窗。`;
    }
    popup.removeAttribute("aria-busy");
    position();
  }

  async function lookup(selected) {
    close();
    current = selected;
    focusBeforePopup = document.activeElement;
    const token = ++version;
    header(selected.word);
    popup.append(node("p", "dictionary-message", "正在查询本地词库…"));
    popup.setAttribute("aria-busy", "true");
    position();
    if (cache.has(selected.word)) { showResult(cache.get(selected.word)); return; }
    const request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => request.abort(), 5000);
    try {
      const response = await fetch(`/api/dictionary/lookup?word=${encodeURIComponent(selected.word)}`,
        {signal: request.signal, cache: "no-store"});
      let data;
      try { data = await response.json(); } catch { throw new Error("当前后台尚未提供查词接口，请使用新版后台打开阅读库。"); }
      if (!response.ok) {
        throw new Error(response.status === 404 ? "当前后台尚未提供查词接口，请使用新版后台打开阅读库。" :
          data.error || "本地词库暂时无法查询。");
      }
      if (token !== version) return;
      if (!Array.isArray(data.entries) || !["found", "not_found"].includes(data.status)) throw new Error("词库返回格式不正确。");
      if (cache.size >= 200) cache.delete(cache.keys().next().value);
      cache.set(selected.word, data);
      showResult(data);
    } catch (error) {
      if (token !== version) return;
      header(selected.word);
      popup.append(node("p", "dictionary-message", error.name === "AbortError" ? "本地查询超时，请重新选词。" :
        error instanceof TypeError ? "无法连接本地服务，请检查阅读库后台。" : error.message));
      popup.removeAttribute("aria-busy");
      position();
    } finally {
      clearTimeout(timeout);
      if (controller === request) controller = null;
    }
  }

  document.addEventListener("pointerdown", event => {
    if (popup.contains(event.target)) return;
    close();
    const original = isLookupGesture(event) && originalAt(event.target);
    if (original) lookupPress = {original, x: event.clientX, y: event.clientY};
  });
  // Cancel the browser's native text-selection action only for Alt + left click.
  document.addEventListener("mousedown", event => {
    if (isLookupGesture(event) && originalAt(event.target)) event.preventDefault();
  }, true);
  document.addEventListener("click", event => {
    const press = lookupPress;
    lookupPress = null;
    if (!press || event.button !== 0) return;
    event.preventDefault();
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 6 ||
        originalAt(event.target) !== press.original) return;
    // Clear an earlier user selection, but never select the word being queried.
    window.getSelection()?.removeAllRanges();
    const word = wordAtPoint(press.original, event.clientX, event.clientY);
    if (word) lookup(word);
  }, true);
  document.addEventListener("pointercancel", close);
  document.addEventListener("keydown", event => {
    if (popup.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.altKey && event.key === "ArrowDown") {
      event.preventDefault();
      popup.querySelector("button")?.focus({preventScroll: true});
    }
  });
  reader.addEventListener("scroll", close, {passive: true});
  window.addEventListener("resize", close);
  window.addEventListener("blur", close);
  window.visualViewport?.addEventListener("resize", close);
  document.addEventListener("visibilitychange", () => { if (document.hidden) close(); });
  return {close};
})();
