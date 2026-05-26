# TransTheFlip — BLE Remote Bad USB

> 📖 Version française : [README.fr.md](README.fr.md)

Send text from a master PC to a Flipper Zero over Bluetooth LE.
The Flipper automatically types it as USB HID keystrokes on the target PC.

```
[Master PC] ──BLE NUS──▶ [Flipper Zero] ──USB HID──▶ [Target PC]
 trans_client.py            TransTheFlip.fap           (keyboard)
```

## How it works

1. Plug the Flipper via **USB** into the **target PC**
2. Launch the TransTheFlip app on the Flipper
3. **Pair** the Flipper with the **master PC** (Windows) — see below.
   Required: BLE characteristics are protected by authentication.
4. Start `trans_client.py` on the **master PC**
5. Type your text in the client — it appears on the Flipper screen
6. Press **OK** (center button) → the Flipper types the text on the target PC
7. Press **Back** to cancel

> ⚠️ **Pairing is mandatory.** The Flipper serial service requires an encrypted,
> bonded link. The client attempts pairing automatically (`client.pair()`), but
> if it fails: **Windows Settings > Bluetooth & devices > Add a device**, select
> the Flipper, then **confirm the 6-digit code shown on the Flipper screen**.
> Once bonded, restart the client. Without pairing, the connection succeeds but
> **no data is transmitted**.

## Special key syntax

Insert `[TAG]` tokens directly in the text string:

| Tag | Key |
|-----|-----|
| `[ENTER]` | Enter |
| `[TAB]` | Tab |
| `[ESC]` | Escape |
| `[BACKSPACE]` | Backspace |
| `[DEL]` | Delete |
| `[UP]` `[DOWN]` `[LEFT]` `[RIGHT]` | Arrow keys |
| `[HOME]` `[END]` `[PGUP]` `[PGDN]` | Navigation |
| `[F1]`..`[F12]` | Function keys |
| `[CTRL+c]` | Ctrl+C |
| `[ALT+F4]` | Alt+F4 |
| `[WIN+r]` | Win+R (Run) |
| `[CTRL+SHIFT+ESC]` | Task Manager |
| `[DELAY:500]` | Pause 500 ms |

**Examples:**
```
Hello World[ENTER]
[WIN+r]notepad[ENTER]
[CTRL+a][DEL]
ipconfig /all[ENTER]
net user[ENTER][DELAY:1000][ALT+F4]
```

## ⚠️ QWERTY keyboard layout

The ASCII → HID mapping is based on **QWERTY US**.  
If the target PC uses a different layout (e.g. **AZERTY**, **QWERTZ**), special
characters (`@`, `&`, accented chars, etc.) will be wrong.

**Workaround:** temporarily switch the target PC to QWERTY
(Windows: `Win+Space` to cycle layouts) before sending.

> Native AZERTY support is planned: it requires adding an
> `azerty_hid_table[95]` array in `trans_the_flip_hid.c` and a layout
> selection parameter.

## Requirements

### Flipper Zero
- Firmware: **Unleashed** or **Momentum** (tested)
- **ufbt** installed: `pip install ufbt`

### Master PC (Python client — Windows)
- Windows 10/11 with **Bluetooth LE** enabled
- Python ≥ 3.9
- `pip install -r pc_client/requirements.txt`
- Flipper **paired** beforehand (Settings > Bluetooth & devices)

## Build & Install (Flipper)

```bash
# 1. Install ufbt
pip install ufbt

# 2. From the project directory
cd TransTheFlip
ufbt update --channel=release   # download the Unleashed SDK

# 3. Compile
ufbt build

# 4. Deploy (Flipper connected via USB)
ufbt launch
```

The app appears in the **GPIO** menu on the Flipper.

## Using the Python client

```bash
cd pc_client
pip install -r requirements.txt
python trans_client.py
```

```
🔍  Scanning BLE (6s)...
✅  Found: Flipper Zero  (AA:BB:CC:DD:EE:FF)
🔗  Connecting to Flipper Zero...
✅  Connected! MTU=247
🔐  Pairing established (encrypted link).

=======================================================
  TransTheFlip Client — Flipper Zero BLE Remote HID
=======================================================
  Syntax   : plain text + [TAGS] for special keys
  Examples : Hello[ENTER]   [WIN+r]notepad[ENTER]
=======================================================

> [WIN+r]
📤  Sent (7 bytes). Confirm on the Flipper (OK button).
📥  Flipper: text received, waiting for confirmation...
> ✅  Flipper: text sent successfully
```

## Project structure

```
TransTheFlip/
├── application.fam          FAP manifest
├── trans_the_flip.c         Entry point, state machine, main loop
├── trans_the_flip.h         Structs, enums, constants
├── trans_the_flip_hid.{c,h} ASCII→HID table, [TAG] parser, USB HID send
├── trans_the_flip_bt.{c,h}  BLE serial callbacks (NUS)
├── trans_the_flip_view.{c,h} GUI rendering (ViewPort)
└── pc_client/
    ├── trans_client.py      BLE Python client (bleak)
    └── requirements.txt
```

## Technical architecture

### BLE: Flipper Zero proprietary serial service
The Flipper does **not** expose the standard NUS but its own serial service:
- **Service UUID**: `8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000`
- **RX** (PC→Flipper, Write): `19ed82ae-ed21-4c9d-4145-228e62fe0000`
- **TX** (Flipper→PC, Notify): `19ed82ae-ed21-4c9d-4145-228e63fe0000`
- Message terminator: `\n` (newline)
- Flipper→PC status messages: `RECV`, `OK`, `ERR`, `CANCEL`

> 🔐 **Authentication required.** The RX/TX characteristics are declared
> `ATTR_PERMISSION_AUTHEN` in the firmware: the link must be **paired and
> encrypted**. The client calls `client.pair()` and writes with `response=True`
> so that any security rejection raises an exception instead of failing silently.
>
> ⚙️ **Callback override on connect.** On every BLE connection the firmware `bt`
> service registers its own RPC handler over the serial profile, overriding any
> app-level callback. `trans_the_flip_bt.c` re-registers the app callback inside
> `ttf_bt_on_connect()` to reclaim data delivery after the connection event.

### USB HID
- The Flipper switches to **USB HID keyboard** mode at app startup
- Keystrokes are sent in a separate thread to keep the UI responsive
- 12 ms delay between each keystroke (prevents character drops)
- Original USB config is restored on exit

### State machine
```
WaitingBT → Connected → TextReceived → Sending → Done → Connected
                ↑____________________________________________↑
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Connected but no text received / no OK button** | **Flipper not paired.** Pair it via Windows Settings > Bluetooth & devices and confirm the code on the Flipper screen, then restart the client. |
| Flipper not found | Check BLE is enabled and the app is running |
| Wrong characters typed | Switch target PC to QWERTY (see layout section) |
| USB not recognized | Restart the app (restores USB config) |
| `furi_hal_bt_serial.h` not found | Check Unleashed/Momentum SDK version |

## License

**GNU GPLv3** — Copyright © 2026 Romain Champliau — see [LICENSE](LICENSE).

You may use, study, modify and redistribute this project, but **any derivative
work must remain open source under GPLv3** and retain attribution to
Romain Champliau. Nobody can turn it into a closed proprietary product.

> ⚠️ For educational use and authorized security testing only.
> Do not use this tool on systems without explicit permission.
