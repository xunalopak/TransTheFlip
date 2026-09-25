#!/usr/bin/env python3
"""
TransTheFlip — PC Client (GUI)
CustomTkinter front-end for the BLE remote-HID client.

Like the CLI (trans_client.py), it connects to a Flipper Zero over the
proprietary BLE serial service and sends text that the Flipper types as
USB HID keystrokes on the target PC. Special key tags are inline in the
text (see the "Keys" buttons or the CLI docstring):

  [ENTER] [TAB] [ESC] [BACKSPACE] [DEL]
  [UP] [DOWN] [LEFT] [RIGHT] [HOME] [END] [PGUP] [PGDN]
  [F1]..[F12]
  [CTRL+c] [ALT+F4] [WIN+r] [CTRL+SHIFT+ESC]
  [DELAY:500]

Usage:
  pip install -r requirements.txt
  python trans_gui.py

Threading model:
  bleak is asyncio-based, so its event loop runs in a dedicated background
  thread (BleWorker). The Tk main loop stays on the main thread. The worker
  reports results by pushing (kind, payload) events into a thread-safe queue;
  the GUI drains that queue periodically with after() — never touching Tk
  widgets from the asyncio thread.
"""

import os
import sys
import queue
import asyncio
import threading
from typing import Optional

# Make the sibling trans_client module importable regardless of the cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import customtkinter as ctk
except ImportError:
    raise SystemExit("customtkinter is not installed. Run: pip install customtkinter")

try:
    from bleak import BleakScanner, BleakClient
    from bleak.backends.characteristic import BleakGATTCharacteristic
except ImportError:
    raise SystemExit("bleak is not installed. Run: pip install bleak")

# Reuse the protocol constants from the CLI client (single source of truth).
from trans_client import (
    FLIPPER_SERVICE_UUID,
    FLIPPER_RX_CHAR_UUID,
    FLIPPER_TX_CHAR_UUID,
    BLE_CHUNK_SIZE,
)

# BLE scan duration for the GUI (seconds). Deliberately shorter than the CLI
# default: devices stream in live through the detection callback, so a nearby
# Flipper usually shows up within the first second — no need to wait longer.
GUI_SCAN_TIMEOUT = 5.0

# Flipper → PC status codes, mapped to human-readable lines.
STATUS_MAP = {
    "OK":     "✅  Flipper: text sent successfully",
    "ERR":    "❌  Flipper: HID send error",
    "CANCEL": "🚫  Flipper: send cancelled by user",
    "RECV":   "📥  Flipper: text received, waiting for confirmation...",
}

# Quick-insert special key tags shown as buttons (label -> tag).
SPECIAL_KEYS = [
    "[ENTER]", "[TAB]", "[ESC]", "[BACKSPACE]", "[DEL]",
    "[UP]", "[DOWN]", "[LEFT]", "[RIGHT]", "[DELAY:500]",
    "[CTRL+c]", "[CTRL+v]", "[CTRL+a]", "[ALT+F4]", "[WIN+r]",
]


def _order_devices(found: dict) -> list:
    """Return [(label, address)] sorted with Flipper devices first, then by name.

    `found` maps address -> (label, address, is_flipper).
    """
    entries = sorted(found.values(), key=lambda e: (not e[2], e[0].lower()))
    return [(label, address) for (label, address, _is_flipper) in entries]


# ============================================================
# Background BLE worker (owns an asyncio loop on its own thread)
# ============================================================
class BleWorker:
    """Runs an asyncio event loop in a background thread and exposes
    fire-and-forget helpers callable from the GUI thread. All results are
    reported back through `emit(kind, payload)`, which must be thread-safe
    (here it is queue.Queue.put)."""

    def __init__(self, emit):
        self._emit = emit
        self._loop = asyncio.new_event_loop()
        self._client: Optional[BleakClient] = None
        self._devices = {}
        self._connect_task = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ---- public API (called from the GUI thread) ----
    def scan(self) -> None:
        self._submit(self._scan())

    def connect(self, address: str, name: str) -> None:
        self._submit(self._connect(address, name))

    def disconnect(self) -> None:
        self._submit(self._disconnect())

    def send(self, text: str) -> None:
        self._submit(self._send(text))

    def shutdown(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        future.add_done_callback(lambda _: self._loop.call_soon_threadsafe(self._loop.stop))

    async def _shutdown(self) -> None:
        await self._disconnect()
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- coroutines (run on the asyncio thread) ----
    async def _scan(self) -> None:
        self._emit("scanning", True)
        self._emit("log", f"🔍  Scanning BLE ({GUI_SCAN_TIMEOUT:.0f}s)...")

        # Collect devices live via a detection callback (address -> entry).
        # This is more robust than discover() across backends and lets the
        # dropdown fill as devices are seen, so a nearby Flipper appears almost
        # immediately instead of only after the full timeout has elapsed.
        found: dict[str, tuple] = {}

        def _on_detection(dev, adv) -> None:
            self._devices[dev.address] = dev
            uuids_lower = [u.lower() for u in (adv.service_uuids or [])]
            is_flipper = (FLIPPER_SERVICE_UUID in uuids_lower) or (
                bool(dev.name) and "flipper" in dev.name.lower()
            )
            label = f"{dev.name or '(no name)'}  [{dev.address}]"
            found[dev.address] = (label, dev.address, is_flipper)
            self._emit("devices", _order_devices(found))

        try:
            async with BleakScanner(detection_callback=_on_detection):
                await asyncio.sleep(GUI_SCAN_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"❌  Scan error: {exc}")
            self._emit(
                "log",
                "    → Check that Bluetooth is on and the bluetooth service is running.",
            )
            self._emit("scanning", False)
            return

        n_flippers = sum(1 for entry in found.values() if entry[2])
        self._emit(
            "log",
            f"📡  {len(found)} device(s) found, {n_flippers} Flipper(s).",
        )
        self._emit("devices", _order_devices(found))
        self._emit("scanning", False)

    async def _connect(self, address: str, name: str) -> None:
        if self._connect_task is not None or self._client is not None:
            return
        self._connect_task = asyncio.current_task()
        self._emit("log", f"🔗  Connecting to {name}...")
        self._emit("log", "Confirm the pairing code on the Flipper if prompted (up to 60s).")
        try:
            client = BleakClient(
                self._devices.get(address, address), timeout=60.0, pair=True,
                winrt={"use_cached_services": False},
                disconnected_callback=self._on_disconnected,
            )
            self._client = client
            await client.connect()
            if not client.is_connected:
                raise RuntimeError("Connection failed.")

            # Verify the Flipper serial service is actually present.
            service_uuids = [s.uuid.lower() for s in client.services]
            if FLIPPER_SERVICE_UUID not in service_uuids:
                raise RuntimeError(
                    "Flipper serial service not found — open TransTheFlip on the Flipper, "
                    "close other Bluetooth clients, then reconnect."
                )

            await client.start_notify(FLIPPER_TX_CHAR_UUID, self._on_notify)
            if self._client is not client or not client.is_connected:
                raise RuntimeError("Device disconnected during setup.")
            self._emit("connected", name)
        except asyncio.CancelledError:
            await self._disconnect()
            raise
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"❌  BLE error: {exc}")
            await self._disconnect()
        finally:
            self._connect_task = None

    async def _disconnect(self) -> None:
        task = self._connect_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return
        if self._client is not None:
            client = self._client
            self._client = None
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                self._emit("log", f"⚠️  Disconnect error: {exc}")
            self._client = None
            self._emit("log", "👋  Disconnected.")
        self._emit("disconnected", None)

    async def _send(self, text: str) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("log", "❌  Not connected.")
            return

        payload = (text + "\n").encode("utf-8")
        total = len(payload)
        sent = 0
        try:
            while sent < total:
                chunk = payload[sent : sent + BLE_CHUNK_SIZE]
                # Write WITH response: the RX characteristic is authenticated,
                # so a failed write raises instead of being silently dropped.
                await client.write_gatt_char(FLIPPER_RX_CHAR_UUID, chunk, response=True)
                sent += len(chunk)
                if sent < total:
                    await asyncio.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"❌  Send error: {exc}")
            return
        self._emit(
            "log", f"📤  Sent ({total} bytes). Confirm on the Flipper (OK button)."
        )

    # ---- bleak callbacks (asyncio thread) ----
    def _on_notify(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        msg = data.decode("utf-8", errors="replace").strip()
        # The Flipper only ever sends meaningful status tokens (RECV/OK/ERR/
        # CANCEL). Empty notifications come from the BLE serial profile's
        # flow-control / keep-alive traffic — drop them so they don't spam the
        # log with blank "📡 Flipper:" lines.
        if not msg:
            return
        self._emit("notify", msg)

    def _on_disconnected(self, _client: BleakClient) -> None:
        if _client is not self._client:
            return
        self._client = None
        self._emit("log", "🔌  Link lost (device disconnected).")
        self._emit("disconnected", None)


# ============================================================
# GUI
# ============================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TransTheFlip — BLE Remote HID")
        self.geometry("740x580")
        self.minsize(660, 500)

        self._events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        # Wrap put() so emit(kind, payload) enqueues a single (kind, payload)
        # tuple. Passing self._events.put directly would call it as
        # put(item=kind, block=payload) — only the kind string lands in the
        # queue and the consumer's `kind, payload = ...` unpacking blows up.
        self._worker = BleWorker(
            lambda kind, payload: self._events.put((kind, payload))
        )
        self._dev_map: dict[str, str] = {}
        self._connected = False
        self._busy = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_events)

    # ---- layout ----
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)  # log row expands

        # Row 0 — connection bar
        bar = ctk.CTkFrame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        bar.grid_columnconfigure(1, weight=1)

        self.status_label = ctk.CTkLabel(
            bar, text="● Disconnected", text_color="#e05555", anchor="w"
        )
        self.status_label.grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=8)

        self.device_var = ctk.StringVar(value="(scan first)")
        self.device_menu = ctk.CTkOptionMenu(
            bar, values=["(scan first)"], variable=self.device_var, width=240
        )
        self.device_menu.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=8)

        self.scan_btn = ctk.CTkButton(bar, text="Scan", width=80, command=self._on_scan)
        self.scan_btn.grid(row=1, column=2, padx=6, pady=8)

        self.connect_btn = ctk.CTkButton(
            bar, text="Connect", width=110, command=self._on_connect_click
        )
        self.connect_btn.grid(row=1, column=3, padx=6, pady=8)
        self.disconnect_btn = ctk.CTkButton(
            bar, text="Déconnecter", width=110,
            command=self._on_disconnect_click, state="disabled",
        )
        self.disconnect_btn.grid(row=1, column=4, padx=(6, 10), pady=8)

        # Row 1 — text entry + send
        entry_frame = ctk.CTkFrame(self, fg_color="transparent")
        entry_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        entry_frame.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            entry_frame, placeholder_text="Text to send, e.g.  [WIN+r]notepad[ENTER]"
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", self._on_send)

        self.send_btn = ctk.CTkButton(
            entry_frame, text="Send", width=110, command=self._on_send, state="disabled"
        )
        self.send_btn.grid(row=0, column=1)

        # Row 2 — special key quick-insert buttons
        keys_frame = ctk.CTkFrame(self)
        keys_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=6)
        cols = 5
        for i in range(cols):
            keys_frame.grid_columnconfigure(i, weight=1)
        for idx, tag in enumerate(SPECIAL_KEYS):
            btn = ctk.CTkButton(
                keys_frame,
                text=tag,
                height=28,
                fg_color="#2b2b3c",
                hover_color="#3a3a52",
                command=lambda t=tag: self._insert_key(t),
            )
            btn.grid(row=idx // cols, column=idx % cols, padx=4, pady=4, sticky="ew")

        # Row 3 — log / console
        self.log_box = ctk.CTkTextbox(self, wrap="word")
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=10, pady=(6, 10))
        self.log_box.configure(state="disabled")
        self._log("Ready. Click Scan to discover your Flipper Zero.")

    # ---- helpers ----
    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _insert_key(self, tag: str) -> None:
        self.entry.insert(self.entry.index("insert"), tag)
        self.entry.focus_set()

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text, text_color=color)

    # ---- button handlers ----
    def _on_scan(self) -> None:
        self.scan_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self._worker.scan()

    def _on_connect_click(self) -> None:
        if self._connected:
            return
        label = self.device_var.get()
        address = self._dev_map.get(label)
        if not address:
            self._log("⚠️  Select a device first (click Scan).")
            return
        self.connect_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")
        self.device_menu.configure(state="disabled")
        self.disconnect_btn.configure(state="normal")
        self._busy = True
        self._set_status("● Connecting...", "#e0a955")
        self._worker.connect(address, label)

    def _on_disconnect_click(self) -> None:
        self._connected = False
        self.disconnect_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self._set_status("● Disconnecting...", "#e0a955")
        self._worker.disconnect()

    def _on_send(self, _event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        if not self._connected:
            self._log("❌  Not connected.")
            return
        self._log(f"→  {text}")
        self._worker.send(text)
        self.entry.delete(0, "end")

    # ---- event pump (drains worker events on the Tk thread) ----
    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def _handle_event(self, kind: str, payload: object) -> None:
        if kind == "log":
            self._log(str(payload))
        elif kind == "scanning":
            if payload:
                if not self._connected:
                    self._set_status("● Scanning...", "#e0a955")
            else:
                if not self._connected and not self._busy:
                    self.scan_btn.configure(state="normal")
                    self.connect_btn.configure(state="normal")
                    self._set_status("● Disconnected", "#e05555")
        elif kind == "devices":
            self._populate_devices(payload)  # type: ignore[arg-type]
        elif kind == "connected":
            self._connected = True
            self._busy = False
            self.connect_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            self.send_btn.configure(state="normal")
            self._set_status(f"● Connected: {payload}", "#55cc66")
        elif kind == "disconnected":
            self._connected = False
            self._busy = False
            self.connect_btn.configure(state="normal")
            self.scan_btn.configure(state="normal")
            self.device_menu.configure(state="normal")
            self.disconnect_btn.configure(state="disabled")
            self.send_btn.configure(state="disabled")
            self._set_status("● Disconnected", "#e05555")
        elif kind == "notify":
            msg = str(payload)
            self._log(STATUS_MAP.get(msg, f"📡  Flipper: {msg}"))

    def _populate_devices(self, devices: list) -> None:
        self._dev_map = {label: addr for (label, addr) in devices}
        labels = list(self._dev_map.keys())
        if labels:
            self.device_menu.configure(values=labels)
            # Keep the user's current pick if it's still in range; otherwise
            # default to the first entry (Flippers are sorted first). This stops
            # the live updates during a scan from resetting the selection.
            if self.device_var.get() not in self._dev_map:
                self.device_var.set(labels[0])
        else:
            self.device_menu.configure(values=["(no devices)"])
            self.device_var.set("(no devices)")

    # ---- shutdown ----
    def _on_close(self) -> None:
        self.withdraw()
        self._worker.shutdown()
        self._wait_for_shutdown()

    def _wait_for_shutdown(self) -> None:
        if self._worker._thread.is_alive():
            self.after(50, self._wait_for_shutdown)
        else:
            self.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()
