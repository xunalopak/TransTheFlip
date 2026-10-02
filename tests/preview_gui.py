"""Open the real GUI with demo data, without Bluetooth or saved-device access."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
import trans_gui


worker = Mock()
worker._thread.is_alive.return_value = False
with patch.object(trans_gui, "BleWorker", return_value=worker), patch.object(
    trans_gui, "load_last_device", return_value=None
):
    app = trans_gui.App()
    app._on_language_change("English")
    app.language_menu.set("English")
    app.title("DuckerTheFlipper — Documentation preview")
    app.device_var.set("Demo Flipper")
    app._handle_event("connected", "Demo Flipper")
    app.entry.insert("1.0", "Hello from DuckerTheFlipper!\n[ENTER]\nType text here, then send it to your Flipper.\n[DELAY:500][CTRL+a]")
    app.log_box.configure(state="normal")
    app.log_box.delete("1.0", "end")
    app.log_box.configure(state="disabled")
    app._log("Documentation preview — demo data only, no Bluetooth connection.")
    app._log("Protocol: TTF1 · Capacity: 65,536 ASCII bytes · Layout: QWERTY US")
    app._log("Send transfers text. Execute on Flipper starts typing after receipt.")
    if "--capture" in sys.argv:
        from PIL import ImageGrab

        if sys.platform != "win32":
            raise SystemExit("Window-only documentation capture requires Windows and Pillow >= 11.2.1.")
        output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
        output.mkdir(parents=True, exist_ok=True)

        def capture():
            try:
                for name in ("desktop", "activity-log"):
                    if name == "activity-log":
                        app._toggle_log()
                        app.geometry("900x950")
                    app.update()
                    # Only this Tk window: never fall back to a desktop/bounding-box grab.
                    window = int(app.wm_frame(), 16)
                    assert window != 0
                    snapshot = ImageGrab.grab(window=window)
                    assert snapshot.width >= app.winfo_width()
                    assert snapshot.height >= app.winfo_height()
                    snapshot.save(output / f"{name}.png")
                    print(f"Captured docs/screenshots/{name}.png")
            finally:
                app.destroy()

        app.after(800, capture)
    app.mainloop()
