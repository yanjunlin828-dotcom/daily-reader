"""Recoverable article visibility, committed without moving or deleting content."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from storage import ConflictError, DataError, _identifier

COMMIT = re.compile(r"v(\d{8})-[a-f0-9]{32}")


class ArticleLifecycle:
    def __init__(self, articles):
        self.articles = articles
        self.directory = articles.data_dir / "trash"

    def _inside(self, path):
        if (path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
                or not path.resolve(strict=True).is_relative_to(self.articles.data_dir)):
            raise DataError("文章删除记录路径不安全")
        return path

    def history(self, article_id):
        _identifier(article_id, "文章编号")
        if self.directory.is_symlink():
            raise DataError("文章删除记录路径不安全")
        if not self.directory.exists():
            return {}
        self._inside(self.directory)
        directory = self.directory / article_id
        if directory.is_symlink():
            raise DataError("文章删除记录路径不安全")
        if not directory.exists():
            return {}
        self._inside(directory)
        versions = {}
        for commit in directory.iterdir():
            match = COMMIT.fullmatch(commit.name)
            if not match:
                continue
            self._inside(commit)
            marker = commit / "COMMITTED"
            if not marker.exists():
                continue
            self._inside(marker)
            if not marker.is_file():
                raise DataError("文章删除记录提交标记无效")
            version = int(match[1])
            if version < 1 or version in versions:
                raise DataError("文章删除记录版本重复或无效")
            state = self.articles._read(self._inside(commit / "state.json"))
            required = {"schema_version", "article_id", "state_version", "deleted",
                        "content_version", "title", "changed_at"}
            if (not isinstance(state, dict) or set(state) != required
                    or type(state["schema_version"]) is not int or state["schema_version"] != 1
                    or state["article_id"] != article_id or type(state["state_version"]) is not int
                    or state["state_version"] != version or type(state["deleted"]) is not bool
                    or state["deleted"] != (version % 2 == 1)
                    or type(state["content_version"]) is not int or state["content_version"] < 1):
                raise DataError("文章删除记录格式无效")
            try:
                if not isinstance(state["changed_at"], str) or datetime.fromisoformat(state["changed_at"]).tzinfo is None:
                    raise ValueError("invalid date")
            except ValueError as exc:
                raise DataError("文章删除记录时间无效") from exc
            source = self.articles.get(article_id, state["content_version"], include_deleted=True)
            if source["title"] != state["title"]:
                raise DataError("文章删除记录与对应文章不一致")
            versions[version] = state
        if versions and max(versions) != len(versions):
            raise DataError("文章删除记录历史不完整")
        return versions

    def get(self, article_id):
        versions = self.history(article_id)
        return versions[max(versions)] if versions else {"article_id": article_id, "state_version": 0, "deleted": False}

    def set_deleted(self, article_id, expected_version, expected_state_version, expected_title,
                    *, deleted, confirmed=False):
        if confirmed is not True:
            raise DataError("删除或恢复文章需要当前用户明确授权及 confirmed=true")
        if (type(expected_version) is not int or expected_version < 1
                or type(expected_state_version) is not int or expected_state_version < 0):
            raise DataError("需提供当前 expected_version 和 expected_state_version")
        if not isinstance(expected_title, str) or not expected_title.strip():
            raise DataError("需提供读取到的完整 expected_title，以确认目标文章")
        with self.articles._write_lock():
            article = self.articles.get(article_id, include_deleted=True)
            current = self.get(article_id)
            if article["content_version"] != expected_version or article["title"] != expected_title:
                raise ConflictError("文章版本或标题已经变化，请重新核对目标。", "version_conflict", article_id)
            # Exact uncertain retries are harmless. An old request cannot delete an article again after restoration.
            if (current["state_version"] == expected_state_version + 1 and current["deleted"] == deleted
                    and current.get("content_version") == expected_version):
                return {"article_id": article_id, "lifecycle": current, "unchanged": True, "saved": True}
            if current["state_version"] != expected_state_version:
                raise ConflictError("文章删除/恢复状态已变化，请回读后再操作。", "state_conflict", article_id)
            if current["deleted"] == deleted:
                return {"article_id": article_id, "lifecycle": current, "unchanged": True, "saved": True}
            state = {"schema_version": 1, "article_id": article_id, "state_version": current["state_version"] + 1,
                     "deleted": deleted, "content_version": expected_version, "title": article["title"],
                     "changed_at": datetime.now(timezone.utc).isoformat()}
            self.directory.mkdir(exist_ok=True)
            self._inside(self.directory)
            directory = self.directory / article_id
            directory.mkdir(exist_ok=True)
            self._inside(directory)
            commit = directory / f"v{state['state_version']:08d}-{uuid.uuid4().hex}"
            commit.mkdir()
            self._inside(commit)
            with (commit / "state.json").open("xb") as stream:
                stream.write(json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if self.articles._read(commit / "state.json") != state:
                raise DataError("文章删除记录保存校验失败，未发布")
            with (commit / "COMMITTED").open("xb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            return {"article_id": article_id, "lifecycle": state, "unchanged": False, "saved": True}
