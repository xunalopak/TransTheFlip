import asyncio
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

from protocol import encode_text, NotificationLines, write_text, bluetooth_diagnostic
from trans_gui import App, load_last_device, save_last_device


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_frame_and_validation(self):
        writes, progress = [], []

        async def write(uuid, data, response):
            self.assertTrue(response)
            self.assertLessEqual(len(data), 20)
            writes.append(data)

        text = "first\nsecond\t[ENTER]" * 8
        await write_text(SimpleNamespace(write_gatt_char=write), text, progress.append)
        header, payload = b"".join(writes).split(b"\n", 1)
        magic, length, crc = header.split()
        self.assertEqual(magic, b"TTF1")
        self.assertEqual(int(length), len(payload))
        self.assertEqual(int(crc, 16), zlib.crc32(payload))
        self.assertEqual(payload.decode(), text)
        self.assertEqual(progress[-1], 1)
        self.assertEqual(len(encode_text("x" * 255).split(b"\n", 1)[1]), 255)
        for invalid in ("", "x" * 256, "é", "a\0b"):
            with self.assertRaises(ValueError):
                encode_text(invalid)

    async def test_notifications_fragmentation(self):
        lines = NotificationLines()
        self.assertEqual(lines.feed(b"RE"), [])
        self.assertEqual(lines.feed(b"CV\nPROGRESS:2"), ["RECV"])
        self.assertEqual(lines.feed(b"0\nOK\n\n"), ["PROGRESS:20", "OK"])
        self.assertEqual(lines.feed(b"x" * 1025), ["ERR:PROTOCOL"])

    async def test_saved_device_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertIsNone(load_last_device(path))
            save_last_device("AA:BB", "Flipper", path)
            self.assertEqual(load_last_device(path), {"address": "AA:BB", "name": "Flipper"})
            path.write_text("null")
            self.assertIsNone(load_last_device(path))
            path.write_text("broken")
            self.assertIsNone(load_last_device(path))
        self.assertIn("Délai dépassé", bluetooth_diagnostic(TimeoutError()))
        self.assertIn("Appairage refusé", bluetooth_diagnostic(RuntimeError("Access denied")))

    async def test_editor_preserved_and_history_after_success_only(self):
        app = SimpleNamespace(
            entry=Mock(), _pending_text=None, _connected=True, _history=[],
            _worker=Mock(), send_btn=Mock(), progress_bar=Mock(), transfer_label=Mock(),
            history_menu=Mock(), _log=Mock(),
        )
        app._finish_transfer = lambda message: App._finish_transfer(app, message)
        text = "first\nsecond"
        app.entry.get.return_value = text
        self.assertEqual(App._on_send(app), "break")
        App._on_send(app)  # no duplicate click while pending
        app._worker.send.assert_called_once_with(text)
        app.entry.delete.assert_not_called()
        App._handle_event(app, "send_error", "Transfer failed")
        self.assertEqual(app._history, [])
        app.entry.delete.assert_not_called()
        App._on_send(app)
        App._handle_event(app, "notify", "RECV")
        App._handle_event(app, "transfer_progress", 1)
        self.assertEqual(app._transfer_stage, "RECV")
        App._handle_event(app, "notify", "OK")
        self.assertEqual(app._history, [text])
        app.entry.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
