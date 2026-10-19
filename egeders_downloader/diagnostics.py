"""Offline installation check: native UI and bundled browser, no account needed."""

import tempfile
import tkinter as tk
from pathlib import Path

from .browser_client import BrowserClient
from .gui import Application
from .models import Account
from .service import DEFAULT_STATE, Settings


def check_installation() -> dict:
    with tempfile.TemporaryDirectory(prefix="egeders-check-") as directory:
        root = tk.Tk()
        root.withdraw()
        try:
            app = Application(root, settings=Settings(Path(directory) / "state"))
            root.update()
            assert app.username.get() == app.password.get() == ""
            assert not app.selected and not app.remember.get()
            tk_version = str(root.tk.call("info", "patchlevel"))
        finally:
            root.destroy()
        # The temporary directory outlives both GUI and browser checks.
        with BrowserClient(Account("installation-check"), Path(directory), log=lambda _: None) as client:
            page = client.context.new_page()
            page.set_content("<title>Ege Ders installation check</title><h1>Ready</h1>")
            assert page.title() == "Ege Ders installation check"
            browser_version = client.browser.version
    return {"ok": True, "first_run_blank": True, "tk": tk_version, "browser": browser_version, "state_directory": str(DEFAULT_STATE)}
