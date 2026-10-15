"""Deterministic preparation. Draft editing never writes an article."""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone

from storage import DataError, MAX_FILE_BYTES, translation_status, validate_article


def split_plain(text, mode="blank_lines"):
    if mode not in ("blank_lines", "every_line"):
        raise DataError("分段模式必须是 blank_lines 或 every_line")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = normalized.split("\n") if mode == "every_line" else re.split(r"\n[ \t]*\n+", normalized)
    blocks = []
    for chunk in chunks:
        original = chunk.strip()
        if not original:
            continue
        kind = "paragraph"
        if re.fullmatch(r"#{1,6} [^\n]+", original):
            kind, original = "heading", original.split(" ", 1)[1]
        elif all(re.match(r"^[-*] \S", line.strip()) for line in original.split("\n")):
            kind = "list"
            original = "\n".join(line.strip()[2:] for line in original.split("\n"))
        blocks.append({"id": f"p{len(blocks) + 1:03d}", "type": kind, "original": original, "translation": None})
    return blocks


def parse_marked(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, current, language = [], None, None
    buffers = {"en": [], "zh": []}

    def finish():
        if current is None:
            return
        original = "\n".join(buffers["en"]).strip()
        translated = "\n".join(buffers["zh"]).strip()
        if not original:
            raise DataError("标记格式的每个内容块都需要 :::en 和英文正文")
        blocks.append({**current, "original": original, "translation": translated or None})

    for line in lines:
        header = re.fullmatch(r":::(paragraph|heading|list) ([a-z][a-z0-9-]{0,79})", line.strip())
        if header:
            finish()
            current = {"type": header[1], "id": header[2]}
            buffers, language = {"en": [], "zh": []}, None
        elif line.strip() in (":::en", ":::zh"):
            next_language = line.strip()[3:]
            if current is None or (next_language == "zh" and language != "en") or (next_language == "en" and language is not None):
                raise DataError("标记顺序应为内容块、:::en、英文、可选 :::zh、中文")
            language = next_language
        elif line.strip().startswith(":::"):
            raise DataError("存在无法识别的标记；正文含标记字符时请用 JSON 导入")
        elif language:
            buffers[language].append(line)
        elif line.strip():
            raise DataError("正文需放在 :::en 或 :::zh 之后")
    finish()
    return blocks


def prepare_article(metadata, *, raw_text="", input_format="plain", split_mode="blank_lines", blocks=None, folder_ids):
    if not isinstance(metadata, dict):
        raise DataError("文章信息必须是对象")
    if input_format not in ("plain", "marked", "json", "blocks"):
        raise DataError("不支持的录入格式")
    if not isinstance(raw_text, str) or len(raw_text.encode("utf-8")) > MAX_FILE_BYTES:
        raise DataError("粘贴内容超过 2 MiB 上限或不是文本")
    imported = None
    if input_format == "json":
        try:
            imported = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise DataError("JSON 格式不正确，请检查引号、逗号和括号") from exc
        validate_article(imported, folder_ids)
        if imported["content_version"] != 1:
            raise DataError("导入新文章时版本需为 1；已有文章补译请使用补译工具")
        draft = copy.deepcopy(imported)
        # A complete original input record accompanies all newly imported articles.
        draft["schema_version"] = 2
        draft["import_source"] = {"format": "json", "text": raw_text}
    else:
        if input_format == "plain":
            blocks = split_plain(raw_text, split_mode)
        elif input_format == "marked":
            blocks = parse_marked(raw_text)
        else:
            if not isinstance(blocks, list):
                raise DataError("blocks 格式需要内容块数组")
            raw_text = json.dumps(blocks, ensure_ascii=False)
        if not isinstance(blocks, list) or any(not isinstance(block, dict) or "translation" not in block for block in blocks):
            raise DataError("每个内容块都需要 original、translation、type 和 id")
        draft = {"schema_version": 2, "id": metadata.get("id") or f"article-{uuid.uuid4().hex}",
                 "title": metadata.get("title", ""), "source": metadata.get("source") or "来源未填写",
                 "source_url": metadata.get("source_url") or None, "published_date": metadata.get("published_date") or None,
                 "created_at": datetime.now(timezone.utc).isoformat(), "folder_id": metadata.get("folder_id", ""),
                 "content_version": 1, "translation_status": translation_status(blocks) if blocks else "untranslated",
                 "is_demo": metadata.get("is_demo", False), "blocks": copy.deepcopy(blocks),
                 "import_source": {"format": input_format, "text": raw_text}}
    validate_article(draft, folder_ids)
    if len(json.dumps(draft, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_FILE_BYTES:
        raise DataError("文章和保留的粘贴内容合计超过 2 MiB，请缩小输入")
    warnings = []
    if any(len(block["original"]) > 2500 for block in draft["blocks"]):
        warnings.append("有较长的内容块，请检查是否需要在预览中拆分。程序没有按句号猜测分段。")
    if input_format == "plain" and len(draft["blocks"]) == 1 and len(raw_text) > 500:
        warnings.append("未发现段落空行。若复制内容丢失了分段，请切换每行分段，或在预览中按光标拆分。")
    return {"article": draft, "warnings": warnings}
