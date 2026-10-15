"""Local MCP client fallback for chats whose native tool catalogue has not reloaded."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent
TOOLS = {"list_articles", "get_article", "prepare_article", "save_article", "add_translations",
         "prepare_reading_guide", "get_reading_guide", "save_reading_guide",
         "update_article_blocks", "delete_article", "restore_article"}
WRITES = {"save_article", "add_translations", "save_reading_guide", "update_article_blocks", "delete_article", "restore_article"}


def inside(path, *, must_exist=True):
    resolved = Path(path).resolve(strict=must_exist)
    if not resolved.is_relative_to(ROOT):
        raise ValueError("路径必须位于英语新闻阅读库项目内")
    return resolved


async def call_tool(name, arguments, data_dir):
    parameters = StdioServerParameters(command=sys.executable,
        args=["-B", "-u", str(ROOT / "mcp_server.py"), "--data-dir", str(data_dir)],
        cwd=str(ROOT), env={"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client:
            await client.initialize()
            result = await client.call_tool(name, arguments)
            if result.isError:
                message = "\n".join(item.text for item in result.content if item.type == "text")
                raise ValueError(message or "MCP 工具拒绝请求")
            if result.structuredContent is not None:
                return result.structuredContent
            return json.loads(next(item.text for item in result.content if item.type == "text"))


def main():
    parser = argparse.ArgumentParser(description="调用阅读库 MCP；读取/准备不写文章，写操作需 --confirmed")
    parser.add_argument("tool", choices=sorted(TOOLS))
    parser.add_argument("--request", type=Path, help="UTF-8 参数 JSON；省略时从标准输入读取")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--confirmed", action="store_true", help="仅在用户明确授权当前写操作后使用")
    parser.add_argument("--output", type=Path, help="可选：只创建新的结果文件，不覆盖")
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    output_stream = None
    try:
        data_dir = inside(args.data_dir)
        if args.request:
            request = inside(args.request)
            if request.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("请求超过 4 MiB 上限")
            raw = request.read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read(4 * 1024 * 1024 + 1)
            if len(raw.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("请求超过 4 MiB 上限")
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        if "confirmed" in arguments:
            raise ValueError("请求中不要放 confirmed；写操作使用显式 --confirmed")
        if args.tool in WRITES:
            if not args.confirmed:
                raise ValueError("写操作需要当前用户授权及 --confirmed；未调用写工具")
            arguments["confirmed"] = True
        elif args.confirmed:
            raise ValueError("读取和准备无需 --confirmed")
        # Reserve an exclusive result before a write tool: existing output must not trigger a save.
        if args.output:
            output = inside(args.output, must_exist=False)
            if not output.parent.is_dir():
                raise ValueError("结果文件的父目录必须已经存在")
            output_stream = output.open("x", encoding="utf-8", newline="\n")
        result = asyncio.run(call_tool(args.tool, arguments, data_dir))
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if output_stream:
            output_stream.write(encoded + "\n")
            output_stream.flush()
            article = result.get("article", {})
            guide = result.get("guide") or {}
            lifecycle = result.get("lifecycle") or {}
            print(json.dumps({"output": str(output), "tool": args.tool, "saved": result.get("saved", False),
                "article_id": article.get("id") or result.get("article_id") or guide.get("article_id"), "content_version": article.get("content_version") or lifecycle.get("content_version"),
                "lifecycle": result.get("lifecycle"),
                "guide_version": guide.get("guide_version"),
                "returned_blocks": len(article.get("blocks") or guide.get("blocks") or result.get("blocks") or []),
                "warnings": result.get("warnings", [])}, ensure_ascii=False))
        else:
            print(encoded)
        return 0
    except (ValueError, OSError, StopIteration) as exc:
        # JSON parse errors must not echo the article body.
        message = "参数不是有效 JSON" if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(json.dumps({"error": message, "note": "若写操作的结果未收到，请先读取文章库确认，不要盲目重试。"}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"error": "本地 MCP 连接或响应失败。请检查解释器、SDK 和数据目录。",
            "note": "本次结果未确认；先回读文章库，不要盲目重试写操作。"}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if output_stream:
            output_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
