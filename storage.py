"""Article repository: immutable snapshots, append-only commits and shared validation."""
from __future__ import annotations

import json
import re
import copy
import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
BLOCK_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,79}$")


class DataError(ValueError):
    pass


class ConflictError(DataError):
    def __init__(self, message, code="conflict", article_id=None):
        super().__init__(message)
        self.code = code
        self.article_id = article_id


def original_fingerprint(article):
    text = " ".join(block["original"] for block in article["blocks"])
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _text(value, name, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{name} 必须是非空文本")


def _identifier(value, name, pattern=ID_PATTERN):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DataError(f"{name} 格式不正确")


def translation_status(blocks):
    translated = sum(block["translation"] is not None for block in blocks)
    return "complete" if translated == len(blocks) else "untranslated" if translated == 0 else "partial"


def validate_article(article, folder_ids):
    if not isinstance(article, dict):
        raise DataError("文章必须是对象")
    required = {"schema_version", "id", "title", "source", "source_url", "published_date",
                "created_at", "folder_id", "content_version", "translation_status", "is_demo", "blocks"}
    if article.get("schema_version") == 2:
        required.add("import_source")
    if set(article) != required:
        raise DataError("文章字段缺失或含未支持字段")
    if type(article["schema_version"]) is not int or article["schema_version"] not in (1, 2):
        raise DataError("不支持的文章结构版本")
    if article["schema_version"] == 2:
        source = article["import_source"]
        if not isinstance(source, dict) or set(source) != {"format", "text"}:
            raise DataError("粘贴原文记录格式不正确")
        if source["format"] not in ("plain", "marked", "json", "blocks"):
            raise DataError("不支持的录入格式")
        _text(source["text"], "最初粘贴内容")
    _identifier(article["id"], "文章编号")
    for field in ("title", "source", "folder_id"):
        _text(article[field], field)
    if article["folder_id"] not in folder_ids:
        raise DataError("文章所属文件夹不存在")
    if article["source_url"] is not None:
        from urllib.parse import urlsplit
        _text(article["source_url"], "原文链接")
        url = urlsplit(article["source_url"])
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise DataError("原文链接必须是无凭据的 HTTP(S) 地址")
    try:
        _text(article["published_date"], "发布日期", nullable=True)
        if article["published_date"] is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", article["published_date"]):
                raise ValueError("invalid date format")
            date.fromisoformat(article["published_date"])
        _text(article["created_at"], "录入时间")
        if datetime.fromisoformat(article["created_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise DataError("日期或录入时间格式不正确") from exc
    if type(article["content_version"]) is not int or article["content_version"] < 1:
        raise DataError("内容版本必须是正整数")
    if type(article["is_demo"]) is not bool:
        raise DataError("演示标记必须是布尔值")
    blocks = article["blocks"]
    if not isinstance(blocks, list) or not blocks or len(blocks) > 1000:
        raise DataError("文章需包含 1–1000 个内容块")
    seen = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != {"id", "type", "original", "translation"}:
            raise DataError("内容块字段不正确")
        _identifier(block["id"], "内容块编号", BLOCK_PATTERN)
        if block["id"] in seen:
            raise DataError("内容块编号重复")
        seen.add(block["id"])
        if block["type"] not in ("paragraph", "heading", "list"):
            raise DataError("不支持的内容块类型")
        _text(block["original"], "英文原文")
        _text(block["translation"], "中文译文", nullable=True)
    if article["translation_status"] != translation_status(blocks):
        raise DataError("翻译状态与实际内容不一致")
    return article


class ArticleStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve(strict=True)
        self.articles_dir = self.data_dir / "articles"
        self.records_dir = self.data_dir / "records"

    def _read(self, path):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.data_dir) or path.is_symlink():
            raise DataError("数据路径越出文章目录")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise DataError("数据文件超过 2 MiB 上限")
        try:
            return json.loads(resolved.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DataError("数据文件不是有效的 UTF-8 JSON") from exc

    def folders(self):
        data = self._read(self.data_dir / "folders.json")
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
                or data["schema_version"] != 1 or not isinstance(data.get("folders"), list)):
            raise DataError("文件夹配置格式不正确")
        seen = set()
        for folder in data["folders"]:
            if not isinstance(folder, dict) or set(folder) != {"id", "name"}:
                raise DataError("文件夹字段不正确")
            _identifier(folder["id"], "文件夹编号")
            _text(folder["name"], "文件夹名称")
            if folder["id"] in seen:
                raise DataError("文件夹编号重复")
            seen.add(folder["id"])
        return data["folders"]

    def _snapshots(self, article_id):
        _identifier(article_id, "文章编号")
        snapshots = {}
        base = self.articles_dir / f"{article_id}.json"
        if base.exists():
            snapshots[1] = base
        directory = self.records_dir / article_id
        if directory.exists():
            if not directory.resolve(strict=True).is_relative_to(self.data_dir):
                raise DataError("版本目录越出数据目录")
            for commit in directory.iterdir():
                match = re.fullmatch(r"v(\d{8})-[a-f0-9]{32}", commit.name)
                if not match or not (commit / "COMMITTED").is_file():
                    continue
                if not commit.resolve(strict=True).is_relative_to(self.data_dir):
                    raise DataError("版本文件越出数据目录")
                version = int(match.group(1))
                if version in snapshots:
                    raise DataError("同一文章存在重复版本，停止读取以避免猜测")
                snapshots[version] = commit / "article.json"
        return snapshots

    def get(self, article_id, content_version=None):
        snapshots = self._snapshots(article_id)
        if not snapshots:
            raise FileNotFoundError(article_id)
        version = max(snapshots) if content_version is None else content_version
        if type(version) is not int or version not in snapshots:
            raise DataError("指定文章版本不存在")
        article = validate_article(self._read(snapshots[version]),
                                   {folder["id"] for folder in self.folders()})
        if article["id"] != article_id or article["content_version"] != version:
            raise DataError("文章编号或版本与存储位置不一致")
        return article

    @contextmanager
    def _write_lock(self):
        """OS advisory lock coordinates HTTP and independently started MCP processes."""
        lock_path = self.data_dir / ".write-lock"
        if lock_path.exists() and (lock_path.is_symlink() or not lock_path.resolve().is_relative_to(self.data_dir)):
            raise DataError("写入锁路径不安全")
        with lock_path.open("a+b") as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + 5
            acquired = False
            while not acquired:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise ConflictError("另一项保存尚未结束，请稍后重试。", "busy") from exc
                    time.sleep(0.025)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _commit(self, article):
        payload = json.dumps(article, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise DataError("文章与最初粘贴内容合计超过 2 MiB 上限")
        self.records_dir.mkdir(exist_ok=True)
        if not self.records_dir.resolve(strict=True).is_relative_to(self.data_dir):
            raise DataError("保存目录越出数据目录")
        directory = self.records_dir / article["id"]
        directory.mkdir(exist_ok=True)
        if not directory.resolve(strict=True).is_relative_to(self.data_dir):
            raise DataError("保存目录越出数据目录")
        commit = directory / f"v{article['content_version']:08d}-{uuid.uuid4().hex}"
        commit.mkdir()
        # A interrupted commit stays on disk for inspection, but is never published.
        with (commit / "article.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if self._read(commit / "article.json") != article:
            raise DataError("保存后校验不一致，文章未发布")
        with (commit / "COMMITTED").open("xb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        return article

    def save(self, article, *, confirmed=False):
        if confirmed is not True:
            raise DataError("保存需要 confirmed=true，表示用户已要求保存这篇文章")
        article = copy.deepcopy(article)
        validate_article(article, {folder["id"] for folder in self.folders()})
        if article["content_version"] != 1:
            raise DataError("新文章的内容版本必须为 1；补译请使用 add_translations")
        with self._write_lock():
            if self._snapshots(article["id"]):
                raise ConflictError("这个文章编号已经存在，没有覆盖原文章。", "duplicate_article", article["id"])
            fingerprint = original_fingerprint(article)
            index = self.index()
            if index["warnings"]:
                raise DataError("文章库有无法读取的文件，请先检查；暂停保存以确保重复检查可靠")
            for item in index["articles"]:
                if original_fingerprint(self.get(item["id"])) == fingerprint:
                    raise ConflictError("相同英文原文已在文章库中，没有重复保存。", "duplicate_article", item["id"])
            return self._commit(article)

    def add_translations(self, article_id, expected_version, translations, *, confirmed=False):
        if confirmed is not True:
            raise DataError("补译保存需要 confirmed=true，表示用户已要求保存这些译文")
        if type(expected_version) is not int or expected_version < 1:
            raise DataError("expected_version 必须是当前文章版本")
        if not isinstance(translations, list) or not translations:
            raise DataError("至少提供一段译文")
        with self._write_lock():
            article = self.get(article_id)
            if article["content_version"] != expected_version:
                raise ConflictError("文章版本已经变化，请重新读取当前版本后补译。", "version_conflict", article_id)
            blocks = {block["id"]: block for block in article["blocks"]}
            seen, changed = set(), False
            for item in translations:
                if not isinstance(item, dict) or set(item) != {"block_id", "original", "translation"}:
                    raise DataError("每段补译需包含 block_id、original、translation")
                block_id = item["block_id"]
                _identifier(block_id, "内容块编号", BLOCK_PATTERN)
                if block_id not in blocks or block_id in seen:
                    raise DataError("补译编号不存在或重复")
                seen.add(block_id)
                block = blocks[block_id]
                if item["original"] != block["original"]:
                    raise ConflictError("补译对应的英文与保存原文不一致，已停止写入。", "original_mismatch", article_id)
                _text(item["translation"], "中文译文")
                if block["translation"] is not None and block["translation"] != item["translation"]:
                    raise ConflictError("该段已经有不同译文；本工具不会覆盖已有译文。", "translation_exists", article_id)
                changed |= block["translation"] is None
                block["translation"] = item["translation"]
            if not changed:
                return {"article": article, "unchanged": True}
            article["content_version"] += 1
            article["translation_status"] = translation_status(article["blocks"])
            validate_article(article, {folder["id"] for folder in self.folders()})
            return {"article": self._commit(article), "unchanged": False}

    def index(self):
        folders = self.folders()
        if not self.articles_dir.resolve(strict=True).is_relative_to(self.data_dir):
            raise DataError("文章目录越出数据目录")
        summaries, warnings = [], []
        article_ids = {path.stem for path in self.articles_dir.glob("*.json")}
        if self.records_dir.exists():
            if not self.records_dir.resolve(strict=True).is_relative_to(self.data_dir):
                raise DataError("版本目录越出数据目录")
            for directory in self.records_dir.iterdir():
                if directory.is_dir() and ID_PATTERN.fullmatch(directory.name):
                    if self._snapshots(directory.name):
                        article_ids.add(directory.name)
        for article_id in sorted(article_ids):
            try:
                article = self.get(article_id)
                summary = {key: article[key] for key in ("id", "title", "source", "published_date", "created_at",
                           "folder_id", "content_version", "translation_status", "is_demo")}
                summary["block_count"] = len(article["blocks"])
                summary["translated_count"] = sum(block["translation"] is not None for block in article["blocks"])
                summary["word_count"] = sum(len(block["original"].split()) for block in article["blocks"])
                summaries.append(summary)
            except (DataError, OSError):
                warnings.append("一篇文章文件无法读取，请检查数据目录中的 JSON 格式和字段。")
        summaries.sort(key=lambda article: datetime.fromisoformat(article["created_at"].replace("Z", "+00:00")), reverse=True)
        return {"schema_version": 1, "folders": folders, "articles": summaries, "warnings": warnings}
