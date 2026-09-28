"""Verify BLE; optional byte count tests reception only. Do not press OK on Flipper."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
from trans_gui import BleWorker


async def main(address, byte_count=0):
    events = []

    def emit(kind, payload):
        events.append((kind, payload))
        if kind != "transfer_progress":
            print(kind, payload, flush=True)

    worker = BleWorker(emit)
    future = asyncio.run_coroutine_threadsafe(worker._connect(address, "Flipper test"), worker._loop)
    try:
        await asyncio.wrap_future(future)
        assert any(kind == "connected" for kind, _ in events), "TTF1 handshake failed"
        await asyncio.sleep(2)
        assert worker._client and worker._client.is_connected, "Link lost after handshake"
        if byte_count:
            assert 0 < byte_count <= worker._max_text_bytes, "Requested size exceeds capacity"
            text = ("TTF reception test. " * byte_count)[:byte_count]
            transfer = asyncio.run_coroutine_threadsafe(worker._send(text), worker._loop)
            await asyncio.wrap_future(transfer)
            assert ("notify", "RECV") in events, "Complete frame was not acknowledged"
            assert not any(kind == "send_error" for kind, _ in events), events
            print(f"REAL BLE {byte_count}-BYTE TRANSFER PASSED — no HID confirmation", flush=True)
        else:
            print("REAL BLE HANDSHAKE PASSED — no text sent", flush=True)
    finally:
        worker.shutdown()
        await asyncio.to_thread(worker._thread.join, 15)
        assert not worker._thread.is_alive(), "Worker failed to disconnect"
        worker._loop.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0))
