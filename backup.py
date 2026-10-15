"""Verified, create-only ZIP backups and isolated restores; never replaces a library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from storage import ArticleStore, DataError
from reading_guide import ReadingGuideStore

ROOT = Path(__file__).resolve().parent
MANIFEST = "backup-manifest.json"
MAX_TOTAL = 256 * 1024 * 1024
MAX_ENTRIES = 10000


def inside(path, *, exists=True):
    path = Path(path)
    resolved = path.resolve(strict=exists)
    if (not resolved.is_relative_to(ROOT) or path.is_symlink()
            or (hasattr(path, "is_junction") and path.is_junction())):
        raise DataError("路径必须是项目内的实际目录或文件")
    return resolved


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def validate_library(directory):
    store = ArticleStore(directory)
    index = store.index()
    if index["warnings"]:
        raise DataError("文章库含无法读取的文章，暂停备份/恢复")
    versions = {}
    guide_versions = {}
    guides = ReadingGuideStore(store)
    for item in index["articles"]:
        snapshots = store._snapshots(item["id"])
        for version in snapshots:
            store.get(item["id"], version)
        versions[item["id"]] = sorted(snapshots)
        guide = guides.get(item["id"])
        if guide:
            guide_versions[item["id"]] = guide["guide_version"]
    result = {"articles": len(index["articles"]), "versions": versions}
    if guide_versions:
        result["guides"] = guide_versions
    return result


def read_files(directory):
    directory = inside(directory)
    files, total = {}, 0
    for path in sorted(directory.rglob("*")):
        resolved = path.resolve(strict=True)
        if (path.is_symlink() or not resolved.is_relative_to(directory)
                or (hasattr(path, "is_junction") and path.is_junction())):
            raise DataError("数据目录包含符号链接或指向外部的路径，暂停备份")
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative == ".write-lock":
            continue  # A process lock is not article data and is never restored.
        if not path.is_file() or relative == MANIFEST:
            raise DataError("数据目录包含不支持的文件")
        if path.stat().st_size > MAX_TOTAL - total or len(files) >= MAX_ENTRIES:
            raise DataError("备份超过 256 MiB 或 10000 个文件上限")
        with path.open("rb") as stream:
            payload = stream.read(MAX_TOTAL - total + 1)
        total += len(payload)
        if total > MAX_TOTAL:
            raise DataError("读取期间文件大小变化或超过上限")
        files[relative] = payload
    if "folders.json" not in files:
        raise DataError("缺少 folders.json")
    return files


def inspect_backup(path):
    path = inside(path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names, total = set(), 0
            if len(infos) > MAX_ENTRIES + 1:
                raise DataError("备份文件数超过上限")
            for info in infos:
                name = info.filename
                pure = PurePosixPath(name)
                if (name in names or not name or pure.is_absolute() or "\\" in name or ":" in name
                        or any(part in ("", ".", "..") for part in name.split("/")) or info.is_dir()
                        or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise DataError("备份含重复、加密、符号链接或不安全路径")
                names.add(name)
                total += info.file_size
                if total > MAX_TOTAL or info.file_size > MAX_TOTAL:
                    raise DataError("备份展开大小超过 256 MiB 上限")
            if MANIFEST not in names:
                raise DataError("备份缺少校验清单")
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            if (not isinstance(manifest, dict) or manifest.get("format") != "english-news-backup-v1"
                    or not isinstance(manifest.get("files"), dict) or not isinstance(manifest.get("library"), dict)):
                raise DataError("备份清单格式无效")
            expected = manifest["files"]
            if set(expected) != names - {MANIFEST} or "folders.json" not in expected:
                raise DataError("备份文件与清单不一致")
            files = {}
            for name in sorted(expected):
                payload = archive.read(name)
                item = expected[name]
                if not isinstance(item, dict) or item != {"size": len(payload), "sha256": digest(payload)}:
                    raise DataError("备份文件校验失败；尚未写入恢复目录")
                files[name] = payload
            return manifest, files
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        raise DataError("备份不是完整有效的阅读库 ZIP") from exc


def create_backup(data_dir=ROOT / "data", destination=None):
    data_dir = inside(data_dir)
    before = validate_library(data_dir)
    files = read_files(data_dir)
    # No lock writes on the source: immutable snapshots permit a read-only consistency check.
    if before != validate_library(data_dir) or files != read_files(data_dir):
        raise DataError("备份期间文章库发生变化，请在保存结束后重试")
    if destination is None:
        parent = ROOT / "backups"
        if parent.exists():
            inside(parent)
        else:
            parent.mkdir()
        destination = parent / f"news-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.zip"
    destination = inside(destination, exists=False)
    if destination.is_relative_to(data_dir) or not destination.parent.is_dir():
        raise DataError("备份必须在数据目录外，且父目录已经存在")
    manifest = {"format": "english-news-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                "library": before, "files": {name: {"size": len(payload), "sha256": digest(payload)} for name, payload in files.items()}}
    with destination.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in files.items():
                archive.writestr(name, payload)
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    checked, _ = inspect_backup(destination)
    return {"backup": str(destination), "files": len(files), "library": checked["library"], "verified": True}


def restore_backup(archive_path, destination):
    manifest, files = inspect_backup(archive_path)  # Verify every byte and path before creating output.
    destination = inside(destination, exists=False)
    if destination.exists() or destination == ROOT or destination.is_relative_to(ROOT / "data"):
        raise DataError("恢复目录必须是全新的独立目录，不能是正式 data 或其中路径")
    if not destination.parent.is_dir():
        raise DataError("恢复目录的父目录必须已经存在")
    destination.mkdir()  # Exclusive: no merging and no replacement of existing data.
    (destination / "articles").mkdir()
    for name, payload in files.items():
        path = destination.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    if files != read_files(destination) or validate_library(destination) != manifest["library"]:
        raise DataError("恢复后校验不一致；保留独立目录供检查，正式库未修改")
    return {"restored_data_dir": str(destination), "library": manifest["library"], "verified": True}


def main():
    parser = argparse.ArgumentParser(description="阅读库备份与独立恢复；不删除或覆盖已有文件")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--data-dir", type=Path, default=ROOT / "data")
    create.add_argument("--output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "create":
            result = create_backup(args.data_dir, args.output)
        elif args.command == "verify":
            manifest, files = inspect_backup(args.archive)
            result = {"verified": True, "files": len(files), "library": manifest["library"]}
        else:
            result = restore_backup(args.archive, args.destination)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DataError, OSError) as exc:
        print(json.dumps({"error": str(exc), "note": "没有删除或替换现有库。失败生成物保留，不视为有效备份/恢复。"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
