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
import json
from pathlib import Path
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
from protocol import EXECUTE_COMMAND, RX_UUID, NotificationLines, STATUS_TEXT, MAX_TEXT_BYTES, peer_capacity, encode_text, encode_layout, write_text, bluetooth_diagnostic

SETTINGS_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TransTheFlip" / "settings.json"


def load_last_device(path=SETTINGS_PATH):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and all(isinstance(value.get(k), str) and value[k] for k in ("address", "name")):
            return value
    except (OSError, ValueError):
        pass
    return None


def save_last_device(address, name, path=SETTINGS_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"address": address, "name": name}), encoding="utf-8")
    temporary.replace(path)

# BLE scan duration for the GUI (seconds). Deliberately shorter than the CLI
# default: devices stream in live through the detection callback, so a nearby
# Flipper usually shows up within the first second — no need to wait longer.
GUI_SCAN_TIMEOUT = 5.0

GUI_TEXT = {
    "en": {
        "title": "TransTheFlip — BLE Remote HID",
        "disconnected": "● Disconnected", "scanning": "● Scanning...",
        "connecting": "● Connecting...", "disconnecting": "● Disconnecting...",
        "connected": "● Connected: {name}", "scan": "Scan", "connect": "Connect",
        "disconnect": "Disconnect", "send": "Send", "execute": "Execute on Flipper",
        "scan_first": "(scan first)", "no_devices": "(no devices)",
        "history": "Session history", "hint": "Up to {bytes} bytes · Enter: new line · Ctrl+Enter: send",
        "ready": "Ready. Click Scan to discover your Flipper Zero.",
        "select_device": "⚠️  Select a device first (click Scan).",
        "not_connected": "❌  Not connected.", "transfer": "Bluetooth transfer in progress…",
        "execute_requested": "Execution requested on the Flipper…",
        "connection_lost": "Connection lost or closed. Result unconfirmed; text kept.",
        "transfer_progress": "Bluetooth transfer: {percent:.0%} — waiting for verification",
        "hid_progress": "Typing on target PC: {percent}% — return to Flipper to stop",
        "capacity": "Flipper capacity: {bytes} bytes, including tags.",
        "upgrade_capacity": " Update the FAP to reach {bytes} bytes.",
        "remembered": "Last Flipper remembered: click Connect to reconnect.",
        "remember_error": "Unable to remember the device: {error}",
        "layout": "Flipper keyboard layout",
        "layout_changed": "Keyboard layout: {name}",
        "layout_error": "Unable to load keyboard layout: {error}",
        "language": "Language",
        "subtitle": "Bluetooth text · USB keyboard",
        "connection_section": "Connection",
        "text_section": "Text to send", "keys_section": "Quick keys",
        "show_log": "Show activity ▾", "hide_log": "Hide activity ▴",
        "scan_start": "🔍  Scanning BLE ({seconds:.0f}s)...",
        "service_hint": "    → Check that Bluetooth is on and the Bluetooth service is running.",
        "devices_found": "📡  {count} device(s) found, {flippers} Flipper(s).",
        "no_device_log": "No device found. Enable Bluetooth, move the Flipper closer, and open TransTheFlip.",
        "connecting_log": "🔗  Connecting to {name}...",
        "pairing": "Confirm the pairing code on the Flipper if prompted (up to 60s).",
        "device_not_found": "Device not found — scan again.",
        "service_not_found": "Flipper serial service not found — open TransTheFlip, close other Bluetooth clients, then reconnect.",
        "protocol_timeout": "TransTheFlip did not answer the TTF1 protocol. Install the latest Flipper app and open it.",
        "disconnected_setup": "Device disconnected during setup.", "disconnect_error": "⚠️  Disconnect error: {error}",
        "disconnected_log": "👋  Disconnected.", "already_waiting": "A transfer is already waiting for the Flipper result.",
        "send_error": "Transfer interrupted. The text is kept.", "receipt_timeout": "No receipt from the Flipper.",
        "retry_suffix": " Text kept; reconnect before retrying.", "no_pending": "No text is waiting on the Flipper.",
        "execution_log": "▶  Execution requested from the PC.", "execution_error": "Execution failed: {error}",
        "link_lost": "🔌  Link lost (device disconnected).", "preserved": "Connection lost or closed. Result unconfirmed; text kept.",
    },
    "fr": {
        "title": "TransTheFlip — HID distant BLE",
        "disconnected": "● Déconnecté", "scanning": "● Recherche...",
        "connecting": "● Connexion...", "disconnecting": "● Déconnexion...",
        "connected": "● Connecté : {name}", "scan": "Rechercher", "connect": "Connecter",
        "disconnect": "Déconnecter", "send": "Envoyer", "execute": "Exécuter sur le Flipper",
        "scan_first": "(lancer une recherche)", "no_devices": "(aucun appareil)",
        "history": "Historique de la session", "hint": "Jusqu’à {bytes} octets · Entrée : nouvelle ligne · Ctrl+Entrée : envoyer",
        "ready": "Prêt. Cliquez sur Rechercher pour détecter votre Flipper Zero.",
        "select_device": "⚠️  Sélectionnez d’abord un appareil (cliquez sur Rechercher).",
        "not_connected": "❌  Non connecté.", "transfer": "Transfert Bluetooth en cours…",
        "execute_requested": "Exécution demandée au Flipper…",
        "connection_lost": "Connexion perdue ou fermée. Résultat non confirmé ; texte conservé.",
        "transfer_progress": "Transfert Bluetooth : {percent:.0%} — attente de vérification",
        "hid_progress": "Frappe sur le PC cible : {percent}% — retour sur le Flipper pour arrêter",
        "capacity": "Capacité du Flipper : {bytes} octets, tags compris.",
        "upgrade_capacity": " Mettez le FAP à jour pour passer à {bytes} octets.",
        "remembered": "Dernier Flipper mémorisé : cliquez sur Connecter pour vous reconnecter.",
        "remember_error": "Impossible de mémoriser le périphérique : {error}",
        "layout": "Disposition du clavier du Flipper",
        "layout_changed": "Disposition du clavier : {name}",
        "layout_error": "Impossible de charger la disposition : {error}",
        "language": "Langue",
        "subtitle": "Texte Bluetooth · Clavier USB",
        "connection_section": "Connexion",
        "text_section": "Texte à envoyer", "keys_section": "Touches rapides",
        "show_log": "Afficher le journal ▾", "hide_log": "Masquer le journal ▴",
        "scan_start": "🔍  Recherche Bluetooth ({seconds:.0f} s)...",
        "service_hint": "    → Vérifiez que le Bluetooth et le service Bluetooth sont actifs.",
        "devices_found": "📡  {count} appareil(s) trouvé(s), dont {flippers} Flipper(s).",
        "no_device_log": "Aucun appareil détecté. Activez le Bluetooth, rapprochez le Flipper et ouvrez TransTheFlip.",
        "connecting_log": "🔗  Connexion à {name}...",
        "pairing": "Confirmez le code d’appairage sur le Flipper si demandé (jusqu’à 60 s).",
        "device_not_found": "Appareil introuvable — relancez la recherche.",
        "service_not_found": "Service série du Flipper introuvable — ouvrez TransTheFlip, fermez les autres clients Bluetooth, puis reconnectez-vous.",
        "protocol_timeout": "TransTheFlip ne répond pas au protocole TTF1. Installez et ouvrez la dernière application Flipper.",
        "disconnected_setup": "L’appareil s’est déconnecté pendant la configuration.", "disconnect_error": "⚠️  Erreur de déconnexion : {error}",
        "disconnected_log": "👋  Déconnecté.", "already_waiting": "Un transfert attend déjà le résultat du Flipper.",
        "send_error": "Transfert interrompu. Le texte est conservé.", "receipt_timeout": "Aucun accusé de réception du Flipper.",
        "retry_suffix": " Texte conservé ; reconnectez-vous avant de réessayer.", "no_pending": "Aucun texte en attente sur le Flipper.",
        "execution_log": "▶  Exécution demandée depuis le PC.", "execution_error": "Exécution impossible : {error}",
        "link_lost": "🔌  Connexion perdue (appareil déconnecté).", "preserved": "Connexion perdue ou fermée. Résultat non confirmé ; texte conservé.",
    },
}

STATUS_TEXT_FR = {
    "RECV": "Texte vérifié — en attente de confirmation sur le Flipper.",
    "WAIT_USB": "En attente du branchement USB au PC cible.",
    "SENDING": "Frappe en cours sur le PC cible…",
    "OK": "Terminé : frappe confirmée par le Flipper.",
    "CANCEL": "Annulé sur le Flipper. Le texte est conservé.",
    "ERR:HID": "Échec de frappe : vérifiez la connexion USB au PC cible.",
    "ERR:LENGTH": "Le texte dépasse la capacité annoncée par le Flipper, tags compris.",
    "ERR:CHECKSUM": "Transfert corrompu ou incomplet : reconnectez-vous avant de réessayer.",
    "ERR:TIMEOUT": "Transfert incomplet : reconnectez-vous avant de réessayer.",
    "ERR:OVERFLOW": "Données Bluetooth perdues : reconnectez-vous avant de réessayer.",
    "ERR:PROTOCOL": "Protocole incompatible : installez la nouvelle application Flipper.",
    "ERR:CHAR": "Caractère non pris en charge par le clavier du Flipper.",
    "ERR:BUSY": "Flipper occupé : terminez ou annulez l’envoi sur le Flipper.",
    "ERR:MEMORY": "Mémoire insuffisante sur le Flipper.",
    "ERR:STORAGE": "Erreur de lecture/écriture sur la carte SD du Flipper.",
    "ERR:LAYOUT": "Disposition introuvable sur la carte SD du Flipper.",
}

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

LAYOUT_OPTIONS = {
    "QWERTY US": "QWERTY US",
    "AZERTY FR (fr-FR.kl)": "fr-FR.kl",
    "QWERTZ DE (de-DE.kl)": "de-DE.kl",
    "QWERTY US (en-US.kl)": "en-US.kl",
}


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
        self._send_task = None
        self._layout_task = None
        self._awaiting_result = False
        self._notifications = NotificationLines()
        self._ready = asyncio.Event()
        self._received = asyncio.Event()
        self._layout_event = asyncio.Event()
        self._layout_result = None
        self._layout_error = None
        self._receive_error = None
        self._max_text_bytes = MAX_TEXT_BYTES
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

    def execute(self) -> None:
        self._submit(self._execute())

    def set_layout(self, name: str) -> None:
        self._submit(self._set_layout(name))

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
        self._emit("log", ("i18n", "scan_start", {"seconds": GUI_SCAN_TIMEOUT}))

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
            self._emit("diagnostic", exc)
            self._emit("log", ("i18n", "service_hint", {}))
            self._emit("scanning", False)
            return

        n_flippers = sum(1 for entry in found.values() if entry[2])
        self._emit(
            "log",
            ("i18n", "devices_found", {"count": len(found), "flippers": n_flippers}),
        )
        self._emit("devices", _order_devices(found))
        if not found:
            self._emit("log", ("i18n", "no_device_log", {}))
        self._emit("scanning", False)

    async def _connect(self, address: str, name: str) -> None:
        if self._connect_task is not None or self._client is not None:
            return
        self._connect_task = asyncio.current_task()
        self._emit("log", ("i18n", "connecting_log", {"name": name}))
        self._emit("log", ("i18n", "pairing", {}))
        try:
            device = self._devices.get(address)
            if device is None:
                device = await BleakScanner.find_device_by_address(address, timeout=12.0)
                if device is None:
                    raise RuntimeError("Device not found — scan again.")
                self._devices[address] = device
            self._notifications = NotificationLines()
            self._ready = asyncio.Event()
            self._received = asyncio.Event()
            self._layout_event = asyncio.Event()
            self._layout_result = None
            self._layout_error = None
            self._awaiting_result = False
            self._receive_error = None
            client = BleakClient(
                device, timeout=60.0, pair=True,
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
                    "Flipper serial service not found — open TransTheFlip, "
                    "close other Bluetooth clients, then reconnect."
                )

            await client.start_notify(
                FLIPPER_TX_CHAR_UUID,
                lambda characteristic, data: self._on_notify(characteristic, data)
                if self._client is client else None,
            )
            await client.write_gatt_char(FLIPPER_RX_CHAR_UUID, b"TTF?\n", response=True)
            try:
                await asyncio.wait_for(self._ready.wait(), 5.0)
            except asyncio.TimeoutError:
                raise RuntimeError("TransTheFlip did not answer the TTF1 protocol. Install the latest Flipper app and open it.") from None
            if self._receive_error:
                raise RuntimeError(self._receive_error)
            if self._client is not client or not client.is_connected:
                raise RuntimeError("Device disconnected during setup.")
            self._emit("connected", name)
        except asyncio.CancelledError:
            await self._disconnect()
            raise
        except Exception as exc:  # noqa: BLE001
            self._emit("diagnostic", exc)
            await self._disconnect()
        finally:
            self._connect_task = None

    async def _disconnect(self) -> None:
        task = self._connect_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return
        send_task = self._send_task
        if send_task is not None and send_task is not asyncio.current_task():
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
        layout_task = getattr(self, "_layout_task", None)
        if layout_task is not None and layout_task is not asyncio.current_task():
            layout_task.cancel()
            await asyncio.gather(layout_task, return_exceptions=True)
        self._awaiting_result = False
        if self._client is not None:
            client = self._client
            self._client = None
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                self._emit("log", ("i18n", "disconnect_error", {"error": str(exc)}))
            self._client = None
            self._emit("log", ("i18n", "disconnected_log", {}))
        self._emit("disconnected", None)

    async def _send(self, text: str) -> None:
        client = self._client
        if getattr(self, "_layout_task", None) is not None:
            self._emit("send_error", "Wait for keyboard layout confirmation before sending.")
            return
        if client is None or not client.is_connected:
            self._emit("send_error", "Not connected. The text is kept.")
            return
        if self._awaiting_result:
            self._emit("log", "A transfer is already waiting for the Flipper result.")
            return
        self._send_task = asyncio.current_task()
        self._awaiting_result = True
        self._received.clear()
        self._receive_error = None
        try:
            await write_text(client, text, lambda value: self._emit("transfer_progress", value),
                             max_bytes=self._max_text_bytes)
            await asyncio.wait_for(self._received.wait(), 7.0)
            if self._receive_error:
                raise RuntimeError(self._receive_error)
        except asyncio.CancelledError:
            self._awaiting_result = False
            self._emit("send_error", "Transfer interrupted. The text is kept.")
            raise
        except Exception as exc:  # noqa: BLE001
            self._awaiting_result = False
            message = "No receipt from the Flipper." if isinstance(exc, TimeoutError) else str(exc)
            self._emit("send_error", message + " Text kept; reconnect before retrying.")
            await self._disconnect()
        finally:
            self._send_task = None

    async def _execute(self) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("send_error", "Not connected. The text is kept.")
            return
        if getattr(self, "_layout_task", None) is not None:
            self._emit("send_error", "Wait for keyboard layout confirmation before sending.")
            return
        if not self._awaiting_result:
            self._emit("log", "No text is waiting on the Flipper.")
            return
        try:
            await client.write_gatt_char(RX_UUID, EXECUTE_COMMAND, response=True)
            self._emit("log", "▶  Execution requested from the PC.")
        except Exception as exc:  # noqa: BLE001
            self._emit("send_error", f"Execution failed: {exc}")

    async def _set_layout(self, name: str) -> None:
        client = self._client
        if client is None or not client.is_connected:
            self._emit("layout_error", "Not connected.")
            return
        if self._awaiting_result or self._layout_task is not None:
            self._emit("layout_error", "Finish the current transfer before changing the layout.")
            return
        self._layout_task = asyncio.current_task()
        self._layout_event = asyncio.Event()
        self._layout_result = None
        self._layout_error = None
        try:
            await client.write_gatt_char(RX_UUID, encode_layout(name), response=True)
            await asyncio.wait_for(self._layout_event.wait(), 5.0)
            if self._layout_error:
                raise RuntimeError(self._layout_error)
            if self._layout_result is None:
                raise RuntimeError("No layout confirmation from the Flipper.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            message = "No layout confirmation from the Flipper." if isinstance(exc, TimeoutError) else str(exc)
            self._emit("layout_error", message)
            if isinstance(exc, TimeoutError):
                await self._disconnect()
        finally:
            self._layout_task = None

    # ---- bleak callbacks (asyncio thread) ----
    def _on_notify(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        for msg in self._notifications.feed(data):
            capacity = peer_capacity(msg)
            if capacity is not None:
                self._max_text_bytes = capacity
                self._emit("capacity", capacity)
                self._ready.set()
                continue
            if msg == "RECV":
                self._received.set()
            elif msg.startswith("ERR"):
                if getattr(self, "_layout_task", None) is not None:
                    self._layout_error = msg
                    self._layout_event.set()
                    continue
                self._receive_error = STATUS_TEXT.get(msg, msg)
                self._received.set()
                if self._connect_task is not None:
                    self._ready.set()
                self._awaiting_result = False
            elif msg in ("OK", "CANCEL"):
                self._awaiting_result = False
            elif msg.startswith("LAYOUT:"):
                self._layout_result = msg.split(":", 1)[1]
                if hasattr(self, "_layout_event"):
                    self._layout_event.set()
                self._emit("layout_status", self._layout_result)
                continue
            self._emit("notify", msg)

    def _on_disconnected(self, _client: BleakClient) -> None:
        if _client is not self._client:
            return
        self._client = None
        self._awaiting_result = False
        self._receive_error = "Bluetooth connection lost."
        self._received.set()
        if getattr(self, "_layout_task", None) is not None:
            self._layout_error = "Bluetooth connection lost."
            self._layout_event.set()
        self._emit("log", "🔌  Link lost (device disconnected).")
        self._emit("disconnected", None)


# ============================================================
# GUI
# ============================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        super().__init__()
        self._language = "fr"
        self._connected_name = ""
        self.title(GUI_TEXT[self._language]["title"])
        self.geometry("900x800")
        self.minsize(740, 650)
        self.configure(fg_color="#101318")

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
        self._pending_text = None
        self._max_text_bytes = MAX_TEXT_BYTES
        self._transfer_stage = "idle"
        self._history = []  # Session only: sent commands are never saved to disk.
        self._last_device = load_last_device()

        self._build_ui()
        if self._last_device:
            self._populate_devices([(self._last_device["name"], self._last_device["address"])])
            self._log(self._tr("remembered"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_events)

    # ---- layout ----
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        accent = "#FF902E"
        secondary = dict(fg_color="#29313D", hover_color="#364252", height=34, corner_radius=8)
        menu_style = dict(fg_color="#222A35", button_color="#354152",
                          button_hover_color="#46566C", height=32, corner_radius=8)
        heading_font = ctk.CTkFont(size=14, weight="bold")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="TransTheFlip", text_color=accent,
                     font=ctk.CTkFont(size=26, weight="bold"), anchor="w").grid(row=0, column=0, sticky="w")
        self.subtitle_label = ctk.CTkLabel(header, text=self._tr("subtitle"), text_color="#A5AFBE", anchor="w")
        self.subtitle_label.grid(row=1, column=0, sticky="w")
        self.language_menu = ctk.CTkOptionMenu(header, values=["Français", "English"],
                                               width=115, command=self._on_language_change, **menu_style)
        self.language_menu.set("Français")
        self.language_menu.grid(row=0, column=1, rowspan=2)

        bar = ctk.CTkFrame(self, fg_color="#1A2029", corner_radius=12)
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.connection_heading = ctk.CTkLabel(bar, text=self._tr("connection_section"), font=heading_font)
        self.connection_heading.grid(row=0, column=0, sticky="w", padx=16, pady=(8, 0))
        self.status_label = ctk.CTkLabel(bar, text=self._tr("disconnected"), text_color="#e05555", anchor="e")
        self.status_label.grid(row=0, column=1, columnspan=3, sticky="e", padx=16)
        self.device_var = ctk.StringVar(value=self._tr("scan_first"))
        self.device_menu = ctk.CTkOptionMenu(bar, values=[self._tr("scan_first")], variable=self.device_var,
                                             width=210, **menu_style)
        self.device_menu.grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(6, 14))
        self.scan_btn = ctk.CTkButton(bar, text=self._tr("scan"), width=100, command=self._on_scan, **secondary)
        self.scan_btn.grid(row=1, column=1, padx=4, pady=(6, 14))
        self.connect_btn = ctk.CTkButton(bar, text=self._tr("connect"), width=100,
                                        command=self._on_connect_click, **secondary)
        self.connect_btn.grid(row=1, column=2, padx=4, pady=(6, 14))
        self.disconnect_btn = ctk.CTkButton(bar, text=self._tr("disconnect"), width=110,
                                           command=self._on_disconnect_click, state="disabled", **secondary)
        self.disconnect_btn.grid(row=1, column=3, padx=(4, 16), pady=(6, 14))

        settings = ctk.CTkFrame(self, fg_color="#1A2029", corner_radius=12)
        settings.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        settings.grid_columnconfigure((0, 1), weight=1)
        self.layout_label = ctk.CTkLabel(settings, text=self._tr("layout"), anchor="w", text_color="#A5AFBE")
        self.layout_label.grid(row=0, column=0, sticky="w", padx=16, pady=(6, 0))
        self.history_label = ctk.CTkLabel(settings, text=self._tr("history"), anchor="w", text_color="#A5AFBE")
        self.history_label.grid(row=0, column=1, sticky="w", padx=16, pady=(6, 0))
        self.layout_menu = ctk.CTkOptionMenu(settings, values=list(LAYOUT_OPTIONS), width=190,
                                            command=self._on_layout_change, state="disabled", **menu_style)
        self.layout_menu.set("QWERTY US")
        self._layout_previous = "QWERTY US"
        self._layout_pending = False
        self.layout_menu.grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(2, 12))
        self.history_menu = ctk.CTkOptionMenu(settings, values=[self._tr("history")],
                                             command=self._restore_history, **menu_style)
        self.history_menu.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=(2, 12))

        entry_frame = ctk.CTkFrame(self, fg_color="#1A2029", corner_radius=12)
        entry_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 10))
        entry_frame.grid_columnconfigure(0, weight=1)
        entry_frame.grid_rowconfigure(1, weight=1)
        self.text_heading = ctk.CTkLabel(entry_frame, text=self._tr("text_section"), font=heading_font)
        self.text_heading.grid(row=0, column=0, sticky="w", padx=16, pady=(8, 4))
        self.entry = ctk.CTkTextbox(entry_frame, height=90, wrap="word", fg_color="#11161E",
                                   text_color="#EDF1F7", corner_radius=8, border_width=1,
                                   border_color="#303A48", font=ctk.CTkFont(family="Consolas", size=14))
        self.entry.grid(row=1, column=0, sticky="nsew", padx=16)
        self.entry.bind("<Control-Return>", self._on_send)
        self.transfer_label = ctk.CTkLabel(entry_frame, text=self._tr("hint", bytes=MAX_TEXT_BYTES),
                                           wraplength=640, anchor="w", justify="left", text_color="#A5AFBE", font=ctk.CTkFont(size=12))
        self.transfer_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 0))
        self.progress_bar = ctk.CTkProgressBar(entry_frame, height=4, progress_color=accent, fg_color="#303A48")
        self.progress_bar.set(0)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 8))
        actions = ctk.CTkFrame(entry_frame, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="e", padx=16, pady=(0, 12))
        self.send_btn = ctk.CTkButton(actions, text=self._tr("send"), width=145, height=38,
                                      fg_color=accent, hover_color="#E77B19", text_color="#151515",
                                      text_color_disabled="#77716A", font=heading_font,
                                      command=self._on_send, state="disabled", corner_radius=8)
        self.send_btn.grid(row=0, column=0, padx=(0, 10))
        self.execute_btn = ctk.CTkButton(actions, text=self._tr("execute"), width=205,
                                         border_width=1, border_color=accent, command=self._on_execute,
                                         state="disabled", **secondary)
        self.execute_btn.grid(row=0, column=1)

        keys_frame = ctk.CTkFrame(self, fg_color="transparent")
        keys_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 6))
        self.keys_heading = ctk.CTkLabel(keys_frame, text=self._tr("keys_section"), font=heading_font)
        self.keys_heading.grid(row=0, column=0, columnspan=5, sticky="w", padx=4)
        for i in range(5):
            keys_frame.grid_columnconfigure(i, weight=1, uniform="keys")
        for idx, tag in enumerate(SPECIAL_KEYS):
            btn = ctk.CTkButton(keys_frame, text=tag, height=26, width=80,
                                fg_color="#222A35", hover_color="#364252", text_color="#C4CCD8",
                                corner_radius=6, font=ctk.CTkFont(size=12),
                                command=lambda t=tag: self._insert_key(t))
            btn.grid(row=1 + idx // 5, column=idx % 5, padx=4, pady=3, sticky="ew")

        self.log_frame = ctk.CTkFrame(self, fg_color="#1A2029", corner_radius=12)
        self.log_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 16))
        self.log_frame.grid_columnconfigure(0, weight=1)
        self._log_visible = False
        self.log_toggle = ctk.CTkButton(self.log_frame, text=self._tr("show_log"), anchor="w",
                                        fg_color="transparent", hover_color="#29313D", height=32,
                                        text_color="#A5AFBE", command=self._toggle_log)
        self.log_toggle.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        self.log_box = ctk.CTkTextbox(self.log_frame, height=120, wrap="word", fg_color="#11161E",
                                     font=ctk.CTkFont(size=12))
        self.log_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.log_box.grid_remove()
        self.log_box.configure(state="disabled")
        self._log(self._tr("ready"))

    def _toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.minsize(740, 800)
            if self.winfo_height() < 800:
                self.geometry(f"{self.winfo_width()}x800")
            self.log_box.grid()
        else:
            self.log_box.grid_remove()
            self.minsize(740, 650)
        self.log_toggle.configure(text=self._tr("hide_log" if self._log_visible else "show_log"))

    # ---- helpers ----
    def _tr(self, key: str, **values) -> str:
        return GUI_TEXT[getattr(self, "_language", "fr")][key].format(**values)

    def _localize_text(self, text: str) -> str:
        language = getattr(self, "_language", "fr")
        if language == "en":
            return text
        for code, english in STATUS_TEXT.items():
            if text == english:
                return STATUS_TEXT_FR.get(code, text)
        replacements = {
            "Not connected. The text is kept.": App._tr(self, "not_connected"),
            "Not connected.": App._tr(self, "not_connected"),
            "Transfer interrupted. The text is kept.": App._tr(self, "send_error"),
            "A transfer is already waiting for the Flipper result.": App._tr(self, "already_waiting"),
            "No text is waiting on the Flipper.": App._tr(self, "no_pending"),
            "Finish the current transfer before changing the layout.": "Terminez le transfert en cours avant de changer la disposition.",
            "ERR:LAYOUT": "Disposition introuvable sur le Flipper.",
            "ERR:PROTOCOL": "Le FAP ne prend pas en charge le changement de disposition.",
            "No layout confirmation from the Flipper.": "Aucune confirmation de disposition reçue du Flipper.",
            "Bluetooth connection lost.": "Connexion Bluetooth perdue.",
            "Text is empty.": "Le texte est vide.",
            "Unsupported character: use ASCII text, tabs, and line breaks.": "Caractère non pris en charge : utilisez du texte ASCII, des tabulations et des retours à la ligne.",
        }
        if text in replacements:
            return replacements[text]
        if text.startswith("Text too long:"):
            return text.replace("Text too long:", "Texte trop long:").replace(" bytes, maximum ", " octets, maximum ").replace(" (including tags).", " (tags compris).")
        if text.startswith("No receipt from the Flipper."):
            return App._tr(self, "receipt_timeout") + text[len("No receipt from the Flipper."):]
        if text.endswith(" Text kept; reconnect before retrying."):
            return text[:-len(" Text kept; reconnect before retrying.")] + App._tr(self, "retry_suffix")
        if text.startswith("Execution failed: "):
            return App._tr(self, "execution_error", error=text.removeprefix("Execution failed: "))
        if text.startswith("Device not found"):
            return App._tr(self, "device_not_found")
        if text.startswith("Flipper serial service not found"):
            return App._tr(self, "service_not_found")
        if text.startswith("TransTheFlip did not answer"):
            return App._tr(self, "protocol_timeout")
        if text == "Device disconnected during setup.":
            return App._tr(self, "disconnected_setup")
        if text.startswith("🔌  Link lost"):
            return App._tr(self, "link_lost")
        if text.startswith("Connection lost or closed"):
            return App._tr(self, "connection_lost")
        return text

    def _on_language_change(self, label: str) -> None:
        self._language = "en" if label == "English" else "fr"
        self._apply_language()

    def _apply_language(self) -> None:
        self.title(self._tr("title"))
        self.scan_btn.configure(text=self._tr("scan"))
        self.connect_btn.configure(text=self._tr("connect"))
        self.disconnect_btn.configure(text=self._tr("disconnect"))
        self.send_btn.configure(text=self._tr("send"))
        self.execute_btn.configure(text=self._tr("execute"))
        self.layout_label.configure(text=self._tr("layout"))
        self.subtitle_label.configure(text=self._tr("subtitle"))
        self.connection_heading.configure(text=self._tr("connection_section"))
        self.history_label.configure(text=self._tr("history"))
        self.text_heading.configure(text=self._tr("text_section"))
        self.keys_heading.configure(text=self._tr("keys_section"))
        self.log_toggle.configure(text=self._tr("hide_log" if self._log_visible else "show_log"))
        self.transfer_label.configure(text=self._tr("hint", bytes=self._max_text_bytes))
        if self._history:
            self.history_menu.configure(values=[f"{i}. {item[:45].replace(chr(10), ' ↵ ')}" for i, item in enumerate(self._history, 1)])
        else:
            self.history_menu.configure(values=[self._tr("history")])
            self.history_menu.set(self._tr("history"))
        if not self._dev_map:
            self.device_menu.configure(values=[self._tr("scan_first")])
            self.device_var.set(self._tr("scan_first"))
        if self._connected:
            self._set_status(self._tr("connected", name=self._connected_name), "#55cc66")
        elif self._busy:
            self._set_status(self._tr("connecting"), "#e0a955")
        else:
            self._set_status(self._tr("disconnected"), "#e05555")

    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _insert_key(self, tag: str) -> None:
        self.entry.insert(self.entry.index("insert"), tag)
        self.entry.focus_set()

    def _restore_history(self, label):
        try:
            text = self._history[int(label.split(".", 1)[0]) - 1]
        except (ValueError, IndexError):
            return
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", text)

    def _finish_transfer(self, message):
        self._pending_text = None
        self._transfer_stage = "idle"
        self.transfer_label.configure(text=message)
        self.send_btn.configure(state="normal" if self._connected else "disabled")
        self.execute_btn.configure(state="disabled")
        if hasattr(self, "layout_menu"):
            self.layout_menu.configure(state="normal" if self._connected else "disabled")

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
            self._log(self._tr("select_device"))
            return
        self.connect_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")
        self.device_menu.configure(state="disabled")
        self.disconnect_btn.configure(state="normal")
        self._busy = True
        self._set_status(self._tr("connecting"), "#e0a955")
        self._worker.connect(address, label)

    def _on_disconnect_click(self) -> None:
        self._connected = False
        self.disconnect_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self.execute_btn.configure(state="disabled")
        if hasattr(self, "layout_menu"):
            self.layout_menu.configure(state="disabled")
        self._set_status(App._tr(self, "disconnecting"), "#e0a955")
        self._worker.disconnect()

    def _on_send(self, _event=None) -> None:
        if getattr(self, "_layout_pending", False):
            return "break"
        text = self.entry.get("1.0", "end-1c")
        if not text:
            return "break"
        if self._pending_text is not None:
            return "break"
        if not self._connected:
            self._log(self._tr("not_connected"))
            return "break"
        try:
            encode_text(text, self._max_text_bytes)
        except ValueError as exc:
            self.transfer_label.configure(text=self._localize_text(str(exc)))
            return "break"
        self._pending_text = text
        self._transfer_stage = "transfer"
        self.send_btn.configure(state="disabled")
        if hasattr(self, "layout_menu"):
            self.layout_menu.configure(state="disabled")
        self.progress_bar.set(0)
        self.transfer_label.configure(text=App._tr(self, "transfer"))
        self._log(f"→  {text}")
        self._worker.send(text)
        return "break"

    def _on_execute(self) -> None:
        if self._pending_text is None or self._transfer_stage != "RECV":
            return
        self.execute_btn.configure(state="disabled")
        self.transfer_label.configure(text=App._tr(self, "execute_requested"))
        self._worker.execute()

    def _on_layout_change(self, label: str) -> None:
        if not self._connected or label not in LAYOUT_OPTIONS or self._pending_text is not None:
            return
        self._layout_pending = True
        self.layout_menu.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        self._worker.set_layout(LAYOUT_OPTIONS[label])

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
            if isinstance(payload, tuple) and len(payload) == 3 and payload[0] == "i18n":
                self._log(App._tr(self, str(payload[1]), **payload[2]))
            else:
                self._log(self._localize_text(str(payload)))
        elif kind == "diagnostic":
            self._log(bluetooth_diagnostic(payload, getattr(self, "_language", "fr")))
        elif kind == "scanning":
            if payload:
                if not self._connected:
                    self._set_status(App._tr(self, "scanning"), "#e0a955")
            else:
                if not self._connected and not self._busy:
                    self.scan_btn.configure(state="normal")
                    self.connect_btn.configure(state="normal")
                    self._set_status(App._tr(self, "disconnected"), "#e05555")
        elif kind == "devices":
            self._populate_devices(payload)  # type: ignore[arg-type]
        elif kind == "capacity":
            self._max_text_bytes = int(payload)
            message = App._tr(self, "capacity", bytes=payload)
            if self._max_text_bytes < MAX_TEXT_BYTES:
                message += App._tr(self, "upgrade_capacity", bytes=MAX_TEXT_BYTES)
            self.transfer_label.configure(text=message)
            self._log(message)
        elif kind == "connected":
            self._connected = True
            self._busy = False
            self._connected_name = str(payload)
            self.connect_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            self.send_btn.configure(state="normal")
            if hasattr(self, "layout_menu"):
                self.layout_menu.configure(state="normal")
            self._set_status(App._tr(self, "connected", name=payload), "#55cc66")
            address = self._dev_map.get(str(payload))
            if address:
                try:
                    save_last_device(address, str(payload))
                except OSError as exc:
                    self._log(App._tr(self, "remember_error", error=exc))
        elif kind == "disconnected":
            self._layout_pending = False
            self._connected = False
            self._busy = False
            self.connect_btn.configure(state="normal")
            self.scan_btn.configure(state="normal")
            self.device_menu.configure(state="normal")
            self.disconnect_btn.configure(state="disabled")
            self.send_btn.configure(state="disabled")
            self.execute_btn.configure(state="disabled")
            if hasattr(self, "layout_menu"):
                self.layout_menu.configure(state="disabled")
            self._connected_name = ""
            self._set_status(App._tr(self, "disconnected"), "#e05555")
            if self._pending_text is not None:
                self._finish_transfer(App._tr(self, "connection_lost"))
        elif kind == "transfer_progress":
            if self._pending_text is not None and self._transfer_stage == "transfer":
                self.progress_bar.set(float(payload))
                self.transfer_label.configure(text=App._tr(self, "transfer_progress", percent=float(payload)))
        elif kind == "send_error":
            message = App._localize_text(self, str(payload))
            self._log(message)
            self._finish_transfer(message)
        elif kind in ("layout", "layout_status"):
            self._layout_pending = False
            layout_name = str(payload)
            if hasattr(self, "layout_menu"):
                for label, wire_name in LAYOUT_OPTIONS.items():
                    if layout_name == "QWERTY US" and wire_name == "QWERTY US":
                        self.layout_menu.set(label)
                    elif layout_name == wire_name.removesuffix(".kl"):
                        self.layout_menu.set(label)
                self._layout_previous = self.layout_menu.get()
                self.layout_menu.configure(state="normal" if self._connected and self._pending_text is None else "disabled")
            self.send_btn.configure(state="normal" if self._connected and self._pending_text is None else "disabled")
            self._log(App._tr(self, "layout_changed", name=layout_name))
        elif kind == "layout_error":
            self._layout_pending = False
            message = App._localize_text(self, str(payload))
            if hasattr(self, "layout_menu"):
                self.layout_menu.set(getattr(self, "_layout_previous", "QWERTY US"))
                self.layout_menu.configure(state="normal" if self._connected and self._pending_text is None else "disabled")
            self.send_btn.configure(state="normal" if self._connected and self._pending_text is None else "disabled")
            self._log(App._tr(self, "layout_error", error=message))
        elif kind == "notify":
            msg = str(payload)
            self._transfer_stage = msg
            language = getattr(self, "_language", "fr")
            message = STATUS_TEXT_FR.get(msg, f"Flipper : {msg}") if language == "fr" else STATUS_TEXT.get(msg, f"Flipper: {msg}")
            if msg.startswith("PROGRESS:"):
                try:
                    percent = max(0, min(100, int(msg.split(":", 1)[1])))
                except ValueError:
                    return
                self.progress_bar.set(percent / 100)
                self.transfer_label.configure(text=App._tr(self, "hid_progress", percent=percent))
                return
            self._log(message)
            self.transfer_label.configure(text=message)
            if msg in ("RECV", "SENDING"):
                self.progress_bar.set(0)
            if msg == "RECV" and self._pending_text is not None:
                self.execute_btn.configure(state="normal")
            elif msg == "SENDING":
                self.execute_btn.configure(state="disabled")
            if msg == "OK":
                if self._pending_text is not None:
                    text = self._pending_text
                    self._history = [text] + [item for item in self._history if item != text][:19]
                    self.history_menu.configure(values=[f"{i}. {item[:45].replace(chr(10), ' ↵ ')}" for i, item in enumerate(self._history, 1)])
                self.progress_bar.set(1)
            if msg in ("OK", "CANCEL") or msg.startswith("ERR"):
                self._finish_transfer(message)

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
            placeholder = App._tr(self, "no_devices")
            self.device_menu.configure(values=[placeholder])
            self.device_var.set(placeholder)

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
