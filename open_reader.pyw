"""Desktop entry: reuse a healthy local reader or start it hidden, then open the browser."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}"


class LaunchError(RuntimeError):
    pass


def service_state(port=PORT):
    """Never follow redirects or proxies; inspect only the intended loopback endpoint."""
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        def read(path):
            connection.request("GET", path)
            response = connection.getresponse()
            payload = response.read(4 * 1024 * 1024 + 1)
            if response.status != 200 or len(payload) > 4 * 1024 * 1024:
                raise ValueError("Invalid reader response")
            return json.loads(payload)
        health = read("/api/health")
        if health != {"status": "ok", "schema_version": 2, "read_only": False}:
            raise ValueError("Not the current reader")
        library = read("/api/library")
        if (not isinstance(library, dict) or library.get("schema_version") != 1
                or not all(isinstance(library.get(name), list) for name in ("folders", "articles", "warnings"))):
            raise ValueError("Reader library unavailable")
        return "ready"
    except (OSError, ValueError, HTTPException):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return "occupied"
        except OSError:
            return "stopped"
    finally:
        connection.close()


def start_background(port=PORT, data_dir=None):
    output = ROOT / "output"
    if output.exists() and not output.resolve().is_relative_to(ROOT):
        raise LaunchError("日志目录指向项目外部，已停止启动。")
    output.mkdir(exist_ok=True)
    log = output / f"launcher-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.log"
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    arguments = [str(executable), "-B", "-X", "utf8", str(ROOT / "server.py"), "--port", str(port)]
    if data_dir is not None:
        data_dir = Path(data_dir).resolve(strict=True)
        if not data_dir.is_relative_to(ROOT):
            raise LaunchError("数据目录必须在阅读库项目内。")
        arguments.extend(["--data-dir", str(data_dir)])
    with log.open("xb") as stream:
        process = subprocess.Popen(arguments,
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return process, log


def launch_reader():
    state = service_state()
    if state == "occupied":
        raise LaunchError("8765 端口已被其他服务占用，或阅读库暂时不能读取。请检查原启动窗口；没有结束任何程序。")
    started = False
    if state == "stopped":
        process, log = start_background()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = service_state()
            if state == "ready":
                started = True
                break
            if process.poll() is not None:
                raise LaunchError(f"阅读库没有成功启动。请查看日志：\n{log}")
            time.sleep(0.2)
        else:
            raise LaunchError(f"启动等待超时，未确认网页可用。请查看日志：\n{log}")
    if not webbrowser.open(URL, new=2):
        raise LaunchError(f"阅读库已运行，但浏览器没有打开。请手动访问：\n{URL}")
    return {"url": URL, "started_service": started, "ready": True}


def main():
    parser = argparse.ArgumentParser(description="一键打开英语新闻阅读库")
    parser.add_argument("--check", action="store_true", help="只读检查，不启动或打开浏览器")
    args = parser.parse_args()
    if args.check:
        state = service_state()
        if sys.stdout:
            print(json.dumps({"state": state, "url": URL}, ensure_ascii=False))
        return 0 if state == "ready" else 1
    try:
        launch_reader()
        return 0
    except (LaunchError, OSError) as exc:
        message = str(exc) if isinstance(exc, LaunchError) else "启动文件或浏览器无法访问，请检查项目目录和 Python。"
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "英语新闻阅读库", 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
