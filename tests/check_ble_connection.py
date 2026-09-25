"""Verify a real GUI connection, without sending text or USB keystrokes."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
from trans_gui import BleWorker


async def main(address):
    events = []

    def emit(kind, payload):
        events.append((kind, payload))
        print(kind, payload, flush=True)

    worker = BleWorker(emit)
    future = asyncio.run_coroutine_threadsafe(worker._connect(address, "Flipper test"), worker._loop)
    try:
        await asyncio.wrap_future(future)
        assert any(kind == "connected" for kind, _ in events), "TTF1 handshake failed"
        await asyncio.sleep(2)
        assert worker._client and worker._client.is_connected, "Link lost after handshake"
        print("REAL BLE HANDSHAKE PASSED — no text sent", flush=True)
    finally:
        worker.shutdown()
        await asyncio.to_thread(worker._thread.join, 15)
        assert not worker._thread.is_alive(), "Worker failed to disconnect"
        worker._loop.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1]))
