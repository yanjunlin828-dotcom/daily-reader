"""Stage 5–6 acceptance: only new isolated output directories, never production writes."""
import asyncio
import copy
import hashlib
import json
import subprocess
import sys
import unittest
import uuid
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backup import create_backup, inspect_backup, restore_backup, MANIFEST
from importer import prepare_article
from library_cli import call_tool
from storage import ArticleStore, DataError


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").rglob("*") if p.is_file()}
        cls.directory = ROOT / "output" / f"stage56-{uuid.uuid4().hex}"
        cls.directory.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").rglob("*") if p.is_file()}
        if after != cls.baseline:
            raise AssertionError("Production data changed")
        print(f"Retained isolated acceptance data: {cls.directory}")

    def fixture(self):
        path = self.directory / uuid.uuid4().hex
        path.mkdir()
        (path / "articles").mkdir()
        with (path / "folders.json").open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "folders": [{"id": "economy", "name": "经济"}]}, stream, ensure_ascii=False)
        return path, ArticleStore(path)

    def draft(self, text="An isolated article remains available."):
        return prepare_article({"title": "Acceptance article", "folder_id": "economy", "is_demo": True},
                               raw_text=text, folder_ids={"economy"})["article"]

    def test_backup_restore_all_versions_and_pending_bytes(self):
        path, store = self.fixture()
        article = store.save(self.draft('The rate may fall by 25 basis points.\n\nA "billion" is not a trillion.'), confirmed=True)
        block = article["blocks"][0]
        store.add_translations(article["id"], 1, [{"block_id": block["id"], "original": block["original"], "translation": "利率可能下降 25 个基点。"}], confirmed=True)
        pending = path / "records" / article["id"] / f"v00000003-{uuid.uuid4().hex}"
        pending.mkdir()
        (pending / "article.json").write_bytes(b'{"incomplete":')
        archive = self.directory / f"{uuid.uuid4().hex}.zip"
        saved = create_backup(path, archive)
        self.assertTrue(saved["verified"])
        restored = self.directory / f"restored-{uuid.uuid4().hex}"
        result = restore_backup(archive, restored)
        self.assertTrue(result["verified"])
        recovered = ArticleStore(restored)
        self.assertEqual(recovered.get(article["id"]), store.get(article["id"]))
        self.assertEqual(recovered.get(article["id"], 1), article)
        self.assertEqual((restored / pending.relative_to(path) / "article.json").read_bytes(), b'{"incomplete":')
        self.assertFalse((restored / ".write-lock").exists())

    def test_existing_archive_and_restore_directory_refused(self):
        path, store = self.fixture()
        store.save(self.draft(), confirmed=True)
        archive = self.directory / f"{uuid.uuid4().hex}.zip"
        create_backup(path, archive)
        before = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            create_backup(path, archive)
        self.assertEqual(archive.read_bytes(), before)
        with self.assertRaises(DataError):
            restore_backup(archive, path)
        self.assertEqual(len(store.index()["articles"]), 1)

    def test_corrupted_payload_refused_before_restore(self):
        path, store = self.fixture()
        store.save(self.draft(), confirmed=True)
        archive = self.directory / f"{uuid.uuid4().hex}.zip"
        create_backup(path, archive)
        corrupt = self.directory / f"corrupt-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "x") as target:
            for name in source.namelist():
                target.writestr(name, source.read(name) + (b"tampered" if name == "folders.json" else b""))
        destination = self.directory / f"refused-{uuid.uuid4().hex}"
        with self.assertRaises(DataError):
            restore_backup(corrupt, destination)
        self.assertFalse(destination.exists())

    def test_traversal_duplicate_symlink_and_oversized_archive_refused(self):
        for kind in ("traversal", "duplicate", "symlink", "oversize"):
            archive = self.directory / f"{kind}-{uuid.uuid4().hex}.zip"
            with zipfile.ZipFile(archive, "x") as target:
                name = "../outside.json" if kind == "traversal" else "folders.json"
                info = zipfile.ZipInfo(name)
                if kind == "symlink":
                    info.create_system = 3
                    info.external_attr = (0o120777 << 16)
                target.writestr(info, b"x")
                if kind == "duplicate":
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        target.writestr("folders.json", b"another")
            if kind == "oversize":
                # Alter advertised uncompressed size; do not allocate a zip-bomb payload.
                payload = bytearray(archive.read_bytes())
                offset = payload.index(b"PK\x01\x02")
                payload[offset + 24:offset + 28] = (300 * 1024 * 1024).to_bytes(4, "little")
                # Only this freshly created isolated test fixture is changed.
                with archive.open("r+b") as stream:
                    stream.write(payload)
            with self.subTest(kind=kind), self.assertRaises(DataError):
                inspect_backup(archive)

    def test_readonly_backup_rejects_corrupt_library(self):
        path, store = self.fixture()
        (path / "articles" / "broken.json").write_bytes(b"not-json")
        destination = self.directory / f"{uuid.uuid4().hex}.zip"
        with self.assertRaises(DataError):
            create_backup(path, destination)
        self.assertFalse(destination.exists())

    def test_cli_requires_current_write_confirmation(self):
        path, store = self.fixture()
        request = self.directory / f"request-{uuid.uuid4().hex}.json"
        with request.open("x", encoding="utf-8") as stream:
            json.dump({"article": self.draft()}, stream)
        command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "library_cli.py"), "save_article", "--request", str(request), "--data-dir", str(path)]
        denied = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(denied.returncode, 1)
        self.assertEqual(store.index()["articles"], [])
        saved = subprocess.run(command + ["--confirmed"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertTrue(json.loads(saved.stdout)["saved"])
        self.assertEqual(len(store.index()["articles"]), 1)

    def test_cli_existing_result_prevents_write(self):
        path, store = self.fixture()
        request = self.directory / f"request-{uuid.uuid4().hex}.json"
        request.write_text(json.dumps({"article": self.draft()}), encoding="utf-8")
        output = self.directory / f"result-{uuid.uuid4().hex}.json"
        output.write_text("valuable existing result", encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "library_cli.py"), "save_article", "--request", str(request),
            "--data-dir", str(path), "--confirmed", "--output", str(output)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_text(), "valuable existing result")
        self.assertEqual(store.index()["articles"], [])

    def test_cli_file_result_keeps_raw_input_and_returns_small_summary(self):
        path, store = self.fixture()
        raw = "A long draft remains an unsaved draft.\n\n" * 25
        request = self.directory / f"request-{uuid.uuid4().hex}.json"
        request.write_text(json.dumps({"metadata": {"title": "File result", "folder_id": "economy"}, "raw_text": raw}), encoding="utf-8")
        output = self.directory / f"result-{uuid.uuid4().hex}.json"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "library_cli.py"), "prepare_article", "--request", str(request),
            "--data-dir", str(path), "--output", str(output)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["returned_blocks"], 25)
        self.assertNotIn("article", summary)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["article"]["import_source"]["text"], raw)
        self.assertEqual(store.index()["articles"], [])

    def test_long_article_paging_batches_and_resume_via_real_mcp(self):
        path, store = self.fixture()

        async def scenario():
            raw = "\n\n".join(f"Paragraph {index}: The committee may lower its rate by 25 basis points, not 25 percent. "
                'It said, "The estimate remains uncertain."\nThis ordinary wrap stays in the same paragraph.' for index in range(1, 138))
            prepared = await call_tool("prepare_article", {"metadata": {"title": "Long isolated article", "folder_id": "economy", "is_demo": True}, "raw_text": raw}, path)
            article = prepared["article"]
            await call_tool("save_article", {"article": article, "confirmed": True}, path)
            gathered = []
            for offset in range(0, 137, 17):
                page = await call_tool("get_article", {"article_id": article["id"], "content_version": 1, "block_offset": offset, "block_limit": 17}, path)
                gathered.extend(page["article"]["blocks"])
                self.assertEqual(page["total_blocks"], 137)
                self.assertEqual(page["has_more"], offset + 17 < 137)
            self.assertEqual(gathered, article["blocks"])
            version = 1
            # Simulate a first conversation ending after one batch, then resume using disk state.
            for offset in range(0, 137, 23):
                current = await call_tool("get_article", {"article_id": article["id"], "block_offset": offset, "block_limit": 23}, path)
                self.assertEqual(current["article"]["content_version"], version)
                batch = [{"block_id": b["id"], "original": b["original"], "translation": f"第 {offset + i + 1} 段：委员会可能下调利率 25 个基点，而不是 25%。估算仍不确定。"}
                    for i, b in enumerate(current["article"]["blocks"]) if b["translation"] is None]
                result = await call_tool("add_translations", {"article_id": article["id"], "expected_version": version, "translations": batch, "confirmed": True}, path)
                version = result["article"]["content_version"]
            final = ArticleStore(path).get(article["id"])
            self.assertEqual(final["translation_status"], "complete")
            self.assertEqual(len(final["blocks"]), 137)
            self.assertEqual(final["import_source"], article["import_source"])
            self.assertEqual([b["original"] for b in final["blocks"]], [b["original"] for b in article["blocks"]])
            self.assertEqual(store.get(article["id"], 1), article)
            self.long_article_id = article["id"]
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
