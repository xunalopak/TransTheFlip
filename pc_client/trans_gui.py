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
    SCAN_TIMEOUT,
)

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
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._client: Optional[BleakClient] = None

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
        self._submit(self._disconnect())
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ---- coroutines (run on the asyncio thread) ----
    async def _scan(self) -> None:
        self._emit("scanning", True)
        self._emit("log", f"🔍  Scanning BLE ({SCAN_TIMEOUT:.0f}s)...")
        try:
            results = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"❌  Scan error: {exc}")
            self._emit("scanning", False)
            return

        flippers, others = [], []
        for dev, adv in results.values():
            uuids_lower = [u.lower() for u in (adv.service_uuids or [])]
            is_flipper = (FLIPPER_SERVICE_UUID in uuids_lower) or (
                bool(dev.name) and "flipper" in dev.name.lower()
            )
            label = f"{dev.name or '(no name)'}  [{dev.address}]"
            (flippers if is_flipper else others).append((label, dev.address))

        ordered = flippers + others
        self._emit(
            "log",
            f"📡  {len(results)} device(s) found, {len(flippers)} Flipper(s).",
        )
        self._emit("devices", ordered)
        self._emit("scanning", False)

    async def _connect(self, address: str, name: str) -> None:
        self._emit("log", f"🔗  Connecting to {name}...")
        try:
            client = BleakClient(
                address, timeout=15.0, disconnected_callback=self._on_disconnected
            )
            await client.connect()
            if not client.is_connected:
                self._emit("log", "❌  Connection failed.")
                self._emit("disconnected", None)
                return
            self._client = client
            mtu = getattr(client, "mtu_size", BLE_CHUNK_SIZE)
            self._emit("log", f"✅  Connected! MTU={mtu}")

            # Pairing (bonding) is mandatory: the RX/TX characteristics require
            # an authenticated, encrypted link or writes are silently rejected.
            try:
                paired = await client.pair()
                if paired:
                    self._emit("log", "🔐  Pairing established (encrypted link).")
                else:
                    self._emit(
                        "log",
                        "⚠️  Pairing not confirmed — confirm the code on the Flipper.",
                    )
            except Exception as exc:  # noqa: BLE001
                self._emit("log", f"⚠️  Automatic pairing failed: {exc}")

            # Verify the Flipper serial service is actually present.
            service_uuids = [s.uuid.lower() for s in client.services]
            if FLIPPER_SERVICE_UUID not in service_uuids:
                self._emit(
                    "log",
                    "⚠️  Flipper serial service not found — is the app running on the Flipper?",
                )
                await client.disconnect()
                self._client = None
                self._emit("disconnected", None)
                return

            await client.start_notify(FLIPPER_TX_CHAR_UUID, self._on_notify)
            self._emit("connected", name)
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"❌  BLE error: {exc}")
            self._client = None
            self._emit("disconnected", None)

    async def _disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
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
        self._emit("notify", msg)

    def _on_disconnected(self, _client: BleakClient) -> None:
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
        self._worker = BleWorker(self._events.put)
        self._dev_map: dict[str, str] = {}
        self._connected = False

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
        self.status_label.grid(row=0, column=0, padx=(10, 12), pady=8)

        self.device_var = ctk.StringVar(value="(scan first)")
        self.device_menu = ctk.CTkOptionMenu(
            bar, values=["(scan first)"], variable=self.device_var, width=320
        )
        self.device_menu.grid(row=0, column=1, sticky="ew", padx=6, pady=8)

        self.scan_btn = ctk.CTkButton(bar, text="Scan", width=80, command=self._on_scan)
        self.scan_btn.grid(row=0, column=2, padx=6, pady=8)

        self.connect_btn = ctk.CTkButton(
            bar, text="Connect", width=110, command=self._on_connect_click
        )
        self.connect_btn.grid(row=0, column=3, padx=(6, 10), pady=8)

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
        self._worker.scan()

    def _on_connect_click(self) -> None:
        if self._connected:
            self._worker.disconnect()
            return
        label = self.device_var.get()
        address = self._dev_map.get(label)
        if not address:
            self._log("⚠️  Select a device first (click Scan).")
            return
        self.connect_btn.configure(state="disabled")
        self._set_status("● Connecting...", "#e0a955")
        self._worker.connect(address, label)

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
                self.scan_btn.configure(state="normal")
                if not self._connected:
                    self._set_status("● Disconnected", "#e05555")
        elif kind == "devices":
            self._populate_devices(payload)  # type: ignore[arg-type]
        elif kind == "connected":
            self._connected = True
            self.connect_btn.configure(text="Disconnect", state="normal")
            self.send_btn.configure(state="normal")
            self._set_status(f"● Connected: {payload}", "#55cc66")
        elif kind == "disconnected":
            self._connected = False
            self.connect_btn.configure(text="Connect", state="normal")
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
            self.device_var.set(labels[0])
        else:
            self.device_menu.configure(values=["(no devices)"])
            self.device_var.set("(no devices)")

    # ---- shutdown ----
    def _on_close(self) -> None:
        try:
            self._worker.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.after(250, self.destroy)


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App().mainloop()


if __name__ == "__main__":
    main()
