"use strict";

(() => {
  const find = (selector) => document.querySelector(selector);
  const key = "english-news-import.v1";
  let draft = null;
  let dirty = false;
  let busy = false;
  let session = null;
  let existingId = null;
  let persistTimer;
  let restoringForm = false;
  let savedState = null;
  try { savedState = JSON.parse(sessionStorage.getItem(key) || "null"); } catch { /* Reading remains usable. */ }

  function message(text, error = false) {
    const node = find(error ? "#import-error" : "#import-warning");
    node.textContent = text;
    node.hidden = !text;
  }

  function metadata() {
    return {title: find("#import-article-title").value.trim(), folder_id: find("#import-folder").value,
      source: find("#import-source").value.trim(), source_url: find("#import-url").value.trim() || null,
      published_date: find("#import-date").value || null};
  }

  function persist() {
    clearTimeout(persistTimer);
    const state = {metadata: metadata(), raw_text: find("#import-raw").value,
      input_format: find("#import-format").value, every_line: find("#split-every-line").checked, draft, dirty, last_saved_id: existingId};
    try { sessionStorage.setItem(key, JSON.stringify(state)); }
    catch { message("浏览器无法保留录入草稿。请保持此页面打开，刷新前复制重要内容。", true); }
  }

  function setBusy(value, action = "prepare") {
    busy = value;
    find("#prepare-import").setAttribute("aria-busy", String(value && action === "prepare"));
    find("#save-import").setAttribute("aria-busy", String(value && action === "save"));
    find("#prepare-import .button-label").textContent = value && action === "prepare" ? "正在生成预览" : "生成分段预览";
    find("#save-import .button-label").textContent = value && action === "save" ? "正在保存" : "保存并阅读";
    find("#prepare-import").disabled = value;
    find("#save-import").disabled = value || !draft || dirty;
    // Prevent edits after the snapshot is submitted, while allowing return to reading.
    for (const field of document.querySelectorAll("#import-form input,#import-form textarea,#import-form select,#preview-blocks input,#preview-blocks textarea,#preview-blocks select,#preview-blocks button")) field.disabled = value;
    if (!value) updateCounts();
  }

  async function post(path, payload) {
    // A server restart invalidates the previous CSRF token; refresh without losing input.
    if (!session) {
      const response = await fetch("/api/session", {cache: "no-store"});
      if (!response.ok) throw new Error("录入会话无法连接，请确认本地服务正在运行。");
      session = await response.json();
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Reader-CSRF": session.csrf_token},
        body: JSON.stringify(payload), signal: controller.signal});
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 403) session = null;
        const error = new Error(data.error || "录入失败，输入仍然保留。");
        error.existingId = data.code === "duplicate_article" ? data.article_id : null;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) throw new Error("无法连接本地服务，或保存结果尚未确认。输入仍在；重试时会检查重复文章。");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function applyMetadata() {
    if (!draft) return;
    Object.assign(draft, metadata());
    if (!draft.source) draft.source = "来源未填写";
  }

  function updateCounts() {
    if (!draft) return;
    const translated = draft.blocks.filter((block) => block.translation !== null && block.translation.trim()).length;
    find("#preview-count").textContent = `${draft.blocks.length} 个内容块`;
    find("#save-summary").textContent = `已填写 ${translated}/${draft.blocks.length} 个内容块的译文`;
    find("#save-import").disabled = busy || dirty;
    draft.translation_status = translated === draft.blocks.length ? "complete" : translated === 0 ? "untranslated" : "partial";
    document.querySelectorAll(".block-actions button").forEach((button) => {
      const index = Number(button.dataset.index);
      const block = draft.blocks[index];
      button.disabled = busy || block.translation !== null || (button.dataset.action === "merge" && (!draft.blocks[index + 1] || draft.blocks[index + 1].translation !== null));
    });
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function renderPreview() {
    const container = find("#preview-blocks");
    container.replaceChildren();
    draft.blocks.forEach((block, index) => {
      const section = node("section", "preview-block");
      const heading = node("div", "block-editor-heading");
      heading.append(node("strong", "", `内容块 ${index + 1}`));
      const select = node("select");
      select.setAttribute("aria-label", `第 ${index + 1} 个内容块的类型`);
      for (const [value, name] of [["paragraph", "正文段落"], ["heading", "小标题"], ["list", "列表（每行一项）"]]) {
        const option = node("option", "", name); option.value = value; select.append(option);
      }
      select.value = block.type;
      select.addEventListener("change", () => { block.type = select.value; persist(); });
      heading.append(select);
      const editors = node("div", "block-editors");
      const originalLabel = node("label", "field", "英文原文");
      const original = node("textarea");
      original.rows = Math.max(3, Math.min(8, Math.ceil(block.original.length / 70)));
      original.value = block.original;
      original.lang = "en";
      original.spellcheck = false;
      original.setAttribute("aria-label", `第 ${index + 1} 个内容块的英文原文`);
      original.addEventListener("input", () => { block.original = original.value; persist(); });
      originalLabel.append(original);
      const translationLabel = node("label", "field", "中文译文（可留空）");
      const translation = node("textarea", "translation-editor");
      translation.rows = 3;
      translation.value = block.translation || "";
      translation.placeholder = "留空保存为待翻译";
      translation.setAttribute("aria-label", `第 ${index + 1} 个内容块的中文译文`);
      translation.addEventListener("input", () => { block.translation = translation.value.trim() ? translation.value : null; updateCounts(); persist(); });
      translationLabel.append(translation);
      editors.append(originalLabel, translationLabel);
      const actions = node("div", "block-actions");
      const split = node("button", "text-button", "按光标拆分");
      split.dataset.index = index; split.dataset.action = "split";
      split.setAttribute("aria-label", `拆分第 ${index + 1} 个内容块`);
      split.addEventListener("click", () => {
        const position = original.selectionStart;
        if (!block.original.slice(0, position).trim() || !block.original.slice(position).trim()) {
          message("先将光标放在英文原文中需要分段的位置，再点击按光标拆分。", true); return;
        }
        if (draft.blocks.length >= 1000) { message("最多支持 1000 个内容块。", true); return; }
        const right = {...block, original: block.original.slice(position).trim(), translation: null};
        block.original = block.original.slice(0, position).trim();
        draft.blocks.splice(index + 1, 0, right);
        reindex(); renderPreview(); persist();
      });
      const merge = node("button", "text-button", "与下一段合并");
      merge.dataset.index = index; merge.dataset.action = "merge";
      merge.setAttribute("aria-label", `将第 ${index + 1} 个内容块与下一段合并`);
      merge.addEventListener("click", () => {
        block.original += "\n\n" + draft.blocks[index + 1].original;
        draft.blocks.splice(index + 1, 1);
        reindex(); renderPreview(); persist();
      });
      actions.append(split, merge);
      section.append(heading, editors, actions);
      container.append(section);
    });
    find("#preview-panel").hidden = false;
    updateCounts();
  }

  function reindex() {
    draft.blocks.forEach((block, index) => { block.id = `p${String(index + 1).padStart(3, "0")}`; });
  }

  function formatHelp() {
    const format = find("#import-format").value;
    find("#split-option").hidden = format !== "plain";
    find("#marked-example").hidden = format !== "marked";
    find("#format-help").textContent = format === "plain" ? "默认按空行分段；普通换行保留。只识别明确的 Markdown 小标题和列表，不按句号拆分。"
      : format === "marked" ? "每块按 :::paragraph p001、:::en、英文、可选 :::zh、中文排列。保留小标题和列表的类型。"
      : "粘贴完整文章 JSON（结构版本 1 或 2）。先检查原文、译文、编号及翻译状态；标题等信息以 JSON 为准。";
    // The JSON metadata comes from the pasted document, so blank form metadata is allowed before preview.
    find("#import-article-title").required = format !== "json";
  }

  find("#import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    message("", true); message(""); find("#open-existing").hidden = true;
    setBusy(true);
    find("#prepare-status").textContent = "正在检查原文与分段…";
    try {
      const result = await post("/api/prepare", {metadata: metadata(), raw_text: find("#import-raw").value,
        input_format: find("#import-format").value, split_mode: find("#split-every-line").checked ? "every_line" : "blank_lines"});
      draft = result.article; dirty = false;
      fillMetadata(draft);
      renderPreview();
      message(result.warnings.join("\n"));
      find("#input-details").open = false;
      find("#prepare-status").textContent = "预览尚未保存，可继续调整";
      persist();
    } catch (error) { message(error.message, true); find("#prepare-status").textContent = "输入已保留，请检查后重试"; }
    finally { setBusy(false); }
  });

  find("#save-import").addEventListener("click", async () => {
    if (busy || !draft || dirty || !find("#import-form").reportValidity()) return;
    applyMetadata();
    updateCounts();
    message("", true); find("#open-existing").hidden = true;
    setBusy(true, "save");
    find("#prepare-status").textContent = "正在保存并检查…";
    try {
      const result = await post("/api/articles", {article: draft, confirmed: true});
      const articleId = result.article.id;
      // Keep the saved ID available if opening the reading view fails.
      find("#prepare-status").textContent = "已保存，正在打开文章";
      existingId = articleId;
      find("#open-existing").textContent = "打开刚保存的文章";
      find("#open-existing").hidden = false;
      draft = null; dirty = false;
      find("#preview-panel").hidden = true;
      find("#import-raw").value = "";
      find("#import-article-title").value = "";
      find("#import-source").value = "";
      find("#import-url").value = "";
      find("#import-date").value = "";
      find("#input-details").open = true;
      persist();
      window.dispatchEvent(new CustomEvent("reader:open-article", {detail: {articleId}}));
    } catch (error) {
      message(error.message, true);
      existingId = error.existingId;
      find("#open-existing").textContent = "打开已存在的文章";
      find("#open-existing").hidden = !existingId;
      find("#prepare-status").textContent = "输入已保留";
      persist();
    } finally { setBusy(false); }
  });

  find("#open-existing").addEventListener("click", () => {
    if (existingId) window.dispatchEvent(new CustomEvent("reader:open-article", {detail: {articleId: existingId}}));
  });
  function fillMetadata(value) {
    find("#import-article-title").value = value.title || "";
    find("#import-folder").value = value.folder_id || "";
    find("#import-source").value = value.source || "";
    find("#import-url").value = value.source_url || "";
    find("#import-date").value = value.published_date || "";
  }
  find("#import-form").addEventListener("input", (event) => {
    if (restoringForm) return;
    if (["import-raw", "import-format", "split-every-line"].includes(event.target.id) && draft) {
      dirty = true;
      find("#prepare-status").textContent = "粘贴内容已变化，请重新生成预览";
      updateCounts();
    } else applyMetadata();
    clearTimeout(persistTimer); persistTimer = setTimeout(persist, 300);
  });
  find("#import-format").addEventListener("change", () => { formatHelp(); persist(); });
  window.addEventListener("reader:import-open", (event) => {
    const folder = find("#import-folder");
    const selected = folder.value;
    folder.replaceChildren(...event.detail.folders.map((value) => { const option = node("option", "", value.name); option.value = value.id; return option; }));
    folder.value = selected || (event.detail.folderId !== "all" ? event.detail.folderId : event.detail.folders[0]?.id);
    if (savedState && typeof savedState === "object") {
      restoringForm = true;
      try {
        fillMetadata(savedState.metadata || {});
        find("#import-raw").value = savedState.raw_text || "";
        find("#import-format").value = savedState.input_format || "plain";
        find("#split-every-line").checked = Boolean(savedState.every_line);
        if (savedState.draft && Array.isArray(savedState.draft.blocks)) {
          draft = savedState.draft; dirty = Boolean(savedState.dirty); renderPreview();
        }
        if (typeof savedState.last_saved_id === "string") {
          existingId = savedState.last_saved_id;
          find("#open-existing").hidden = false;
        }
      } catch { message("录入草稿无法恢复，请重新粘贴内容。", true); }
      restoringForm = false; savedState = null;
    }
    if (!folder.value) folder.value = event.detail.folders[0]?.id || "";
    formatHelp();
  });
  window.addEventListener("pagehide", () => { if (!find("#import-panel").hidden) persist(); });
  formatHelp();
})();
