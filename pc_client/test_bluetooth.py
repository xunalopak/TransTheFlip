"""Run without Bluetooth hardware: python -m unittest discover -s pc_client."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import trans_gui as gui


class FakeClient:
    failure = None
    instances = []

    def __init__(self, device, **options):
        self.device = device
        self.options = options
        self.is_connected = False
        self.closed = False
        self.services = [SimpleNamespace(uuid=gui.FLIPPER_SERVICE_UUID)]
        self.instances.append(self)

    async def connect(self):
        self.is_connected = True
        if self.failure == "connect":
            raise RuntimeError("pairing rejected")
        if self.failure == "wait":
            await asyncio.Event().wait()
        if self.failure == "service":
            self.services = []

    async def start_notify(self, uuid, callback):
        if self.failure == "notify":
            raise RuntimeError("notification subscription failed")

    async def disconnect(self):
        self.closed = True
        self.is_connected = False
        self.options["disconnected_callback"](self)


class BluetoothTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_lifecycle(self):
        events = []
        worker = gui.BleWorker.__new__(gui.BleWorker)
        worker._emit = lambda *event: events.append(event)
        worker._client = None
        worker._connect_task = None
        device = object()
        worker._devices = {"address": device}
        with patch.object(gui, "BleakClient", FakeClient):
            for failure in ("connect", "service", "notify", None):
                FakeClient.failure = failure
                events.clear()
                await worker._connect("address", "Flipper")
                client = FakeClient.instances[-1]
                self.assertIs(client.device, device)
                self.assertTrue(client.options["pair"])
                self.assertFalse(client.options["winrt"]["use_cached_services"])
                if failure:
                    self.assertTrue(client.closed)
                    self.assertIsNone(worker._client)
                    self.assertNotIn(("connected", "Flipper"), events)
                else:
                    self.assertIn(("connected", "Flipper"), events)
                    # A late callback from a previous connection must not clear this one.
                    worker._on_disconnected(FakeClient.instances[-2])
                    self.assertIs(worker._client, client)
                    await worker._disconnect()
                    self.assertTrue(client.closed)
                    self.assertNotIn(("log", "🔌  Link lost (device disconnected)."), events)
                self.assertIn(("disconnected", None), events)

            FakeClient.failure = "wait"
            task = asyncio.create_task(worker._connect("address", "Flipper"))
            await asyncio.sleep(0)
            await worker._disconnect()
            self.assertTrue(task.cancelled())
            self.assertTrue(FakeClient.instances[-1].closed)
            self.assertIsNone(worker._connect_task)
            FakeClient.failure = None
            await worker._connect("address", "Flipper")
            self.assertIsNotNone(worker._client)
            await worker._disconnect()

    async def test_gui_disconnect_states(self):
        app = SimpleNamespace(
            _connected=False, _busy=True, connect_btn=Mock(), disconnect_btn=Mock(),
            send_btn=Mock(), scan_btn=Mock(), device_menu=Mock(),
            _set_status=Mock(), _worker=Mock(),
        )
        gui.App._handle_event(app, "connected", "Flipper")
        self.assertTrue(app._connected)
        app.disconnect_btn.configure.assert_called_with(state="normal")
        app.connect_btn.configure.assert_called_with(state="disabled")
        gui.App._on_disconnect_click(app)
        app._worker.disconnect.assert_called_once()
        app.send_btn.configure.assert_called_with(state="disabled")
        gui.App._handle_event(app, "disconnected", None)
        self.assertFalse(app._connected)
        app.disconnect_btn.configure.assert_called_with(state="disabled")
        app.connect_btn.configure.assert_called_with(state="normal")

    async def test_shutdown_waits_for_disconnect(self):
        worker = gui.BleWorker(lambda *_: None)
        closed = []

        async def disconnect():
            await asyncio.sleep(0.05)
            closed.append(True)

        worker._client = SimpleNamespace(disconnect=disconnect)
        worker.shutdown()
        await asyncio.to_thread(worker._thread.join, 3)
        self.assertFalse(worker._thread.is_alive())
        self.assertEqual(closed, [True])
        worker._loop.close()


if __name__ == "__main__":
    unittest.main()
