#!/usr/bin/env python3
"""
TransTheFlip — PC Client
Sends text to the Flipper Zero over BLE (Flipper serial service).
The Flipper receives the text, waits for confirmation, then types it
as USB HID keystrokes on the target PC.

Special key syntax (inline in the text):
  [ENTER]             Enter key
  [TAB]               Tab
  [ESC]               Escape
  [BACKSPACE]         Backspace
  [DEL]               Delete
  [UP/DOWN/LEFT/RIGHT]  Arrow keys
  [F1]..[F12]         Function keys
  [CTRL+c]            Key combos (modifier + key)
  [ALT+F4]
  [WIN+r]
  [CTRL+SHIFT+ESC]
  [DELAY:500]         Pause 500 ms on the Flipper

Examples:
  Hello World[ENTER]
  [WIN+r]notepad[ENTER]
  [CTRL+a][DEL]
  ipconfig /all[ENTER]

Usage:
  pip install -r requirements.txt
  python trans_client.py
"""

import asyncio
import sys
from typing import Optional
from protocol import NotificationLines, STATUS_TEXT, write_text, bluetooth_diagnostic

try:
    from bleak import BleakScanner, BleakClient
    from bleak.backends.device import BLEDevice
    from bleak.backends.characteristic import BleakGATTCharacteristic
except ImportError:
    print("❌  bleak is not installed. Run:")
    print("    pip install bleak")
    sys.exit(1)

# ============================================================
# Flipper Zero proprietary BLE serial service UUIDs
# (discovered by GATT introspection — not standard NUS)
# ============================================================
FLIPPER_SERVICE_UUID = "8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000"
FLIPPER_RX_CHAR_UUID = "19ed82ae-ed21-4c9d-4145-228e62fe0000"  # PC → Flipper (Write)
# FE61 carries serial indications; FE63 only reports available receive-buffer space.
FLIPPER_TX_CHAR_UUID = "19ed82ae-ed21-4c9d-4145-228e61fe0000"  # Flipper → PC (Indicate)

# Aliases for compatibility
NUS_SERVICE_UUID = FLIPPER_SERVICE_UUID
NUS_RX_CHAR_UUID = FLIPPER_RX_CHAR_UUID
NUS_TX_CHAR_UUID = FLIPPER_TX_CHAR_UUID

# Max BLE chunk size (safe for BLE 4.x without DLE)
BLE_CHUNK_SIZE = 20

# BLE scan timeout (seconds)
SCAN_TIMEOUT = 12.0

# ============================================================
# Notification handler (Flipper → PC status messages)
# ============================================================
_last_status: str = ""
_notifications = NotificationLines()
_status_queue = None

def _notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
    global _last_status
    for msg in _notifications.feed(data):
        _last_status = msg
        if _status_queue is not None:
            _status_queue.put_nowait(msg)
        print(STATUS_TEXT.get(msg, f"Flipper: {msg}"))


# ============================================================
# BLE scan and Flipper selection
# ============================================================
async def scan_for_flipper() -> Optional[BLEDevice]:
    print(f"🔍  Scanning BLE ({SCAN_TIMEOUT:.0f}s)...")

    results = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)

    if results:
        print(f"📡  {len(results)} BLE device(s) found:")
        for dev, adv in sorted(results.values(), key=lambda x: x[0].name or ""):
            uuids = ", ".join(adv.service_uuids[:2]) if adv.service_uuids else "—"
            print(f"     • {dev.name or '(no name)':<30} {dev.address}  [{uuids}]")
    else:
        print("⚠️   No BLE devices found at all (is Bluetooth enabled on this PC?)")

    flippers: list[BLEDevice] = []
    for dev, adv in results.values():
        uuids_lower = [u.lower() for u in (adv.service_uuids or [])]
        # Criterion 1: Flipper serial service advertised
        if FLIPPER_SERVICE_UUID in uuids_lower:
            flippers.append(dev)
            continue
        # Criterion 2: device name contains "flipper"
        if dev.name and "flipper" in dev.name.lower():
            flippers.append(dev)

    if not flippers:
        print("\n❌  No Flipper Zero detected automatically.")
        print("    → If your Flipper appears in the list above, enter its number:")
        numbered = list(results.values())
        for i, (dev, _) in enumerate(numbered, start=1):
            print(f"       {i}. {dev.name or '(no name)'}  ({dev.address})")
        print(f"       0. Quit")
        try:
            raw = await asyncio.to_thread(input, "Choice: ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(numbered):
                return numbered[idx][0]
        except (ValueError, EOFError):
            pass
        return None

    if len(flippers) == 1:
        dev = flippers[0]
        print(f"✅  Found: {dev.name}  ({dev.address})")
        return dev

    # Multiple Flippers detected
    print("\n📋  Multiple Flipper devices found:")
    for i, d in enumerate(flippers, start=1):
        print(f"    {i}. {d.name}  ({d.address})")

    while True:
        try:
            raw = await asyncio.to_thread(input, "Select number: ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(flippers):
                return flippers[idx]
        except (ValueError, EOFError):
            pass
        print("    Invalid input, try again.")


# ============================================================
# Send text (chunked if > BLE_CHUNK_SIZE)
# ============================================================
async def send_text(client: BleakClient, text: str) -> None:
    """Send a complete CRC-checked frame; never split multiline text into commands."""
    await write_text(client, text)


# ============================================================
# Interactive loop
# ============================================================
async def interactive_loop(client: BleakClient) -> None:
    global _status_queue, _notifications
    _status_queue = asyncio.Queue()
    _notifications = NotificationLines()
    print("\n" + "="*55)
    print("  TransTheFlip Client — Flipper Zero BLE Remote HID")
    print("="*55)
    print("  Syntax  : plain text + [TAGS] for special keys")
    print("  Examples: Hello[ENTER]   [WIN+r]notepad[ENTER]")
    print("            [CTRL+SHIFT+ESC]   [DELAY:1000]")
    print("  Commands: quit / exit  → disconnect")
    print("            help         → show key syntax")
    print("="*55 + "\n")

    await client.start_notify(NUS_TX_CHAR_UUID, _notification_handler)
    await client.write_gatt_char(NUS_RX_CHAR_UUID, b"TTF?\n", response=True)
    try:
        ready = await asyncio.wait_for(_status_queue.get(), 5)
    except asyncio.TimeoutError:
        raise RuntimeError("Installez et ouvrez la nouvelle application Flipper (protocole TTF1).") from None
    if ready != "READY:1:255":
        raise RuntimeError("Flipper occupé ou incompatible : " + ready)

    while True:
        try:
            raw = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print("\n👋  Disconnecting...")
            break

        text = raw.strip()

        if not text:
            continue

        if text.lower() in ("quit", "exit", "q"):
            print("👋  Disconnecting...")
            break

        if text.lower() == "help":
            print("""
  Special keys (inline in the text):
    [ENTER]  [TAB]  [ESC]  [BACKSPACE]  [DEL]
    [UP]  [DOWN]  [LEFT]  [RIGHT]
    [HOME]  [END]  [PGUP]  [PGDN]
    [F1]..[F12]
    [CTRL+<key>]   e.g. [CTRL+c]  [CTRL+SHIFT+ESC]
    [ALT+<key>]    e.g. [ALT+F4]
    [WIN+<key>]    e.g. [WIN+r]
    [DELAY:<ms>]   e.g. [DELAY:500]
""")
            continue

        try:
            await send_text(client, text)
        except ValueError as exc:
            print(exc)
            continue
        # Do not send another frame until local confirmation and typing finish.
        received = False
        while True:
            if not client.is_connected:
                raise RuntimeError("Bluetooth disconnected; result unknown.")
            try:
                status = await asyncio.wait_for(_status_queue.get(), 1 if received else 7)
            except asyncio.TimeoutError:
                if received:
                    continue
                raise RuntimeError("No receipt from Flipper; reconnect before retrying.") from None
            if status == "RECV":
                received = True
            elif status in ("OK", "CANCEL"):
                break
            elif status.startswith("ERR"):
                raise RuntimeError(STATUS_TEXT.get(status, status))


# ============================================================
# Entry point
# ============================================================
async def main() -> None:
    device = await scan_for_flipper()
    if device is None:
        return

    print(f"🔗  Connecting to {device.name}...")
    try:
        async with BleakClient(
            device, timeout=60.0, pair=True, winrt={"use_cached_services": False}
        ) as client:
            if not client.is_connected:
                print("❌  Connection failed.")
                return

            mtu = getattr(client, "mtu_size", BLE_CHUNK_SIZE)
            print(f"✅  Connected! MTU={mtu}")

            # Verify that the Flipper serial service is present
            service_uuids = [s.uuid.lower() for s in client.services]
            if FLIPPER_SERVICE_UUID not in service_uuids:
                print(f"⚠️   Flipper serial service not found ({FLIPPER_SERVICE_UUID})")
                print("    Is the TransTheFlip app running on the Flipper?")
                return

            await interactive_loop(client)

    except Exception as exc:
        print(bluetooth_diagnostic(exc))
        if "not found" in str(exc).lower() or "no such device" in str(exc).lower():
            print("    The device may have turned off or disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋  Keyboard interrupt.")
