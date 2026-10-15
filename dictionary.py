"""Offline ECDICT lookup. Runtime connections are always read-only."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "resources" / "ecdict" / "dictionary.sqlite"
FIELDS = ("word", "phonetic", "definition", "translation", "pos", "collins",
          "oxford", "tag", "bnc", "frq", "exchange", "detail", "audio")
WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*\Z")


class DictionaryUnavailable(Exception):
    pass


def word_key(value):
    return value.replace("’", "'").replace("‘", "'").replace("‐", "-").replace("‑", "-").lower()


def validate_query(value):
    if not isinstance(value, str):
        raise ValueError("请选择一个完整英文单词。")
    value = value.strip()
    if not 1 <= len(value) <= 80 or not WORD_PATTERN.fullmatch(word_key(value)):
        raise ValueError("请选择一个完整英文单词，可包含词内的连字符或撇号。")
    return value


def display_text(value):
    # ECDICT encodes line breaks as literal backslash-n in CSV fields.
    # Keep the raw field alongside this display-only representation.
    return value.replace("\\n", "\n")


class DictionaryStore:
    def __init__(self, database=DEFAULT_DATABASE):
        self.database = Path(database)

    def lookup(self, query):
        query = validate_query(query)
        key = word_key(query)
        try:
            resolved = self.database.resolve(strict=True)
            if not resolved.is_relative_to(ROOT) or not resolved.is_file():
                raise DictionaryUnavailable("词库路径不可用。")
            with closing(sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise DictionaryUnavailable("本地词库版本不受支持。")
                source = json.loads(connection.execute(
                    "SELECT value FROM metadata WHERE key = 'source'").fetchone()[0])
                if not isinstance(source, dict):
                    raise DictionaryUnavailable("本地词库来源信息无效。")
                rows = connection.execute(
                    "SELECT * FROM entries WHERE word = ? ORDER BY id LIMIT 9", (query,)).fetchall()
                match_type = "exact"
                if not rows:
                    rows = connection.execute(
                        "SELECT * FROM entries WHERE word_key = ? ORDER BY id LIMIT 9", (key,)).fetchall()
                    match_type = "normalized"
                if not rows:
                    rows = connection.execute(
                        "SELECT DISTINCT e.* FROM aliases a JOIN entries e ON e.id = a.entry_id "
                        "WHERE a.form_key = ? ORDER BY e.id LIMIT 9", (key,)).fetchall()
                    match_type = "inflection"
                entries = []
                for row in rows[:8]:
                    entries.append({
                        "word": row["word"], "phonetic": row["phonetic"],
                        "translation": display_text(row["translation"]),
                        "definition": display_text(row["definition"]),
                        "pos": row["pos"], "exchange": row["exchange"],
                        "raw_translation": row["translation"], "raw_definition": row["definition"],
                    })
                return {"query": query, "status": "found" if entries else "not_found",
                        "match_type": match_type if entries else None,
                        "entries": entries, "truncated": len(rows) > 8,
                        "source": {"name": "ECDICT", "commit": source.get("commit", ""),
                                   "url": "https://github.com/skywind3000/ECDICT"}}
        except (OSError, sqlite3.Error, ValueError, TypeError, IndexError) as exc:
            raise DictionaryUnavailable("本地词库未安装或无法读取。") from exc
