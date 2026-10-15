"""Folder operations use fresh retained fixtures; personal data is never modified."""
import copy
import json
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backup import create_backup, restore_backup, validate_library
from importer import prepare_article
from organization import OrganizationStore
from reading_guide import ReadingGuideStore
from server import LocalHTTPServer, make_handler
from storage import ArticleStore, ConflictError, DataError

RUN = ROOT / "output" / f"organization-tests-{uuid.uuid4().hex}"


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = RUN / self._testMethodName
        (self.directory / "articles").mkdir(parents=True)
        (self.directory / "folders.json").write_text(json.dumps({"schema_version": 1,
            "folders": [{"id": "economy", "name": "经济"}]}), encoding="utf-8")
        self.articles = ArticleStore(self.directory)
        draft = prepare_article({"title": "Original folder test", "folder_id": "economy"},
            raw_text="An original paragraph stays in its immutable snapshot.", folder_ids={"economy"})["article"]
        draft["blocks"][0]["translation"] = "原创段落保留在不可变的快照中。"
        draft["translation_status"] = "complete"
        self.article = self.articles.save(draft, confirmed=True)
        guides = ReadingGuideStore(self.articles)
        self.guide = guides.save(self.article["id"], 1,
            [{"id": "g001", "type": "paragraph", "text": "这是独立的中文导读。"}], confirmed=True)["guide"]
        self.original = {p.relative_to(self.directory): p.read_bytes() for p in self.directory.rglob("*")
                         if p.is_file() and p.name != ".write-lock"}
        self.org = OrganizationStore(self.articles)

    def tearDown(self):
        for relative, data in self.original.items():
            self.assertEqual((self.directory / relative).read_bytes(), data, relative)
        self.assertEqual(self.articles.get(self.article["id"]), self.article)
        self.assertEqual(ReadingGuideStore(self.articles).get(self.article["id"]), self.guide)

    def create(self, name="宏观经济"):
        return self.org.create(name, self.org.get()["revision"], confirmed=True)["folder_id"]

    def test_old_library_stays_flat_and_reading_does_not_initialize_metadata(self):
        self.assertEqual(self.org.get(), {"schema_version": 1, "revision": 0, "folders": [], "article_folders": {}})
        self.assertFalse((self.directory / "organization").exists())

    def test_create_rename_move_out_and_remove_keep_article_and_guide_bytes(self):
        folder = self.create()
        self.org.move(self.article["id"], folder, 1, confirmed=True)
        self.org.rename(folder, "长期阅读", 2, confirmed=True)
        self.assertEqual(self.org.get()["folders"], [{"id": folder, "name": "长期阅读"}])
        self.assertEqual(OrganizationStore(ArticleStore(self.directory)).get()["article_folders"], {self.article["id"]: folder})
        self.org.move(self.article["id"], None, 3, confirmed=True)
        self.assertEqual(self.org.get()["article_folders"], {})
        self.org.move(self.article["id"], folder, 4, confirmed=True)
        result = self.org.remove(folder, 5, confirmed=True)
        self.assertEqual(result["moved_to_root"], 1)
        self.assertEqual(result["organization"]["folders"], [])
        self.assertEqual(result["organization"]["article_folders"], {})
        self.assertEqual(self.org.get(2)["article_folders"], {self.article["id"]: folder})
        self.assertEqual(len(self.articles._snapshots(self.article["id"])), 1)

    def test_names_are_normalized_and_duplicates_or_invalid_names_do_not_write(self):
        folder = self.create("  News  ")
        self.assertEqual(self.org.get()["folders"][0]["name"], "News")
        for name in ("news", "Ｎｅｗｓ", "", "../News", "a\\b", "a\nB", ".", "x" * 61):
            with self.subTest(name=name), self.assertRaises(DataError):
                self.org.create(name, 1, confirmed=True)
        other = self.create("Other")
        with self.assertRaises(DataError):
            self.org.rename(other, "News", 2, confirmed=True)
        self.assertEqual(self.org.get()["revision"], 2)
        unchanged = self.org.rename(folder, "News", 2, confirmed=True)
        self.assertTrue(unchanged["unchanged"])

    def test_noop_moves_and_missing_targets_do_not_advance_revision(self):
        folder = self.create()
        result = self.org.move(self.article["id"], None, 1, confirmed=True)
        self.assertTrue(result["unchanged"])
        with self.assertRaises(ConflictError):
            self.org.move(self.article["id"], "folder-missing", 1, confirmed=True)
        with self.assertRaises(FileNotFoundError):
            self.org.move("article-missing", folder, 1, confirmed=True)
        self.assertEqual(self.org.get()["revision"], 1)

    def test_concurrent_writers_reject_stale_revision(self):
        def create(name):
            try:
                return self.org.create(name, 0, confirmed=True)
            except ConflictError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ("One", "Two")))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertIn("library_version_conflict", results)
        self.assertEqual(self.org.get()["revision"], 1)

    def test_explicit_consent_and_revision_type_are_required(self):
        with self.assertRaises(DataError):
            self.org.create("No consent", 0)
        for revision in (True, -1, "0", None):
            with self.subTest(revision=revision), self.assertRaises(DataError):
                self.org.create("Bad revision", revision, confirmed=True)
        self.assertEqual(self.org.get()["revision"], 0)

    def test_interrupted_metadata_is_invisible_and_next_commit_remains_available(self):
        with patch("organization.os.fsync", side_effect=OSError("isolated write interruption")):
            with self.assertRaises(OSError):
                self.org.create("Interrupted", 0, confirmed=True)
        self.assertEqual(self.org.get()["revision"], 0)
        self.create("Saved")
        self.assertEqual(self.org.get()["folders"][0]["name"], "Saved")
        self.assertEqual(len(list((self.directory / "organization").iterdir())), 2)

    def test_backup_and_isolated_restore_include_current_and_historical_organization(self):
        folder = self.create()
        self.org.move(self.article["id"], folder, 1, confirmed=True)
        self.org.rename(folder, "Revised", 2, confirmed=True)
        archive = RUN / "organization-backup.zip"
        result = create_backup(self.directory, archive)
        self.assertEqual(result["library"]["organization_revision"], 3)
        restored = RUN / "restored-organization"
        restore_backup(archive, restored)
        copy_store = OrganizationStore(ArticleStore(restored))
        self.assertEqual(copy_store.get(), self.org.get())
        self.assertEqual(copy_store.get(1), self.org.get(1))

    def test_duplicate_committed_version_fails_instead_of_silently_losing_folders(self):
        self.create()
        duplicate = self.directory / "organization" / f"v00000001-{uuid.uuid4().hex}"
        duplicate.mkdir()
        (duplicate / "library.json").write_text(json.dumps(self.org.get()), encoding="utf-8")
        (duplicate / "COMMITTED").touch()
        with self.assertRaises(DataError):
            self.org.get()
        with self.assertRaises(DataError):
            validate_library(self.directory)

    def test_http_auth_conflicts_remove_and_corrupt_metadata_readonly_fallback(self):
        with LocalHTTPServer(("127.0.0.1", 0), make_handler(self.articles)) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, payload=None, authorized=True):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                headers = {}
                body = None
                if payload is not None:
                    body = json.dumps(payload)
                    headers["Content-Type"] = "application/json"
                    if authorized:
                        headers.update({"Origin": f"http://127.0.0.1:{server.server_port}", "X-Reader-CSRF": token})
                try:
                    connection.request("POST" if payload is not None else "GET", path, body=body, headers=headers)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            try:
                _, session = request("/api/session")
                token = session["csrf_token"]
                payload = {"name": "HTTP folder", "expected_revision": 0, "confirmed": True}
                self.assertEqual(request("/api/library/folders", payload, False)[0], 403)
                status, created = request("/api/library/folders", payload)
                self.assertEqual(status, 200)
                self.assertEqual(request("/api/library/folders", payload)[0], 409)
                folder = created["folder_id"]
                move = {"folder_id": folder, "expected_revision": 1, "confirmed": True}
                self.assertEqual(request(f"/api/library/articles/{self.article['id']}/move", move)[0], 200)
                remove = {"expected_revision": 2, "confirmed": True}
                self.assertEqual(request(f"/api/library/folders/{folder}/remove", remove)[1]["moved_to_root"], 1)
                _, library = request("/api/library")
                self.assertEqual(library["organization"]["article_folders"], {})
                self.assertEqual(len(library["articles"]), 1)
                latest = self.org.revisions()[3]
                invalid = copy.deepcopy(self.org.get())
                invalid["article_folders"][self.article["id"]] = "missing-folder"
                latest.write_text(json.dumps(invalid), encoding="utf-8")  # Corrupt only this new fixture.
                status, library = request("/api/library")
                self.assertEqual(status, 200)
                self.assertIsNone(library["organization"])
                self.assertIn("organization_error", library)
                self.assertEqual(request(f"/api/articles/{self.article['id']}")[0], 200)
                self.assertEqual(request("/api/library/folders", {**payload, "expected_revision": 3})[0], 422)
            finally:
                server.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
