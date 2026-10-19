"""First-run privacy, explicit remembering, and selective sign-out."""

import tkinter as tk

import pytest

from egeders_downloader import service
from egeders_downloader.gui import Application
from egeders_downloader.models import Account


def test_forget_login_removes_auth_but_preserves_history_and_other_accounts(tmp_path):
    account, other = Account("sample"), Account("other")
    for profile in (account, other):
        folder = tmp_path / "accounts" / profile.slug
        folder.mkdir(parents=True)
        for name in ("storage_state.json", "session_account.json", "manifest.sqlite3"):
            (folder / name).write_text("test fixture")
    settings = service.Settings(tmp_path)
    settings.save({"account": account.key, "selected": {account.key: [1], other.key: [2]}, "output": "D:/materials"})
    service.forget_login(account, tmp_path)
    folder = tmp_path / "accounts" / account.slug
    assert not (folder / "storage_state.json").exists()
    assert not (folder / "session_account.json").exists()
    assert (folder / "manifest.sqlite3").read_text() == "test fixture"
    assert (tmp_path / "accounts" / other.slug / "storage_state.json").is_file()
    assert settings.load() == {"selected": {other.key: [2]}, "output": "D:/materials"}


def test_first_run_is_blank_and_remembering_is_explicit(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Native GUI display is unavailable")
    root.withdraw()
    try:
        settings = service.Settings(tmp_path)
        app = Application(root, settings)
        root.update()
        assert app.username.get() == app.password.get() == ""
        assert not app.remember.get() and not app.selected
        account = Account("sample")
        app.output.set(str(tmp_path / "downloads"))
        app._save(account)
        assert "account" not in settings.load()
        app.remember.set(True)
        app._save(account)
        assert settings.load()["account"] == account.key
        app.remember.set(False)
        app._save(account)
        assert "account" not in settings.load()
    finally:
        root.destroy()
