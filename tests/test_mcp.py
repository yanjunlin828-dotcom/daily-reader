"""Real SDK STDIO client/server integration; writes only new isolated output fixtures."""
import asyncio
import json
import sys
import threading
import uuid
from datetime import timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from server import make_handler
from storage import ArticleStore


async def main():
    directory = ROOT / "output" / f"mcp-test-{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    (directory / "articles").mkdir()
    with (directory / "folders.json").open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "folders": [{"id": "economy", "name": "经济"}]}, stream, ensure_ascii=False)
    store = ArticleStore(directory)
    http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    parameters = StdioServerParameters(command=sys.executable,
        args=["-B", "-u", str(ROOT / "mcp_server.py"), "--data-dir", str(directory)],
        cwd=str(ROOT), env={"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    checks = []
    try:
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "english-news-library"
                assert "confirmed=true" in initialized.instructions
                checks.append("SDK initialize + server instructions")
                tools = (await client.list_tools()).tools
                assert {tool.name for tool in tools} == {"prepare_article", "save_article", "list_articles", "get_article", "add_translations",
                    "prepare_reading_guide", "get_reading_guide", "save_reading_guide"}
                assert next(tool for tool in tools if tool.name == "save_article").annotations.readOnlyHint is False
                assert next(tool for tool in tools if tool.name == "prepare_article").annotations.readOnlyHint is True
                checks.append("tool schemas and write annotations")

                async def call(name, arguments, *, error=False):
                    result = await client.call_tool(name, arguments)
                    assert bool(result.isError) is error, f"Unexpected error status: {name}"
                    if error:
                        return result
                    return result.structuredContent or json.loads(result.content[0].text)

                prepared = await call("prepare_article", {"metadata": {"title": "MCP isolated article", "folder_id": "economy"},
                    "raw_text": "A reader saves a short article.\n\nThe original remains available.\n\nTranslations arrive in batches."})
                article = prepared["article"]
                assert len(article["blocks"]) == 3
                await call("save_article", {"article": article}, error=True)
                await call("save_article", {"article": article, "confirmed": "true"}, error=True)
                assert not store.index()["articles"]
                saved = await call("save_article", {"article": article, "confirmed": True})
                assert saved["saved"]
                checks.append("prepare -> explicit save -> disk read")
                await call("save_article", {"article": article, "confirmed": True}, error=True)
                listed = await call("list_articles", {"query": "isolated", "limit": 1})
                assert listed["total"] == 1 and listed["articles"][0]["id"] == article["id"]
                page = await call("get_article", {"article_id": article["id"], "block_limit": 1})
                assert page["has_more"] and page["total_blocks"] == 3
                assert page["import_source_available"] and "import_source" not in page["article"]
                assert page["article"]["blocks"][0] == article["blocks"][0]
                checks.append("duplicate refusal, search, block pagination")
                first = article["blocks"][0]
                translated = await call("add_translations", {"article_id": article["id"], "expected_version": 1,
                    "translations": [{"block_id": first["id"], "original": first["original"], "translation": "读者保存了一篇短文。"}], "confirmed": True})
                assert translated["article"]["content_version"] == 2
                second = article["blocks"][1]
                batch = {"article_id": article["id"], "expected_version": 1,
                    "translations": [{"block_id": second["id"], "original": second["original"], "translation": "原文仍然可供查看。"}], "confirmed": True}
                await call("add_translations", batch, error=True)
                batch["expected_version"] = 2
                translated = await call("add_translations", batch)
                assert translated["article"]["content_version"] == 3
                history = await call("get_article", {"article_id": article["id"], "content_version": 1})
                assert history["article"]["blocks"][0]["translation"] is None
                checks.append("batch translations, stale version refusal, immutable history")
                before_guide = store.get(article["id"])
                assert (await call("get_reading_guide", {"article_id": article["id"]}))["guide"] is None
                guide_draft = await call("prepare_reading_guide", {"article_id": article["id"],
                    "raw_text": "# 阅读线索\n\n这是一份隔离测试的中文导读，说明文章如何保存原文，并把译文按段落补齐。\n\n- 先保留英文原文\n- 再理解中文译文"})
                assert [b["type"] for b in guide_draft["blocks"]] == ["heading", "paragraph", "list"]
                guide_request = {key: guide_draft[key] for key in ("article_id", "expected_content_version", "blocks")}
                await call("save_reading_guide", guide_request, error=True)
                await call("save_reading_guide", {**guide_request, "confirmed": "true"}, error=True)
                await call("save_reading_guide", {**guide_request, "expected_content_version": 1, "confirmed": True}, error=True)
                guide_saved = await call("save_reading_guide", {**guide_request, "confirmed": True})
                assert guide_saved["saved"] and not guide_saved["unchanged"]
                assert (await call("save_reading_guide", {**guide_request, "confirmed": True}))["unchanged"]
                guide_read = await call("get_reading_guide", {"article_id": article["id"]})
                assert guide_read["guide"]["blocks"] == guide_draft["blocks"]
                await call("save_reading_guide", {**guide_request, "blocks": [{"id": "g001", "type": "paragraph", "text": "不同导读不可覆盖。"}], "confirmed": True}, error=True)
                assert store.get(article["id"]) == before_guide
                from backup import validate_library
                assert validate_library(directory)["guides"] == {article["id"]: 1}
                checks.append("Chinese guide: preview, strict consent/version, save, readback, retry, no overwrite, unchanged article, backup visibility")
                connection = HTTPConnection("127.0.0.1", http.server_port, timeout=3)
                try:
                    connection.request("GET", f"/api/articles/{article['id']}")
                    response = connection.getresponse()
                    assert response.status == 200
                    current = json.loads(response.read())
                    assert current["content_version"] == 3
                    assert current["blocks"][1]["translation"] == "原文仍然可供查看。"
                    assert current["reading_guide"]["blocks"] == guide_draft["blocks"]
                finally:
                    connection.close()
                checks.append("HTTP reads changes committed by separate MCP process")
                await call("unknown_tool", {}, error=True)
                checks.append("unknown tool returns protocol error without crashing")
    finally:
        http.shutdown(); http.server_close(); thread.join(timeout=3)
    print(json.dumps({"passed": checks, "retained_fixture": str(directory)}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
