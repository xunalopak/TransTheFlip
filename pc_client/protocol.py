"""Framed transfers shared by GUI and CLI; requires the matching Flipper app."""
import asyncio
import zlib

RX_UUID = "19ed82ae-ed21-4c9d-4145-228e62fe0000"
MAX_TEXT_BYTES = 255
CHUNK_SIZE = 20

STATUS_TEXT = {
    "RECV": "Texte vérifié — en attente de confirmation sur le Flipper.",
    "WAIT_USB": "En attente du branchement USB au PC cible.",
    "SENDING": "Frappe en cours sur le PC cible…",
    "OK": "Terminé : frappe confirmée par le Flipper.",
    "CANCEL": "Annulé sur le Flipper. Le texte est conservé.",
    "ERR:HID": "Échec de frappe : vérifiez la connexion USB au PC cible.",
    "ERR:LENGTH": "Texte trop long : maximum 255 octets, tags compris.",
    "ERR:CHECKSUM": "Transfert corrompu ou incomplet : reconnectez-vous avant de réessayer.",
    "ERR:TIMEOUT": "Transfert incomplet : reconnectez-vous avant de réessayer.",
    "ERR:OVERFLOW": "Données Bluetooth perdues : reconnectez-vous avant de réessayer.",
    "ERR:PROTOCOL": "Protocole incompatible : installez la nouvelle application Flipper.",
    "ERR:CHAR": "Caractère non pris en charge par le clavier du Flipper.",
    "ERR:BUSY": "Flipper occupé : terminez ou annulez l’envoi sur le Flipper.",
    "ERR:MEMORY": "Mémoire insuffisante sur le Flipper.",
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


def encode_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        raise ValueError("Le texte est vide.")
    if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\t") for c in text):
        raise ValueError("Caractère non pris en charge : utilisez du texte ASCII, des tabulations et des retours à la ligne.")
    payload = text.encode("ascii")
    if len(payload) > MAX_TEXT_BYTES:
        raise ValueError(f"Texte trop long : {len(payload)} octets, maximum {MAX_TEXT_BYTES} (tags compris).")
    return f"TTF1 {len(payload)} {zlib.crc32(payload):08x}\n".encode("ascii") + payload


async def write_text(client, text, progress=lambda value: None):
    frame = encode_text(text)
    for offset in range(0, len(frame), CHUNK_SIZE):
        await client.write_gatt_char(RX_UUID, frame[offset:offset + CHUNK_SIZE], response=True)
        progress(min(offset + CHUNK_SIZE, len(frame)) / len(frame))
        if offset + CHUNK_SIZE < len(frame):
            await asyncio.sleep(0.05)


def bluetooth_diagnostic(exc):
    detail = str(exc) or type(exc).__name__
    lower = detail.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        hint = "Délai dépassé : rapprochez le Flipper, ouvrez TransTheFlip et confirmez l’appairage."
    elif any(word in lower for word in ("pair", "auth", "denied", "access", "0x80070005")):
        hint = "Appairage refusé : confirmez le code sur le Flipper. Si nécessaire, supprimez l’ancien appairage Windows puis recommencez."
    elif any(word in lower for word in ("not found", "not available", "unreachable")):
        hint = "Appareil ou service introuvable : activez le Bluetooth, ouvrez TransTheFlip sur le Flipper et relancez Scan."
    else:
        hint = "Vérifiez le Bluetooth, ouvrez TransTheFlip et fermez les autres clients Bluetooth avant de réessayer."
    return f"{hint}\nDétail : {detail}"
