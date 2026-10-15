"""Local font/static integration; writes only a new empty, isolated fixture under output."""
from __future__ import annotations
import hashlib
import json
import sys
import threading
import unittest
import uuid
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import LocalHTTPServer, make_handler
from storage import ArticleStore


class RefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT / "output" / f"refinement-tests-{uuid.uuid4().hex}"
        (directory / "articles").mkdir(parents=True)
        with (directory / "folders.json").open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "folders": [{"id": "economy", "name": "经济"}]}, stream, ensure_ascii=False)
        cls.server = LocalHTTPServer(("127.0.0.1", 0), make_handler(ArticleStore(directory)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        print("Isolated fixture retained:", directory)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, method="GET"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_fonts_are_original_valid_local_assets(self):
        manifest = json.loads((ROOT / "web/fonts/manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["original_files_unmodified"])
        for item in manifest["fonts"]:
            with self.subTest(font=item["file"]):
                status, headers, data = self.request("/fonts/" + item["file"])
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "font/woff2")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(data[:4], b"wOF2")
                self.assertEqual(len(data), item["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])
                self.assertGreater(item["unicode_count"], 30000)
                self.assertEqual(item["axes"]["wght"], [250.0, 900.0])
                status, _, body = self.request("/fonts/" + item["file"], "HEAD")
                self.assertEqual(status, 200)
                self.assertEqual(body, b"")

    def test_assets_in_order_and_csp_unchanged(self):
        status, headers, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        html = html.decode("utf-8")
        self.assertLess(html.index('src="/motion.js"'), html.index('src="/app.js"'))
        self.assertLess(html.index('href="/import.css"'), html.index('href="/refinement.css"'))
        for asset in ("/fonts.css", "/refinement.css", "/motion.js"):
            self.assertEqual(self.request(asset)[0], 200)

    def test_whitelist_does_not_expose_data_or_extra_files(self):
        for target in ("/fonts/manifest.json", "/fonts/serif-LICENSE.txt", "/fonts/../manifest.json", "/data/folders.json", "/fonts/%2e%2e/server.py"):
            with self.subTest(target=target):
                self.assertEqual(self.request(target)[0], 404)
        self.assertEqual(json.loads(self.request("/api/library")[2])["articles"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
