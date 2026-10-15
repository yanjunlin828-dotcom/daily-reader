"""Deletion/edit acceptance uses new retained fictional libraries, never formal data."""
import asyncio
import copy
import json
import subprocess
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from article_lifecycle import ArticleLifecycle
from backup import create_backup, restore_backup, validate_library
from importer import prepare_article
from organization import OrganizationStore
from reading_guide import ReadingGuideStore
from server import LocalHTTPServer, make_handler
from storage import ArticleStore, ConflictError, DataError

RUN = ROOT / "output" / f"article-maintenance-{uuid.uuid4().hex}"


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = RUN / self._testMethodName
        (self.directory / "articles").mkdir(parents=True)
        (self.directory / "folders.json").write_text(json.dumps({"schema_version": 1,
            "folders": [{"id": "economy", "name": "经济"}]}), encoding="utf-8")
        self.store = ArticleStore(self.directory)
        draft = prepare_article({"title": "Fictional maintenance article", "folder_id": "economy", "is_demo": True},
            raw_text="A fictional reader saves the first paragraph.\n\nThe second paragraph stays unchanged.", folder_ids={"economy"})["article"]
        for block in draft["blocks"]:
            block["translation"] = "这是独立测试的原有译文。"
        draft["translation_status"] = "complete"
        self.article = self.store.save(draft, confirmed=True)
        self.id = self.article["id"]
        self.guides = ReadingGuideStore(self.store)
        self.guide_blocks = [{"id": "g001", "type": "paragraph", "text": "这是独立测试的中文导读。"}]
        self.guides.save(self.id, 1, self.guide_blocks, confirmed=True)
        self.org = OrganizationStore(self.store)
        self.folder = self.org.create("测试目录", 0, confirmed=True)["folder_id"]
        self.org.move(self.id, self.folder, 1, confirmed=True)
        self.baseline = {p.relative_to(self.directory): p.read_bytes() for p in self.directory.rglob("*")
                         if p.is_file() and p.name != ".write-lock"}

    def tearDown(self):
        for name, payload in self.baseline.items():
            self.assertEqual((self.directory / name).read_bytes(), payload, name)

    def update(self, after, *, version=1, before=None):
        block = copy.deepcopy(before or self.article["blocks"][0])
        return self.store.update_blocks(self.id, version, [{"block_id": block["id"], "before": block, "after": after}], confirmed=True)

    def delete(self, *, version=1, state=0, confirmed=True, title=None):
        return self.store.delete(self.id, version, state, title or self.article["title"], confirmed=confirmed)

    def restore(self, *, version=1, state=1):
        return self.store.restore(self.id, version, state, self.article["title"], confirmed=True)

    def test_delete_restore_preserve_all_files_and_directory_membership(self):
        deleted = self.delete()
        self.assertTrue(deleted["lifecycle"]["deleted"])
        self.assertEqual(self.store.index()["articles"], [])
        self.assertTrue(self.store.index(include_deleted=True)["articles"][0]["deleted"])
        self.assertEqual(self.store.get(self.id, include_deleted=True), self.article)
        with self.assertRaises(ConflictError) as refused:
            self.store.get(self.id)
        self.assertEqual(refused.exception.code, "article_deleted")
        self.restore()
        self.assertEqual(self.store.get(self.id), self.article)
        self.assertFalse(self.store.index()["articles"][0]["deleted"])
        self.assertEqual(self.org.get()["article_folders"][self.id], self.folder)
        self.assertEqual(self.guides.get(self.id)["blocks"], self.guide_blocks)

    def test_deleted_legacy_article_can_restore_without_moving_its_file(self):
        legacy = copy.deepcopy(self.article)
        legacy.update(id="legacy-example", title="Legacy fictional article", schema_version=1)
        legacy.pop("import_source")
        with (self.directory / "articles" / "legacy-example.json").open("x", encoding="utf-8") as stream:
            json.dump(legacy, stream)
        self.store.delete(legacy["id"], 1, 0, legacy["title"], confirmed=True)
        self.store.restore(legacy["id"], 1, 1, legacy["title"], confirmed=True)
        self.assertEqual(self.store.get(legacy["id"]), legacy)
        self.assertFalse((self.directory / "records" / legacy["id"]).exists())

    def test_delete_requires_authorization_precise_title_and_versions(self):
        for fields in ({"confirmed": False}, {"confirmed": "true"}, {"version": True}, {"state": False},
                       {"version": 2}, {"state": 9}, {"title": "Another article"}):
            with self.assertRaises(DataError):
                self.delete(**fields)
        self.assertEqual(self.store.index()["articles"][0]["id"], self.id)
        self.assertFalse((self.directory / "trash").exists())

    def test_uncertain_retry_does_not_redelete_after_restore(self):
        self.delete()
        self.assertTrue(self.delete()["unchanged"])
        self.restore()
        self.assertTrue(self.restore()["unchanged"])
        with self.assertRaises(ConflictError) as refused:
            self.delete()
        self.assertEqual(refused.exception.code, "state_conflict")
        self.assertFalse(self.store.lifecycle(self.id)["deleted"])

    def test_deleted_article_refuses_edits_translations_guide_save_and_duplicate_import(self):
        self.delete()
        with self.assertRaises(ConflictError):
            self.update({"translation": "不允许修改回收区。"})
        with self.assertRaises(ConflictError):
            block = self.article["blocks"][0]
            self.store.add_translations(self.id, 1, [{"block_id": block["id"], "original": block["original"],
                "translation": block["translation"]}], confirmed=True)
        with self.assertRaises(ConflictError):
            self.guides.save(self.id, 1, self.guide_blocks, confirmed=True)
        duplicate = copy.deepcopy(self.article)
        duplicate["id"] = "different-id"
        with self.assertRaises(ConflictError) as refused:
            self.store.save(duplicate, confirmed=True)
        self.assertEqual(refused.exception.code, "duplicate_article")

    def test_deleted_article_returns_to_root_if_its_folder_has_since_been_removed(self):
        self.delete()
        self.org.remove(self.folder, 2, confirmed=True)
        self.restore()
        self.assertNotIn(self.id, self.org.get()["article_folders"])

    def test_translation_correction_creates_history_and_keeps_guide_valid(self):
        changed = self.update({"translation": "这是明确校正后的译文。"})["article"]
        self.assertEqual(changed["content_version"], 2)
        self.assertEqual(changed["blocks"][1], self.article["blocks"][1])
        self.assertEqual(changed["import_source"], self.article["import_source"])
        self.assertEqual(self.store.get(self.id, 1), self.article)
        self.assertFalse(self.guides.status(self.id)["stale"])
        self.assertTrue(self.update({"translation": changed["blocks"][0]["translation"]}, version=2,
                                    before=changed["blocks"][0])["unchanged"])
        self.assertEqual(max(self.store._snapshots(self.id)), 2)

    def test_original_change_requires_explicit_translation_and_supports_new_guide(self):
        with self.assertRaises(DataError):
            self.update({"original": "A corrected fictional paragraph."})
        changed = self.update({"original": "A corrected fictional paragraph.", "translation": None})["article"]
        self.assertEqual(changed["translation_status"], "partial")
        self.assertEqual(self.guides.status(self.id), {"guide": None, "stale": True})
        self.assertEqual(self.guides.versions(self.id)[1]["blocks"], self.guide_blocks)
        self.assertTrue(self.guides.prepare(self.id, "这是新原文的中文导读。")["guide_stale"])
        new_blocks = [{"id": "g001", "type": "paragraph", "text": "这是新原文的中文导读。"}]
        current = self.guides.save(self.id, 2, new_blocks, confirmed=True)["guide"]
        self.assertEqual(current["guide_version"], 2)
        self.assertFalse(self.guides.status(self.id)["stale"])
        self.assertEqual(len(self.guides.versions(self.id)), 2)

    def test_batch_is_atomic_and_rejects_stale_before_duplicate_ids_or_invalid_changes(self):
        first, second = copy.deepcopy(self.article["blocks"])
        entries = [{"block_id": first["id"], "before": first, "after": {"translation": "第一段校改。"}},
                   {"block_id": second["id"], "before": {**second, "original": "Wrong original."}, "after": {"translation": "第二段校改。"}}]
        with self.assertRaises(ConflictError):
            self.store.update_blocks(self.id, 1, entries, confirmed=True)
        for change in ({"id": "new-id"}, {"original": "" ,"translation": None}, {"translation": ""},
                       {"type": "unsupported"}, {}):
            with self.assertRaises(DataError):
                self.update(change)
        with self.assertRaises(DataError):
            self.store.update_blocks(self.id, 1, [entries[0], entries[0]], confirmed=True)
        with self.assertRaises(DataError):
            self.store.update_blocks(self.id, 1, entries[:1], confirmed=False)
        self.assertEqual(self.store.get(self.id), self.article)

    def test_concurrent_edit_and_delete_do_not_lose_either_side_silently(self):
        def edit():
            try:
                self.update({"translation": "并发修改。"})
                return "saved"
            except ConflictError:
                return "conflict"
        def remove():
            try:
                self.delete()
                return "saved"
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in (pool.submit(edit), pool.submit(remove))]
        self.assertEqual(sorted(results), ["conflict", "saved"])

    def test_interrupted_deletion_commit_is_not_visible(self):
        with patch("article_lifecycle.os.fsync", side_effect=OSError("isolated interruption")):
            with self.assertRaises(OSError):
                self.delete()
        self.assertFalse(self.store.lifecycle(self.id)["deleted"])
        self.delete()
        self.assertTrue(self.store.lifecycle(self.id)["deleted"])

    def test_backup_restore_preserves_deleted_articles_and_stale_guide_history(self):
        self.update({"original": "A changed fictional original.", "translation": None})
        self.delete(version=2)
        archive = RUN / (uuid.uuid4().hex + ".zip")
        result = create_backup(self.directory, archive)
        self.assertEqual(result["library"]["articles"], 0)
        self.assertEqual(result["library"]["article_states"], {self.id: 1})
        recovered = RUN / ("restored-" + uuid.uuid4().hex)
        restore_backup(archive, recovered)
        store = ArticleStore(recovered)
        self.assertEqual(store.index()["articles"], [])
        self.assertEqual(store.get(self.id, 1, include_deleted=True), self.article)
        self.assertEqual(ReadingGuideStore(store).status(self.id, include_deleted=True), {"guide": None, "stale": True})
        store.restore(self.id, 2, 1, self.article["title"], confirmed=True)
        self.assertEqual(store.get(self.id)["content_version"], 2)

    def test_cli_write_without_confirmation_is_refused_before_mcp_call(self):
        request = self.directory / "delete-request.json"
        request.write_text(json.dumps({"article_id": self.id, "expected_version": 1,
            "expected_state_version": 0, "expected_title": self.article["title"]}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "library_cli.py"), "delete_article",
            "--data-dir", str(self.directory), "--request", str(request)], capture_output=True, text=True,
            encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--confirmed", result.stderr)
        self.assertFalse((self.directory / "trash").exists())

    def test_real_mcp_edit_delete_restore_and_http_visibility(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def scenario(port):
            parameters = StdioServerParameters(command=sys.executable,
                args=["-B", "-u", str(ROOT / "mcp_server.py"), "--data-dir", str(self.directory)], cwd=str(ROOT),
                env={"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client:
                    await client.initialize()
                    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                    self.assertEqual(len(tools), 11)
                    self.assertTrue(tools["delete_article"].annotations.destructiveHint)
                    self.assertTrue(tools["update_article_blocks"].annotations.destructiveHint)
                    async def call(name, arguments, error=False):
                        result = await client.call_tool(name, arguments)
                        self.assertEqual(bool(result.isError), error, name)
                        if not error:
                            return result.structuredContent or json.loads(result.content[0].text)
                    change = {"article_id": self.id, "expected_version": 1, "updates": [{"block_id": self.article["blocks"][0]["id"],
                              "before": self.article["blocks"][0], "after": {"original": "A fictional corrected line.", "translation": None}}]}
                    await call("update_article_blocks", change, True)
                    await call("update_article_blocks", {**change, "confirmed": "true"}, True)
                    await call("update_article_blocks", {**change, "expected_version": True, "confirmed": True}, True)
                    updated = await call("update_article_blocks", {**change, "confirmed": True})
                    self.assertTrue(updated["reading_guide_stale"])
                    self.assertEqual(updated["article"]["content_version"], 2)
                    await call("update_article_blocks", {**change, "confirmed": True}, True)
                    status, payload = http_get(port, f"/api/articles/{self.id}")
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["reading_guide_stale"])
                    self.assertIsNone(payload["reading_guide"])
                    removal = {"article_id": self.id, "expected_version": 2, "expected_state_version": 0,
                               "expected_title": self.article["title"]}
                    await call("delete_article", removal, True)
                    await call("delete_article", {**removal, "confirmed": "true"}, True)
                    await call("delete_article", {**removal, "expected_state_version": False, "confirmed": True}, True)
                    await call("delete_article", {**removal, "confirmed": True})
                    self.assertEqual((await call("list_articles", {}))["total"], 0)
                    self.assertEqual((await call("list_articles", {"include_deleted": True}))["total"], 1)
                    await call("get_article", {"article_id": self.id}, True)
                    readback = await call("get_article", {"article_id": self.id, "include_deleted": True})
                    self.assertTrue(readback["lifecycle"]["deleted"])
                    self.assertEqual(http_get(port, "/api/library")[1]["articles"], [])
                    self.assertEqual(http_get(port, f"/api/articles/{self.id}")[0], 410)
                    recovery = {**removal, "expected_state_version": 1, "confirmed": True}
                    await call("restore_article", {k: v for k, v in recovery.items() if k != "confirmed"}, True)
                    await call("restore_article", recovery)
                    self.assertEqual(http_get(port, f"/api/articles/{self.id}")[0], 200)
                    self.assertFalse((await call("get_article", {"article_id": self.id}))["lifecycle"]["deleted"])
                    await call("delete_article", {**removal, "confirmed": True}, True)
        def http_get(port, path):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        with LocalHTTPServer(("127.0.0.1", 0), make_handler(self.store)) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                asyncio.run(scenario(server.server_port))
            finally:
                server.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    print("Retained isolated fixtures:", RUN)
    unittest.main(verbosity=2)
