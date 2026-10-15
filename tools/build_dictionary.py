"""Create and validate a new ECDICT SQLite database; never replace an existing file."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dictionary import FIELDS, word_key


def row_bytes(row):
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def build(source, target, provenance):
    source = source.resolve(strict=True)
    target = target.absolute()
    if not source.is_relative_to(ROOT) or not target.resolve().is_relative_to(ROOT):
        raise ValueError("输入和输出必须在项目目录内。")
    if target.exists() or target.with_name("manifest.json").exists() or not target.parent.is_dir():
        raise ValueError("输出文件必须不存在，且父目录必须已存在。")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if provenance.get("files", {}).get("ecdict.csv", {}).get("sha256") != source_hash:
        raise ValueError("CSV 与来源清单的 SHA256 不一致。")
    with target.open("xb"):
        pass
    connection = sqlite3.connect(target)
    count, empty_translations, alias_count = 0, 0, 0
    original_digest = hashlib.sha256()
    try:
        connection.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, word_key TEXT NOT NULL, " +
                           ", ".join(f'"{field}" TEXT NOT NULL' for field in FIELDS) + ")")
        connection.execute("CREATE TABLE aliases (form_key TEXT NOT NULL, entry_id INTEGER NOT NULL, "
                           "kind TEXT NOT NULL, UNIQUE(form_key, entry_id, kind))")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        columns = ",".join('"' + field + '"' for field in FIELDS)
        insert = f"INSERT INTO entries (word_key,{columns}) VALUES ({','.join('?' for _ in range(14))})"
        csv.field_size_limit(4 * 1024 * 1024)
        with source.open(encoding="utf-8-sig", newline="") as stream, connection:
            reader = csv.reader(stream, strict=True)
            if tuple(next(reader)) != FIELDS:
                raise ValueError("CSV 字段与 ECDICT 格式不一致。")
            for row in reader:
                if len(row) != len(FIELDS) or not row[0] or any("\x00" in value for value in row):
                    raise ValueError(f"CSV 第 {reader.line_num} 行字段异常，已停止导入。")
                count += 1
                empty_translations += not bool(row[3])
                original_digest.update(row_bytes(row))
                cursor = connection.execute(insert, [word_key(row[0]), *row])
                # Index documented inflections only. Keep every original field unchanged.
                for part in row[10].split("/"):
                    kind, separator, forms = part.partition(":")
                    if not separator or kind not in {"p", "d", "i", "3", "r", "t", "s"}:
                        continue
                    for form in forms.split(","):
                        if form:
                            connection.execute("INSERT OR IGNORE INTO aliases VALUES (?, ?, ?)",
                                               (word_key(form), cursor.lastrowid, kind))
                if count % 200000 == 0:
                    print(f"Imported {count:,} entries", flush=True)
        if not count:
            raise ValueError("CSV 没有词条。")
        connection.execute("CREATE INDEX entries_word ON entries(word)")
        connection.execute("CREATE INDEX entries_key ON entries(word_key)")
        connection.execute("CREATE INDEX aliases_key ON aliases(form_key)")
        alias_count = connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        duplicates = connection.execute(
            "SELECT COALESCE(SUM(n - 1), 0) FROM (SELECT COUNT(*) n FROM entries GROUP BY word HAVING n > 1)"
        ).fetchone()[0]
        report = {**provenance, "schema_version": 1, "entries": count,
                  "empty_translations": empty_translations, "aliases": alias_count,
                  "duplicate_words_preserved": duplicates,
                  "built_at": datetime.now(timezone.utc).isoformat(), "csv_sha256": source_hash}
        connection.execute("INSERT INTO metadata VALUES ('source', ?)", (json.dumps(report),))
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 完整性检查失败。")
        restored_digest = hashlib.sha256()
        for row in connection.execute(f"SELECT {columns} FROM entries ORDER BY id"):
            restored_digest.update(row_bytes(list(row)))
        if original_digest.digest() != restored_digest.digest():
            raise ValueError("导入后字段内容与 CSV 不一致。")
        report["all_fields_verified"] = True
    finally:
        connection.close()
    report["database_bytes"] = target.stat().st_size
    report["database_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = target.with_name("manifest.json")
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
    print(json.dumps(build(args.source, args.output, provenance), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
