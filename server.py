"""Local-only article HTTP service. Run: python -B server.py"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from storage import ArticleStore, DataError, ConflictError
from importer import prepare_article
from reading_guide import ReadingGuideStore
from organization import OrganizationStore
from dictionary import DictionaryStore, DictionaryUnavailable, DEFAULT_DATABASE

ROOT = Path(__file__).resolve().parent
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/import.js": ("import.js", "text/javascript; charset=utf-8"),
          "/import.css": ("import.css", "text/css; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/fonts.css": ("fonts.css", "text/css; charset=utf-8"),
          "/refinement.css": ("refinement.css", "text/css; charset=utf-8"),
          "/motion.js": ("motion.js", "text/javascript; charset=utf-8"),
          "/library.js": ("library.js", "text/javascript; charset=utf-8"),
          "/library.css": ("library.css", "text/css; charset=utf-8"),
          "/dictionary.js": ("dictionary.js", "text/javascript; charset=utf-8"),
          "/dictionary.css": ("dictionary.css", "text/css; charset=utf-8"),
          "/fonts/source-han-serif-cn-vf.woff2": ("fonts/source-han-serif-cn-vf.woff2", "font/woff2"),
          "/fonts/source-han-sans-cn-vf.woff2": ("fonts/source-han-sans-cn-vf.woff2", "font/woff2"),
          "/fonts/newsreader-latin-500.woff2": ("fonts/newsreader-latin-500.woff2", "font/woff2"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two processes to bind the same endpoint.
    # Fail clearly instead of letting an old and new service answer at random.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_handler(store, *, dictionary_path=DEFAULT_DATABASE):
    csrf_token = secrets.token_urlsafe(32)
    guides = ReadingGuideStore(store)
    organization = OrganizationStore(store)
    dictionary = DictionaryStore(dictionary_path)
    class Handler(BaseHTTPRequestHandler):
        def _allowed_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def _send(self, status, body, content_type="application/json; charset=utf-8", *, head=False, cache_control="no-store"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _get(self, *, head=False):
            if not self._allowed_host():
                self._send(403, {"error": "请使用本机地址打开阅读库。"}, head=head)
                return
            path = urlsplit(self.path).path
            try:
                if path in ASSETS:
                    filename, content_type = ASSETS[path]
                    # Fonts are unchanged official assets; keep them in the browser's private cache.
                    cache = "private, max-age=86400" if content_type == "font/woff2" else "no-store"
                    self._send(200, (ROOT / "web" / filename).read_bytes(), content_type, head=head, cache_control=cache)
                elif path == "/api/dictionary/lookup":
                    try:
                        if len(self.path) > 1024:
                            raise ValueError("查词请求过长。")
                        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=4)
                        if set(params) != {"word"} or len(params["word"]) != 1:
                            raise ValueError("请提供一个英文单词。")
                        result = dictionary.lookup(params["word"][0])
                    except ValueError as exc:
                        self._send(400, {"error": str(exc)}, head=head)
                    except DictionaryUnavailable:
                        self._send(503, {"error": "本地词库未安装或无法读取，请检查 resources/ecdict 中的词库文件。"}, head=head)
                    else:
                        self._send(200, result, head=head)
                elif path == "/api/library":
                    index = store.index()
                    try:
                        index["organization"] = organization.get()
                    except (DataError, OSError):
                        index["organization"] = None
                        index["organization_error"] = "目录记录暂时无法读取，文章仍可阅读；请检查数据后再整理。"
                    self._send(200, index, head=head)
                elif path == "/api/session":
                    self._send(200, {"csrf_token": csrf_token}, head=head)
                elif path.startswith("/api/articles/"):
                    article = store.get(path.removeprefix("/api/articles/"))
                    try:
                        guide_status = guides.status(article["id"])
                        article["reading_guide"] = guide_status["guide"]
                        article["reading_guide_stale"] = guide_status["stale"]
                    except (DataError, OSError):
                        article["reading_guide"] = None
                        article["reading_guide_error"] = True
                    self._send(200, article, head=head)
                elif path == "/api/health":
                    self._send(200, {"status": "ok", "schema_version": 2, "read_only": False}, head=head)
                else:
                    self._send(404, {"error": "这个页面或文章不存在。"}, head=head)
            except ConflictError as exc:
                self._send(410 if exc.code == "article_deleted" else 409,
                           {"error": str(exc), "code": exc.code}, head=head)
            except FileNotFoundError:
                self._send(404, {"error": "文章或配置文件不存在，请检查本地数据目录。"}, head=head)
            except (DataError, OSError, ValueError):
                self._send(422, {"error": "文章数据无法读取，请检查 JSON 格式、段落编号和翻译状态。"}, head=head)

        def do_GET(self):
            self._get()

        def do_HEAD(self):
            self._get(head=True)

        def do_POST(self):
            path = urlsplit(self.path).path
            folder_action = re.fullmatch(r"/api/library/folders/([a-z0-9-]{1,80})/(rename|remove)", path)
            move_action = re.fullmatch(r"/api/library/articles/([a-z0-9-]{1,80})/move", path)
            if path not in ("/api/prepare", "/api/articles", "/api/library/folders") and not folder_action and not move_action:
                self._send(405, {"error": "此接口不支持写入。"})
                return
            origin = self.headers.get("Origin")
            if (not self._allowed_host() or origin != f"http://{self.headers.get('Host')}"
                    or not secrets.compare_digest(self.headers.get("X-Reader-CSRF", ""), csrf_token)):
                self._send(403, {"error": "录入会话已失效，请保留输入并重试。"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                self._send(415, {"error": "录入内容必须使用 JSON 请求。"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4 * 1024 * 1024:
                    self._send(413, {"error": "录入请求为空或超过 4 MiB。"})
                    return
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DataError("请求必须是对象")
                if path == "/api/library/folders" or folder_action or move_action:
                    allowed = {"expected_revision", "confirmed"}
                    if path == "/api/library/folders" or (folder_action and folder_action[2] == "rename"):
                        allowed.add("name")
                    if move_action:
                        allowed.add("folder_id")
                    if set(payload) != allowed:
                        raise DataError("文件夹操作的请求字段不正确")
                    options = {"expected_revision": payload["expected_revision"], "confirmed": payload["confirmed"]}
                    if path == "/api/library/folders":
                        result = organization.create(payload["name"], **options)
                    elif move_action:
                        result = organization.move(move_action[1], payload["folder_id"], **options)
                    elif folder_action[2] == "rename":
                        result = organization.rename(folder_action[1], payload["name"], **options)
                    else:
                        result = organization.remove(folder_action[1], **options)
                    self._send(200, result)
                elif path == "/api/prepare":
                    allowed = {"metadata", "raw_text", "input_format", "split_mode", "blocks"}
                    if set(payload) - allowed or "metadata" not in payload:
                        raise DataError("预览请求字段不正确")
                    result = prepare_article(**payload, folder_ids={folder["id"] for folder in store.folders()})
                    self._send(200, result)
                else:
                    if set(payload) != {"article", "confirmed"}:
                        raise DataError("保存请求字段不正确")
                    article = store.save(payload["article"], confirmed=payload["confirmed"])
                    self._send(201, {"article": article, "saved": True})
            except ConflictError as exc:
                self._send(409, {"error": str(exc), "code": exc.code, "article_id": exc.article_id})
            except FileNotFoundError:
                self._send(404, {"error": "文章已不存在，请刷新文章库后再试。"})
            except (DataError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                self._send(422, {"error": str(exc) if isinstance(exc, DataError) else "请求格式无效，请检查内容。"})
            except OSError:
                self._send(503, {"error": "本地保存暂时失败。输入仍在，未完成文章不会发布；请检查磁盘和目录权限。"})

        def _unsupported(self):
            self._send(405, {"error": "不支持覆盖或删除文章。"})

        do_PUT = _unsupported
        do_PATCH = _unsupported
        do_DELETE = _unsupported

        def log_message(self, format, *args):
            # Do not echo user-supplied paths or headers to the terminal.
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description="英语新闻阅读库：本地服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口需在 1024–65535 之间")
    try:
        data_dir = args.data_dir.resolve(strict=True)
        if not data_dir.is_relative_to(ROOT):
            parser.error("数据目录必须位于英语新闻阅读库项目内")
        store = ArticleStore(data_dir)
        library = store.index()
        with LocalHTTPServer(("127.0.0.1", args.port), make_handler(store)) as server:
            print(f"英语新闻阅读库：http://127.0.0.1:{args.port}", flush=True)
            print(f"已读取 {len(library['articles'])} 篇文章；仅绑定本机；按 Ctrl+C 停止。", flush=True)
            if library["warnings"]:
                print("提示：部分文章无法读取，请检查数据文件。", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\n服务已停止。已保存的文章和历史版本仍在本地。", flush=True)
    except (DataError, OSError) as exc:
        print("启动失败：请检查数据格式和端口占用；没有修改文章文件。", flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
