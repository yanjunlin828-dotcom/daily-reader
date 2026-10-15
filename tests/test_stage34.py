"""Isolated write tests. Every run is retained under output; no user files are modified or removed."""
from __future__ import annotations

import copy
import json
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from importer import prepare_article, split_plain, parse_marked
from storage import ArticleStore, DataError, ConflictError
from server import make_handler

RUN = ROOT / "output" / f"stage34-tests-{uuid.uuid4().hex}"


def fixture(directory):
    directory.mkdir(parents=True)
    (directory / "articles").mkdir()
    with (directory / "folders.json").open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "folders": [{"id": "economy", "name": "经济"}]}, stream, ensure_ascii=False)
    return ArticleStore(directory)


class Stage34Tests(unittest.TestCase):
    def setUp(self):
        self.store = fixture(RUN / self._testMethodName)
        self.metadata = {"title": "An isolated test article", "folder_id": "economy"}
        self.raw = "Dr. Smith reads the news.\nThis is a soft line break.\n\nA second paragraph stays separate."
        self.article = prepare_article(self.metadata, raw_text=self.raw, folder_ids={"economy"})["article"]

    def test_soft_wrap_and_abbreviation_not_split(self):
        self.assertEqual(len(self.article["blocks"]), 2)
        self.assertIn("\nThis", self.article["blocks"][0]["original"])
        self.assertIn("Dr. Smith", self.article["blocks"][0]["original"])
        self.assertEqual(self.article["import_source"]["text"], self.raw)

    def test_explicit_every_line(self):
        self.assertEqual(len(split_plain(self.raw, "every_line")), 3)

    def test_markdown_heading_and_list(self):
        blocks = split_plain("# Heading\n\n- First item\n- Second item\n\nShort sentence.")
        self.assertEqual([block["type"] for block in blocks], ["heading", "list", "paragraph"])
        self.assertEqual(blocks[1]["original"], "First item\nSecond item")

    def test_marked_translation_and_missing(self):
        blocks = parse_marked(":::heading h001\n:::en\nA heading\n:::zh\n小标题\n\n:::paragraph p001\n:::en\nUntranslated.")
        self.assertEqual(blocks[0]["translation"], "小标题")
        self.assertIsNone(blocks[1]["translation"])

    def test_invalid_marker_order_and_duplicates(self):
        for raw in (":::paragraph p001\n:::zh\n中文", ":::paragraph p001\n:::en\nEnglish\n:::bad"):
            with self.assertRaises(DataError):
                parse_marked(raw)
        with self.assertRaises(DataError):
            prepare_article(self.metadata, raw_text=":::paragraph p001\n:::en\nOne\n:::paragraph p001\n:::en\nTwo",
                            input_format="marked", folder_ids={"economy"})

    def test_invalid_json_keeps_store_empty(self):
        with self.assertRaises(DataError):
            prepare_article({}, raw_text="{broken}", input_format="json", folder_ids={"economy"})
        self.assertEqual(self.store.index()["articles"], [])

    def test_json_legacy_import_preserves_input(self):
        old = copy.deepcopy(self.article)
        old["schema_version"] = 1
        del old["import_source"]
        raw = json.dumps(old, ensure_ascii=False)
        result = prepare_article({}, raw_text=raw, input_format="json", folder_ids={"economy"})["article"]
        self.assertEqual(result["import_source"]["text"], raw)
        self.assertEqual(result["blocks"], old["blocks"])

    def test_explicit_save_and_reopen(self):
        with self.assertRaises(DataError):
            self.store.save(self.article)
        self.assertFalse((self.store.data_dir / "records").exists())
        self.store.save(self.article, confirmed=True)
        reopened = ArticleStore(self.store.data_dir)
        self.assertEqual(reopened.get(self.article["id"]), self.article)

    def test_same_id_no_overwrite(self):
        self.store.save(self.article, confirmed=True)
        changed = copy.deepcopy(self.article)
        changed["blocks"][0]["original"] = "Changed text."
        with self.assertRaises(ConflictError):
            self.store.save(changed, confirmed=True)
        self.assertEqual(self.store.get(self.article["id"]), self.article)

    def test_duplicate_content_with_new_id_and_segmentation(self):
        self.store.save(self.article, confirmed=True)
        duplicate = copy.deepcopy(self.article)
        duplicate["id"] = "article-other"
        duplicate["blocks"] = [{"id": "p001", "type": "paragraph", "original": " ".join(block["original"] for block in duplicate["blocks"]), "translation": None}]
        with self.assertRaises(ConflictError) as error:
            self.store.save(duplicate, confirmed=True)
        self.assertEqual(error.exception.article_id, self.article["id"])
        self.assertEqual(len(self.store.index()["articles"]), 1)

    def test_batch_translation_and_immutable_history(self):
        self.store.save(self.article, confirmed=True)
        item = self.article["blocks"][0]
        result = self.store.add_translations(self.article["id"], 1, [{"block_id": item["id"], "original": item["original"], "translation": "隔离测试译文。"}], confirmed=True)
        self.assertEqual(result["article"]["content_version"], 2)
        self.assertEqual(result["article"]["translation_status"], "partial")
        self.assertEqual(self.store.get(self.article["id"], 1), self.article)
        self.assertEqual(self.store.get(self.article["id"])["blocks"][0]["translation"], "隔离测试译文。")

    def test_version_conflict_no_write(self):
        self.store.save(self.article, confirmed=True)
        block = self.article["blocks"][0]
        with self.assertRaises(ConflictError):
            self.store.add_translations(self.article["id"], 2, [{"block_id": block["id"], "original": block["original"], "translation": "译文"}], confirmed=True)
        self.assertEqual(self.store.get(self.article["id"])["content_version"], 1)

    def test_mismatched_original_and_partial_bad_batch_no_write(self):
        self.store.save(self.article, confirmed=True)
        first, second = self.article["blocks"]
        translations = [{"block_id": first["id"], "original": first["original"], "translation": "第一段译文"},
                        {"block_id": second["id"], "original": "wrong", "translation": "第二段译文"}]
        with self.assertRaises(ConflictError):
            self.store.add_translations(self.article["id"], 1, translations, confirmed=True)
        self.assertEqual(self.store.get(self.article["id"]), self.article)

    def test_existing_translation_not_overwritten_and_identical_retry(self):
        self.store.save(self.article, confirmed=True)
        block = self.article["blocks"][0]
        item = {"block_id": block["id"], "original": block["original"], "translation": "译文"}
        self.store.add_translations(self.article["id"], 1, [item], confirmed=True)
        self.assertTrue(self.store.add_translations(self.article["id"], 2, [item], confirmed=True)["unchanged"])
        with self.assertRaises(ConflictError):
            self.store.add_translations(self.article["id"], 2, [{**item, "translation": "另一个译文"}], confirmed=True)
        self.assertEqual(self.store.get(self.article["id"])["content_version"], 2)

    def test_interrupted_commit_is_invisible(self):
        with patch("storage.os.fsync", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                self.store.save(self.article, confirmed=True)
        self.assertEqual(self.store.index()["articles"], [])
        self.assertTrue(list(self.store.records_dir.rglob("article.json")))
        self.store.save(self.article, confirmed=True)
        self.assertEqual(len(self.store.index()["articles"]), 1)

    def test_concurrent_duplicate_saves_only_one_commit(self):
        def save():
            try:
                ArticleStore(self.store.data_dir).save(self.article, confirmed=True)
                return "saved"
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: save(), range(2))), ["conflict", "saved"])
        self.assertEqual(len(self.store.index()["articles"]), 1)

    def test_invalid_blocks_rejected_as_data_error(self):
        with self.assertRaises(DataError):
            prepare_article(self.metadata, input_format="blocks", blocks=[{}], folder_ids={"economy"})

    def test_http_prepare_save_auth_and_duplicate(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, payload=None, *, csrf=None, origin=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                headers = {"Content-Type": "application/json"}
                if csrf: headers["X-Reader-CSRF"] = csrf
                if origin: headers["Origin"] = origin
                connection.request("POST" if payload else "GET", path, body=json.dumps(payload) if payload else None, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            token = request("/api/session")[1]["csrf_token"]
            origin = f"http://127.0.0.1:{server.server_port}"
            payload = {"metadata": self.metadata, "raw_text": self.raw}
            self.assertEqual(request("/api/prepare", payload)[0], 403)
            self.assertEqual(request("/api/prepare", payload, csrf=token, origin="https://example.com")[0], 403)
            status, result = request("/api/prepare", payload, csrf=token, origin=origin)
            self.assertEqual(status, 200)
            save = {"article": result["article"], "confirmed": True}
            self.assertEqual(request("/api/articles", save, csrf=token, origin=origin)[0], 201)
            self.assertEqual(request("/api/articles", save, csrf=token, origin=origin)[0], 409)
            self.assertEqual(request("/api/articles", {**save, "confirmed": False}, csrf=token, origin=origin)[0], 422)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == "__main__":
    print("Isolated tests retained in: " + str(RUN))
    unittest.main(verbosity=2)
