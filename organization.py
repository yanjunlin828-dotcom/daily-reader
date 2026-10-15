"""One-level user folders, stored as independent immutable library revisions."""
from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
import uuid

from storage import ConflictError, DataError, ID_PATTERN, MAX_FILE_BYTES

COMMIT = re.compile(r"v(\d{8})-[a-f0-9]{32}")


def folder_name(value):
    if not isinstance(value, str):
        raise DataError("请输入文件夹名称")
    value = value.strip()
    if (not 1 <= len(value) <= 60 or value in (".", "..")
            or any(ord(char) < 32 or ord(char) == 127 or char in "/\\" for char in value)):
        raise DataError("文件夹名称需为1–60个字符，不能包含斜杠或控制字符")
    return value


def name_key(value):
    return unicodedata.normalize("NFKC", value).casefold()


class OrganizationStore:
    def __init__(self, articles):
        self.articles = articles
        self.directory = articles.data_dir / "organization"

    def _inside(self, path):
        if (not path.resolve(strict=True).is_relative_to(self.articles.data_dir)
                or path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())):
            raise DataError("文章库目录记录路径不安全")
        return path

    def revisions(self):
        if self.directory.is_symlink() or (hasattr(self.directory, "is_junction") and self.directory.is_junction()):
            raise DataError("文章库目录记录路径不安全")
        if not self.directory.exists():
            return {}
        self._inside(self.directory)
        revisions = {}
        for entry in self.directory.iterdir():
            match = COMMIT.fullmatch(entry.name)
            if not match:
                continue
            self._inside(entry)
            marker = entry / "COMMITTED"
            if not marker.exists():
                continue  # Interrupted writes are retained but never exposed.
            self._inside(marker)
            if not marker.is_file():
                raise DataError("文章库目录记录的提交标记无效")
            version = int(match[1])
            if version < 1 or version in revisions:
                raise DataError("文章库目录记录版本重复或无效，停止读取以避免猜测")
            revisions[version] = self._inside(entry / "library.json")
        if revisions and max(revisions) != len(revisions):
            raise DataError("文章库目录记录的历史版本不完整")
        return revisions

    @staticmethod
    def validate(state, revision):
        if (not isinstance(state, dict)
                or set(state) != {"schema_version", "revision", "folders", "article_folders"}
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or type(state["revision"]) is not int or state["revision"] != revision
                or not isinstance(state["folders"], list) or len(state["folders"]) > 200
                or not isinstance(state["article_folders"], dict) or len(state["article_folders"]) > 10000):
            raise DataError("文章库目录记录格式无效")
        ids, names = set(), set()
        for folder in state["folders"]:
            if (not isinstance(folder, dict) or set(folder) != {"id", "name"}
                    or not isinstance(folder["id"], str) or not ID_PATTERN.fullmatch(folder["id"])
                    or folder["id"] in ids or folder_name(folder["name"]) != folder["name"]
                    or name_key(folder["name"]) in names):
                raise DataError("文件夹名称或编号无效、重复")
            ids.add(folder["id"])
            names.add(name_key(folder["name"]))
        for article_id, folder_id in state["article_folders"].items():
            if (not isinstance(article_id, str) or not ID_PATTERN.fullmatch(article_id)
                    or not isinstance(folder_id, str) or folder_id not in ids):
                raise DataError("文章与文件夹的归属记录无效")
        return state

    def get(self, revision=None):
        snapshots = self.revisions()
        if revision is None:
            revision = max(snapshots, default=0)
        if type(revision) is not int or revision < 0 or (revision and revision not in snapshots):
            raise DataError("指定的文章库目录版本不存在")
        state = (self.articles._read(snapshots[revision]) if revision else
                 {"schema_version": 1, "revision": 0, "folders": [], "article_folders": {}})
        return self.validate(state, revision)

    def _commit(self, state):
        self.validate(state, state["revision"])
        payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise DataError("文章库目录记录超过2 MiB上限")
        self.directory.mkdir(exist_ok=True)
        self._inside(self.directory)
        destination = self.directory / f"v{state['revision']:08d}-{uuid.uuid4().hex}"
        destination.mkdir()
        self._inside(destination)
        with (destination / "library.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if self.articles._read(destination / "library.json") != state:
            raise DataError("文件夹保存校验失败，目录记录未发布")
        with (destination / "COMMITTED").open("xb") as stream:
            stream.flush()
            os.fsync(stream.fileno())

    def _edit(self, expected_revision, confirmed, change):
        if confirmed is not True:
            raise DataError("整理文章库需要当前用户授权及 confirmed=true")
        if type(expected_revision) is not int or expected_revision < 0:
            raise DataError("expected_revision 必须是当前文章库目录版本")
        with self.articles._write_lock():
            state = self.get()
            if state["revision"] != expected_revision:
                raise ConflictError("文章库刚刚有其他修改，请刷新后再试", "library_version_conflict")
            before = copy.deepcopy(state)
            extra = change(state)
            unchanged = before == state
            if not unchanged:
                state["revision"] += 1
                self._commit(state)
            return {"organization": state, "unchanged": unchanged, **extra}

    @staticmethod
    def _folder(state, folder_id):
        if not isinstance(folder_id, str) or not ID_PATTERN.fullmatch(folder_id):
            raise DataError("文件夹编号无效")
        folder = next((f for f in state["folders"] if f["id"] == folder_id), None)
        if folder is None:
            raise ConflictError("这个文件夹已不存在，请刷新文章库", "folder_missing")
        return folder

    @staticmethod
    def _unique(state, name, excluding=None):
        if any(f["id"] != excluding and name_key(f["name"]) == name_key(name) for f in state["folders"]):
            raise DataError("已有同名文件夹，请换一个名称")

    def create(self, name, expected_revision, *, confirmed=False):
        name = folder_name(name)
        def change(state):
            if len(state["folders"]) >= 200:
                raise DataError("最多支持200个文件夹")
            self._unique(state, name)
            folder = {"id": f"folder-{uuid.uuid4().hex}", "name": name}
            state["folders"].append(folder)
            return {"folder_id": folder["id"]}
        return self._edit(expected_revision, confirmed, change)

    def rename(self, folder_id, name, expected_revision, *, confirmed=False):
        name = folder_name(name)
        def change(state):
            folder = self._folder(state, folder_id)
            self._unique(state, name, excluding=folder_id)
            folder["name"] = name
            return {}
        return self._edit(expected_revision, confirmed, change)

    def move(self, article_id, folder_id, expected_revision, *, confirmed=False):
        def change(state):
            self.articles.get(article_id)  # Validate existence; never change the article snapshot.
            if folder_id is None:
                state["article_folders"].pop(article_id, None)
            else:
                self._folder(state, folder_id)
                state["article_folders"][article_id] = folder_id
            return {}
        return self._edit(expected_revision, confirmed, change)

    def remove(self, folder_id, expected_revision, *, confirmed=False):
        def change(state):
            self._folder(state, folder_id)
            released = [key for key, value in state["article_folders"].items() if value == folder_id]
            state["folders"] = [folder for folder in state["folders"] if folder["id"] != folder_id]
            state["article_folders"] = {key: value for key, value in state["article_folders"].items() if value != folder_id}
            return {"moved_to_root": len(released)}
        return self._edit(expected_revision, confirmed, change)
