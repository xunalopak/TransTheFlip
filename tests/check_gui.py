"""Exercise real Tk widgets without connecting to Bluetooth or writing settings."""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
import trans_gui


with patch.object(trans_gui, "BleWorker", return_value=Mock()), patch.object(
    trans_gui, "load_last_device", return_value=None
):
    app = trans_gui.App()
    try:
        app.geometry("740x650")
        app.update()
        for widget in (app.entry, app.send_btn, app.disconnect_btn, app.history_menu, app.progress_bar):
            assert widget.winfo_ismapped()
            right = widget.winfo_rootx() - app.winfo_rootx() + widget.winfo_width()
            bottom = widget.winfo_rooty() - app.winfo_rooty() + widget.winfo_height()
            assert right <= app.winfo_width(), (widget, right)
            assert bottom <= app.winfo_height(), (widget, bottom)
        app.entry.insert("1.0", "first\nsecond")
        app._connected = True
        app._on_send()
        app._worker.send.assert_called_once_with("first\nsecond")
        app._handle_event("send_error", "Simulated error")
        assert app.entry.get("1.0", "end-1c") == "first\nsecond"
        app._on_send()
        app._handle_event("notify", "RECV")
        app._handle_event("notify", "PROGRESS:50")
        app._handle_event("notify", "OK")
        assert app._history == ["first\nsecond"]
        app.entry.delete("1.0", "end")
        app._restore_history("1. first")
        assert app.entry.get("1.0", "end-1c") == "first\nsecond"
        app.update()
        print("Real Tk layout, multiline editor, progress and history checks passed")
    finally:
        app.destroy()
