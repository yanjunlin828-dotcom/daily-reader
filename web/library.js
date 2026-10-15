"use strict";

// The sidebar owns directory interactions; it never replaces or scrolls the reader.
window.ReaderLibrary = class ReaderLibrary {
  constructor({root, newButton, onOpen, onOrganization, onReload, onNotice}) {
    Object.assign(this, {root, newButton, onOpen, onOrganization, onReload, onNotice});
    this.openFolders = new Set();
    try {
      const saved = JSON.parse(localStorage.getItem("daily-reader.open-folders.v1") || "[]");
      if (Array.isArray(saved)) this.openFolders = new Set(saved.filter(id => typeof id === "string"));
    } catch { /* Folder expansion is optional; article data is unaffected. */ }
    this.busy = false;
    this.suppressClickUntil = 0;
    this.newButton.addEventListener("click", () => this.editFolder(null));
    this.root.addEventListener("dragover", event => this.dragOver(event));
    this.root.addEventListener("drop", event => this.drop(event));
    this.root.addEventListener("dragleave", event => {
      if (!this.root.contains(event.relatedTarget)) this.clearDropTarget();
    });
    this.root.addEventListener("scroll", () => this.closeMenu(false), {passive: true});
    document.addEventListener("dragend", () => this.finishDrag());
    document.addEventListener("pointerdown", event => {
      if (this.menu && !this.menu.contains(event.target) && !this.menuAnchor?.contains(event.target)) this.closeMenu(false);
    });
    document.addEventListener("keydown", event => this.menuKey(event), true);
    window.addEventListener("resize", () => this.closeMenu(false));
  }

  node(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(svg.namespaceURI, "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  remember() {
    try { localStorage.setItem("daily-reader.open-folders.v1", JSON.stringify([...this.openFolders])); } catch { /* Optional preference. */ }
  }

  render(data) {
    if (this.draggedId) this.finishDrag();
    this.closeMenu(false);
    const previousActive = this.data?.activeId;
    this.data = data;
    const org = data.organization;
    this.newButton.disabled = this.busy || !org;
    this.newButton.title = org ? "新建文件夹" : "目录暂不可用，请刷新文章库";
    if (org && data.activeId !== previousActive && org.article_folders[data.activeId]) this.openFolders.add(org.article_folders[data.activeId]);
    const scroll = this.root.scrollTop;
    this.root.replaceChildren();
    this.root.setAttribute("aria-busy", String(this.busy));
    if (data.error) this.root.append(this.node("p", "library-directory-error", data.error));
    if (this.edit?.id === null) this.root.append(this.editor());
    const placements = org?.article_folders || {};
    for (const folder of org?.folders || []) {
      const articles = data.items.filter(item => placements[item.id] === folder.id);
      if (data.searching && !articles.length && this.edit?.id !== folder.id) continue;
      const group = this.node("div", "library-folder");
      group.dataset.folderId = folder.id;
      if (this.edit?.id === folder.id) group.append(this.editor());
      else group.append(this.folderRow(folder, data.allItems.filter(item => placements[item.id] === folder.id).length));
      const children = this.node("div", "folder-children");
      children.id = `children-${folder.id}`;
      children.setAttribute("role", "group");
      children.setAttribute("aria-label", folder.name);
      const inner = this.node("div", "folder-children-inner");
      if (!articles.length) inner.append(this.node("p", "folder-empty", "暂无文章"));
      for (const item of articles) inner.append(this.articleRow(item));
      children.append(inner);
      group.append(children);
      this.root.append(group);
      this.setFolderOpen(folder.id, this.openFolders.has(folder.id) || (data.searching && articles.length > 0), false);
    }
    const root = this.node("div", "library-root");
    root.dataset.folderId = "";
    for (const item of data.items.filter(item => !placements[item.id])) root.append(this.articleRow(item));
    const drop = this.node("div", "library-root-drop", "移回文章库");
    drop.setAttribute("aria-hidden", "true");
    root.append(drop);
    this.root.append(root);
    if (!data.items.length && !this.edit && (data.searching || !(org?.folders.length))) {
      this.root.prepend(this.node("p", "sidebar-empty", data.searching ? "没有匹配的文章，试试其他关键词。" : "还没有文章，点击右上角录入。"));
    }
    this.root.scrollTop = scroll;
  }

  folderRow(folder, count) {
    const row = this.node("div", "folder-row");
    const toggle = this.node("button", "folder-toggle");
    toggle.type = "button";
    toggle.title = folder.name;
    toggle.setAttribute("aria-controls", `children-${folder.id}`);
    toggle.append(this.icon("chevron"), this.icon("folder"), this.node("span", "folder-name", folder.name), this.node("span", "folder-count", String(count)));
    toggle.addEventListener("click", () => {
      const group = row.closest(".library-folder");
      this.setFolderOpen(folder.id, !group.classList.contains("is-open"));
    });
    toggle.addEventListener("dblclick", () => this.editFolder(folder));
    toggle.addEventListener("keydown", event => {
      if (event.key === "F2") { event.preventDefault(); this.editFolder(folder); }
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault(); event.stopPropagation();
        this.setFolderOpen(folder.id, event.key === "ArrowRight");
      }
    });
    const menu = this.moreButton(`${folder.name}的操作`, () => this.folderMenu(folder, menu));
    row.addEventListener("contextmenu", event => { event.preventDefault(); this.folderMenu(folder, menu, event); });
    row.append(toggle, menu);
    return row;
  }

  articleRow(item) {
    const row = this.node("div", "library-article-row");
    row.dataset.articleId = item.id;
    const button = this.node("button", `article-button${this.data.activeId === item.id ? " active" : ""}`);
    button.type = "button";
    button.dataset.articleId = item.id;
    button.title = item.hint;
    button.setAttribute("aria-label", `${item.title}，载入于 ${item.date}`);
    if (this.data.activeId === item.id) button.setAttribute("aria-current", "true");
    button.append(this.icon("document"), this.node("span", `article-name${this.data.dateMode ? " date-label" : ""}`, this.data.dateMode ? item.date : item.title));
    button.addEventListener("click", () => {
      if (performance.now() >= this.suppressClickUntil) this.onOpen(item.id);
    });
    button.draggable = Boolean(this.data.organization);
    button.addEventListener("dragstart", event => this.startDrag(event, item, row));
    const menu = this.moreButton(`移动文章：${item.title}`, () => this.moveMenu(item, menu));
    row.addEventListener("contextmenu", event => { event.preventDefault(); this.moveMenu(item, menu, event); });
    row.append(button, menu);
    return row;
  }

  moreButton(label, action) {
    const button = this.node("button", "library-more");
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-haspopup", "menu");
    button.disabled = this.busy || !this.data.organization;
    button.append(this.icon("more"));
    button.addEventListener("click", action);
    return button;
  }

  setFolderOpen(id, open, persist = true) {
    const group = this.root.querySelector(`[data-folder-id="${id}"]`);
    if (!group) return;
    group.classList.toggle("is-open", open);
    group.querySelector(".folder-toggle")?.setAttribute("aria-expanded", String(open));
    const children = group.querySelector(".folder-children");
    children.inert = !open;
    children.setAttribute("aria-hidden", String(!open));
    if (persist) {
      open ? this.openFolders.add(id) : this.openFolders.delete(id);
      this.remember();
    }
  }

  editFolder(folder) {
    if (this.busy || !this.data?.organization) return;
    this.edit = {id: folder?.id ?? null, value: folder?.name || "", focus: true, error: ""};
    this.render(this.data);
  }

  editor() {
    const edit = this.edit;
    const wrapper = this.node("div", "folder-editor");
    const line = this.node("div", "folder-editor-line");
    line.append(this.icon("folder"));
    const input = this.node("input", "folder-name-input");
    input.type = "text"; input.maxLength = 60; input.value = edit.value;
    input.placeholder = "文件夹名称";
    input.setAttribute("aria-label", edit.id ? "修改文件夹名称" : "新文件夹名称");
    input.setAttribute("aria-describedby", "folder-edit-message");
    input.setAttribute("aria-invalid", String(Boolean(edit.error)));
    input.disabled = this.busy;
    input.addEventListener("input", () => { edit.value = input.value; });
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation();
        if (this.busy) return;
        this.edit = null; this.render(this.data); this.focusFolder(edit.id);
      } else if (event.key === "Enter") {
        event.preventDefault(); event.stopPropagation(); this.saveEditor();
      }
    });
    input.addEventListener("blur", () => {
      if (this.edit === edit && !this.busy) {
        if (!edit.value.trim() && edit.id === null) { this.edit = null; this.render(this.data); }
        else this.saveEditor();
      }
    });
    line.append(input);
    const error = this.node("p", "folder-edit-message", edit.error || "Enter 保存 · Esc 取消");
    error.id = "folder-edit-message";
    if (edit.error) error.setAttribute("role", "alert");
    wrapper.append(line, error);
    if (edit.focus) {
      edit.focus = false;
      requestAnimationFrame(() => { if (input.isConnected && this.edit === edit) { input.focus({preventScroll: true}); input.select(); } });
    }
    return wrapper;
  }

  async saveEditor() {
    const edit = this.edit;
    if (!edit || this.busy) return;
    const name = edit.value.trim();
    if (!name || name.length > 60 || /[\u0000-\u001f\u007f/\\]/.test(name) || name === "." || name === "..") {
      edit.error = "请输入1–60个字符的有效名称";
      edit.focus = true; this.render(this.data); return;
    }
    await this.perform(edit.id ? `/api/library/folders/${edit.id}/rename` : "/api/library/folders", {name}, result => {
      this.edit = null;
      const id = edit.id || result.folder_id;
      this.openFolders.add(id); this.remember();
      this.onOrganization(result.organization, {created: !edit.id});
      this.focusFolder(id);
    });
  }

  focusFolder(id) {
    (id ? this.root.querySelector(`[data-folder-id="${id}"] .folder-toggle`) : null)?.focus({preventScroll: true});
    if (!id || !this.root.querySelector(`[data-folder-id="${id}"]`)) this.newButton.focus({preventScroll: true});
  }

  showMenu(anchor, entries, point) {
    if (this.busy || !this.data.organization) return;
    this.closeMenu(false);
    this.menuAnchor = anchor;
    this.menu = this.node("div", "library-menu");
    this.menu.setAttribute("role", "menu");
    this.menu.setAttribute("aria-label", "文章库操作");
    for (const entry of entries) {
      const button = this.node("button", entry.danger ? "menu-danger" : "", entry.label);
      button.type = "button";
      button.setAttribute("role", entry.current !== undefined ? "menuitemradio" : "menuitem");
      if (entry.current !== undefined) button.setAttribute("aria-checked", String(entry.current));
      button.disabled = Boolean(entry.current);
      button.tabIndex = -1;
      button.title = entry.label;
      button.addEventListener("click", () => { this.closeMenu(false); entry.action(); });
      this.menu.append(button);
    }
    // Keep mobile menu focus inside the article-library dialog.
    document.querySelector("#sidebar").append(this.menu);
    anchor.setAttribute("aria-expanded", "true");
    const rect = anchor.getBoundingClientRect();
    const size = this.menu.getBoundingClientRect();
    const x = point?.clientX || rect.right - size.width;
    const y = point?.clientY || rect.bottom + 4;
    this.menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - size.width - 8))}px`;
    this.menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - size.height - 8))}px`;
    this.menu.querySelector("button:not(:disabled)")?.focus({preventScroll: true});
  }

  folderMenu(folder, anchor, point) {
    this.showMenu(anchor, [
      {label: "重命名", action: () => this.editFolder(folder)},
      {label: "删除文件夹", danger: true, action: () => this.removeFolder(folder)}
    ], point);
  }

  moveMenu(item, anchor, point) {
    const current = this.data.organization?.article_folders[item.id] || null;
    const entries = [{label: "移回文章库", current: current === null, action: () => this.moveArticle(item.id, null)}];
    for (const folder of this.data.organization?.folders || []) entries.push({label: `移入 ${folder.name}`, current: current === folder.id, action: () => this.moveArticle(item.id, folder.id)});
    this.showMenu(anchor, entries, point);
  }

  closeMenu(returnFocus = true) {
    if (!this.menu) return;
    this.menu.remove(); this.menu = null;
    this.menuAnchor?.setAttribute("aria-expanded", "false");
    if (returnFocus && this.menuAnchor?.isConnected) this.menuAnchor.focus({preventScroll: true});
    this.menuAnchor = null;
  }

  menuKey(event) {
    if (!this.menu || !this.menu.contains(event.target)) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.closeMenu(); }
    else if (event.key === "Tab") this.closeMenu();
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      const buttons = [...this.menu.querySelectorAll("button:not(:disabled)")];
      if (!buttons.length) return;
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
      buttons[next].focus();
    }
  }

  async perform(path, payload, complete) {
    if (this.busy || !this.data.organization) return false;
    this.busy = true;
    this.newButton.disabled = true;
    this.root.setAttribute("aria-busy", "true");
    const editor = this.root.querySelector(".folder-name-input");
    if (editor) editor.disabled = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const session = await fetch("/api/session", {cache: "no-store", signal: controller.signal});
      if (!session.ok) throw new Error("无法连接本地服务，请稍后重试");
      const token = (await session.json()).csrf_token;
      const response = await fetch(path, {method: "POST", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-Reader-CSRF": token},
        body: JSON.stringify({...payload, expected_revision: this.data.organization.revision, confirmed: true})});
      const result = await response.json();
      if (!response.ok) { const error = new Error(result.error || "文件夹操作失败"); error.status = response.status; throw error; }
      this.busy = false;
      complete(result);
      return true;
    } catch (error) {
      this.busy = false;
      const uncertain = error.name === "AbortError" || error instanceof TypeError;
      const message = uncertain ? "未能确认保存结果，已尝试重新读取目录；请核对后再操作" : error.message;
      if (this.edit) { this.edit.error = message; this.edit.focus = true; }
      // Never retry a write whose response may have been lost.
      if (uncertain || error.status === 409) {
        try {
          const response = await fetch("/api/library", {cache: "no-store", signal: AbortSignal.timeout(10000)});
          if (response.ok) this.onReload(await response.json());
        } catch { /* Preserve the last confirmed state and the editor's unsaved text. */ }
      }
      this.render(this.data);
      if (this.deleteDialog?.open) this.deleteError.textContent = message;
      this.onNotice(message, 5000);
      return false;
    } finally {
      clearTimeout(timeout);
      this.busy = false;
      this.newButton.disabled = !this.data.organization;
      this.root.setAttribute("aria-busy", "false");
    }
  }

  moveArticle(id, folderId) {
    if ((this.data.organization?.article_folders[id] || null) === folderId) return;
    return this.perform(`/api/library/articles/${id}/move`, {folder_id: folderId}, result => {
      if (folderId) this.openFolders.add(folderId);
      this.remember();
      this.onOrganization(result.organization);
      this.onNotice(folderId ? "文章已移入文件夹" : "文章已移回文章库", 1800);
    });
  }

  removeFolder(folder) {
    const count = this.data.allItems.filter(item => this.data.organization.article_folders[item.id] === folder.id).length;
    if (!count) { this.commitRemoval(folder); return; }
    if (!this.deleteDialog) {
      this.deleteDialog = this.node("dialog", "library-delete-dialog");
      this.deleteDialog.setAttribute("aria-labelledby", "folder-delete-title");
      this.deleteDialog.setAttribute("aria-describedby", "folder-delete-description");
      const heading = this.node("h2", "", "删除文件夹？"); heading.id = "folder-delete-title";
      this.deleteDescription = this.node("p"); this.deleteDescription.id = "folder-delete-description";
      this.deleteError = this.node("p", "folder-delete-error"); this.deleteError.setAttribute("role", "alert");
      const actions = this.node("div", "library-dialog-actions");
      const cancel = this.node("button", "text-button", "取消"); cancel.type = "button"; cancel.autofocus = true;
      cancel.addEventListener("click", () => { if (!this.busy) this.deleteDialog.close(); });
      this.deleteConfirm = this.node("button", "folder-delete-confirm", "删除文件夹"); this.deleteConfirm.type = "button";
      this.deleteConfirm.addEventListener("click", () => this.commitRemoval(this.deleting));
      actions.append(cancel, this.deleteConfirm);
      this.deleteDialog.append(heading, this.deleteDescription, this.deleteError, actions);
      this.deleteDialog.addEventListener("cancel", event => { if (this.busy) event.preventDefault(); });
      this.deleteDialog.addEventListener("close", () => this.focusFolder(this.deleting?.id));
      document.body.append(this.deleteDialog);
    }
    this.deleting = folder;
    this.deleteError.textContent = "";
    this.deleteDescription.textContent = `“${folder.name}”中的 ${count} 篇文章会保留并移回文章库，原文、译文和导读都不会删除。`;
    this.deleteDialog.showModal();
  }

  async commitRemoval(folder) {
    if (this.busy || !folder) return;
    if (this.deleteConfirm) this.deleteConfirm.disabled = true;
    await this.perform(`/api/library/folders/${folder.id}/remove`, {}, result => {
      this.openFolders.delete(folder.id); this.remember();
      this.onOrganization(result.organization);
      if (this.deleteDialog?.open) this.deleteDialog.close();
      this.focusFolder(null);
      this.onNotice(result.moved_to_root ? "文件夹已删除，文章已移回文章库" : "文件夹已删除", 2000);
    });
    if (this.deleteConfirm) this.deleteConfirm.disabled = false;
  }

  startDrag(event, item, row) {
    if (this.busy || this.edit || !this.data.organization || !event.dataTransfer) { event.preventDefault(); return; }
    this.closeMenu(false);
    this.draggedId = item.id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-daily-reader-article", item.id);
    this.ghost = this.node("div", "library-drag-preview");
    this.ghost.append(this.icon("document"), this.node("span", "", item.title));
    document.body.append(this.ghost);
    event.dataTransfer.setDragImage(this.ghost, 16, 16);
    // Preserve the source row and its height throughout the drag.
    requestAnimationFrame(() => { if (this.draggedId) { row.classList.add("is-drag-source"); this.root.classList.add("is-dragging"); } });
  }

  dragOver(event) {
    if (!this.draggedId || this.busy || !event.dataTransfer?.types.includes("application/x-daily-reader-article")) return;
    event.preventDefault();
    const group = event.target.closest(".library-folder");
    const id = group?.dataset.folderId || null;
    const current = this.data.organization.article_folders[this.draggedId] || null;
    event.dataTransfer.dropEffect = id === current ? "none" : "move";
    const target = group || this.root.querySelector(".library-root");
    if (this.dropTarget !== target) {
      this.clearDropTarget();
      this.dropTarget = target;
      if (id !== current) target.classList.add("is-drop-target");
      if (id && !group.classList.contains("is-open")) this.expandTimer = setTimeout(() => {
        if (this.draggedId && this.dropTarget === target) this.setFolderOpen(id, true);
      }, 550);
    }
    const bounds = this.root.getBoundingClientRect();
    const edge = 38;
    this.scrollSpeed = event.clientY < bounds.top + edge ? -Math.min(9, (bounds.top + edge - event.clientY) / 4)
      : event.clientY > bounds.bottom - edge ? Math.min(9, (event.clientY - bounds.bottom + edge) / 4) : 0;
    if (this.scrollSpeed && !this.scrollFrame) {
      const tick = () => {
        this.scrollFrame = null;
        if (this.draggedId && this.scrollSpeed) { this.root.scrollTop += this.scrollSpeed; this.scrollFrame = requestAnimationFrame(tick); }
      };
      this.scrollFrame = requestAnimationFrame(tick);
    }
  }

  drop(event) {
    if (!this.draggedId || !event.dataTransfer?.types.includes("application/x-daily-reader-article")) return;
    event.preventDefault();
    const id = this.draggedId;
    const folderId = event.target.closest(".library-folder")?.dataset.folderId || null;
    this.finishDrag();
    this.moveArticle(id, folderId);
  }

  clearDropTarget() {
    clearTimeout(this.expandTimer);
    this.dropTarget?.classList.remove("is-drop-target");
    this.dropTarget = null;
    this.scrollSpeed = 0;
  }

  finishDrag() {
    if (this.draggedId) this.suppressClickUntil = performance.now() + 250;
    this.draggedId = null;
    this.clearDropTarget();
    cancelAnimationFrame(this.scrollFrame); this.scrollFrame = null;
    this.ghost?.remove(); this.ghost = null;
    this.root.classList.remove("is-dragging");
    this.root.querySelectorAll(".is-drag-source").forEach(node => node.classList.remove("is-drag-source"));
  }
};
