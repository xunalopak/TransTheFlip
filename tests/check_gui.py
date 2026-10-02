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
        assert not app._log_visible
        for language in ("English", "Français"):
            app._on_language_change(language)
            for size in ("740x650", "900x800"):
                app.geometry(size)
                for visible in (False, True):
                    if app._log_visible != visible:
                        app._toggle_log()
                    app.update()
                    assert bool(app.log_box.winfo_ismapped()) == visible
                    for widget in (app.entry, app.send_btn, app.execute_btn, app.disconnect_btn,
                                   app.history_menu, app.layout_menu, app.progress_bar, app.log_toggle):
                        assert widget.winfo_ismapped(), (size, visible, widget)
                        right = widget.winfo_rootx() - app.winfo_rootx() + widget.winfo_width()
                        bottom = widget.winfo_rooty() - app.winfo_rooty() + widget.winfo_height()
                        assert right <= app.winfo_width(), (size, visible, widget, right)
                        assert bottom <= app.winfo_height(), (size, visible, widget, bottom)
                        assert widget.winfo_rooty() >= app.winfo_rooty()
                    assert app.entry.winfo_height() >= 40, (size, visible, app.entry.winfo_height())
        app._toggle_log()
        assert "Prêt." in app.log_box.get("1.0", "end")
        app.entry.delete("1.0", "end")
        app.entry.insert("1.0", "first\nsecond")
        app._connected = True
        app._on_send()
        app._worker.send.assert_called_once_with("first\nsecond")
        app._handle_event("notify", "RECV")
        app._on_execute()
        app._worker.execute.assert_called_once()
        app._handle_event("send_error", "Simulated error")
        assert app.entry.get("1.0", "end-1c") == "first\nsecond"
        app._handle_event("capacity", 4096)
        app.entry.delete("1.0", "end")
        app.entry.insert("1.0", "x" * 4096)
        app._on_send()
        app._worker.send.assert_called_with("x" * 4096)
        app._handle_event("send_error", "Simulated error")
        app._worker.send.reset_mock()
        app._handle_event("capacity", 255)
        app._on_send()
        app._worker.send.assert_not_called()
        assert "maximum 255" in app.transfer_label.cget("text")
        app._handle_event("capacity", 4096)
        app.entry.delete("1.0", "end")
        app.entry.insert("1.0", "first\nsecond")
        app._on_send()
        app._handle_event("notify", "RECV")
        app._handle_event("notify", "PROGRESS:50")
        app._handle_event("notify", "OK")
        assert app._history == ["first\nsecond"]
        app.entry.delete("1.0", "end")
        app._restore_history("1. first")
        assert app.entry.get("1.0", "end-1c") == "first\nsecond"
        app._on_language_change("English")
        assert app.send_btn.cget("text") == "Send"
        assert app.disconnect_btn.cget("text") == "Disconnect"
        app._on_language_change("Français")
        assert app.send_btn.cget("text") == "Envoyer"
        assert app.disconnect_btn.cget("text") == "Déconnecter"
        app._connected = True
        app._on_layout_change("AZERTY FR (fr-FR.kl)")
        app._worker.set_layout.assert_called_with("fr-FR.kl")
        app._insert_key("[ENTER]")
        assert "[ENTER]" in app.entry.get("1.0", "end-1c")
        app.update()
        if "--screenshot" in sys.argv:
            from PIL import ImageGrab
            app.entry.delete("1.0", "end")
            app.entry.insert("1.0", "Hello from TransTheFlip!\n[DELAY:500][ENTER]")
            app._handle_event("disconnected", None)
            app._dev_map.clear()
            app.device_var.set(app._tr("scan_first"))
            app.lift()
            app.update()
            x, y = app.winfo_rootx(), app.winfo_rooty()
            preview = Path(__file__).resolve().parents[1] / "build" / "gui-preview.png"
            preview.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(preview)
        print("Real Tk layout, multiline editor, progress and history checks passed")
    finally:
        app.destroy()
