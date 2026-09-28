"""Framed transfers shared by GUI and CLI; requires the matching Flipper app."""
import asyncio
import zlib
import re

RX_UUID = "19ed82ae-ed21-4c9d-4145-228e62fe0000"
EXECUTE_COMMAND = b"TTFEXEC\n"
MAX_TEXT_BYTES = 65536
CHUNK_SIZE = 20

STATUS_TEXT = {
    "RECV": "Text verified — waiting for confirmation on the Flipper.",
    "WAIT_USB": "Waiting for the USB connection to the target PC.",
    "SENDING": "Typing on the target PC…",
    "OK": "Done: typing confirmed by the Flipper.",
    "CANCEL": "Cancelled on the Flipper. The text is kept.",
    "ERR:HID": "Typing failed: check the USB connection to the target PC.",
    "ERR:LENGTH": "The text exceeds the capacity reported by the Flipper, including tags.",
    "ERR:CHECKSUM": "Corrupted or incomplete transfer: reconnect before retrying.",
    "ERR:TIMEOUT": "Incomplete transfer: reconnect before retrying.",
    "ERR:OVERFLOW": "Bluetooth data was lost: reconnect before retrying.",
    "ERR:PROTOCOL": "Incompatible protocol: install the latest Flipper app.",
    "ERR:CHAR": "Character not supported by the Flipper keyboard.",
    "ERR:BUSY": "Flipper is busy: finish or cancel the transfer on the Flipper.",
    "ERR:MEMORY": "Not enough memory on the Flipper.",
    "ERR:STORAGE": "Read/write error on the Flipper SD card.",
}


class NotificationLines:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 1024:
            self.buffer.clear()
            return ["ERR:PROTOCOL"]
        lines = []
        while b"\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition(b"\n")
            if line.strip():
                lines.append(line.decode("ascii", errors="replace").strip())
        return lines


def peer_capacity(message):
    """Read the firmware limit; retain compatibility with 255-byte Flipper apps."""
    match = re.fullmatch(r"READY:1:([1-9][0-9]{0,5})", message)
    if match and int(match[1]) <= 999999:
        return min(int(match[1]), MAX_TEXT_BYTES)
    return None


def encode_text(text, max_bytes=MAX_TEXT_BYTES):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        raise ValueError("Text is empty.")
    if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\t") for c in text):
        raise ValueError("Unsupported character: use ASCII text, tabs, and line breaks.")
    payload = text.encode("ascii")
    limit = min(max_bytes, MAX_TEXT_BYTES)
    if len(payload) > limit:
        raise ValueError(f"Text too long: {len(payload)} bytes, maximum {limit} (including tags).")
    return f"TTF1 {len(payload)} {zlib.crc32(payload):08x}\n".encode("ascii") + payload


async def write_text(client, text, progress=lambda value: None, max_bytes=MAX_TEXT_BYTES):
    frame = encode_text(text, max_bytes)
    for offset in range(0, len(frame), CHUNK_SIZE):
        await client.write_gatt_char(RX_UUID, frame[offset:offset + CHUNK_SIZE], response=True)
        progress(min(offset + CHUNK_SIZE, len(frame)) / len(frame))
        if offset + CHUNK_SIZE < len(frame):
            await asyncio.sleep(0.05)


def bluetooth_diagnostic(exc, language="en"):
    detail = str(exc) or type(exc).__name__
    lower = detail.lower()
    if language == "fr":
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            hint = "Délai dépassé : rapprochez le Flipper, ouvrez TransTheFlip et confirmez l’appairage."
        elif any(word in lower for word in ("pair", "auth", "denied", "access", "0x80070005")):
            hint = "Appairage refusé : confirmez le code sur le Flipper. Si nécessaire, supprimez l’ancien appairage Windows puis recommencez."
        elif any(word in lower for word in ("not found", "not available", "unreachable")):
            hint = "Appareil ou service introuvable : activez le Bluetooth, ouvrez TransTheFlip sur le Flipper et relancez le scan."
        else:
            hint = "Vérifiez le Bluetooth, ouvrez TransTheFlip et fermez les autres clients Bluetooth avant de réessayer."
        return f"{hint}\nDétail : {detail}"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        hint = "Timed out: move the Flipper closer, open TransTheFlip, and confirm pairing."
    elif any(word in lower for word in ("pair", "auth", "denied", "access", "0x80070005")):
        hint = "Pairing was refused: confirm the code on the Flipper. If needed, remove the old Windows pairing and try again."
    elif any(word in lower for word in ("not found", "not available", "unreachable")):
        hint = "Device or service not found: enable Bluetooth, open TransTheFlip on the Flipper, and scan again."
    else:
        hint = "Check Bluetooth, open TransTheFlip, and close other Bluetooth clients before trying again."
    return f"{hint}\nDetails: {detail}"
