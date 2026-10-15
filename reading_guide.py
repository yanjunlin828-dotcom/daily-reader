"""Chinese reading guides: independent, create-only commits alongside unchanged articles."""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone

from importer import split_plain
from storage import BLOCK_PATTERN, MAX_FILE_BYTES, ConflictError, DataError, original_fingerprint


def validate_blocks(blocks):
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 200:
        raise DataError("中文导读需包含 1–200 个内容块")
    seen = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {"id", "type", "text"}:
            raise DataError("导读内容块只包含 id、type、text，不需要英文或逐段译文")
        if not isinstance(block["id"], str) or not BLOCK_PATTERN.fullmatch(block["id"]) or block["id"] in seen:
            raise DataError("导读内容块编号无效或重复")
        seen.add(block["id"])
        if block["type"] not in ("paragraph", "heading", "list"):
            raise DataError("导读类型需为 paragraph、heading 或 list")
        if (not isinstance(block["text"], str) or not block["text"].strip()
                or not re.search(r"[\u3400-\u9fff]", block["text"])):
            raise DataError("每个导读内容块需要中文文本，可包含必要的英文专名和数字")
    if len(json.dumps(blocks, ensure_ascii=False).encode("utf-8")) > MAX_FILE_BYTES - 4096:
        raise DataError("中文导读超过大小上限")
    return blocks


class ReadingGuideStore:
    def __init__(self, articles):
        self.articles = articles
        self.directory = articles.data_dir / "guides"

    def _inside(self, path):
        resolved = path.resolve(strict=True)
        if (not resolved.is_relative_to(self.articles.data_dir) or path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())):
            raise DataError("导读路径不安全")
        return resolved

    def get(self, article_id):
        article = self.articles.get(article_id)  # Validate the ID and owning article first.
        if not self.directory.exists():
            return None
        self._inside(self.directory)
        directory = self.directory / article_id
        if not directory.exists():
            return None
        self._inside(directory)
        commits = []
        for entry in directory.iterdir():
            if re.fullmatch(r"v00000001-[a-f0-9]{32}", entry.name):
                self._inside(entry)
                marker = entry / "COMMITTED"
                if marker.is_file():
                    self._inside(marker)
                    commits.append(entry)
        if not commits:
            return None
        if len(commits) != 1:
            raise DataError("同一文章存在多份已发布导读，停止读取以避免猜测")
        guide = self.articles._read(commits[0] / "guide.json")
        required = {"schema_version", "article_id", "guide_version", "source_content_version", "source_fingerprint", "created_at", "blocks"}
        if (not isinstance(guide, dict) or set(guide) != required or type(guide["schema_version"]) is not int
                or guide["schema_version"] != 1 or type(guide["guide_version"]) is not int or guide["guide_version"] != 1
                or guide["article_id"] != article_id or type(guide["source_content_version"]) is not int
                or not 1 <= guide["source_content_version"] <= article["content_version"]
                or guide["source_fingerprint"] != original_fingerprint(article)):
            raise DataError("导读身份、结构版本或对应原文校验失败")
        try:
            if not isinstance(guide["created_at"], str) or datetime.fromisoformat(guide["created_at"]).tzinfo is None:
                raise ValueError("invalid guide date")
        except ValueError as exc:
            raise DataError("导读保存时间格式不正确") from exc
        validate_blocks(guide["blocks"])
        return guide

    def prepare(self, article_id, raw_text, split_mode="blank_lines"):
        article = self.articles.get(article_id)
        if not isinstance(raw_text, str) or len(raw_text.encode("utf-8")) > MAX_FILE_BYTES:
            raise DataError("导读输入需要中文文本，且不超过 2 MiB")
        blocks = [{"id": f"g{index:03d}", "type": block["type"], "text": block["original"]}
                  for index, block in enumerate(split_plain(raw_text, split_mode), 1)]
        validate_blocks(blocks)
        return {"article_id": article_id, "expected_content_version": article["content_version"], "blocks": blocks,
                "saved": False, "guide_exists": self.get(article_id) is not None}

    def save(self, article_id, expected_content_version, blocks, *, confirmed=False):
        if confirmed is not True:
            raise DataError("保存中文导读需要当前用户授权及 confirmed=true")
        if type(expected_content_version) is not int or expected_content_version < 1:
            raise DataError("expected_content_version 必须是当前文章版本")
        blocks = copy.deepcopy(validate_blocks(blocks))
        with self.articles._write_lock():
            article = self.articles.get(article_id)
            existing = self.get(article_id)
            # An uncertain save may be retried exactly, even after another process adds translations.
            if existing:
                if existing["blocks"] == blocks:
                    return {"guide": existing, "saved": True, "unchanged": True}
                raise ConflictError("这篇文章已有不同的中文导读，不会覆盖。", "guide_exists", article_id)
            if article["content_version"] != expected_content_version:
                raise ConflictError("文章版本已经变化，请回读后再保存导读。", "version_conflict", article_id)
            guide = {"schema_version": 1, "article_id": article_id, "guide_version": 1,
                     "source_content_version": expected_content_version, "source_fingerprint": original_fingerprint(article),
                     "created_at": datetime.now(timezone.utc).isoformat(), "blocks": blocks}
            payload = json.dumps(guide, ensure_ascii=False, indent=2).encode("utf-8")
            if len(payload) > MAX_FILE_BYTES:
                raise DataError("导读超过 2 MiB 上限")
            self.directory.mkdir(exist_ok=True)
            self._inside(self.directory)
            directory = self.directory / article_id
            directory.mkdir(exist_ok=True)
            self._inside(directory)
            commit = directory / f"v00000001-{uuid.uuid4().hex}"
            commit.mkdir()
            with (commit / "guide.json").open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if self.articles._read(commit / "guide.json") != guide:
                raise DataError("导读保存校验失败，未发布")
            with (commit / "COMMITTED").open("xb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            return {"guide": guide, "saved": True, "unchanged": False}
