"""Hardware check: change/restore layouts and transfer text without executing it."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
from bleak import BleakClient
from protocol import RX_UUID, NotificationLines, encode_layout, write_text
from trans_client import FLIPPER_TX_CHAR_UUID
from trans_gui import load_last_device


async def main():
    device = load_last_device()
    assert device, "Connect once with the GUI to remember the test device."
    lines = NotificationLines()
    replies = asyncio.Queue()

    def notify(_, data):
        for line in lines.feed(data):
            replies.put_nowait(line)

    async with BleakClient(device["address"], pair=True, timeout=60,
                           winrt={"use_cached_services": False}) as client:
        await client.start_notify(FLIPPER_TX_CHAR_UUID, notify)

        async def command(data):
            await client.write_gatt_char(RX_UUID, data, response=True)
            return await asyncio.wait_for(replies.get(), 7)

        assert (await command(b"TTF?\n")).startswith("READY:1:")
        original = await asyncio.wait_for(replies.get(), 7)
        assert original.startswith("LAYOUT:"), original
        name = original.removeprefix("LAYOUT:")
        restore = name if name == "QWERTY US" else name + ".kl"
        try:
            assert await command(encode_layout("QWERTY US")) == "LAYOUT:QWERTY US"
            french = await command(encode_layout("fr-FR.kl"))
            assert french in ("LAYOUT:fr-FR", "ERR:LAYOUT"), french
            print("FR layout:", "available" if french == "LAYOUT:fr-FR" else "missing on SD")
            assert await command(encode_layout("ttf-missing.kl")) == "ERR:LAYOUT"
            assert await command(encode_layout("QWERTY US")) == "LAYOUT:QWERTY US"
            await asyncio.sleep(6)  # Check that a completed layout command never times out.
            assert replies.empty(), "Unexpected status after layout command"
        finally:
            assert (await command(encode_layout(restore))).startswith("LAYOUT:")
        await write_text(client, "Layout reception test only")
        assert await asyncio.wait_for(replies.get(), 7) == "RECV"
        assert await command(encode_layout("QWERTY US")) == "ERR:BUSY"
        print("BLE layout/restore/missing-file/busy/transfer checks passed; no HID execution")


if __name__ == "__main__":
    asyncio.run(main())
