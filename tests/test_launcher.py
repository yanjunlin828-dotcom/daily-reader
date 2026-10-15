"""Desktop launcher behavior checks; no real browser opens or production process changes."""
import importlib.machinery
import importlib.util
import json
import sys
import unittest
import uuid
import time
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("reader_launcher", str(ROOT / "open_reader.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_real_hidden_server_starts_and_answers_with_isolated_data(self):
        directory = ROOT / "output" / f"launcher-cold-test-{uuid.uuid4().hex}"
        directory.mkdir(parents=True)
        (directory / "articles").mkdir()
        (directory / "folders.json").write_text(json.dumps({"schema_version": 1,
            "folders": [{"id": "economy", "name": "经济"}]}, ensure_ascii=False), encoding="utf-8")
        # Select an available unprivileged loopback port; the server binds exclusively.
        with launcher.socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process, log = launcher.start_background(port, directory)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.poll() is None:
                if launcher.service_state(port) == "ready":
                    break
                time.sleep(0.1)
            self.assertEqual(launcher.service_state(port), "ready", f"See retained log {log}")
            self.assertEqual(list((directory / "articles").iterdir()), [])
        finally:
            if process.poll() is None:
                process.terminate()  # Only the exact child this isolated test just started.
            process.wait(timeout=5)

    def test_running_service_reused_without_new_process(self):
        with patch.object(launcher, "service_state", return_value="ready"), patch.object(launcher, "start_background") as spawn, patch.object(launcher.webbrowser, "open", return_value=True) as browser:
            self.assertEqual(launcher.launch_reader(), {"url": "http://127.0.0.1:8765", "started_service": False, "ready": True})
            spawn.assert_not_called()
            browser.assert_called_once_with(launcher.URL, new=2)

    def test_foreign_or_unreadable_listener_does_not_start_or_open(self):
        with patch.object(launcher, "service_state", return_value="occupied"), patch.object(launcher, "start_background") as spawn, patch.object(launcher.webbrowser, "open") as browser:
            with self.assertRaises(launcher.LaunchError):
                launcher.launch_reader()
            spawn.assert_not_called()
            browser.assert_not_called()

    def test_cold_start_waits_for_readiness_before_browser(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(launcher, "service_state", side_effect=["stopped", "stopped", "ready"]), patch.object(launcher, "start_background", return_value=(process, ROOT / "output" / "test.log")), patch.object(launcher.time, "sleep"), patch.object(launcher.webbrowser, "open", return_value=True) as browser:
            self.assertTrue(launcher.launch_reader()["started_service"])
            browser.assert_called_once()

    def test_failed_start_never_claims_browser_open(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(launcher, "service_state", return_value="stopped"), patch.object(launcher, "start_background", return_value=(process, ROOT / "output" / "test.log")), patch.object(launcher.webbrowser, "open") as browser:
            with self.assertRaises(launcher.LaunchError):
                launcher.launch_reader()
            browser.assert_not_called()

    def test_hidden_child_uses_project_and_new_log(self):
        directory = ROOT / "output" / f"launcher-test-{uuid.uuid4().hex}"
        directory.mkdir()
        with patch.object(launcher, "ROOT", directory), patch.object(launcher.subprocess, "Popen") as spawn:
            _, log = launcher.start_background()
            self.assertTrue(log.is_file())
            arguments = spawn.call_args.args[0]
            self.assertIn(str(directory / "server.py"), arguments)
            self.assertEqual(spawn.call_args.kwargs["cwd"], str(directory))
            self.assertEqual(spawn.call_args.kwargs["env"]["PYTHONUTF8"], "1")
            self.assertEqual(spawn.call_args.kwargs["creationflags"], launcher.subprocess.CREATE_NO_WINDOW)
            self.assertEqual(log.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
