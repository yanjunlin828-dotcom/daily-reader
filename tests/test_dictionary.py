"""Small offline smoke suite; creates only one retained, isolated article fixture."""
from __future__ import annotations

import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dictionary import DictionaryStore, DictionaryUnavailable, DEFAULT_DATABASE
from server import LocalHTTPServer, make_handler
from storage import ArticleStore


class DictionarySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dictionary = DictionaryStore()
        cls.before = hashlib.sha256(DEFAULT_DATABASE.read_bytes()).digest()
        cls.fixture = ROOT / "output" / f"dictionary-smoke-{uuid.uuid4().hex}"
        (cls.fixture / "articles").mkdir(parents=True)
        with (cls.fixture / "folders.json").open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "folders": [{"id": "economy", "name": "经济"}]}, stream)
        cls.server = LocalHTTPServer(("127.0.0.1", 0), make_handler(ArticleStore(cls.fixture)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        if hashlib.sha256(DEFAULT_DATABASE.read_bytes()).digest() != cls.before:
            raise AssertionError("Dictionary changed during lookup")
        print("Retained isolated fixture:", cls.fixture)

    def request(self, path, method="GET"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_real_entries_preserve_text(self):
        for word in ("record", "yield", "bond", "rate", "sanction", "increased", "markets", "don't"):
            with self.subTest(word=word):
                result = self.dictionary.lookup(word)
                self.assertEqual(result["status"], "found")
                entry = result["entries"][0]
                self.assertEqual(entry["translation"], entry["raw_translation"].replace("\\n", "\n"))
        normalized = self.dictionary.lookup("RECORD")
        self.assertEqual(normalized["status"], "found")

    def test_unknown_and_invalid(self):
        self.assertEqual(self.dictionary.lookup("zzzzdictionaryunknownzzzz")["status"], "not_found")
        for value in ("", "two words", "中文", "x' OR 1=1", "a" * 81):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.dictionary.lookup(value)
        with self.assertRaises(DictionaryUnavailable):
            DictionaryStore(self.fixture / "not-installed.sqlite").lookup("record")

    def test_http_and_assets(self):
        status, body = self.request("/api/dictionary/lookup?word=yield")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "found")
        for path in ("/dictionary.js", "/dictionary.css", "/api/library", "/api/health"):
            self.assertEqual(self.request(path)[0], 200)
        for path in ("/api/dictionary/lookup", "/api/dictionary/lookup?word=two%20words",
                     "/api/dictionary/lookup?word=a&word=b"):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request("/resources/ecdict/dictionary.sqlite")[0], 404)
        self.assertEqual(self.request("/api/dictionary/lookup?word=record", "POST")[0], 405)
        self.assertEqual(self.request("/api/dictionary/lookup?word=record", "HEAD"), (200, b""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
