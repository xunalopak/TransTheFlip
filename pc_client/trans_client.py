#!/usr/bin/env python3
"""
TransTheFlip — PC Client
Envoie du texte au Flipper Zero via BLE (Nordic UART Service).
Le Flipper reçoit le texte, demande une confirmation, puis le tape
via USB HID sur le PC cible.

Syntaxe des touches spéciales dans le texte :
  [ENTER]           Touche Entrée
  [TAB]             Tabulation
  [ESC]             Échap
  [BACKSPACE]       Retour arrière
  [DEL]             Supprimer
  [UP/DOWN/LEFT/RIGHT]  Flèches directionnelles
  [F1]..[F12]       Touches de fonction
  [CTRL+c]          Combinaisons (modificateurs + touche)
  [ALT+F4]
  [WIN+r]
  [CTRL+SHIFT+ESC]
  [DELAY:500]       Pause 500 ms sur le Flipper

Exemples :
  Hello World[ENTER]
  [WIN+r]notepad[ENTER]
  [CTRL+a][DEL]
  ipconfig /all[ENTER]

Utilisation :
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
    print("❌  bleak n'est pas installé. Exécutez :")
    print("    pip install bleak")
    sys.exit(1)

# ============================================================
# UUIDs du service BLE serial propriétaire Flipper Zero
# (découverts par introspection GATT — pas NUS standard)
# ============================================================
FLIPPER_SERVICE_UUID = "8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000"
FLIPPER_RX_CHAR_UUID = "19ed82ae-ed21-4c9d-4145-228e62fe0000"  # PC → Flipper (Write)
FLIPPER_TX_CHAR_UUID = "19ed82ae-ed21-4c9d-4145-228e63fe0000"  # Flipper → PC (Notify)

# Alias pour compatibilité avec le reste du code
NUS_SERVICE_UUID = FLIPPER_SERVICE_UUID
NUS_RX_CHAR_UUID = FLIPPER_RX_CHAR_UUID
NUS_TX_CHAR_UUID = FLIPPER_TX_CHAR_UUID

# Taille max d'un chunk BLE (sécuritaire pour BLE 4.x sans DLE)
BLE_CHUNK_SIZE = 20

# Timeout de scan BLE (secondes)
SCAN_TIMEOUT = 12.0

# ============================================================
# Gestion des notifications depuis le Flipper
# ============================================================
_last_status: str = ""

def _notification_handler(characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
    global _last_status
    msg = data.decode("utf-8", errors="replace").strip()
    _last_status = msg
    status_map = {
        "OK":     "✅  Flipper : texte envoyé avec succès",
        "ERR":    "❌  Flipper : erreur lors de l'envoi HID",
        "CANCEL": "🚫  Flipper : envoi annulé par l'utilisateur",
        "RECV":   "📥  Flipper : texte reçu, en attente de confirmation...",
    }
    display = status_map.get(msg, f"📡  Flipper : {msg}")
    print(f"\r{display}")
    print("> ", end="", flush=True)


# ============================================================
# Scan et sélection du Flipper
# ============================================================
async def scan_for_flipper() -> Optional[BLEDevice]:
    print(f"🔍  Scan BLE en cours ({SCAN_TIMEOUT:.0f}s)...")

    # BleakScanner.discover() renvoie des tuples (BLEDevice, AdvertisementData)
    # depuis bleak 0.20+. On utilise return_adv=True pour obtenir les deux.
    results = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
    # results : dict { address: (BLEDevice, AdvertisementData) }

    # --- Debug : afficher tous les appareils détectés ---
    if results:
        print(f"📡  {len(results)} appareil(s) BLE détecté(s) :")
        for dev, adv in sorted(results.values(), key=lambda x: x[0].name or ""):
            uuids = ", ".join(adv.service_uuids[:2]) if adv.service_uuids else "—"
            print(f"     • {dev.name or '(sans nom)':<30} {dev.address}  [{uuids}]")
    else:
        print("⚠️   Aucun appareil BLE détecté du tout (BLE désactivé sur ce PC ?)")

    flippers: list[BLEDevice] = []
    for dev, adv in results.values():
        uuids_lower = [u.lower() for u in (adv.service_uuids or [])]
        # Critère 1 : service Flipper serial propriétaire annoncé
        if FLIPPER_SERVICE_UUID in uuids_lower:
            flippers.append(dev)
            continue
        # Critère 2 : nom contient "flipper" (nom par défaut)
        if dev.name and "flipper" in dev.name.lower():
            flippers.append(dev)

    if not flippers:
        print("\n❌  Aucun Flipper Zero reconnu automatiquement.")
        print("    → Si ton Flipper apparaît dans la liste ci-dessus, entre son numéro :")
        numbered = list(results.values())
        for i, (dev, _) in enumerate(numbered, start=1):
            print(f"       {i}. {dev.name or '(sans nom)'}  ({dev.address})")
        print(f"       0. Quitter")
        try:
            raw = await asyncio.to_thread(input, "Choix : ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(numbered):
                return numbered[idx][0]
        except (ValueError, EOFError):
            pass
        return None

    if len(flippers) == 1:
        dev = flippers[0]
        print(f"✅  Trouvé : {dev.name}  ({dev.address})")
        return dev

    # Plusieurs Flipper détectés
    print("\n📋  Plusieurs appareils Flipper détectés :")
    for i, d in enumerate(flippers, start=1):
        print(f"    {i}. {d.name}  ({d.address})")

    while True:
        try:
            raw = await asyncio.to_thread(input, "Choisissez le numéro : ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(flippers):
                return flippers[idx]
        except (ValueError, EOFError):
            pass
        print("    Saisie invalide, réessayez.")


# ============================================================
# Envoi du texte (chunked si > BLE_CHUNK_SIZE)
# ============================================================
async def send_text(client: BleakClient, text: str) -> None:
    """Envoie le texte + '\n' au Flipper, découpé en chunks BLE."""
    payload = (text + "\n").encode("utf-8")
    total = len(payload)
    sent = 0

    while sent < total:
        chunk = payload[sent : sent + BLE_CHUNK_SIZE]
        await client.write_gatt_char(NUS_RX_CHAR_UUID, chunk, response=False)
        sent += len(chunk)
        if sent < total:
            await asyncio.sleep(0.05)  # Petit délai entre chunks

    print(f"📤  Envoyé ({total} octets). Confirmez sur le Flipper (bouton OK).")


# ============================================================
# Boucle interactive
# ============================================================
async def interactive_loop(client: BleakClient) -> None:
    print("\n" + "="*55)
    print("  TransTheFlip Client — Flipper Zero BLE Remote HID")
    print("="*55)
    print("  Syntaxe  : texte normal + [TAGS] pour touches spéciales")
    print("  Exemples : Hello[ENTER]   [WIN+r]notepad[ENTER]")
    print("             [CTRL+SHIFT+ESC]   [DELAY:1000]")
    print("  Commandes: quit / exit  → déconnexion")
    print("             help         → rappel de la syntaxe")
    print("="*55 + "\n")

    # S'abonner aux notifications du Flipper (TX)
    await client.start_notify(NUS_TX_CHAR_UUID, _notification_handler)

    while True:
        try:
            raw = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print("\n👋  Déconnexion...")
            break

        text = raw.strip()

        if not text:
            continue

        if text.lower() in ("quit", "exit", "q"):
            print("👋  Déconnexion...")
            break

        if text.lower() == "help":
            print("""
  Touches spéciales (inline dans le texte) :
    [ENTER]  [TAB]  [ESC]  [BACKSPACE]  [DEL]
    [UP]  [DOWN]  [LEFT]  [RIGHT]
    [HOME]  [END]  [PGUP]  [PGDN]
    [F1]..[F12]
    [CTRL+<touche>]   ex: [CTRL+c]  [CTRL+SHIFT+ESC]
    [ALT+<touche>]    ex: [ALT+F4]
    [WIN+<touche>]    ex: [WIN+r]
    [DELAY:<ms>]      ex: [DELAY:500]
""")
            continue

        await send_text(client, text)


# ============================================================
# Point d'entrée
# ============================================================
async def main() -> None:
    device = await scan_for_flipper()
    if device is None:
        return

    print(f"🔗  Connexion à {device.name}...")
    try:
        async with BleakClient(device.address, timeout=15.0) as client:
            if not client.is_connected:
                print("❌  Connexion échouée.")
                return

            mtu = getattr(client, "mtu_size", BLE_CHUNK_SIZE)
            print(f"✅  Connecté ! MTU={mtu}")

            # Vérifier que le service Flipper serial est bien présent
            service_uuids = [s.uuid.lower() for s in client.services]
            if FLIPPER_SERVICE_UUID not in service_uuids:
                print(f"⚠️   Service Flipper serial non trouvé ({FLIPPER_SERVICE_UUID})")
                print("    L'app TransTheFlip est-elle bien lancée sur le Flipper ?")
                return

            await interactive_loop(client)

    except Exception as exc:
        print(f"❌  Erreur BLE : {exc}")
        if "not found" in str(exc).lower() or "no such device" in str(exc).lower():
            print("    L'appareil s'est peut-être éteint ou déconnecté.")


if __name__ == "__main__":
    # Note : WindowsSelectorEventLoopPolicy est déprécié depuis Python 3.12
    # et supprimé en 3.16. bleak 0.21+ gère Windows nativement, pas besoin
    # de le forcer.
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋  Interruption clavier.")
