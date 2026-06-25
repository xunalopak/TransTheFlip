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
FLIPPER_TX_CHAR_UUID = "19ed82ae-ed21-4c9d-4145-228e63fe0000"  # Flipper → PC (Notify)

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

def _notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
    global _last_status
    msg = data.decode("utf-8", errors="replace").strip()
    # The Flipper only ever sends meaningful status tokens (RECV/OK/ERR/CANCEL).
    # Empty notifications come from the BLE serial profile's flow-control /
    # keep-alive traffic — drop them so they don't spam blank "Flipper:" lines.
    if not msg:
        return
    _last_status = msg
    status_map = {
        "OK":     "✅  Flipper: text sent successfully",
        "ERR":    "❌  Flipper: HID send error",
        "CANCEL": "🚫  Flipper: send cancelled by user",
        "RECV":   "📥  Flipper: text received, waiting for confirmation...",
    }
    display = status_map.get(msg, f"📡  Flipper: {msg}")
    print(f"\r{display}")
    print("> ", end="", flush=True)


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
    """Send text + '\\n' to the Flipper, split into BLE chunks."""
    payload = (text + "\n").encode("utf-8")
    total = len(payload)
    sent = 0

    while sent < total:
        chunk = payload[sent : sent + BLE_CHUNK_SIZE]
        # Write WITH response: the Flipper RX characteristic requires authentication.
        # Without response, a security rejection is silent (fire-and-forget).
        # With response, bleak raises an exception if the write fails.
        await client.write_gatt_char(NUS_RX_CHAR_UUID, chunk, response=True)
        sent += len(chunk)
        if sent < total:
            await asyncio.sleep(0.05)  # Small delay between chunks

    print(f"📤  Sent ({total} bytes). Confirm on the Flipper (OK button).")


# ============================================================
# Interactive loop
# ============================================================
async def interactive_loop(client: BleakClient) -> None:
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

        await send_text(client, text)


# ============================================================
# Entry point
# ============================================================
async def main() -> None:
    device = await scan_for_flipper()
    if device is None:
        return

    print(f"🔗  Connecting to {device.name}...")
    try:
        async with BleakClient(device.address, timeout=15.0) as client:
            if not client.is_connected:
                print("❌  Connection failed.")
                return

            mtu = getattr(client, "mtu_size", BLE_CHUNK_SIZE)
            print(f"✅  Connected! MTU={mtu}")

            # --- Pairing (bonding) is mandatory ---
            # The Flipper serial RX/TX characteristics require ATTR_PERMISSION_AUTHEN:
            # the link must be authenticated and encrypted. Without bonding, any write
            # to RX is silently rejected by the security layer.
            try:
                paired = await client.pair()
                if paired:
                    print("🔐  Pairing established (encrypted link).")
                else:
                    print("⚠️   Pairing not confirmed — confirm the code shown on the Flipper screen.")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️   Automatic pairing failed: {exc}")
                print("    → Windows: Settings > Bluetooth & devices > Add a device,")
                print("      confirm the code shown on the Flipper screen, then restart the client.")

            # Verify that the Flipper serial service is present
            service_uuids = [s.uuid.lower() for s in client.services]
            if FLIPPER_SERVICE_UUID not in service_uuids:
                print(f"⚠️   Flipper serial service not found ({FLIPPER_SERVICE_UUID})")
                print("    Is the TransTheFlip app running on the Flipper?")
                return

            await interactive_loop(client)

    except Exception as exc:
        print(f"❌  BLE error: {exc}")
        if "not found" in str(exc).lower() or "no such device" in str(exc).lower():
            print("    The device may have turned off or disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋  Keyboard interrupt.")
