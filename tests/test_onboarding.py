"""First-run privacy, explicit remembering, selective sign-out, and GUI inventory."""

import tkinter as tk

import pytest

from egeders_downloader import service
from egeders_downloader.gui import Application
from egeders_downloader.models import Account, Activity


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
        assert not app.remember.get() and not app.selected and not app.convert_pptx.get()
        assert "PDF converter:" in app.converter_status.get()
        account = Account("sample")
        app.output.set(str(tmp_path / "downloads"))
        app._save(account)
        assert "account" not in settings.load()
        assert "convert_pptx" not in settings.load()
        app.remember.set(True)
        app.convert_pptx.set(True)
        app._save(account)
        assert settings.load()["account"] == account.key
        assert settings.load()["convert_pptx"] is True
        app.remember.set(False)
        app.convert_pptx.set(False)
        app._save(account)
        assert "account" not in settings.load()
        assert "convert_pptx" not in settings.load()

        # Verify GUI inventory browsing populates the inventory tree from worker queue.
        sample_items = [
            Activity(
                course_id=101,
                id=1,
                title="Lecture 1.pptx",
                section="Week 1",
                kind="resource",
                type_label="File (PPTX)",
                url="https://egeders.ege.edu.tr/mod/resource/view.php?id=1",
                downloadable=True,
                status="Downloadable file resource (PPT/PPTX → PDF supported)",
                course_title="Algorithms",
            ),
            Activity(
                course_id=101,
                id=2,
                title="Discussion Forum",
                section="Week 1",
                kind="forum",
                type_label="Forum",
                url="https://egeders.ege.edu.tr/mod/forum/view.php?id=2",
                downloadable=False,
                status="Unsupported forum (not downloaded or followed)",
                course_title="Algorithms",
            ),
        ]
        app.events.put(("done", ("inventory", sample_items)))
        app._poll()
        children = app.inventory_tree.get_children()
        assert len(children) == 2
        first_row = app.inventory_tree.item(children[0], "values")
        second_row = app.inventory_tree.item(children[1], "values")
        assert first_row[2] == "File (PPTX)"
        assert first_row[3] == "Lecture 1.pptx"
        assert second_row[2] == "Forum"
        assert "Unsupported" in second_row[4]
    finally:
        root.destroy()
