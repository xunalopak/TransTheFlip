# Updating the PC client and Flipper app

**New in v2.2.0:** texts over 4,096 bytes are stored in a temporary file on the
SD card, with a limit of **65,536 ASCII bytes**. The GUI adds **Execute on
Flipper**: after transfer, the button starts typing without pressing OK. The
physical OK button remains available.

**New in v2.1.0:** up to **4,096 ASCII bytes per transfer**, including tags.
Update both the FAP **and** the PC client to use this capacity. The new client
detects the Flipper limit: with an old v2.0.x FAP, connection remains possible,
but the limit stays at 255 bytes.

**Fix in v2.0.2:** removed the duplicate command display at the bottom of the
Flipper screen. The history shows three lines without covering the footer, and
`Up:Log` no longer covers the central message. Replace the FAP; PC client v2.0.1
remains compatible.

**Fix in v2.0.1:** the PC client now listens to the Flipper serial response
channel (FE61), not the flow-control channel (FE63). This fixes the disconnect
after the “TTF1 protocol not responding” message. If FAP v2.0.0 is already
installed, only the PC client needs replacing. The connection was verified on a
Flipper with compatible Momentum firmware, without sending text or USB keys.

Since a 1.x version, install **both new applications**. Transfers use a length
and CRC32; 1.x versions are not compatible. The client refuses to send until the
Flipper app confirms its protocol version.

## Installation

- PC: run `dist/TransTheFlip-GUI.exe`. Python is not required.
- Flipper: copy `dist/trans_the_flip.fap` to `apps/GPIO` on the SD card with
  qFlipper, then open TransTheFlip. Quit the app before copying: its USB keyboard
  mode replaces the qFlipper transfer port.
- The local FAP is built with the official SDK 1.4.3, API 87.1. If the API is
  incompatible with Momentum/Unleashed, rebuild with the SDK for the installed firmware.
- Click Scan, choose the Flipper, then Connect and confirm pairing if prompted.

## PC software

- The last connected device is saved in `%LOCALAPPDATA%/TransTheFlip/settings.json`.
  On the next launch, Connect can reconnect without selecting the device again.
- Diagnostics distinguish timeout, missing device, refused pairing, incompatible
  app, and link loss.
- Enter adds a line; Ctrl+Enter or Send transfers the text.
- The GUI language menu switches all PC interface messages between French and English;
  the command-line client is English-only.
- The progress bar follows Bluetooth transfer, Flipper confirmation, and typing.
  “Done” appears only after the Flipper responds, not after the last BLE write.
- The text stays in the editor after an error. A pending transfer blocks the next
  one. There is no automatic retry after a link loss: check the target PC before
  trying again, because part of the text may already have been typed.
- History contains the 20 latest texts whose typing was confirmed. It remains in
  memory and disappears on exit; only the device name and address are saved on disk.

## Flipper app

- The header separately shows `BT:ON/OFF` and `USB:Ready/OFF`.
- Before confirmation, Up/Down scrolls through the full text. Line breaks and
  tabs appear as `[ENTER]` and `[TAB]`; tags remain visible.
- Right changes the delay after each keystroke: 8, 25, 50, 100, or 250 ms. A key
  hold lasts 12 ms longer. The setting applies to the current session.
- OK confirms; without USB, the app waits for connection to the target PC.
- During typing, the percentage shows progress through text and tags. Back stops
  the transfer, including during a `[DELAY]`, and releases all keys. A Bluetooth
  link loss also stops typing; a USB loss reports an error.
- Text that is too long, lost data, a bad CRC, or an incomplete transfer after
  five seconds is rejected. No received fragment becomes text awaiting confirmation.
- History keeps up to 10 texts in RAM, within a budget of about 8 KiB (two texts at
  the maximum size). Older entries are removed when necessary.

The limit is **65,536 bytes, including tags** with FAP v2.2.0. Texts over 4,096
bytes use a temporary file on the SD card and are deleted at the end. The existing
keyboard accepts ASCII, tabs, and line breaks. Unsupported characters are rejected
explicitly. The existing layout selector remains available with Left.

## Build and verify

```powershell
python -m pip install -r pc_client/requirements.txt pyinstaller
./pc_client/build_windows.ps1
ufbt build

# GCC/MinGW must be in PATH for the C tests; no hardware is required.
./tests/run_checks.ps1
```

The C tests use the production receiver and typing engine with a simulated
keyboard; they do not send real keystrokes.
