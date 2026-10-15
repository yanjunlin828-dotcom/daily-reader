"use strict";

const $ = (selector) => document.querySelector(selector);
const reader = $("#reader");
const content = $("#reader-content");
const STORAGE_KEY = "english-news-reader.v1";
const folderLabels = new Map();
let library = null;
let article = null;
let expanded = new Set();
let readingMode = "article";
let viewSwapVersion = 0;
let viewSwitching = false;
let readingLayoutFrame;
let requestVersion = 0;
let restoring = false;
let saveTimer;
let toastTimer;
let toastClearTimer;
let refreshFeedbackTimer;
let drawerReturnFocus;
let pointerStart = null;
let storageUnavailable = false;
let searchQuery = "";
let librarySidebar;
let preferences = {schema_version: 1, font_size: 20, last_article: null, folder: "all", list_label_mode: "title", show_paragraph_numbers: false, articles: {}};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(svg.namespaceURI, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function readPreferences() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!saved || saved.schema_version !== 1 || !saved.articles || typeof saved.articles !== "object" || Array.isArray(saved.articles)) return;
    preferences = {...preferences, ...saved};
    preferences.font_size = Number.isInteger(saved.font_size) ? Math.max(18, Math.min(24, saved.font_size)) : 20;
    preferences.list_label_mode = saved.list_label_mode === "date" ? "date" : "title";
    preferences.show_paragraph_numbers = saved.show_paragraph_numbers === true;
  } catch {
    storageUnavailable = true;
  }
}

function writePreferences() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    if (!storageUnavailable) toast("浏览器无法保存阅读位置。你仍可以继续阅读。", 5000);
    storageUnavailable = true;
  }
}

function toast(message, duration = 2200) {
  clearTimeout(toastTimer);
  clearTimeout(toastClearTimer);
  const notice = $("#toast");
  notice.textContent = message;
  notice.classList.add("is-visible");
  toastTimer = setTimeout(() => {
    notice.classList.remove("is-visible");
    // Clear only after the exit finishes; a new notice cancels this cleanup.
    toastClearTimer = setTimeout(() => { notice.textContent = ""; }, 180);
  }, duration);
}

function saveReading() {
  clearTimeout(saveTimer);
  if (!article || restoring || reader.hidden || viewSwitching) return;
  if (readingMode === "guide") return;
  const blocks = [...content.querySelectorAll(".article-block")];
  const readerTop = reader.getBoundingClientRect().top;
  const anchor = blocks.find((block) => block.getBoundingClientRect().bottom > readerTop + 12);
  preferences.last_article = article.id;
  preferences.folder = "all";
  preferences.articles[article.id] = {
    content_version: article.content_version,
    scroll_layout: "unified",
    expanded: [...expanded],
    scroll_top: reader.scrollTop,
    anchor: anchor?.dataset.blockId ?? null,
    anchor_offset: anchor ? anchor.getBoundingClientRect().top - readerTop : 0
  };
  writePreferences();
}

function updateMeter({force = false} = {}) {
  // Intermediate view heights do not represent either reading position.
  if (viewSwitching && !force) return;
  if (reader.hidden) { $("#reading-meter").textContent = "草稿尚未保存"; return; }
  const available = reader.scrollHeight - reader.clientHeight;
  const progress = article ? (available > 1 ? Math.max(0, Math.min(1, reader.scrollTop / available)) : 1) : 0;
  $("#progress-fill").style.transform = `scaleX(${progress})`;
  $("#reading-meter").textContent = article ? `阅读位置 ${Math.round(progress * 100)}%` : "选择一篇文章";
}

async function api(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {signal: controller.signal, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "文章暂时无法读取。");
    return data;
  } catch (error) {
    if (error instanceof TypeError || error.name === "AbortError") {
      throw new Error("无法连接本地服务。请确认启动窗口仍在运行，然后重试。");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function visibleArticles() {
  return library.articles.filter((item) => (item.title + " " + item.source).toLocaleLowerCase().includes(searchQuery.toLocaleLowerCase()));
}

const importedDateFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Singapore", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23"
});

function importedDateLabel(item) {
  const parts = Object.fromEntries(importedDateFormatter.formatToParts(new Date(item.created_at)).map((part) => [part.type, part.value]));
  return `${parts.year}.${parts.month}.${parts.day} ${parts.hour}:${parts.minute}`;
}

function dateLabel(item) {
  // Article dates are date-only strings; never shift them using the host timezone.
  const value = item.published_date || item.created_at.slice(0, 10);
  return value.replaceAll("-", ".");
}

function renderNavigation() {
  if (!library) return;
  const items = visibleArticles();
  $("#library-count").textContent = searchQuery ? `${items.length}/${library.articles.length}` : String(library.articles.length);
  const dateMode = preferences.list_label_mode === "date";
  const modeButton = $("#toggle-list-label");
  $("#list-label-mode").textContent = dateMode ? "载入日期" : "文章名";
  modeButton.setAttribute("aria-pressed", String(dateMode));
  modeButton.setAttribute("aria-label", dateMode ? "改用文章名显示文章列表" : "改用载入日期显示文章列表");
  modeButton.title = dateMode ? "点击切换为文章名" : "点击切换为载入日期";
  librarySidebar.render({items: items.map(item => ({...item, date: importedDateLabel(item),
    hint: `${item.title}\n载入于 ${importedDateLabel(item)}${item.translation_status !== "complete" ? " · 待补译" : ""}`})),
    allItems: library.articles, activeId: article?.id, dateMode, searching: Boolean(searchQuery),
    organization: library.organization, error: library.organization_error});
}

function syncBreadcrumb() {
  if (reader.hidden) return;
  const id = library?.organization?.article_folders[article?.id];
  const folder = library?.organization?.folders.find(item => item.id === id);
  $("#breadcrumb").textContent = folder ? `文章库 / ${folder.name}` : "文章库";
}

function screenState(title, description, buttonText, action) {
  $("#reader-chrome").hidden = true;
  const section = element("div", "screen-state");
  const heading = element("h1", "", title);
  heading.id = "article-title";
  heading.tabIndex = -1;
  section.append(heading, element("p", "", description));
  if (buttonText) {
    const button = element("button", "", buttonText);
    button.addEventListener("click", action);
    section.append(button);
  }
  content.replaceChildren(section);
  reader.scrollTop = 0;
}

function textContentNode(block, translated = false) {
  const text = translated ? block.translation : block.original;
  if (block.type === "list") {
    const list = element("ul");
    for (const line of text.split("\n").filter((line) => line.trim())) list.append(element("li", "", line));
    return list;
  }
  return element(!translated && block.type === "heading" ? "h2" : "p", "", text);
}

function paragraphNumberNode(number) {
  const node = element("span", "paragraph-number", String(number).padStart(2, "0"));
  node.setAttribute("aria-hidden", "true");
  return node;
}

function syncParagraphNumbers() {
  const visible = preferences.show_paragraph_numbers;
  reader.classList.toggle("show-paragraph-numbers", visible);
  const button = $("#toggle-paragraph-numbers");
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", visible ? "隐藏段落编号" : "显示段落编号");
  button.title = visible ? "隐藏段落编号" : "显示段落编号";
}

function blockNode(block, index, paragraphNumber) {
  const section = element("section", `article-block ${block.type}`);
  section.dataset.blockId = block.id;
  if (paragraphNumber !== null) section.append(paragraphNumberNode(paragraphNumber));
  const original = element("div", "original");
  original.lang = "en";
  original.append(textContentNode(block));
  // Keep a keyboard-only control without adding an icon beside the paragraph.
  const button = element("button", "translation-keyboard-toggle sr-only", expanded.has(block.id) ? "收起译文" : "显示译文");
  button.type = "button";
  button.dataset.toggleId = block.id;
  button.setAttribute("aria-controls", `translation-${block.id}`);
  button.setAttribute("aria-expanded", String(expanded.has(block.id)));
  button.setAttribute("aria-label", `${expanded.has(block.id) ? "收起" : "显示"}第 ${index + 1} 个内容块的译文`);
  const translation = element("div", `translation${block.translation === null ? " missing" : ""}`);
  translation.id = `translation-${block.id}`;
  translation.lang = "zh-CN";
  translation.hidden = !expanded.has(block.id);
  translation.setAttribute("role", "region");
  translation.setAttribute("aria-label", `第 ${index + 1} 个内容块的中文译文`);
  const translationContent = element("div", "translation-content");
  if (block.translation === null) translationContent.append(element("span", "translation-label", "待翻译"));
  translationContent.append(block.translation === null ? element("p", "", "这一段还没有译文。原文已保存，后续可以按段落编号补充翻译。") : textContentNode(block, true));
  translation.append(translationContent);
  original.addEventListener("pointerdown", (event) => { pointerStart = {x: event.clientX, y: event.clientY}; });
  let textClickTimer;
  original.addEventListener("click", (event) => {
    clearTimeout(textClickTimer);
    const moved = pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 6;
    pointerStart = null;
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        moved || event.detail > 1 || !window.getSelection()?.isCollapsed) return;
    // A double-click selects a word; do not toggle on its first click.
    textClickTimer = setTimeout(() => {
      if (section.isConnected && window.getSelection()?.isCollapsed) toggleBlock(block.id);
    }, 240);
  });
  button.addEventListener("click", () => toggleBlock(block.id));
  section.append(original, button, translation);
  return section;
}

function syncExpansion({animate = false, anchor = null} = {}) {
  if (!article) return;
  if (animate && !anchor) {
    const top = reader.getBoundingClientRect().top;
    anchor = [...content.querySelectorAll(".article-block")].find((node) => node.getBoundingClientRect().bottom > top + 12);
  }
  const before = anchor?.getBoundingClientRect().top;
  article.blocks.forEach((block, index) => {
    const open = expanded.has(block.id);
    const button = content.querySelector(`[data-toggle-id="${block.id}"]`);
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", `${open ? "收起" : "显示"}第 ${index + 1} 个内容块的译文`);
    button.textContent = open ? "收起译文" : "显示译文";
    ReaderMotion.setOpen(document.getElementById(`translation-${block.id}`), open, animate);
  });
  if (animate) ReaderMotion.holdPosition(reader, anchor, before);
  $("#expand-all").disabled = readingMode === "guide" || expanded.size === article.blocks.length;
  $("#collapse-all").disabled = readingMode === "guide" || expanded.size === 0;
  updateMeter();
}

function toggleBlock(id) {
  expanded.has(id) ? expanded.delete(id) : expanded.add(id);
  const anchor = content.querySelector(`[data-block-id="${id}"]`);
  syncExpansion({animate: true, anchor});
  saveReading();
}

function applyFont() {
  document.documentElement.style.setProperty("--font-size", `${preferences.font_size}px`);
  if ($("#font-value")) {
    $("#font-value").textContent = `${preferences.font_size}px`;
    $("#font-smaller").disabled = preferences.font_size === 18;
    $("#font-larger").disabled = preferences.font_size === 24;
  }
}

function updateReadingLayout() {
  const views = $("#reading-views");
  if (reader.hidden || !views) return;
  // A short view must still let the shared title/toolbar scroll naturally offscreen.
  // This is minimum document space, not a fixed-height or nested scrolling window.
  views.style.minHeight = `${reader.clientHeight}px`;
}

function scheduleReadingLayout() {
  cancelAnimationFrame(readingLayoutFrame);
  readingLayoutFrame = requestAnimationFrame(() => { updateReadingLayout(); updateMeter(); });
}

function changeFont(delta) {
  ReaderViewMotion.finish();
  ReaderMotion.finishAll();
  // Keep the current paragraph in the same place when the font reflows.
  const top = reader.getBoundingClientRect().top;
  const anchor = [...content.querySelectorAll(readingMode === "guide" ? ".guide-block" : ".article-block")].find((node) => node.getBoundingClientRect().bottom > top + 12);
  const before = anchor?.getBoundingClientRect().top;
  preferences.font_size = Math.max(18, Math.min(24, preferences.font_size + delta));
  applyFont();
  if (anchor) reader.scrollTop += anchor.getBoundingClientRect().top - before;
  writePreferences();
  saveReading();
  updateMeter();
}

function guideContents() {
  const nodes = [element("h2", "guide-title", "中文导读")];
  if (!article.reading_guide) {
    const empty = element("div", "guide-empty");
    empty.append(element("p", "", article.reading_guide_error ? "导读暂时无法读取" : article.reading_guide_stale ? "原文已更新，导读待更新" : "这篇文章还没有导读"),
      element("p", "", article.reading_guide_error ? "请刷新文章库后重试，仍可切回原文继续阅读。" : article.reading_guide_stale ? "旧导读已保留，可根据当前原文重新生成并导入。" : "添加后，就可以在这里阅读分好段的中文导读。"));
    nodes.push(empty);
  } else {
    let paragraphNumber = 0;
    for (const block of article.reading_guide.blocks) {
      const section = element("section", `guide-block ${block.type}`);
      section.dataset.guideBlockId = block.id;
      if (block.type !== "heading") section.append(paragraphNumberNode(++paragraphNumber));
      if (block.type === "list") {
        const list = element("ul");
        block.text.split("\n").filter((line) => line.trim()).forEach((line) => list.append(element("li", "", line)));
        section.append(list);
      } else section.append(element(block.type === "heading" ? "h3" : "p", "", block.text));
      nodes.push(section);
    }
    nodes.push(element("p", "guide-end", "导读结束"));
  }
  return nodes;
}

async function toggleReadingGuide() {
  if (!article) return;
  window.ReaderDictionary?.close();
  saveReading();
  const toGuide = readingMode !== "guide";
  const token = ++viewSwapVersion;
  readingMode = toGuide ? "guide" : "article";
  viewSwitching = true;
  $("#main").classList.add("reading-view-switching");
  ReaderMotion.finishAll();
  const button = $("#toggle-reading-guide");
  button.textContent = toGuide ? "返回原文" : "中文导读";
  button.setAttribute("aria-pressed", String(toGuide));
  button.setAttribute("aria-label", toGuide ? "返回英文原文" : "切换到中文导读");
  const from = toGuide ? $("#article-view") : $("#guide-view");
  const to = toGuide ? $("#guide-view") : $("#article-view");
  const views = $("#reading-views");
  // Keep the outgoing document's space until the body is fully transparent.
  views.style.minHeight = `${Math.max(reader.clientHeight, views.getBoundingClientRect().height)}px`;
  syncExpansion();
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (token !== viewSwapVersion) return;
  }
  const completed = await ReaderViewMotion.swap(views, from, to, () => {
    // Keep the shared page position. Clamp only if the new text is shorter and
    // the common title/toolbar are already outside the viewport.
    const scroll = reader.scrollTop;
    updateReadingLayout();
    reader.scrollTop = scroll;
    updateMeter({force: true});
  });
  if (!completed || token !== viewSwapVersion) return;
  updateMeter({force: true});
  viewSwitching = false;
  $("#main").classList.remove("reading-view-switching");
  saveReading();
}

function renderArticle(data) {
  const header = element("header", "article-header");
  const meta = element("div", "article-meta");
  meta.append(element("span", "", folderLabels.get(data.folder_id)));
  if (data.is_demo) meta.append(element("span", "demo-badge", "演示文章 · 非真实新闻"));
  const title = element("h1", "", data.title);
  title.id = "article-title";
  title.tabIndex = -1;
  title.lang = "en";
  const summary = {word_count: data.blocks.reduce((total, block) => total + block.original.split(/\s+/).filter(Boolean).length, 0)};
  const description = element("div", "article-description");
  const count = data.blocks.filter((block) => block.type === "paragraph").length;
  const translated = data.blocks.filter((block) => block.translation !== null).length;
  description.append(element("span", "", `${data.published_date ? "发布" : "录入"}于 ${dateLabel(data)}`), element("span", "separator", "·"), element("span", "", `${summary.word_count} 词 / ${count} 段正文`), element("span", "separator", "·"), element("span", "", translated === data.blocks.length ? "译文已齐全" : `已翻译 ${translated}/${data.blocks.length} 个内容块`));
  header.append(meta, title, description);
  if (data.source_url || !data.is_demo) {
    const source = element("div", "article-source", `来源：${data.source} `);
    if (data.source_url) {
      const link = element("a", "", "查看原文");
      link.href = data.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.append(icon("link"));
      source.append(link);
    }
    header.append(source);
  }
  const toolbar = element("div", "reading-toolbar");
  toolbar.setAttribute("aria-label", "阅读设置");
  const controls = element("div", "translation-controls");
  for (const [id, label, action] of [
    ["expand-all", "全部展开译文", () => { expanded = new Set(data.blocks.map((block) => block.id)); syncExpansion({animate: true}); saveReading(); }],
    ["collapse-all", "全部收起", () => { expanded.clear(); syncExpansion({animate: true}); saveReading(); }]
  ]) {
    const button = element("button", "text-button", label);
    button.id = id;
    button.addEventListener("click", action);
    controls.append(button);
  }
  const guideButton = element("button", "text-button guide-toggle", "中文导读");
  guideButton.id = "toggle-reading-guide";
  guideButton.type = "button";
  guideButton.setAttribute("aria-controls", "guide-view article-view");
  guideButton.setAttribute("aria-pressed", "false");
  guideButton.setAttribute("aria-label", "切换到中文导读");
  guideButton.setAttribute("aria-keyshortcuts", "ArrowRight ArrowLeft");
  guideButton.title = "→ 中文导读 · ← 英文原文";
  guideButton.addEventListener("click", toggleReadingGuide);
  controls.append(guideButton);
  const font = element("div", "font-controls");
  font.setAttribute("role", "group");
  font.setAttribute("aria-label", "正文字号");
  const smaller = element("button", "font-button", "A−");
  smaller.id = "font-smaller";
  smaller.setAttribute("aria-label", "减小字号");
  smaller.addEventListener("click", () => changeFont(-1));
  const larger = element("button", "font-button large", "A+");
  larger.id = "font-larger";
  larger.setAttribute("aria-label", "增大字号");
  larger.addEventListener("click", () => changeFont(1));
  const value = element("span", "", `${preferences.font_size}px`);
  value.id = "font-value";
  value.setAttribute("aria-live", "polite");
  font.append(smaller, value, larger);
  toolbar.append(controls, font);
  const end = element("footer", "article-end");
  end.append(element("span", "", `全文结束 · ${data.blocks.length} 个内容块`));
  ReaderMotion.finishAll();
  ReaderViewMotion.finish();
  const views = element("div", "reading-views");
  views.id = "reading-views";
  const originalView = element("div", "reading-view article-reading-view");
  originalView.id = "article-view";
  originalView.setAttribute("role", "region");
  originalView.setAttribute("aria-label", "英文原文与逐段译文");
  let paragraphNumber = 0;
  originalView.append(...data.blocks.map((block, index) => blockNode(block, index, block.type === "heading" ? null : ++paragraphNumber)), end);
  const guideView = element("section", "reading-view guide-reading-view");
  guideView.id = "guide-view";
  guideView.lang = "zh-CN";
  guideView.setAttribute("aria-label", "中文导读");
  guideView.setAttribute("aria-hidden", "true");
  guideView.inert = true;
  guideView.hidden = true;
  guideView.append(...guideContents());
  views.append(originalView, guideView);
  const chrome = element("div", "reader-chrome-inner");
  chrome.append(header, toolbar);
  $("#reader-chrome").replaceChildren(chrome);
  $("#reader-chrome").hidden = false;
  content.replaceChildren(views);
  updateNextArticle();
  document.title = `${data.title} · Daily Reader`;
  syncBreadcrumb();
  applyFont();
  updateReadingLayout();
  syncExpansion();
}

function updateNextArticle() {
  const end = content.querySelector(".article-end");
  if (!article || !end) return;
  end.querySelector(".next-article")?.remove();
  const items = visibleArticles();
  const index = items.findIndex((item) => item.id === article.id);
  if (items.length < 2 || index < 0) return;
  const next = items[(index + 1) % items.length];
  const button = element("button", "next-article", "阅读下一篇");
  button.append(icon("arrow"));
  button.addEventListener("click", () => loadArticle(next.id, {focus: true}));
  end.append(button);
}

async function loadArticle(id, {focus = false, initial = false} = {}) {
  window.ReaderDictionary?.close();
  hideImportPanel();
  saveReading();
  ReaderViewMotion.finish();
  viewSwapVersion++;
  viewSwitching = false;
  $("#main").classList.remove("reading-view-switching");
  readingMode = "article";
  const token = ++requestVersion;
  article = null;
  restoring = true;
  reader.setAttribute("aria-busy", "true");
  screenState("正在打开文章", "原文与译文一起读取，阅读时无需等待翻译。") ;
  updateMeter();
  try {
    const data = await api(`/api/articles/${encodeURIComponent(id)}`);
    if (token !== requestVersion) return;
    article = data;
    const saved = preferences.articles[id];
    const valid = saved && saved.content_version === data.content_version;
    expanded = new Set(valid && Array.isArray(saved.expanded) ? saved.expanded.filter((value) => data.blocks.some((block) => block.id === value)) : []);
    renderArticle(data);
    renderNavigation();
    // Restore against the final local font metrics, rather than the fallback's height.
    if (document.fonts) await Promise.allSettled([
      document.fonts.load('400 18px "Reader Serif"', "中文译文"),
      document.fonts.ready
    ]);
    if (token !== requestVersion) return;
    updateReadingLayout();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (token !== requestVersion) return;
    const anchor = valid && typeof saved.anchor === "string" ? [...content.querySelectorAll(".article-block")].find((node) => node.dataset.blockId === saved.anchor) : null;
    if (valid && saved.scroll_layout !== "unified" && saved.scroll_top === 0) {
      reader.scrollTop = 0;
    } else if (anchor && Number.isFinite(saved.anchor_offset)) {
      reader.scrollTop += anchor.getBoundingClientRect().top - reader.getBoundingClientRect().top - saved.anchor_offset;
    } else reader.scrollTop = valid && Number.isFinite(saved.scroll_top) ? Math.max(0, saved.scroll_top) : 0;
    restoring = false;
    reader.removeAttribute("aria-busy");
    if (focus) $("#article-title").focus({preventScroll: true});
    updateMeter();
    saveReading();
    if (valid && saved.scroll_top > 40) toast("已回到上次阅读的位置", 1600);
    if (initial && storageUnavailable) toast("浏览器无法读取阅读记录。本次仍可正常阅读。", 5000);
  } catch (error) {
    if (token !== requestVersion) return;
    restoring = false;
    reader.removeAttribute("aria-busy");
    screenState("文章暂时打不开", error.message, "重新打开", () => loadArticle(id, {focus}));
    renderNavigation();
  }
}

async function loadLibrary() {
  screenState("正在打开文章库", "读取本地保存的原文和译文…");
  try {
    library = await api("/api/library");
    folderLabels.clear();
    library.folders.forEach((folder) => folderLabels.set(folder.id, folder.name));
    const last = library.articles.find((item) => item.id === preferences.last_article);
    renderNavigation();
    if (library.warnings.length) {
      $("#notice").textContent = library.warnings.join(" ");
      $("#notice").hidden = false;
    }
    if (!library.articles.length) {
      screenState("从一篇英语新闻开始", "点击右上角的录入文章，粘贴英文正文或已经翻译好的双语内容。");
      updateMeter();
    } else {
      const first = last || visibleArticles()[0];
      if (first) await loadArticle(first.id, {initial: true});
    }
  } catch (error) {
    screenState("暂时无法打开文章库", error.message, "重新连接", loadLibrary);
    $("#article-list").replaceChildren(element("p", "sidebar-empty", "文章列表暂时无法读取。请在右侧重新连接。"));
  }
}

function openDrawer() {
  if (!matchMedia("(max-width: 760px)").matches) return;
  window.ReaderDictionary?.close();
  drawerReturnFocus = document.activeElement;
  $("#sidebar").classList.add("drawer-open");
  $("#sidebar").setAttribute("role", "dialog");
  $("#sidebar").setAttribute("aria-modal", "true");
  $("#drawer-backdrop").hidden = false;
  $("#main").inert = true;
  $("#open-library").setAttribute("aria-expanded", "true");
  $("#close-library").focus();
}

function closeDrawer(returnFocus = true) {
  librarySidebar?.closeMenu(false);
  const wasOpen = $("#sidebar").classList.contains("drawer-open");
  $("#sidebar").classList.remove("drawer-open");
  $("#sidebar").removeAttribute("role");
  $("#sidebar").removeAttribute("aria-modal");
  $("#drawer-backdrop").hidden = true;
  $("#main").inert = false;
  $("#open-library").setAttribute("aria-expanded", "false");
  if (wasOpen && returnFocus) drawerReturnFocus?.focus({preventScroll: true});
}

$("#open-library").addEventListener("click", openDrawer);
$("#close-library").addEventListener("click", () => closeDrawer());
$("#drawer-backdrop").addEventListener("click", () => closeDrawer());
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.target.closest?.("dialog[open], [role=menu]")) return;
  if (!$("#sidebar").classList.contains("drawer-open")) return;
  if (event.key === "Escape") closeDrawer();
  if (event.key === "Tab") {
    const buttons = [...$("#sidebar").querySelectorAll("button,input")].filter((button) => !button.disabled && !button.closest("[inert]") && button.getClientRects().length);
    const first = buttons[0], last = buttons.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  if (event.defaultPrevented || event.isComposing || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (!article || restoring || reader.hidden || $("#main").inert || $("#sidebar").classList.contains("drawer-open") || document.querySelector("dialog[open]")) return;
  const target = event.target;
  // Leave cursor movement, text selection and native widget navigation intact.
  if (target.isContentEditable || target.closest?.("input, textarea, select, [contenteditable]:not([contenteditable=false]), [role=textbox], [role=combobox], [role=slider], [role=spinbutton], [role=menu], [role=listbox], [role=tablist]")) return;
  if (!window.getSelection()?.isCollapsed) return;
  event.preventDefault();
  const mode = event.key === "ArrowRight" ? "guide" : "article";
  if (readingMode !== mode) toggleReadingGuide();
});
reader.addEventListener("scroll", () => {
  updateMeter();
  if (!restoring) { clearTimeout(saveTimer); saveTimer = setTimeout(saveReading, 180); }
}, {passive: true});
window.addEventListener("reader:motion-end", () => { updateMeter(); saveReading(); });
window.addEventListener("pagehide", saveReading);
window.addEventListener("resize", () => { if (!matchMedia("(max-width: 760px)").matches) closeDrawer(false); updateMeter(); });
new ResizeObserver(scheduleReadingLayout).observe($("#main"));
document.fonts?.addEventListener("loadingdone", scheduleReadingLayout);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") saveReading(); });
function hideImportPanel() {
  const panel = $("#import-panel");
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  reader.hidden = false;
  $("#reader-chrome").hidden = !article;
  $(".reading-progress").hidden = false;
  updateReadingLayout();
}

async function refreshLibrary({openId = null} = {}) {
  const button = $("#refresh-library");
  clearTimeout(refreshFeedbackTimer);
  button.classList.remove("refresh-complete");
  button.classList.add("is-refreshing");
  button.setAttribute("aria-busy", "true");
  button.setAttribute("aria-label", "正在刷新文章库");
  button.disabled = true;
  try {
    const next = await api("/api/library");
    library = next;
    folderLabels.clear();
    next.folders.forEach((folder) => folderLabels.set(folder.id, folder.name));
    if (openId) {
      searchQuery = "";
      $("#article-search").value = "";
    }
    renderNavigation();
    syncBreadcrumb();
    $("#notice").hidden = !next.warnings.length;
    $("#notice").textContent = next.warnings.join(" ");
    if (openId) await loadArticle(openId, {focus: true});
    else if (article && !reader.hidden) {
      const latest = next.articles.find((item) => item.id === article.id);
      if (!latest) {
        saveReading();
        window.ReaderDictionary?.close();
        ReaderViewMotion.finish();
        viewSwapVersion++;
        requestVersion++;
        viewSwitching = false;
        restoring = false;
        $("#main").classList.remove("reading-view-switching");
        reader.removeAttribute("aria-busy");
        article = null;
        readingMode = "article";
        document.title = "Daily Reader";
        $("#breadcrumb").textContent = "文章库";
        renderNavigation();
        const first = visibleArticles()[0];
        if (first) await loadArticle(first.id);
        else {
          screenState("当前文章已移出文章库", "可以在左侧选择其他文章，或通过 MCP 恢复回收区中的文章。");
          updateMeter();
        }
      }
      else if (latest.content_version !== article.content_version) await loadArticle(article.id);
      else {
        const loadedId = article.id;
        const current = await api(`/api/articles/${encodeURIComponent(loadedId)}`);
        if (!article || article.id !== loadedId) return true;
        article.reading_guide = current.reading_guide;
        article.reading_guide_error = current.reading_guide_error;
        article.reading_guide_stale = current.reading_guide_stale;
        ReaderViewMotion.finish();
        $("#guide-view").replaceChildren(...guideContents());
        updateNextArticle();
      }
    }
    if (!openId) {
      button.classList.add("refresh-complete");
      toast("文章库已更新", 1800);
      refreshFeedbackTimer = setTimeout(() => button.classList.remove("refresh-complete"), 1600);
    }
    return true;
  } catch (error) {
    toast(error.message, 5000);
    return false;
  } finally {
    button.classList.remove("is-refreshing");
    button.removeAttribute("aria-busy");
    button.setAttribute("aria-label", "刷新文章库");
    button.disabled = false;
  }
}

$("#article-search").addEventListener("input", (event) => {
  searchQuery = event.target.value.trim();
  renderNavigation();
  updateNextArticle();
});
$("#toggle-list-label").addEventListener("click", () => {
  preferences.list_label_mode = preferences.list_label_mode === "date" ? "title" : "date";
  writePreferences();
  renderNavigation();
});
$("#refresh-library").addEventListener("click", () => refreshLibrary());
$("#toggle-paragraph-numbers").addEventListener("click", () => {
  preferences.show_paragraph_numbers = !preferences.show_paragraph_numbers;
  syncParagraphNumbers();
  writePreferences();
});
$("#open-import").addEventListener("click", () => {
  if (!library) { toast("请先连接文章库，再录入文章。"); return; }
  window.ReaderDictionary?.close();
  saveReading();
  viewSwapVersion++;
  ReaderViewMotion.finish();
  viewSwitching = false;
  $("#main").classList.remove("reading-view-switching");
  closeDrawer(false);
  requestVersion++;
  restoring = false;
  reader.removeAttribute("aria-busy");
  reader.hidden = true;
  $("#reader-chrome").hidden = true;
  $(".reading-progress").hidden = true;
  $("#import-panel").hidden = false;
  $("#breadcrumb").textContent = "录入文章";
  $("#reading-meter").textContent = "草稿尚未保存";
  window.dispatchEvent(new CustomEvent("reader:import-open", {detail: {folders: library.folders, folderId: "all"}}));
  $("#import-title").focus({preventScroll: true});
});
$("#cancel-import").addEventListener("click", () => {
  hideImportPanel();
  if (!article && library?.articles.length) {
    loadArticle(library.articles.find(item => item.id === preferences.last_article)?.id || library.articles[0].id, {focus: true});
    return;
  }
  if (article) syncBreadcrumb();
  else $("#breadcrumb").textContent = "英语新闻阅读库";
  updateMeter();
  $("#open-import").focus();
});
window.addEventListener("reader:open-article", (event) => refreshLibrary({openId: event.detail.articleId}));
librarySidebar = new ReaderLibrary({
  root: $("#article-list"), newButton: $("#new-folder"), onNotice: toast,
  onOpen: id => { closeDrawer(false); loadArticle(id, {focus: true}); },
  onOrganization: (organization, {created = false} = {}) => {
    library.organization = organization;
    delete library.organization_error;
    if (created) { searchQuery = ""; $("#article-search").value = ""; }
    renderNavigation();
    syncBreadcrumb();
  },
  onReload: next => { library = next; renderNavigation(); syncBreadcrumb(); }
});
readPreferences();
syncParagraphNumbers();
applyFont();
loadLibrary();
